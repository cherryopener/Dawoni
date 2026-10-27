"""강사 보정 (M8): 기준 예시 고르기·프롬프트용 글·일치도.

기준 예시 = 같은 문항에서 강사가 확정한 채점 (`teacher_confirmed`).
- 채점 대상 학생 자신의 답안은 **절대** 예시로 넣지 않는다 (재채점·효과 측정에서 답이 새지 않게).
- 루브릭 구조(항목 ID·배점)가 지금 루브릭과 같은 예시만 쓴다.
- 고르는 순서: 강사가 고정한 예시 → 강사가 AI 점수를 고친 사례(AI가 어디서 어긋나는지 가장
  많이 알려 줌) → 강사가 직접 채점한 답안 → 확정만 한 답안. 같은 총점이 겹치지 않게 다양하게,
  같은 점수대에서는 대상 답안과 비슷한 것을 먼저.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dawoni.context import Shrinkable
from dawoni.fsio import read_json, write_model_atomic
from dawoni.grading.render import transcript_text
from dawoni.grading.store import ExamStore, safe_name
from dawoni.models.calibration import AgreementStats, CalibrationEval, CalibrationSettings
from dawoni.models.grading import GradeResult, ItemJudgement, Rubric, Transcript

ExemplarKind = Literal["correction", "manual", "confirmed"]
_KIND_WEIGHT: dict[ExemplarKind, float] = {"correction": 3.0, "manual": 2.0, "confirmed": 1.0}
KIND_LABEL: dict[ExemplarKind, str] = {
    "correction": "강사가 AI 점수를 고친 채점",
    "manual": "강사가 직접 채점",
    "confirmed": "강사가 확인한 채점",
}


# ---------------------------------------------------------------- 설정 저장


def settings_path(store: ExamStore, q: str) -> Path:
    return store.root / "calibration" / f"{safe_name(q)}.json"


def load_settings(store: ExamStore, q: str) -> CalibrationSettings:
    p = settings_path(store, q)
    if p.exists():
        return CalibrationSettings.model_validate(read_json(p))
    return CalibrationSettings(question_number=q)


def save_settings(store: ExamStore, s: CalibrationSettings) -> None:
    write_model_atomic(settings_path(store, s.question_number), s)


def save_eval(store: ExamStore, ev: CalibrationEval) -> None:
    write_model_atomic(store.root / "calibration" / "evals" / f"{ev.id}.json", ev)


def list_evals(store: ExamStore, q: str) -> list[CalibrationEval]:
    d = store.root / "calibration" / "evals"
    evs = (
        [CalibrationEval.model_validate(read_json(p)) for p in d.glob("*.json")]
        if d.is_dir()
        else []
    )
    return sorted(
        (e for e in evs if e.question_number == q), key=lambda e: e.created_at, reverse=True
    )


# ---------------------------------------------------------------- 후보


@dataclass
class Exemplar:
    grade: GradeResult
    transcript: Transcript
    kind: ExemplarKind
    pinned: bool = False

    @property
    def student(self) -> str:
        return self.grade.student_id

    @property
    def changed_items(self) -> dict[str, tuple[float, float]]:
        """강사가 고친 항목: ID → (AI 점수, 강사 점수)."""
        if not self.grade.ai_judgements:
            return {}
        ai = {j.rubric_item_id: j.awarded for j in self.grade.ai_judgements}
        return {
            j.rubric_item_id: (ai[j.rubric_item_id], j.awarded)
            for j in self.grade.judgements
            if j.rubric_item_id in ai and ai[j.rubric_item_id] != j.awarded
        }


def compatible(g: GradeResult, rubric: Rubric) -> bool:
    """예시의 항목 구조(ID·항목별 배점)가 지금 루브릭과 같은가."""
    if g.rubric_version == rubric.version:
        return True
    return bool(g.item_points) and g.item_points == {i.id: i.points for i in rubric.items}


def kind_of(g: GradeResult) -> ExemplarKind:
    if g.agent is None:
        return "manual"
    if g.ai_judgements and any(
        a.awarded != b.awarded for a, b in zip(g.ai_judgements, g.judgements, strict=False)
    ):
        return "correction"
    return "confirmed"


def candidates(
    store: ExamStore, q: str, rubric: Rubric, *, exclude_student: str | None = None
) -> list[Exemplar]:
    settings = load_settings(store, q)
    out: list[Exemplar] = []
    for st in store.students():
        if st == exclude_student or st in settings.excluded:
            continue
        g = store.grade(st, q)
        t = store.transcript(st, q)
        if g is None or t is None or g.status != "teacher_confirmed" or not compatible(g, rubric):
            continue
        out.append(Exemplar(g, t, kind_of(g), pinned=st in settings.pinned))
    return out


# ---------------------------------------------------------------- 고르기

_HANGUL_OR_WORD = re.compile(r"[가-힣]|[A-Za-z]+|\d+|[=+\-*/^()]")


def _shingles(t: Transcript) -> set[str]:
    toks = _HANGUL_OR_WORD.findall(" ".join(x.joined for x in t.lines if not x.struck))
    return {a + b for a, b in itertools.pairwise(toks)} or set(toks)


def similarity(a: Transcript, b: Transcript) -> float:
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def select_examples(cands: list[Exemplar], target: Transcript, k: int) -> list[Exemplar]:
    if k <= 0:
        return []
    chosen = [c for c in cands if c.pinned][:k]
    rest = [c for c in cands if not c.pinned]
    sim = {c.student: similarity(c.transcript, target) for c in rest}

    def score(c: Exemplar, seen_totals: list[float]) -> float:
        dup = seen_totals.count(c.grade.total)
        return _KIND_WEIGHT[c.kind] + sim[c.student] - 1.5 * dup

    while len(chosen) < k and rest:
        seen = [c.grade.total for c in chosen]
        best = max(rest, key=lambda c: (score(c, seen), c.student))
        chosen.append(best)
        rest.remove(best)
    return chosen


# ---------------------------------------------------------------- 프롬프트용 글


def _judgement_line(
    j: ItemJudgement, points: float, ex: Exemplar, changed: dict[str, tuple[float, float]]
) -> str:
    rid = j.rubric_item_id
    head = f"- {rid} {j.awarded:g}/{points:g}점"
    note = ex.grade.teacher_item_notes.get(rid)
    if rid in changed:
        ai, teacher = changed[rid]
        body = f" (AI는 {ai:g}점을 줬으나 강사가 {teacher:g}점으로 고침)"
        return head + body + (f": {note}" if note else "")
    reason = note or j.deduction_reason or j.reason
    return head + (f": {reason}" if reason else "")


def exemplar_text(ex: Exemplar, n: int, rubric: Rubric, *, with_answer: bool = True) -> str:
    points = {i.id: i.points for i in rubric.items}
    changed = ex.changed_items
    g = ex.grade
    lines = [f"[기준 예시 {n} — {KIND_LABEL[ex.kind]}] 총점 {g.total:g}/{g.max_points:g}"]
    if with_answer:
        lines.append(transcript_text(ex.transcript).replace("[학생 답안]", "예시 답안", 1))
    lines.append("강사 판정 (이 점수가 기준):")
    lines += [
        _judgement_line(j, points.get(j.rubric_item_id, 0), ex, changed) for j in g.judgements
    ]
    if g.teacher_note:
        lines.append(f"강사 메모: {g.teacher_note}")
    return "\n".join(lines)


def examples_layer(chosen: list[Exemplar], rubric: Rubric) -> Shrinkable | None:
    """자세한 판본(답안+판정) → 판정만 → 예시 1개 순으로 줄일 수 있게."""
    if not chosen:
        return None
    full = "\n\n".join(exemplar_text(e, i, rubric) for i, e in enumerate(chosen, start=1))
    short = "\n\n".join(
        exemplar_text(e, i, rubric, with_answer=False) for i, e in enumerate(chosen, start=1)
    )
    one = exemplar_text(chosen[0], 1, rubric)
    return Shrinkable([full, short, one])


@dataclass
class ExamplePick:
    layer: Shrinkable | None
    students: list[str]


def pick_for(store: ExamStore, q: str, rubric: Rubric, target: Transcript) -> ExamplePick:
    settings = load_settings(store, q)
    if not settings.use_examples:
        return ExamplePick(None, [])
    cands = candidates(store, q, rubric, exclude_student=target.student_id)
    chosen = select_examples(cands, target, settings.max_examples)
    return ExamplePick(examples_layer(chosen, rubric), [c.student for c in chosen])


# ---------------------------------------------------------------- 일치도


def agreement(pairs: list[tuple[dict[str, float], dict[str, float]]]) -> AgreementStats:
    """(AI 항목 점수, 강사 항목 점수) 쌍들 → 일치도."""
    if not pairs:
        return AgreementStats()
    diffs: list[float] = []
    items_same = items_total = 0
    per_item: dict[str, int] = {}
    for ai, teacher in pairs:
        diffs.append(sum(ai.values()) - sum(teacher.values()))
        for rid, tv in teacher.items():
            items_total += 1
            if abs(ai.get(rid, 0) - tv) < 1e-9:
                items_same += 1
            else:
                per_item[rid] = per_item.get(rid, 0) + 1
    n = len(pairs)
    return AgreementStats(
        n=n,
        exact=sum(abs(d) < 1e-9 for d in diffs) / n,
        within1=sum(abs(d) <= 1 + 1e-9 for d in diffs) / n,
        item_agreement=items_same / items_total if items_total else None,
        mean_abs_diff=sum(abs(d) for d in diffs) / n,
        bias=sum(diffs) / n,
        item_disagreements=dict(sorted(per_item.items())),
    )


def ai_vs_teacher(g: GradeResult) -> tuple[dict[str, float], dict[str, float]] | None:
    """강사가 확정한 AI 채점 → (AI 원래 점수, 강사 점수). 강사가 직접 채점한 것은 제외."""
    if g.status != "teacher_confirmed" or g.agent is None:
        return None
    ai = g.ai_judgements or g.judgements
    return (
        {j.rubric_item_id: j.awarded for j in ai},
        {j.rubric_item_id: j.awarded for j in g.judgements},
    )


def question_agreement(store: ExamStore, q: str) -> AgreementStats:
    pairs = [
        p
        for st in store.students()
        if (g := store.grade(st, q)) is not None and (p := ai_vs_teacher(g)) is not None
    ]
    return agreement(pairs)


# ---------------------------------------------------------------- 루브릭 개선 제안 자료


def corrections_text(cases: list[Exemplar], rubric: Rubric) -> str:
    """강사가 AI 점수를 고친 사례 → 제안 작업의 자료."""
    points = {i.id: i.points for i in rubric.items}
    blocks: list[str] = []
    for ex in cases:
        g = ex.grade
        ai = {j.rubric_item_id: j for j in (g.ai_judgements or g.judgements)}
        lines = [f"[사례: 학생 {ex.student}] 강사 총점 {g.total:g}/{g.max_points:g}"]
        lines.append(transcript_text(ex.transcript))
        for j in g.judgements:
            a = ai.get(j.rubric_item_id)
            rid = j.rubric_item_id
            mark = "" if a is None or a.awarded == j.awarded else "  ← 강사가 고침"
            ai_part = f"AI {a.awarded:g}점 ({a.deduction_reason or a.reason})" if a else "AI 없음"
            lines.append(
                f"- {rid} ({points.get(rid, 0):g}점): {ai_part} → 강사 {j.awarded:g}점{mark}"
            )
            note = g.teacher_item_notes.get(rid)
            if note:
                lines.append(f"    강사 이유: {note}")
        if g.teacher_note:
            lines.append(f"강사 메모: {g.teacher_note}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
