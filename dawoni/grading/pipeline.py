"""채점 파이프라인: 범위 해설 → 루브릭 → 채점 (+ 독립 재풀이, 이중 채점).

강사의 단계를 건너뛰지 못하게 막는다:
- 루브릭은 범위 검증을 통과한 해설에서만 만든다
- 채점은 **승인된 루브릭**과 **확인된 판독문**으로만 한다
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from dawoni.config import AgentName
from dawoni.context import ContextEngine, TaskDef, TaskInput
from dawoni.grading import checks
from dawoni.grading.render import (
    question_text,
    rubric_text,
    scope_layer,
    solution_text,
    transcript_text,
)
from dawoni.models.base import utcnow
from dawoni.models.grading import (
    DEFAULT_NEVER_DEDUCT,
    ExamQuestion,
    GradeOutput,
    GradeResult,
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
) -> ScopedSolution:
    def check(d: SolutionDraft) -> list[str]:
        problems = checks.check_solution_math(d)
        if d.out_of_scope_note:
            return problems  # 범위 안에서 못 푼다는 판단은 수리하지 않고 강사에게 알린다
        return problems + checks.check_solution_scope(d, scope)

    task = dataclasses.replace(SOLVE, validator=check)
    inp = TaskInput(
        variables={"level": scope.level},
        scope=scope_layer(scope),
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
            scope=scope_layer(scope),
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


# ---------------------------------------------------------------- 채점


def _grade_once(
    engine: ContextEngine,
    q: ExamQuestion,
    rubric: Rubric,
    transcript: Transcript,
    scope: ScopeProfile,
    agent: AgentName | None,
    model: str | None,
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
) -> GradeResult:
    if rubric.status != "approved":
        raise PipelineError(f"문항 {q.number}: 루브릭이 아직 승인되지 않았습니다.")
    if not transcript.confirmed:
        raise PipelineError(
            f"{transcript.student_id} 문항 {q.number}: 판독문을 강사가 확인하지 않았습니다."
        )
    first, used_agent, used_model = _grade_once(engine, q, rubric, transcript, scope, agent, model)
    flags = list(first.flags)
    second: GradeOutput | None = None
    diff: list[str] = []
    if double is not None:
        second, _, _ = _grade_once(engine, q, rubric, transcript, scope, double.agent, double.model)
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
    )
