# pyright: basic, reportMissingTypeStubs=false
"""채점 사례집 (2026-11-18 사용자 요청 — "애매한 표현의 점수를 바꾸면 md로 정리해서, 다음 시험
채점이 점점 정교해지게. 컨텍스트 창을 넘지 않게 md 파일로").

    채점 검토에서 강사가 점수를 바꿈 → 사례 기록(코드: 학생 표현·기준·AI 점수 → 강사 점수·이유)
    → AI(가벼운 도우미)가 한 건으로 정리 → `<작업공간>/casebook/case-….md`
    → 다음 시험의 0단계 해설 다듬기·1단계 루브릭에 **그 문항과 맞는 사례만** 넣는다

- 사례 1개 = md 파일 1개 (앞머리 + 본문). 사람이 열어 고쳐도 되고, 본문이 AI에게 그대로 간다.
  `사례집.md`는 사람이 보는 목차 (다시 읽지 않음, 고칠 때마다 새로 씀).
- 강사가 직접 정한 점수라 승인 없이 쌓인다. 끄거나(`enabled`) 고치거나 지울 수 있다(배우기 화면).
- 메모리(`context/memory.py`)는 모든 요청에 들어가 600자 상한이 있다. 사례집은 상한 없이 쌓이고,
  쓸 때 문항 글·해설과 겹치는 열쇠말(`keywords`)로 골라 `PROMPT_CHARS`까지만 넣는다.
- 학생 이름은 남기지 않는다 (시험·문항·소문항만). 같은 판단이 또 나오면 새로 만들지 않고 그 사례에
  출처만 더한다(`duplicate_of`).
- 채점 하나하나에는 넣지 않는다 — 루브릭(문항마다 한 번)에 들어가면 채점이 따른다 (토큰).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from dawoni.context import TaskDef, TaskInput
from dawoni.fsio import write_text_atomic
from dawoni.models.base import DawoniModel, new_id, utcnow
from dawoni.paths import WorkspacePaths

log = logging.getLogger(__name__)

CASE_DIR = "casebook"
INDEX = "사례집.md"
PROMPT_CHARS = 2400
"""한 문항의 프롬프트에 넣는 사례 글자 수 상한 (약 10건)."""
ENTRY_CHARS = 600
_FRONT = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.S)
GENERIC = frozenset(
    {"서술형", "단답형", "선택형", "문항", "문제", "답", "정답", "답안", "풀이", "해설", "설명",
     "이유", "계산", "점수", "채점", "기준", "학생", "표현", "부분점수", "그래프", "그림", "식",
     "값", "단위"}
)  # fmt: skip
"""열쇠말로 치지 않는 말 — 거의 모든 문항 글에 있다 (실측 2026-11-18: AI가 '서술형'을 열쇠말로 씀,
문항 글의 "[서술형 8점]"과 겹친다)."""

Verdict = Literal["인정", "불인정"]
"""강사가 AI보다 점수를 더 줌 = 인정, 덜 줌 = 불인정."""


class Case(DawoniModel):
    id: str = Field(default_factory=lambda: new_id("case"))
    title: str = Field(min_length=1, max_length=120)
    verdict: Verdict
    keywords: list[str] = Field(default_factory=lambda: list[str](), max_length=12)
    """이 사례가 다시 쓰일 문항을 찾는 말 (개념·현상·장치·물리량)."""
    reusable: bool = True
    """다른 문항에도 쓸 만한가 (이 문항에만 해당하면 기록으로만 남는다)."""
    enabled: bool = True
    exam: str = ""
    question: str = ""
    part: str = ""
    ai_points: float | None = None
    teacher_points: float | None = None
    max_points: float | None = None
    created_by: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    body: str = Field(min_length=1, max_length=4000)
    """AI에게 그대로 가는 본문 (md)."""

    @property
    def live(self) -> bool:
        return self.enabled and self.reusable


def dump_case(c: Case) -> str:
    data = c.model_dump(mode="json", exclude={"body"})
    head = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in data.items())
    return f"---\n{head}\n---\n\n{c.body.strip()}\n"


def parse_case(text: str, *, fallback_id: str) -> Case:
    m = _FRONT.match(text.replace("\r\n", "\n"))
    if not m:
        raise ValueError("앞머리(--- … ---)가 없음")
    meta: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, raw = line.split(":", 1)
        try:
            meta[key.strip()] = json.loads(raw.strip())
        except json.JSONDecodeError:
            meta[key.strip()] = raw.strip().strip("'\"")  # 손으로 고친 값
    meta.setdefault("id", fallback_id)
    return Case.model_validate({**meta, "body": m.group(2).strip()})


def _n(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _fmt(x: float | None) -> str:
    if x is None:
        return "?"
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


class CaseStore:
    def __init__(self, ws: WorkspacePaths) -> None:
        self.dir = ws.casebook

    def _path(self, case_id: str) -> Path:
        if not re.fullmatch(r"case-[0-9a-f]{32}", case_id):
            raise KeyError(f"사례 {case_id} 없음")
        return self.dir / f"{case_id}.md"

    def all(self) -> list[Case]:
        """새 것부터."""
        if not self.dir.is_dir():
            return []
        out: list[Case] = []
        for p in self.dir.glob("case-*.md"):
            try:
                out.append(parse_case(p.read_text(encoding="utf-8"), fallback_id=p.stem))
            except (ValueError, ValidationError) as e:
                log.warning("사례 파일을 읽지 못함: %s (%s)", p, e)
        return sorted(out, key=lambda c: c.created_at, reverse=True)

    def get(self, case_id: str) -> Case:
        p = self._path(case_id)
        if not p.is_file():
            raise KeyError(f"사례 {case_id} 없음")
        return parse_case(p.read_text(encoding="utf-8"), fallback_id=case_id)

    def save(self, c: Case) -> Case:
        c = c.model_copy(update={"updated_at": utcnow()})
        write_text_atomic(self._path(c.id), dump_case(c))
        self.write_index()
        return c

    def delete(self, case_id: str) -> bool:
        p = self._path(case_id)
        if not p.is_file():
            return False
        p.unlink()
        self.write_index()
        return True

    def write_index(self) -> None:
        """사람이 보는 목차 (`사례집.md`) — 다시 읽지 않는다."""
        cases = self.all()
        lines = [
            "# 채점 사례집",
            "",
            "강사가 채점 검토에서 AI 점수를 고친 사례. 다워니가 다음 시험의 해설 다듬기(0단계)·"
            "루브릭(1단계)에 그 문항과 맞는 것만 넣는다. 사례 파일을 고치면 고친 대로 쓰인다.",
            "",
        ]
        for c in cases:
            state = "" if c.live else (" (끔)" if not c.enabled else " (이 문항만)")
            src = f"{c.exam} {c.question}번{(' ' + c.part) if c.part else ''}".strip()
            lines.append(
                f"- [{c.verdict}] {c.title}{state} — {src}, {c.created_at:%Y-%m-%d} "
                f"([{c.id}.md]({c.id}.md))"
            )
        self.dir.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self.dir / INDEX, "\n".join(lines) + "\n")

    def relevant(self, text: str, *, limit_chars: int = PROMPT_CHARS) -> list[Case]:
        """문항 글·해설과 열쇠말이 겹치는 사례 (겹침 많은 것, 새 것부터) — `limit_chars`까지.
        열쇠말이 셋 이상이면 둘 이상 겹쳐야 한다 ("힘" 하나로 모든 역학 사례가 딸려 오지 않게)."""
        hay = _n(text)
        scored: list[tuple[int, datetime, Case]] = []
        for c in self.all():
            if not c.live:
                continue
            given = [k for k in dict.fromkeys(_n(k) for k in c.keywords) if len(k) >= 2]
            hits = sum(k in hay for k in given if k not in GENERIC)  # 흔한 말은 겹쳐도 치지 않음
            if hits and hits >= (1 if len(given) <= 2 else 2):
                scored.append((hits, c.created_at, c))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        out: list[Case] = []
        used = 0
        for _, _, c in scored:
            size = len(entry_text(c))
            if used + size > limit_chars:
                continue
            out.append(c)
            used += size
        return out


def entry_text(c: Case) -> str:
    src = f"{c.exam} {c.question}번{(' ' + c.part) if c.part else ''}".strip()
    body = c.body.strip()
    if len(body) > ENTRY_CHARS:
        body = body[:ENTRY_CHARS] + " …"
    return f"### [{c.verdict}] {c.title} ({src})\n{body}"


def prompt_text(cases: list[Case]) -> str | None:
    """프롬프트의 [기준 예시] 자리에 넣을 글. 없으면 None."""
    if not cases:
        return None
    head = (
        "[지난 채점 사례] 강사가 지난 시험의 채점 검토에서 AI 점수를 직접 고친 사례다. 이 문항에 "
        "같은 상황(같은 개념·같은 종류의 표현)이 있을 때만 따른다 — 값·기호가 다른 문항이면 "
        "뜻만 따른다."
    )
    return "\n\n".join([head, *(entry_text(c) for c in cases)])


# ---------------------------------------------------------------- 기록 (채점 검토에서)


@dataclasses.dataclass
class PartChange:
    """강사가 바꾼 소문항 점수 하나."""

    label: str
    """채점 검토의 소문항 표시 (`2-(1)`, 소문항이 없으면 `2`)."""
    part: str
    """문항 안 소문항 표시 (`(1)`, 없으면 빈 문자열)."""
    ai: float
    teacher: float
    max: float
    ai_good: list[str]
    ai_bad: list[str]
    note: str = ""


def changes(
    question: str, before: Any, after: Any, rubric: Any, notes: dict[str, str]
) -> list[PartChange]:
    """채점 결과 앞뒤로 소문항 점수가 바뀐 곳 (`notes`: 소문항 표시(`(1)`·'') → 강사 이유)."""
    from dawoni.export.sheet import part_grades

    old = part_grades(question, before, rubric)
    new = part_grades(question, after, rubric)
    out: list[PartChange] = []
    for label, pg in new.items():
        was = old.get(label)
        if was is None or abs(was.awarded - pg.awarded) < 1e-9:
            continue
        part = label.split("-", 1)[1] if "-" in label else ""
        out.append(
            PartChange(
                label=label,
                part=part,
                ai=was.awarded,
                teacher=pg.awarded,
                max=pg.points,
                ai_good=[r for _, r in was.good if r],
                ai_bad=[r for _, r in was.bad if r],
                note=(notes.get(part) or "").strip(),
            )
        )
    return out


class CaseDraft(DawoniModel):
    """AI 출력: 강사가 바꾼 점수 하나를 사례 한 건으로."""

    keep: bool = Field(
        description="다음 시험에 쓸 판단이 있으면 true. 판독 실수·AI의 단순한 착오처럼 배울 것이 "
        "없으면 false"
    )
    duplicate_of: str | None = Field(
        default=None, description="[비슷한 사례]에 같은 판단이 이미 있으면 그 ID (새로 만들지 않음)"
    )
    title: str = Field(
        default="",
        max_length=80,
        description="한 줄 요약 (예: '\"더 많이 꺾인다\" = 굴절률이 크다')",
    )
    expression: str = Field(
        default="", max_length=200, description="판단이 갈린 학생 표현 (판독문에서 그대로 인용)"
    )
    criterion: str = Field(default="", max_length=200, description="그 표현을 견준 채점 기준")
    lesson: str = Field(
        default="",
        max_length=300,
        description="다음부터 기준을 세울 때 쓸 규칙 한두 문장. 이 문항의 값·기호에 묶이지 않게 "
        "쓰되 강사의 판단을 넓히지 않는다",
    )
    keywords: list[str] = Field(
        default_factory=lambda: list[str](),
        max_length=8,
        description="이 사례가 다시 쓰일 문항을 찾을 말 3~6개: 개념·현상·장치·물리량 이름 "
        "(예: 렌즈, 굴절, 초점거리). 문제마다 바뀌는 값·기호는 넣지 않는다",
    )
    reusable: bool = Field(
        default=True, description="다른 문항에도 쓸 판단이면 true, 이 문항에만 해당하면 false"
    )
    reason: str = Field(min_length=1, max_length=300, description="강사에게 보일 한 문장")


WRITE_CASE: TaskDef[CaseDraft] = TaskDef(
    role="light_helper",
    name="write_case",
    template="write_case.md.j2",
    output=CaseDraft,
    rule_target=None,  # 정리하는 작업에 기존 규칙·메모리를 지시로 넣지 않음
    timeout_s=240,
)


def _clip(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[:n] + " …"


def case_material(store: Any, number: str, student: str, ch: PartChange) -> str:
    """사례 정리 자료: 문항, 이 소문항의 채점 기준·해설, 학생 판독문, AI 판단, 강사 점수."""
    from dawoni.export.sheet import sheet_lines
    from dawoni.grading.render import exam_question_text

    q = store.exam().question(number)
    rubric = store.rubric(number)
    key = store.answer_key(number)
    t = store.transcript(student, number)
    items = [i for i in (rubric.items if rubric else []) if (i.part or "") == ch.part]
    lines = [x for x in sheet_lines(number, t.lines if t else []) if x.part == ch.label]
    answer = " ".join(x.math or x.text for x in lines if not x.struck).strip()
    kp = key.part(ch.part) if key else None
    out = [
        f"[문항 {number}{(' ' + ch.part) if ch.part else ''}]",
        _clip(exam_question_text(q), 1500),
        "",
        "[이 소문항의 채점 기준]",
        *[f"- {i.id} ({_fmt(i.points)}점) {i.criterion}" for i in items],
    ]
    if kp is not None:
        out += ["", "[해설지]", _clip(f"답: {kp.answer or '-'}\n{kp.solution}", 800)]
    out += [
        "",
        "[학생 답안 (판독문)]",
        _clip(answer, 1200) or "(빈 답안)",
        "",
        f"[AI 채점] {_fmt(ch.ai)} / {_fmt(ch.max)}점",
        *[f"- 맞음: {r}" for r in ch.ai_good],
        *[f"- 감점: {r}" for r in ch.ai_bad],
        "",
        f"[강사가 바꾼 점수] {_fmt(ch.teacher)} / {_fmt(ch.max)}점",
        f"강사 이유: {ch.note or '(적지 않음 — AI 판단과 강사 점수의 차이에서 읽는다)'}",
    ]
    return "\n".join(out)


def _body(d: CaseDraft, ch: PartChange) -> str:
    out = [
        f"**다음부터**: {d.lesson.strip()}",
        "",
        f"- 기준: {d.criterion.strip()}",
        f'- 학생 표현: "{d.expression.strip()}"',
        f"- 점수: AI {_fmt(ch.ai)}점 → 강사 {_fmt(ch.teacher)}점 (만점 {_fmt(ch.max)}점)",
    ]
    if ch.note:
        out.append(f"- 강사 메모: {ch.note}")
    return "\n".join(out)


@dataclasses.dataclass
class Recorded:
    case: Case | None
    """새로 만들거나 출처를 더한 사례 (배울 것이 없었으면 None)."""
    added: bool
    """새 사례면 True, 같은 사례에 출처만 더했으면 False."""
    reason: str


def record(
    svc: Any, store: Any, number: str, student: str, ch: PartChange, *, by: str | None
) -> Recorded:
    """강사가 바꾼 점수 하나를 사례집에 (AI 호출 1번, 가벼운 도우미)."""
    cases = CaseStore(svc.workspace)
    exam = store.exam()
    material = case_material(store, number, student, ch)
    similar = cases.relevant(material, limit_chars=PROMPT_CHARS)
    ids = {c.id for c in similar}
    verdict: Verdict = "인정" if ch.teacher > ch.ai else "불인정"

    def check(d: CaseDraft) -> list[str]:
        if d.duplicate_of and d.duplicate_of not in ids:
            return [f"duplicate_of에 없는 사례 ID: {d.duplicate_of} ([비슷한 사례]의 ID만)"]
        if not d.keep or d.duplicate_of:
            return []
        missing = [
            n for n in ("title", "expression", "criterion", "lesson") if not getattr(d, n).strip()
        ]
        problems = [f"keep이면 {', '.join(missing)}을 채울 것"] if missing else []
        if len([k for k in d.keywords if k.strip()]) < 2:
            problems.append("keywords를 두 개 이상")
        return problems

    known = "\n\n".join(f"ID {c.id}\n{entry_text(c)}" for c in similar) or "(없음)"
    res = svc.engine.run(
        dataclasses.replace(WRITE_CASE, validator=check),
        TaskInput(
            variables={"verdict": verdict, "up": ch.teacher > ch.ai},
            material=material,
            examples=f"[비슷한 사례]\n{known}",
        ),
    )
    d: CaseDraft = res.value
    if not d.keep:
        return Recorded(None, False, d.reason)
    src = f"{exam.title} {number}번{(' ' + ch.part) if ch.part else ''}"
    if d.duplicate_of:
        old = cases.get(d.duplicate_of)
        when = f"{utcnow():%Y-%m-%d}"
        line = f"- 같은 판단: {src} (AI {_fmt(ch.ai)}점 → 강사 {_fmt(ch.teacher)}점, {when})"
        saved = cases.save(old.model_copy(update={"body": f"{old.body.rstrip()}\n{line}"}))
        return Recorded(saved, False, d.reason)
    case = Case(
        title=d.title.strip(),
        verdict=verdict,
        keywords=[k.strip() for k in d.keywords if k.strip()],
        reusable=d.reusable,
        exam=exam.title,
        question=number,
        part=ch.part,
        ai_points=ch.ai,
        teacher_points=ch.teacher,
        max_points=ch.max,
        created_by=by,
        body=_body(d, ch),
    )
    return Recorded(cases.save(case), True, d.reason)
