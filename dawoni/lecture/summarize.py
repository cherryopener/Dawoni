"""강의 요약: 자막을 약 20분 구간으로 나눠 노트(map) → 합쳐 A4 요약본(reduce).

AIHelper에서 긴 강의를 한 번에 넣다가 시간 초과가 났다 → 구간마다 따로 부른다.
코드 검사:
- 노트·요약의 시각이 그 구간·영상 길이 안인가, 범위 ID가 범위 프로필에 있는가
- 요약의 범위 ID는 노트에 나온 것만 (강의에서 다루지 않은 개념을 '다뤘다'고 하지 않게)
- 모든 식이 Word 수식으로 바뀌는가
- 분량: 줄 수를 어림해 A4 1장(또는 양면 1장)을 넘으면 줄여 달라고 요청
교재 범위와의 비교(다룬 개념·빠진 개념·범위 밖 내용)는 **코드가** 노트에서 계산한다.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dawoni.context import ContextEngine, TaskDef, TaskInput
from dawoni.fsio import read_json, write_model_atomic
from dawoni.grading.render import scope_layer
from dawoni.mathx.omml import default_converter
from dawoni.models.lecture import (
    CaptionLine,
    ChunkNotes,
    LectureSource,
    LectureSummary,
    PageMode,
    SummaryDraft,
)
from dawoni.models.scope import ScopeProfile

CHUNK_S = 1200.0
CHUNK_CHARS = 14000
CHARS_PER_LINE = 24
LINES_PER_PAGE = {1: 92, 2: 200}
"""2단 배치, 9.5pt: 1장 ≈ 한 단 52줄 × 2단 − 머리 표, 양면 1장 ≈ 그 두 배 남짓."""

Progress = Callable[[int, int, str], None]


def _noop(done: int, total: int, message: str) -> None:
    pass


CHUNK: TaskDef[ChunkNotes] = TaskDef(
    name="lecture_chunk", template="lecture_chunk.md.j2", output=ChunkNotes,
    rule_target="lecture", timeout_s=600,
)  # fmt: skip
SUMMARY: TaskDef[SummaryDraft] = TaskDef(
    name="lecture_summary", template="lecture_summary.md.j2", output=SummaryDraft,
    rule_target="lecture", timeout_s=900,
)  # fmt: skip


def stamp(s: float) -> str:
    s = int(s)
    return (
        f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"
    )


@dataclass
class Chunk:
    start: float
    end: float
    lines: list[CaptionLine]

    def text(self) -> str:
        """자막 → 30초마다 시각 표시를 붙인 글."""
        out: list[str] = []
        last = -999.0
        for ln in self.lines:
            if ln.start - last >= 30:
                out.append(f"\n[{stamp(ln.start)}] ")
                last = ln.start
            out.append(ln.text + " ")
        return "".join(out).strip()


def chunks(
    src: LectureSource, *, span_s: float = CHUNK_S, max_chars: int = CHUNK_CHARS
) -> list[Chunk]:
    out: list[Chunk] = []
    cur: list[CaptionLine] = []
    start = 0.0
    chars = 0
    for ln in src.lines:
        if cur and (ln.start - start >= span_s or chars + len(ln.text) > max_chars):
            out.append(Chunk(start, ln.start, cur))
            cur, chars, start = [], 0, ln.start
        if not cur:
            start = ln.start
        cur.append(ln)
        chars += len(ln.text)
    if cur:
        out.append(Chunk(start, src.duration_s, cur))
    return out


def _math_problems(formulas: list[str]) -> list[str]:
    return default_converter().problems(formulas) if formulas else []


def check_chunk(n: ChunkNotes, c: Chunk, allowed: set[str]) -> list[str]:
    problems: list[str] = []
    for t in n.topics:
        if not c.start - 5 <= t.start_s <= c.end + 30:
            problems.append(
                f"주제 '{t.title}'의 start_s {t.start_s:g}초가 "
                f"이 구간({stamp(c.start)}~{stamp(c.end)}) 밖이다"
            )
        bad = sorted(set(t.concept_ids) - allowed)
        if bad:
            problems.append(f"주제 '{t.title}': 범위에 없는 ID {bad} (범위 밖이면 beyond_scope에)")
    problems += _math_problems([f for t in n.topics for f in t.formulas])
    return problems


def estimate_lines(d: SummaryDraft) -> int:
    def lines(s: str) -> int:
        return max(1, math.ceil(len(s) / CHARS_PER_LINE))

    n = 3 + lines(d.overview)  # 제목·개요
    for s in d.sections:
        n += 2 + sum(lines(b) for b in s.bullets) + 2 * len(s.formulas)
    if d.key_formulas:
        n += 2 + 2 * len(d.key_formulas)
    if d.tips:
        n += 2 + sum(lines(t) for t in d.tips)
    if d.review_questions:
        n += 2 + sum(lines(q) for q in d.review_questions)
    return n


def check_summary(
    d: SummaryDraft, src: LectureSource, noted: set[str], pages: PageMode
) -> list[str]:
    problems: list[str] = []
    for s in d.sections:
        if not 0 <= s.start_s <= src.duration_s + 5:
            problems.append(f"'{s.heading}'의 start_s {s.start_s:g}초가 영상 길이 밖이다")
        bad = sorted(set(s.concept_ids) - noted)
        if bad:
            problems.append(f"'{s.heading}': 노트에 없는 범위 ID {bad} — 노트에 나온 ID만")
    problems += _math_problems([f for s in d.sections for f in s.formulas] + d.key_formulas)
    budget = LINES_PER_PAGE[pages]
    used = estimate_lines(d)
    if used > budget:
        problems.append(
            f"분량이 약 {used}줄로 A4 {'1장' if pages == 1 else '양면 1장'}({budget}줄)을 넘는다. "
            f"{used - budget}줄 이상 줄일 것 (덜 중요한 항목·중복을 빼고 문장을 짧게)"
        )
    return problems


_TEMPLATES = Path(__file__).parent.parent / "context" / "templates"


def _note_key(c: Chunk, scope: ScopeProfile | None) -> str:
    """구간 노트 재사용 키: 자막 글 + 범위(버전) + 지시문. 하나라도 바뀌면 다시 만든다."""
    h = hashlib.sha256(c.text().encode())
    h.update((f"|{scope.id}@{scope.version}|" if scope else "|-|").encode())
    h.update((_TEMPLATES / CHUNK.template).read_bytes())
    return h.hexdigest()[:20]


@dataclass
class SummaryRun:
    summary: LectureSummary
    notes: list[ChunkNotes]


def summarize(
    engine: ContextEngine,
    src: LectureSource,
    *,
    scope: ScopeProfile | None = None,
    pages: PageMode = 1,
    progress: Progress = _noop,
    note_cache: Path | None = None,
) -> SummaryRun:
    """`note_cache`: 구간 노트 저장 폴더 — 중간에 실패해도 다시 하면 끝난 구간은 건너뛴다."""
    parts = chunks(src)
    allowed: set[str] = scope.allowed_ids() if scope else set()
    total = len(parts) + 1
    notes: list[ChunkNotes] = []
    for k, c in enumerate(parts):
        progress(k, total, f"구간 {k + 1}/{len(parts)} ({stamp(c.start)}~{stamp(c.end)}) 정리")

        cached = note_cache / f"{_note_key(c, scope)}.json" if note_cache else None
        if cached and cached.is_file():
            notes.append(ChunkNotes.model_validate(read_json(cached)))
            continue

        def check(n: ChunkNotes, c: Chunk = c) -> list[str]:
            return check_chunk(n, c, allowed)

        notes.append(
            engine.run(
                dataclasses.replace(CHUNK, name=f"lecture_chunk_{k + 1}", validator=check),
                TaskInput(
                    variables={
                        "span": f"{stamp(c.start)}~{stamp(c.end)}",
                        "level": scope.level if scope else "",
                        "has_scope": scope is not None,
                    },
                    scope=scope_layer(scope) if scope else None,
                    material=c.text(),
                ),
            ).value
        )
        if cached:
            write_model_atomic(cached, notes[-1])
    noted = {cid for n in notes for t in n.topics for cid in t.concept_ids}

    def check_sum(d: SummaryDraft) -> list[str]:
        return check_summary(d, src, noted, pages)

    progress(len(parts), total, "요약본 만들기")
    notes_text = "\n\n".join(
        f"[구간 {k + 1}]\n" + n.model_dump_json(exclude_defaults=True) for k, n in enumerate(notes)
    )
    res = engine.run(
        dataclasses.replace(SUMMARY, validator=check_sum),
        TaskInput(
            variables={
                "title": src.title,
                "duration": stamp(src.duration_s),
                "pages_text": "1장" if pages == 1 else "양면 1장(2쪽)",
                "max_lines": LINES_PER_PAGE[pages],
                "level": scope.level if scope else "",
                "has_scope": scope is not None,
            },
            scope=scope_layer(scope) if scope else None,
            material=notes_text,
        ),
    )
    d = res.value
    covered = sorted(noted)
    missing = [c.id for c in scope.concepts if c.id not in noted] if scope else []
    beyond = list(dict.fromkeys(b.strip() for n in notes for b in n.beyond_scope if b.strip()))
    summary = LectureSummary(
        **d.model_dump(),
        video_id=src.video_id,
        url=src.url,
        video_title=src.title,
        channel=src.channel,
        duration_s=src.duration_s,
        scope_id=scope.id if scope else None,
        pages=pages,
        covered=covered,
        missing=missing,
        beyond=beyond,
        caption_generated=src.generated,
        agent=res.agent,
        model=res.model,
    )
    progress(total, total, "요약 완료")
    return SummaryRun(summary, notes)
