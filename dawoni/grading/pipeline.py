"""채점 파이프라인: 범위 해설 → 루브릭 → 채점 (+ 독립 재풀이, 이중 채점).

강사의 단계를 건너뛰지 못하게 막는다:
- 루브릭은 범위 검증을 통과한 해설에서만 만든다
- 채점은 **승인된 루브릭**과 **확인된 판독문**으로만 한다
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from dawoni.config import AgentName
from dawoni.context import ContextEngine, Shrinkable, TaskDef, TaskInput
from dawoni.grading import checks
from dawoni.grading.render import (
    question_text,
    rubric_text,
    scope_layer,
    solution_text,
    transcript_text,
)
from dawoni.models.base import utcnow
from dawoni.models.calibration import RubricSuggestion
from dawoni.models.grading import (
    DEFAULT_NEVER_DEDUCT,
    ExamQuestion,
    GradeOutput,
    GradeResult,
    ItemJudgement,
    Rubric,
    RubricDraft,
    ScopedSolution,
    SolutionDraft,
    Transcript,
)
from dawoni.models.scope import ScopeProfile


class PipelineError(Exception):
    """강사가 먼저 해야 할 단계가 남았거나, 자료가 맞지 않음."""


SOLVE: TaskDef[SolutionDraft] = TaskDef(
    name="solve_question",
    template="solve_question.md.j2",
    output=SolutionDraft,
    rule_target="solve",
    timeout_s=600,
)
RUBRIC: TaskDef[RubricDraft] = TaskDef(
    name="derive_rubric",
    template="derive_rubric.md.j2",
    output=RubricDraft,
    rule_target="rubric",
    timeout_s=600,
)
GRADE: TaskDef[GradeOutput] = TaskDef(
    name="grade_answer",
    template="grade_answer.md.j2",
    output=GradeOutput,
    rule_target=("grade", "feedback"),
    timeout_s=600,
)

SUGGEST: TaskDef[RubricSuggestion] = TaskDef(
    name="suggest_rubric",
    template="suggest_rubric.md.j2",
    output=RubricSuggestion,
    rule_target="rubric",
    timeout_s=600,
)

LOW_CONFIDENCE = 0.6


@dataclass(frozen=True)
class SecondOpinion:
    """독립 재풀이·이중 채점에 쓸 AI (None이면 첫 번째와 같은 설정, 새 세션)."""

    agent: AgentName | None = None
    model: str | None = None


# ---------------------------------------------------------------- 해설


def solve_question(
    engine: ContextEngine,
    q: ExamQuestion,
    scope: ScopeProfile,
    *,
    verify: SecondOpinion | None = None,
    agent: AgentName | None = None,
    model: str | None = None,
    with_extensions: bool = False,
) -> ScopedSolution:
    """`with_extensions`: 신규 문제 B — 범위 프로필의 확장 개념(X)도 허용·안내한다."""

    def check(d: SolutionDraft) -> list[str]:
        problems = checks.check_solution_math(d)
        if d.out_of_scope_note:
            return problems  # 범위 안에서 못 푼다는 판단은 수리하지 않고 강사에게 알린다
        return problems + checks.check_solution_scope(d, scope, with_extensions=with_extensions)

    task = dataclasses.replace(SOLVE, validator=check)
    inp = TaskInput(
        variables={"level": scope.level},
        scope=scope_layer(scope, with_extensions=with_extensions),
        material=question_text(q.number, q.points, q.item),
    )
    first = engine.run(task, inp, agent=agent, model=model).value
    notes: list[str] = []
    verified = False
    if first.out_of_scope_note:
        notes.append(f"범위 안에서 풀 수 없다는 판단: {first.out_of_scope_note}")
    elif verify is not None:
        second = engine.run(task, inp, agent=verify.agent, model=verify.model).value
        same = checks.answers_agree(first.final_answer, second.final_answer)
        if same:
            verified = True
        elif same is None:
            notes.append(
                f"재풀이 답을 자동 비교할 수 없음: {first.final_answer.latex} / "
                f"{second.final_answer.latex} — 강사 확인 필요"
            )
        else:
            notes.append(
                f"재풀이 답이 다름: {first.final_answer.latex} ≠ {second.final_answer.latex}"
            )
    return ScopedSolution(
        **first.model_dump(),
        question_number=q.number,
        scope_version=scope.version,
        verified=verified,
        check_notes=notes,
    )


# ---------------------------------------------------------------- 루브릭


def derive_rubric(
    engine: ContextEngine,
    q: ExamQuestion,
    solution: ScopedSolution,
    scope: ScopeProfile,
    *,
    agent: AgentName | None = None,
    model: str | None = None,
    previous: Rubric | None = None,
    with_extensions: bool = False,
) -> Rubric:
    if solution.out_of_scope_note:
        raise PipelineError(f"문항 {q.number}: 범위 안 해설이 없어 루브릭을 만들 수 없습니다.")

    def check(d: RubricDraft) -> list[str]:
        return checks.check_rubric(d, q.points, solution)

    task = dataclasses.replace(RUBRIC, validator=check)
    draft = engine.run(
        task,
        TaskInput(
            variables={
                "level": scope.level,
                "points": q.points,
                "never_deduct": DEFAULT_NEVER_DEDUCT,
            },
            scope=scope_layer(scope, with_extensions=with_extensions),
            material=question_text(q.number, q.points, q.item) + "\n\n" + solution_text(solution),
        ),
        agent=agent,
        model=model,
    ).value
    data = draft.model_dump()
    data["never_deduct"] = checks.merge_never_deduct(draft, DEFAULT_NEVER_DEDUCT)
    return Rubric(
        **data,
        question_number=q.number,
        version=(previous.version + 1) if previous else 1,
    )


def approve_rubric(rubric: Rubric, by: str | None) -> Rubric:
    return rubric.model_copy(
        update={"status": "approved", "approved_by": by, "approved_at": utcnow()}
    )


def edit_rubric(
    previous: Rubric, draft: RubricDraft, q: ExamQuestion, solution: SolutionDraft
) -> Rubric:
    """강사가 고친 루브릭. 합계·해설 단계 검사를 통과해야 하고, 다시 승인해야 채점에 쓴다."""
    problems = checks.check_rubric(draft, q.points, solution)
    if problems:
        raise PipelineError("; ".join(problems))
    bump = 1 if previous.status == "approved" else 0
    return Rubric(
        **draft.model_dump(),
        question_number=previous.question_number,
        version=previous.version + bump,
        status="draft",
    )


# ---------------------------------------------------------------- 채점


def _grade_once(
    engine: ContextEngine,
    q: ExamQuestion,
    rubric: Rubric,
    transcript: Transcript,
    scope: ScopeProfile,
    agent: AgentName | None,
    model: str | None,
    examples: Shrinkable | None = None,
) -> tuple[GradeOutput, AgentName, str | None]:
    def check(o: GradeOutput) -> list[str]:
        return checks.check_grade(o, rubric, transcript, scope)

    task = dataclasses.replace(GRADE, validator=check)
    res = engine.run(
        task,
        TaskInput(
            variables={"level": scope.level},
            scope=scope_layer(scope),
            material=question_text(q.number, q.points, q.item) + "\n\n" + rubric_text(rubric),
            examples=examples,
            target=transcript_text(transcript),
        ),
        agent=agent,
        model=model,
    )
    return res.value, res.agent, res.model


def grade_answer(
    engine: ContextEngine,
    q: ExamQuestion,
    rubric: Rubric,
    transcript: Transcript,
    scope: ScopeProfile,
    *,
    double: SecondOpinion | None = None,
    agent: AgentName | None = None,
    model: str | None = None,
    examples: Shrinkable | None = None,
    example_students: list[str] | None = None,
) -> GradeResult:
    """`examples`: 강사 보정 기준 예시 (`calibration.pick_for`).

    대상 학생 자신의 답안은 예시로 넣지 않는다.
    """
    if example_students and transcript.student_id in example_students:
        raise PipelineError("채점 대상 학생의 답안을 기준 예시로 넣을 수 없습니다.")
    if rubric.status != "approved":
        raise PipelineError(f"문항 {q.number}: 루브릭이 아직 승인되지 않았습니다.")
    if not transcript.confirmed:
        raise PipelineError(
            f"{transcript.student_id} 문항 {q.number}: 판독문을 강사가 확인하지 않았습니다."
        )
    first, used_agent, used_model = _grade_once(
        engine, q, rubric, transcript, scope, agent, model, examples
    )
    flags = list(first.flags)
    second: GradeOutput | None = None
    diff: list[str] = []
    if double is not None:
        second, _, _ = _grade_once(
            engine, q, rubric, transcript, scope, double.agent, double.model, examples
        )
        diff = checks.disagreements(first, second)
        if diff:
            flags.append(f"이중 채점 불일치: {', '.join(diff)}")
    low = [j.rubric_item_id for j in first.judgements if j.confidence < LOW_CONFIDENCE]
    if low:
        flags.append(f"확신도 낮음: {', '.join(low)}")
    needs_review = bool(diff or low or first.flags)
    return GradeResult(
        student_id=transcript.student_id,
        question_number=q.number,
        rubric_version=rubric.version,
        judgements=first.judgements,
        comment=first.comment,
        flags=flags,
        total=checks.total_points(first),
        max_points=rubric.total,
        status="needs_review" if needs_review else "auto",
        second_opinion=second.judgements if second else None,
        disagreements=diff,
        agent=used_agent,
        model=used_model,
        examples_used=list(example_students or []),
        item_points={i.id: i.points for i in rubric.items},
    )


def teacher_review(
    g: GradeResult,
    rubric: Rubric,
    awarded: dict[str, float],
    *,
    note: str | None = None,
    by: str | None = None,
    item_notes: dict[str, str] | None = None,
) -> GradeResult:
    """강사 확인: 항목별 점수를 고칠 수 있다. 처음 고칠 때 AI 판정을 `ai_judgements`에 남긴다."""
    points = {i.id: i.points for i in rubric.items}
    unknown = sorted(set(awarded) - set(points))
    if unknown:
        raise PipelineError(f"루브릭에 없는 항목: {unknown}")
    for k, v in awarded.items():
        if not 0 <= v <= points[k]:
            raise PipelineError(f"{k} 점수 {v:g}는 0~{points[k]:g} 사이여야 합니다")
    judgements = [
        j.model_copy(update={"awarded": awarded.get(j.rubric_item_id, j.awarded)})
        for j in g.judgements
    ]
    changed = any(a.awarded != b.awarded for a, b in zip(judgements, g.judgements, strict=True))
    ai = g.ai_judgements if g.ai_judgements is not None else (g.judgements if changed else None)
    return g.model_copy(
        update={
            "judgements": judgements,
            "total": sum(j.awarded for j in judgements),
            "status": "teacher_confirmed",
            "ai_judgements": ai,
            "teacher_note": note or g.teacher_note,
            "teacher_item_notes": {**g.teacher_item_notes, **(item_notes or {})},
            "confirmed_by": by,
            "confirmed_at": utcnow(),
        }
    )


def teacher_grade(
    transcript: Transcript,
    rubric: Rubric,
    awarded: dict[str, float],
    *,
    comment: str = "",
    note: str | None = None,
    item_notes: dict[str, str] | None = None,
    by: str | None = None,
) -> GradeResult:
    """강사가 AI 없이 직접 채점 (기준 예시 만들기). 모든 항목에 점수가 있어야 한다."""
    if not transcript.confirmed:
        raise PipelineError("확인한 판독문만 채점할 수 있습니다.")
    points = {i.id: i.points for i in rubric.items}
    missing = [i for i in points if i not in awarded]
    if missing:
        raise PipelineError(f"점수를 정하지 않은 항목: {missing}")
    unknown = sorted(set(awarded) - set(points))
    if unknown:
        raise PipelineError(f"루브릭에 없는 항목: {unknown}")
    for k, v in awarded.items():
        if not 0 <= v <= points[k]:
            raise PipelineError(f"{k} 점수 {v:g}는 0~{points[k]:g} 사이여야 합니다")
    notes = item_notes or {}
    judgements = [
        ItemJudgement(
            rubric_item_id=i.id,
            awarded=awarded[i.id],
            reason=notes.get(i.id) or "강사 채점",
            deduction_reason=(notes.get(i.id) or None) if awarded[i.id] < i.points else None,
            confidence=1.0,
        )
        for i in rubric.items
    ]
    return GradeResult(
        student_id=transcript.student_id,
        question_number=rubric.question_number,
        rubric_version=rubric.version,
        judgements=judgements,
        comment=comment,
        total=sum(awarded.values()),
        max_points=rubric.total,
        status="teacher_confirmed",
        teacher_note=note,
        teacher_item_notes=notes,
        confirmed_by=by,
        confirmed_at=utcnow(),
        item_points=dict(points),
    )


# ---------------------------------------------------------------- 보정: 루브릭 개선 제안


def check_suggestion(
    s: RubricSuggestion, q: ExamQuestion, solution: SolutionDraft, case_students: set[str]
) -> list[str]:
    problems: list[str] = []
    if bool(s.changes) != (s.revised is not None):
        problems.append("changes가 있으면 revised(루브릭 전체)를, 없으면 revised는 null로 쓸 것")
    if not s.changes and not s.no_change_reason:
        problems.append("고칠 것이 없으면 no_change_reason에 이유를 쓸 것")
    for c in s.changes:
        bad = sorted(set(c.evidence) - case_students)
        if bad:
            problems.append(f"evidence의 학생 {bad}는 [자료]의 사례가 아니다")
    if s.revised is not None:
        problems += checks.check_rubric(s.revised, q.points, solution)
    return problems


def suggest_rubric(
    engine: ContextEngine,
    q: ExamQuestion,
    rubric: Rubric,
    solution: SolutionDraft,
    scope: ScopeProfile,
    corrections: str,
    case_students: set[str],
) -> RubricSuggestion:
    """강사 수정 사례 → 루브릭 개선 제안. 적용은 강사가 고른 뒤 `edit_rubric` (다시 승인 필요)."""

    def check(v: RubricSuggestion) -> list[str]:
        return check_suggestion(v, q, solution, case_students)

    task = dataclasses.replace(SUGGEST, validator=check)
    out = engine.run(
        task,
        TaskInput(
            variables={"level": scope.level, "points": q.points},
            scope=scope_layer(scope),
            material="\n\n".join(
                [
                    question_text(q.number, q.points, q.item),
                    solution_text(solution),
                    rubric_text(rubric),
                    "[강사가 확정한 채점 사례]\n" + corrections,
                ]
            ),
        ),
    ).value
    if out.revised is not None:
        data = out.revised.model_dump()
        data["never_deduct"] = checks.merge_never_deduct(out.revised, DEFAULT_NEVER_DEDUCT)
        out = out.model_copy(update={"revised": RubricDraft.model_validate(data)})
    return out
