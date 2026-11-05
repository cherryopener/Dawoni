"""강사가 손채점한 답안지에서 빨간 펜 소문항 점수 읽기 (M5, PLAN.md §6 2단계).

답안지의 답안 쪽에는 소문항 줄마다 "배점 | 점수" 칸이 있고, 강사가 점수 칸에 빨간 펜으로 점수를
적는다 (실측: 3쪽 × 24명, 소문항 8개). 표지 판독(`cover_reader`, fast)이 학생 한 명의 답안 쪽을 한
호출로 읽는다 — 답안 글은 읽지 않으므로 그림만 들고 프롬프트는 짧다.

- 읽을 칸은 시험의 소문항 표시 그대로 준다 (`2 · (1)① · 2점`). 없는 칸·배점을 넘는 점수는 수리 요청.
- 칸 표기(`2-(1)①`, 띄어쓰기)·같은 칸을 같은 점수로 두 번 적은 것은 코드가 고친다 (§2-5 선수리).
- 못 읽은 칸은 None으로 남긴다 — 짐작으로 채우지 않고 강사가 확인할 때 채운다.
- 표지 문항 총점과 소문항 합이 다르면 표시한다 (§14 빨간 펜 판독 위험, `problems`).
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Collection, Mapping
from pathlib import Path

from pydantic import Field

from dawoni.context import ContextEngine, TaskDef, TaskInput
from dawoni.models.base import DawoniModel
from dawoni.models.grading import Exam, StudentRecord

Marks = dict[str, dict[str, float | None]]
"""문항 → 소문항(없으면 빈 글자) → 강사 점수 (None = 읽지 못함)."""


class ReadMark(DawoniModel):
    question: str = Field(description="문항 번호 (읽을 칸 목록 그대로)")
    part: str = Field(default="", description="소문항 표시 (읽을 칸 목록 그대로, 없으면 빈 글자)")
    points: float | None = Field(
        default=None, ge=0, description="점수 칸의 강사 점수. 비었거나 안 보이면 null"
    )
    uncertain: str | None = Field(default=None, description="숫자가 애매하면 다른 가능성")


class MarksRead(DawoniModel):
    """AI 출력: 답안 쪽 점수 칸의 강사 점수."""

    marks: list[ReadMark] = Field(default_factory=lambda: list[ReadMark]())
    notes: list[str] = Field(default_factory=lambda: list[str](), description="강사에게 알릴 점")


READ_MARKS: TaskDef[MarksRead] = TaskDef(
    role="cover_reader",
    name="read_marks",
    template="read_marks.md.j2",
    output=MarksRead,
    rule_target="transcribe",
    timeout_s=600,  # 표지와 같게 (flash-medium 표지 1장이 300초를 넘긴 적 있음)
)


@dataclasses.dataclass(frozen=True)
class Slot:
    question: str
    part: str
    points: float


def slots(exam: Exam) -> list[Slot]:
    out: list[Slot] = []
    for q in exam.questions:
        out += [Slot(q.number, p.label, p.points) for p in q.parts] or [
            Slot(q.number, "", q.points)
        ]
    return out


_SPACE = re.compile(r"\s+")
_NO_PART = {"", "-", "전체", "(소문항없음)", "없음"}


def _norm_part(question: str, part: str) -> str:
    p = _SPACE.sub("", part)
    for prefix in (f"{question}-", f"{question}번", f"{question}.", question):
        if p.startswith(prefix) and p[len(prefix) : len(prefix) + 1] in ("(", ""):
            p = p[len(prefix) :]
            break
    return "" if p in _NO_PART else p


def fix_marks(v: MarksRead, exam: Exam) -> tuple[MarksRead, list[str]]:
    """칸 표기만 고친다: 띄어쓰기·문항 번호를 붙인 소문항(`2-(1)①`)·같은 점수로 두 번 적은 칸."""
    known = {(s.question, s.part) for s in slots(exam)}
    fixes: list[str] = []
    seen: dict[tuple[str, str], ReadMark] = {}
    out: list[ReadMark] = []
    for m in v.marks:
        q = _SPACE.sub("", m.question).removeprefix("문항").removesuffix("번")
        part = _norm_part(q, m.part)
        if (q, m.part) != (m.question, part) and (q, part) in known:
            fixes.append(f"칸 표기 {m.question} {m.part!r} → {q} {part!r}")
            m = m.model_copy(update={"question": q, "part": part})
        key = (m.question, m.part)
        prev = seen.get(key)
        if prev is not None and prev.points == m.points:
            fixes.append(f"같은 칸 두 번: {m.question} {m.part}")
            continue
        seen[key] = m
        out.append(m)
    return v.model_copy(update={"marks": out}), fixes


def check_marks(v: MarksRead, exam: Exam) -> list[str]:
    caps = {(s.question, s.part): s.points for s in slots(exam)}
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for m in v.marks:
        key = (m.question, m.part)
        if key not in caps:
            listed = ", ".join(f"{q} {p}".strip() for q, p in caps)
            problems.append(f"칸 '{m.question} {m.part}'은 읽을 칸 목록에 없다. 목록: {listed}")
            continue
        if key in seen:
            problems.append(f"칸 '{m.question} {m.part}'을 두 번 적었다 (점수가 다름) — 하나만")
        seen.add(key)
        if m.points is not None and m.points > caps[key] + 1e-9:
            problems.append(
                f"칸 '{m.question} {m.part}' 점수 {m.points:g}가 배점 {caps[key]:g}점을 넘는다 — "
                "배점 열의 인쇄된 숫자가 아니라 점수 칸의 강사 숫자를 읽을 것 (안 보이면 null)"
            )
    return problems


@dataclasses.dataclass
class MarksOutcome:
    marks: Marks
    uncertain: list[str]
    notes: list[str]


def read_marks(engine: ContextEngine, exam: Exam, pages: list[Path]) -> MarksOutcome:
    """학생 한 명의 답안 쪽(세운 그림) → 소문항별 강사 점수. 호출 1번."""

    def check(v: MarksRead) -> list[str]:
        return check_marks(v, exam)

    def fix(v: MarksRead) -> tuple[MarksRead, list[str]]:
        return fix_marks(v, exam)

    ss = slots(exam)
    out = engine.run(
        dataclasses.replace(READ_MARKS, validator=check, fix=fix),
        TaskInput(
            variables={"n_pages": len(pages), "slots": [dataclasses.asdict(s) for s in ss]},
            attachments=tuple(pages),
        ),
    ).value
    got = {(m.question, m.part): m for m in out.marks}
    marks: Marks = {}
    uncertain: list[str] = []
    for s in ss:
        m = got.get((s.question, s.part))
        marks.setdefault(s.question, {})[s.part] = m.points if m else None
        if m and m.uncertain:
            uncertain.append(f"{s.question} {s.part}".strip() + f": {m.uncertain}")
    return MarksOutcome(marks, uncertain, list(out.notes))


def label(q: str, part: str) -> str:
    return f"{q}{'-' + part if part else ''}"


def fill_from_cover(marks: Marks, cover: Mapping[str, float], exam: Exam) -> list[str]:
    """비어 있는 점수 칸을 표지 문항 총점으로 채운다 (제자리, 채운 것을 알림으로 돌려줌).

    실측: 빈 답안은 강사가 점수 칸을 비워 두고 표지에만 0을 적었다. 표지가 0점이면 빈 칸은
    모두 0, 빈 칸이 하나뿐이면 표지 총점 − 나머지 합 (0~배점일 때만). 강사가 확인할 때 본다."""
    caps = {(s.question, s.part): s.points for s in slots(exam)}
    notes: list[str] = []
    for q, parts in marks.items():
        total = cover.get(q)
        empty = [p for p, v in parts.items() if v is None]
        if total is None or not empty:
            continue
        if abs(total) < 1e-9:
            for p in empty:
                parts[p] = 0.0
        elif len(empty) == 1:
            rest = total - sum(v for v in parts.values() if v is not None)
            if not -1e-9 <= rest <= caps.get((q, empty[0]), 0.0) + 1e-9:
                continue
            parts[empty[0]] = max(rest, 0.0)
        else:
            continue
        cells = ", ".join(label(q, p) for p in empty)
        got = ", ".join(f"{parts[p]:g}" for p in empty)
        notes.append(f"{cells}: 점수 칸이 비어 표지 총점 {total:g}점에 맞춰 {got}점으로 채움")
    return notes


def problems(
    record: StudentRecord, exam: Exam, questions: Collection[str] | None = None
) -> list[str]:
    """강사가 확인할 점 (코드, 토큰 0): 못 읽은 칸, 표지 문항 총점 ≠ 소문항 합, 없는 칸.
    `questions`: 이 문항들만 본다 (강사 채점으로 넣을 문항)."""
    out: list[str] = []
    known = {(s.question, s.part) for s in slots(exam)}
    for q, parts in record.teacher_marks.items():
        if questions is not None and q not in questions:
            continue
        for part, pts in parts.items():
            if (q, part) not in known:
                out.append(f"{label(q, part)}: 이 시험에 없는 칸")
            elif pts is None:
                out.append(f"{label(q, part)}: 점수를 읽지 못함")
        cover = record.teacher_scores.get(q)
        vals = list(parts.values())
        if cover is not None and vals and all(v is not None for v in vals):
            total = sum(v for v in vals if v is not None)
            if abs(total - cover) > 1e-9:
                out.append(f"문항 {q}: 소문항 합 {total:g}점 ≠ 표지 {cover:g}점")
    return out
