"""채점 파이프라인: (해설지 넓히기 | 범위 해설 → 루브릭) → 채점 (+ 독립 재풀이, 이중 채점).

해설지가 있으면 기본은 **해설지 넓히기** (`expand_key_rubric`): 루브릭 역할 호출 한 번으로
문항 + 해설지 → 루브릭(해설지의 부분 점수 논리) + 같은 뜻의 다른 표현 제안(강사 인정 전에는
채점에 안 씀). AI 풀이·검토·검산 호출이 없다. 해설지 오타가 걱정되면 "AI 풀기 (해설지 검산)"로
아래 예전 경로(`derive_rubric`)를 쓴다.

강사의 단계를 건너뛰지 못하게 막는다:
- 루브릭은 범위 검증을 통과한 해설에서만 만든다
- 채점은 **승인된 루브릭**과 **확인된 판독문**으로만 한다

해설지가 있으면 루브릭은 해설지를 기준으로 (실측: AI 풀이만으로 만든 루브릭이 AI의 오답을
정답으로 인정했다):
1. AI는 해설지를 보지 않고 먼저 혼자 푼다 (소문항별 답)
2. 루브릭 = 해설지의 정답·배점·부분 점수 + 소문항별 비교(같음 / AI 오답 / 다른 정답 후보 /
   해설지 의문). 소문항 배점·부분 점수 구조는 코드가 검사한다
3. 다른 AI 세션이 루브릭이 해설지를 지키는지 검토 → 지적이 있으면 한 번 고쳐 다시 만든다
4. 다른 정답 후보는 문제에 대입해 독립 검산. **강사가 인정하기 전에는 점수를 주지 않고**,
   학생 답이 후보와 같으면 채점을 '검토 필요'로 둔다
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError
from rapidfuzz import fuzz

from dawoni.config import AgentName
from dawoni.context import ContextEngine, PartialRepair, Shrinkable, TaskDef, TaskInput
from dawoni.grading import checks
from dawoni.grading.answers import (
    CODE_AGENT,
    grade_by_code,
    missing_answers,
    part_lines,
    pre_grade,
)
from dawoni.grading.render import (
    exam_question_text,
    key_text,
    rubric_text,
    scope_layer,
    solution_text,
    transcript_text,
)
from dawoni.models.answer_key import AnswerKey
from dawoni.models.base import utcnow
from dawoni.models.calibration import RubricSuggestion
from dawoni.models.grading import (
    DEFAULT_NEVER_DEDUCT,
    UNREADABLE,
    AcceptedAlternative,
    AnswerCandidate,
    AnswerCheck,
    AnswerSide,
    AnswerSideOutput,
    CandidateMatches,
    ExamQuestion,
    GradeOutput,
    GradeResult,
    ItemJudgement,
    KeyChoice,
    KeyConflict,
    KeyRubricDraft,
    PartAnswerSides,
    PartCheck,
    PartialCredit,
    Rubric,
    RubricAudit,
    RubricDraft,
    RubricEdit,
    RubricItem,
    ScopedSolution,
    SolutionDraft,
    StudentAnswerSide,
    Transcript,
)
from dawoni.models.scope import ScopeProfile


class PipelineError(Exception):
    """강사가 먼저 해야 할 단계가 남았거나, 자료가 맞지 않음."""


SOLVE: TaskDef[SolutionDraft] = TaskDef(
    role="solver_verifier",
    name="solve_question",
    template="solve_question.md.j2",
    output=SolutionDraft,
    rule_target="solve",
    timeout_s=600,
)
RUBRIC: TaskDef[RubricDraft] = TaskDef(
    role="rubric_builder",
    name="derive_rubric",
    template="derive_rubric.md.j2",
    output=RubricDraft,
    rule_target="rubric",
    timeout_s=600,
)
GRADE: TaskDef[GradeOutput] = TaskDef(
    role="grader",
    name="grade_answer",
    template="grade_answer.md.j2",
    output=GradeOutput,
    rule_target=("grade", "feedback"),
    timeout_s=600,
)

EXPAND_KEY: TaskDef[KeyRubricDraft] = TaskDef(
    role="rubric_builder",
    name="expand_key_rubric",
    template="expand_key_rubric.md.j2",
    output=KeyRubricDraft,
    rule_target="rubric",
    timeout_s=120,
    max_repairs=1,
)
"""해설지 넓히기: 한 번에 끝나는 가벼운 작업이라 2분, 수리 1번 (역할 설정)."""

AUDIT: TaskDef[RubricAudit] = TaskDef(
    role="rubric_builder",
    name="audit_rubric",
    template="audit_rubric.md.j2",
    output=RubricAudit,
    rule_target="rubric",
    timeout_s=600,
)
CHECK_ANSWER: TaskDef[AnswerCheck] = TaskDef(
    role="solver_verifier",
    name="check_answer",
    template="check_answer.md.j2",
    output=AnswerCheck,
    rule_target="solve",
    timeout_s=600,
)

MATCH: TaskDef[AnswerSideOutput] = TaskDef(
    role="light_helper",
    name="match_candidates",
    template="match_candidates.md.j2",
    output=AnswerSideOutput,
    rule_target="grade",
    timeout_s=600,
)

SUGGEST: TaskDef[RubricSuggestion] = TaskDef(
    role="calibrator",
    name="suggest_rubric",
    template="suggest_rubric.md.j2",
    output=RubricSuggestion,
    rule_target="rubric",
    timeout_s=600,
)

LOW_CONFIDENCE = 0.6

SecondWhen = Literal["low", "all"]


@dataclass(frozen=True)
class SecondOpinion:
    """독립 재풀이·이중 채점에 쓸 AI (None이면 첫 번째와 같은 설정, 새 세션)."""

    agent: AgentName | None = None
    model: str | None = None
    when: SecondWhen = "low"
    """low = 확신도 낮음·수리가 있었던 채점만 (기본), all = 모두."""


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
    simple: bool = False,
    given: str | None = None,
    fresh: bool = False,
) -> ScopedSolution:
    """`with_extensions`: 신규 문제 B — 범위 프로필의 확장 개념(X)도 허용·안내한다.

    `simple`: 문제은행용 — 정답과 모범 풀이 1가지만 (다른 풀이·루브릭 안내 없음).
    `given`: 검수된 원본 정답·해설 (simple일 때) — 이 답에 이르는 간결한 풀이를 쓴다.
    `fresh`: 방금 만든 문항의 독립 풀이 — 인터넷에 없으니 검색하지 않게 한다
    (실측: 8번 검색, 280초)."""

    def check(d: SolutionDraft) -> list[str]:
        problems = checks.check_solution_math(d) + checks.check_part_answers(d, q)
        if d.out_of_scope_note:
            return problems  # 범위 안에서 못 푼다는 판단은 수리하지 않고 강사에게 알린다
        return problems + checks.check_solution_scope(d, scope, with_extensions=with_extensions)

    task = dataclasses.replace(SOLVE, validator=check)
    inp = TaskInput(
        variables={
            "level": scope.level,
            "has_parts": bool(q.parts),
            "simple": simple,
            "given": given is not None,
            "fresh": fresh,
        },
        scope=scope_layer(scope, with_extensions=with_extensions),
        material=exam_question_text(q)
        + (f"\n\n[원본 정답] (검수됨)\n{given}" if given is not None else ""),
    )
    made = engine.run(task, inp, agent=agent, model=model)
    first = made.value
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
        agent=made.agent,
        model=made.model,
    )


# ---------------------------------------------------------------- 루브릭


def expand_key_rubric(
    engine: ContextEngine,
    q: ExamQuestion,
    key: AnswerKey,
    scope: ScopeProfile,
    *,
    agent: AgentName | None = None,
    model: str | None = None,
    previous: Rubric | None = None,
) -> Rubric:
    """강사 해설지를 넓혀 루브릭을 만든다 — AI 호출 한 번 (풀이·검토·검산 없음).

    보내는 것: 이 문항과 이 문항의 해설지만, 범위는 짧은 판본(이름만)."""

    def check(d: KeyRubricDraft) -> list[str]:
        draft = d.to_rubric_draft()
        return checks.check_rubric(draft, q, None, key) + checks.check_rubric_scope(
            draft, q, key, scope
        )

    variables = {
        "level": scope.level,
        "points": q.points,
        "parts": bool(q.parts),
        "never_deduct": DEFAULT_NEVER_DEDUCT,
        "concepts": bool(key.concepts),
    }
    res = engine.run(
        dataclasses.replace(EXPAND_KEY, validator=check),
        TaskInput(
            variables=variables,
            scope=scope_layer(scope).variants[-1],
            material="\n\n".join([exam_question_text(q), key_text(key, q)]),
        ),
        agent=agent,
        model=model,
    )
    draft = res.value.to_rubric_draft()
    notes = list(key.warnings)
    if not key.confirmed:
        notes.append(
            "해설지 판독을 강사가 아직 확인하지 않았습니다 — 원본과 비교한 뒤 확인하세요"
            " (AI가 수식의 숫자를 바꿔 읽은 사례가 있음)"
        )
    notes += [f"해설지 기준 확인: {x.note}" for x in draft.key_conflicts]
    if draft.accepted_alternatives:
        notes.append(
            f"AI가 넓힌 표현 {len(draft.accepted_alternatives)}개 — 인정한 것만 채점에 씁니다"
        )
    notes.append("해설지를 넓혀 만든 루브릭 (AI 풀이 없음) — 해설지 오타가 걱정되면 'AI 풀기'")
    data = draft.model_dump()
    data["never_deduct"] = checks.merge_never_deduct(draft, DEFAULT_NEVER_DEDUCT)
    data["key_conflicts"] = [c.model_dump() for c in start_key_conflicts(draft)]
    return Rubric(
        **data,
        question_number=q.number,
        based_on_key=True,
        review_notes=notes,
        version=(previous.version + 1) if previous else 1,
        agent=res.agent,
        model=res.model,
    )


def derive_rubric(
    engine: ContextEngine,
    q: ExamQuestion,
    solution: ScopedSolution,
    scope: ScopeProfile,
    *,
    key: AnswerKey | None = None,
    audit: bool = True,
    agent: AgentName | None = None,
    model: str | None = None,
    previous: Rubric | None = None,
    with_extensions: bool = False,
) -> Rubric:
    """`key`: 강사 해설지. 있으면 루브릭의 기준이 되고 AI 풀이는 비교 대상이 된다."""
    if solution.out_of_scope_note and key is None:
        raise PipelineError(f"문항 {q.number}: 범위 안 해설이 없어 루브릭을 만들 수 없습니다.")

    def check(d: RubricDraft) -> list[str]:
        return checks.check_rubric(d, q, solution, key) + checks.check_rubric_scope(
            d, q, key, scope
        )

    task = dataclasses.replace(RUBRIC, validator=check)
    base = [exam_question_text(q)]
    if key is not None:
        base.append(key_text(key, q))
        base.append(solution_text(solution, "[AI 독립 풀이] (해설지를 보지 않고 풂)"))
    else:
        base.append(solution_text(solution))
    variables = {
        "level": scope.level,
        "points": q.points,
        "parts": bool(q.parts),
        "has_key": key is not None,
        "never_deduct": DEFAULT_NEVER_DEDUCT,
    }
    layer = scope_layer(scope, with_extensions=with_extensions)

    made: list[tuple[str, str | None]] = []

    def draft_once(feedback: list[str]) -> RubricDraft:
        extra = (
            ["[검토 의견] 이전 초안을 다른 검토자가 보고 지적한 점 — 반영해서 다시 만들 것:"]
            + [f"- {x}" for x in feedback]
            if feedback
            else []
        )
        res = engine.run(
            task,
            TaskInput(variables=variables, scope=layer, material="\n\n".join(base + extra)),
            agent=agent,
            model=model,
        )
        made.append((res.agent, res.model))
        return res.value

    def audit_once(d: RubricDraft) -> list[str]:
        assert key is not None
        return engine.run(
            AUDIT,
            TaskInput(
                variables={"level": scope.level},
                scope=layer,
                material="\n\n".join(
                    [exam_question_text(q), key_text(key, q), rubric_text(d, q.parts)]
                ),
            ),
            agent=agent,
            model=model,
        ).value.issues

    draft = draft_once([])
    notes: list[str] = []
    if key is not None:
        notes += key.warnings
        if not key.confirmed:
            notes.append(
                "해설지 판독을 강사가 아직 확인하지 않았습니다 — 원본과 비교한 뒤 확인하세요"
                " (AI가 수식의 숫자를 바꿔 읽은 사례가 있음)"
            )
        if audit:
            issues = audit_once(draft)
            if issues:
                draft = draft_once(issues)
                left = audit_once(draft)
                notes += [f"검토 의견(반영 후에도 남음): {x}" for x in left]
    else:
        notes.append("강사 해설지 없이 AI 풀이로 만든 루브릭 — 정답과 배점을 꼭 확인하세요")

    def verify(part: str, answer: str) -> AnswerCheck:
        return _verify_answer(engine, q, scope, part, answer, agent=agent, model=model)

    candidates = [
        c.model_copy(
            update={"status": "proposed", "check": (r := verify(c.part, c.answer)).verdict,
                    "check_note": r.reasoning}
        )
        for c in draft.candidates
    ]  # fmt: skip
    part_checks = [
        _verify_disagreement(c, key, solution, verify) if key is not None else c
        for c in draft.checks
    ]
    notes += [n for c in part_checks if (n := _disagreement_note(c))]
    notes += [f"해설지 기준 확인: {x.note}" for x in draft.key_conflicts]
    data = draft.model_dump()
    data["never_deduct"] = checks.merge_never_deduct(draft, DEFAULT_NEVER_DEDUCT)
    data["candidates"] = [c.model_dump() for c in candidates]
    data["checks"] = [c.model_dump() for c in part_checks]
    data["key_conflicts"] = [c.model_dump() for c in start_key_conflicts(draft)]
    return Rubric(
        **data,
        question_number=q.number,
        based_on_key=key is not None,
        review_notes=notes,
        version=(previous.version + 1) if previous else 1,
        agent=made[-1][0],
        model=made[-1][1],
    )


def _verify_answer(
    engine: ContextEngine,
    q: ExamQuestion,
    scope: ScopeProfile,
    part: str,
    answer: str,
    *,
    agent: AgentName | None,
    model: str | None,
) -> AnswerCheck:
    """답 하나를 문제에 대입해 검산 — 해설지·AI 풀이·루브릭 없이 새 세션에서."""
    where = f"소문항 {part}" if part else "문항"
    return engine.run(
        CHECK_ANSWER,
        TaskInput(
            variables={"level": scope.level},
            scope=scope_layer(scope),
            material=exam_question_text(q) + f"\n\n[확인할 답] {where}: {answer}",
        ),
        agent=agent,
        model=model,
    ).value


def _verify_disagreement(
    c: PartCheck,
    key: AnswerKey,
    solution: SolutionDraft,
    verify: Callable[[str, str], AnswerCheck],
) -> PartCheck:
    """해설지 답과 AI 답이 다르면 **둘 다** 독립 검산한다.

    실측: 해설지가 그림과 다른 기호로 답을 적었는데(θ를 반사각으로 씀) 루브릭 AI는 'AI 오답'으로
    판정했다. 어느 쪽이 맞는지는 루브릭을 만든 AI의 의견이 아니라 검산 결과로 강사에게 보여 준다.
    """
    if c.verdict == "same":
        return c
    kp = key.part(c.part)
    ai = checks.ai_part_answer(solution, c.part)
    update: dict[str, object] = {}
    if kp is not None and kp.answer:
        r = verify(c.part, kp.answer)
        update |= {"key_check": r.verdict, "key_check_note": r.reasoning}
    if ai is not None:
        r = verify(c.part, ai.latex)
        update |= {"ai_check": r.verdict, "ai_check_note": r.reasoning}
    return c.model_copy(update=update)


def _disagreement_note(c: PartCheck) -> str | None:
    name = f"소문항 {c.part}" if c.part else "문항"
    if c.key_check == "invalid" and c.ai_check == "valid":
        return (
            f"⚠ {name}: 해설지 답은 검산을 통과하지 못했고 AI 답({c.ai_answer})은 통과했습니다 "
            "— 해설지의 답·기호를 확인하세요 (루브릭은 지금 해설지 기준)"
        )
    if c.key_check == "invalid":
        return f"⚠ {name}: 해설지 답이 독립 검산을 통과하지 못했습니다 — 해설지를 확인하세요"
    if c.verdict == "ai_wrong" and c.ai_check == "valid":
        return (
            f"{name}: 'AI 오답'으로 판정했지만 AI 답({c.ai_answer})도 검산을 통과했습니다 "
            "— 다른 정답일 수 있습니다"
        )
    return None


def approve_rubric(rubric: Rubric, by: str | None) -> Rubric:
    missing = missing_answers(rubric)
    if missing:
        raise PipelineError(
            f"문항 {rubric.question_number}: 정답이 없는 소문항 {', '.join(missing)} — "
            "정답을 적거나 전원 정답으로 한 뒤 승인하세요."
        )
    return rubric.model_copy(
        update={"status": "approved", "approved_by": by, "approved_at": utcnow()}
    )


def edit_rubric(
    previous: Rubric, draft: RubricDraft, q: ExamQuestion, solution: SolutionDraft | None
) -> Rubric:
    """강사가 고친 루브릭 (정답 후보 인정·제외 포함). 합계·해설 단계 검사를 통과해야 하고,
    다시 승인해야 채점에 쓴다. 해설지 구조 검사는 하지 않는다 — 기준은 강사가 정한다."""
    problems = checks.check_rubric(draft, q, solution, strict=False)
    if problems:
        raise PipelineError("; ".join(problems))
    ids = {i.id for i in draft.items}
    lost = [r.part or "(전체)" for r in previous.answer_rules if r.item_id not in ids]
    if lost:
        raise PipelineError(f"코드 채점 항목을 지울 수 없습니다 (소문항 {', '.join(lost)})")
    bump = 1 if previous.status == "approved" else 0
    data = draft.model_dump()
    data["key_conflicts"] = [
        c.model_dump() for c in sync_key_conflicts(draft.key_conflicts, draft.items)
    ]
    return Rubric(
        **data,
        question_number=previous.question_number,
        answer_rules=previous.answer_rules,
        based_on_key=previous.based_on_key,
        review_notes=previous.review_notes,
        version=previous.version + bump,
        status="draft",
        created_by=previous.created_by,
        created_at=previous.created_at,
        agent=previous.agent,
        model=previous.model,
    )


def edit_rubric_items(
    previous: Rubric, edit: RubricEdit, q: ExamQuestion, solution: SolutionDraft | None
) -> Rubric:
    """루브릭 화면에서 강사가 채점 항목·부분 점수를 고친다 (승인·확정한 뒤에도).

    승인된 루브릭이면 새 판(초안)이 되고 다시 승인해야 채점에 쓴다 — 예전 판으로 채점한 결과는
    '다시 채점 필요'가 된다. 지운 항목을 가리키던 넓힌 표현·정답 후보의 연결은 떼어 낸다."""
    old_items = {i.id: i for i in previous.items}
    items = [
        old_items[e.id].model_copy(update=e.model_dump())
        if e.id in old_items
        else RubricItem(**e.model_dump())
        for e in edit.items
    ]
    ids = {i.id for i in items}
    old_partial = {(p.item_id, p.condition): p for p in previous.partial}
    partial = [
        PartialCredit(
            **p.model_dump(),
            # 강사가 적은 부분 점수는 근거 있는 것 (AI 제안을 그대로 두면 예전 표시)
            from_key=old.from_key if (old := old_partial.get((p.item_id, p.condition))) else True,
        )
        for p in edit.partial
    ]
    alts = [
        a.model_copy(update={"item_ids": [x for x in a.item_ids if x in ids]})
        for a in previous.accepted_alternatives
        if any(x in ids for x in a.item_ids)
    ]
    cands = [
        c.model_copy(update={"item_ids": [x for x in c.item_ids if x in ids]})
        for c in previous.candidates
    ]
    data = {k: getattr(previous, k) for k in RubricDraft.model_fields}
    data.update(items=items, partial=partial, accepted_alternatives=alts, candidates=cands)
    try:
        draft = RubricDraft.model_validate(data)
    except ValidationError as e:
        raise PipelineError(_validation_message(e)) from e
    before = RubricDraft.model_validate({k: getattr(previous, k) for k in RubricDraft.model_fields})
    if draft == before:
        return previous  # 바뀐 것이 없으면 승인을 풀지 않는다
    return edit_rubric(previous, draft, q, solution)


def _validation_message(e: ValidationError) -> str:
    msgs = [str(x["msg"]).removeprefix("Value error, ") for x in e.errors()]
    return "; ".join(m for m in msgs if m) or str(e)


_UNAPPROVED: dict[str, object] = {
    "status": "draft",
    "approved_by": None,
    "approved_at": None,
    "finalized_by": None,
    "finalized_at": None,
    "finalized_note": None,
}
"""초안으로 돌아갈 때 지울 승인·확정 기록 — 남기면 다시 승인만 해도 '확정'으로 보인다."""


def decide_candidates(rubric: Rubric, accept: list[str], reject: list[str]) -> Rubric:
    """강사가 다른 정답 후보(A…)·해설지 넓히기 제안(E…)을 인정·제외 (승인 전에).
    결정이 바뀌면 다시 승인해야 한다."""
    ids = {c.id for c in rubric.candidates} | {a.id for a in rubric.accepted_alternatives if a.id}
    unknown = sorted((set(accept) | set(reject)) - ids)
    if unknown:
        raise PipelineError(f"정답 후보에 없는 ID: {unknown}")
    both = sorted(set(accept) & set(reject))
    if both:
        raise PipelineError(f"인정과 제외에 같은 후보: {both}")

    def status(c: AnswerCandidate | AcceptedAlternative) -> str:
        return "accepted" if c.id in accept else "rejected" if c.id in reject else c.status

    cands = [c.model_copy(update={"status": status(c)}) for c in rubric.candidates]
    alts = [a.model_copy(update={"status": status(a)}) for a in rubric.accepted_alternatives]
    if cands == rubric.candidates and alts == rubric.accepted_alternatives:
        return rubric
    bump = 1 if rubric.status == "approved" else 0
    return rubric.model_copy(
        update={
            "candidates": cands,
            "accepted_alternatives": alts,
            "version": rubric.version + bump,
            **_UNAPPROVED,
        }
    )


# ---------------------------------------------------------------- 해설지와 다른 기준 (강사가 고름)


def _same_text(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def start_key_conflicts(draft: RubricDraft) -> list[KeyConflict]:
    """AI가 적은 '해설지와 다른 기준'을 강사가 고를 수 있게: K1, K2 …, 문제 기준 문장, 아직 안 정함.

    항목이 없거나 해설지대로의 기준이 빠졌거나 항목 기준과 같으면 고를 수 없는 것(확인만)으로 둔다
    — 이것 때문에 루브릭 만들기가 실패하지 않게 수리 요청은 하지 않는다."""
    items = {i.id: i for i in draft.items}
    out: list[KeyConflict] = []
    for n, c in enumerate(draft.key_conflicts, start=1):
        item = items.get(c.item_id) if c.item_id else None
        key = (c.key_criterion or "").strip() if item else ""
        ok = item is not None and bool(key) and not _same_text(key, item.criterion)
        out.append(
            c.model_copy(
                update={
                    "id": f"K{n}",
                    "item_id": item.id if item else None,
                    "key_criterion": key if ok else None,
                    "question_criterion": item.criterion if ok and item else None,
                    "choice": None,
                }
            )
        )
    return out


def _choice_of(c: KeyConflict, items: dict[str, RubricItem]) -> KeyChoice | None:
    """항목의 지금 기준 문장으로 본 선택. 강사가 고친 문장이면 edited, 항목을 지웠어도 edited."""
    if not c.switchable:
        return c.choice
    item = items.get(c.item_id or "")
    if item is None:
        return "edited"
    if _same_text(item.criterion, c.key_criterion or ""):
        return "key"
    if _same_text(item.criterion, c.question_criterion or ""):
        # 아직 정하지 않았으면 그대로 (AI가 쓴 문제 기준을 강사가 고른 것은 아니다)
        return None if c.choice is None else "question"
    return "edited"


def sync_key_conflicts(conflicts: list[KeyConflict], items: list[RubricItem]) -> list[KeyConflict]:
    """항목을 고친 뒤(화면의 고치기·보정 제안 적용): 선택을 항목의 지금 기준 문장에 맞춘다."""
    by_id = {i.id: i for i in items}
    return [
        c
        if (choice := _choice_of(c, by_id)) == c.choice
        else c.model_copy(update={"choice": choice})
        for c in conflicts
    ]


def choose_key_conflicts(rubric: Rubric, choices: dict[str, KeyChoice]) -> Rubric:
    """강사가 해설지와 다른 기준을 고른다: question=문제 기준, key=해설지 기준 (항목 기준 문장을
    바꾼다). 고를 수 없는 것(예전 저장본 등)은 question으로 확인만 한다.

    항목 기준이 바뀌면 다시 승인해야 한다 (승인된 것이면 새 판). 확인만 하면 승인은 그대로다."""
    conflicts = {c.id: c for c in rubric.key_conflicts if c.id}
    unknown = sorted(set(choices) - set(conflicts))
    if unknown:
        raise PipelineError(f"해설지와 다른 기준에 없는 ID: {unknown}")
    if any(v == "edited" for v in choices.values()):
        raise PipelineError("'직접 고침'은 고르는 것이 아니라 고치기로 기준을 바꾸면 됩니다")
    have = {i.id for i in rubric.items}
    criteria: dict[str, str] = {}
    for cid, v in choices.items():
        c = conflicts[cid]
        if not c.switchable:
            if v == "key":
                raise PipelineError(f"{cid}: 해설지대로의 기준이 없습니다 — 고치기로 바꾸세요")
            continue
        assert c.item_id and c.key_criterion and c.question_criterion
        if c.item_id not in have:
            raise PipelineError(f"{cid}: 항목 {c.item_id}을 지워서 고를 수 없습니다")
        new = c.key_criterion if v == "key" else c.question_criterion
        if criteria.setdefault(c.item_id, new) != new:
            raise PipelineError(f"{c.item_id}에 서로 다른 기준을 함께 골랐습니다")
    items = [
        i.model_copy(update={"criterion": criteria[i.id]}) if i.id in criteria else i
        for i in rubric.items
    ]
    by_id = {i.id: i for i in items}
    # 고르지 않은 것도 바뀐 문장에 맞춘다 (한 항목에 충돌이 둘 걸린 경우)
    conflicts_out = [
        c.model_copy(update={"choice": choices[c.id] if c.id in choices else _choice_of(c, by_id)})
        for c in rubric.key_conflicts
    ]
    if items == rubric.items and conflicts_out == rubric.key_conflicts:
        return rubric
    if items == rubric.items:
        return rubric.model_copy(update={"key_conflicts": conflicts_out})
    bump = 1 if rubric.status == "approved" else 0
    return rubric.model_copy(
        update={
            "items": items,
            "key_conflicts": conflicts_out,
            "version": rubric.version + bump,
            **_UNAPPROVED,
        }
    )


def check_sides(
    out: AnswerSideOutput, labels: dict[str, Transcript], candidate_ids: set[str]
) -> list[str]:
    problems: list[str] = []
    seen = [s.student for s in out.students]
    missing = [k for k in labels if k not in seen]
    unknown = sorted({k for k in seen if k not in labels})
    dups = sorted({k for k in seen if seen.count(k) > 1})
    if missing:
        problems.append(f"{', '.join(missing)}이 빠졌다. 학생마다 한 번씩 적을 것")
    if unknown:
        problems.append(f"[대상]에 없는 학생 {unknown}")
    if dups:
        problems.append(f"{dups}를 두 번 이상 적었다")
    for s in out.students:
        if s.side == "candidate" and s.candidate_id not in candidate_ids:
            problems.append(f"{s.student}: candidate_id는 {sorted(candidate_ids)} 중 하나")
        if s.side != "candidate" and s.candidate_id is not None:
            problems.append(f"{s.student}: side가 candidate가 아니면 candidate_id는 null")
        t = labels.get(s.student)
        if t is None:
            continue
        if s.side != "other" and not s.quote.strip():
            problems.append(f"{s.student}: 결론을 담은 부분을 quote에 인용할 것")
        elif s.quote.strip() and not checks.quote_in_transcript(s.quote, t):
            problems.append(
                f"{s.student}: 인용 '{s.quote[:40]}'이 답안에 그대로 없다 — "
                "고쳐 읽지 말고 적힌 그대로"
            )
    return problems


def _lhs(latex: str) -> str:
    plain = checks.normalize_for_compare(latex)
    return plain.rsplit("=", 1)[0] if "=" in plain else ""


def _has_answer(last: str, answer: str) -> bool:
    """결론 줄에 답이 있는가.

    1. 정규화한 글이 그대로 있음 — 숫자가 든 답은 앞뒤가 다른 숫자로 이어지면(1/2와 1/20) 아님
    2. 값이 같음(1/2와 \\frac12) — 답에 좌변이 있으면 좌변도 같아야 (cosθ=1은 sinθ=1이 아님)
    3. 숫자 없는 긴 글 답은 판독 흔들림을 조금 봐준다 (rapidfuzz 95 이상)"""
    hay = checks.normalize_for_compare(last)
    n = checks.normalize_for_compare(answer)
    if not n:
        return False
    start = hay.find(n)
    while start >= 0:
        before = hay[start - 1] if start else ""
        after = hay[start + len(n) : start + len(n) + 1]
        if not before.isalnum() and not (after and (after.isalnum() or after in "./^_")):
            return True
        start = hay.find(n, start + 1)
    mine, theirs = checks.latex_values(last), checks.latex_values(answer)
    if mine and theirs and checks.same_values(mine, theirs):
        lhs = _lhs(answer)
        return not lhs or _lhs(last) in ("", lhs)
    if len(n) < 6 or any(ch.isdigit() for ch in n):
        return False
    return fuzz.partial_ratio(n, hay) >= 95


def prematch_side(
    t: Transcript, key_answer: str, cands: list[AnswerCandidate]
) -> StudentAnswerSide | None:
    """결론(마지막 줄)에 해설지 답이나 후보 **하나만** 그대로 있으면 AI 없이 정한다.

    둘 이상이 보이거나 아무것도 없으면 None — AI(가벼운 도우미)에게 묻는다."""
    lines = [x for x in t.lines if not x.struck and x.joined.strip()]
    if not lines:
        return None
    last = lines[-1].joined
    hits: list[tuple[AnswerSide, str | None]] = []
    if key_answer and _has_answer(last, key_answer):
        hits.append(("key", None))
    for c in cands:
        if _has_answer(last, c.answer):
            hits.append(("candidate", c.id))
    if len(hits) != 1:
        return None
    side, cid = hits[0]
    return StudentAnswerSide(student=t.student_id, side=side, candidate_id=cid, quote=last)


def answer_sides(
    engine: ContextEngine,
    q: ExamQuestion,
    rubric: Rubric,
    transcripts: list[Transcript],
    scope: ScopeProfile,
) -> CandidateMatches:
    """정답 후보가 있는 소문항마다, 학생 답이 해설지 답·후보 중 어느 쪽인지 나눈다.

    AI에는 학생 이름 대신 학생1, 학생2 …만 보낸다. 빈 답, 결론 줄에 답 하나가 그대로 있는
    답(`prematch_side`)은 AI에 보내지 않는다 — 모두 정해지면 호출하지 않는다.
    결론 인용이 판독문에 그대로 없으면 다시 시킨다 (해설지에 맞춰 고쳐 읽기 방지).
    """
    if not rubric.candidates:
        raise PipelineError(f"문항 {q.number}: 정답 후보가 없습니다.")
    key_answers = {c.part: c.key_answer for c in rubric.checks}
    parts: list[PartAnswerSides] = []
    agent: str | None = None
    model: str | None = None
    for part in dict.fromkeys(c.part for c in rubric.candidates):
        cands = [c for c in rubric.candidates if c.part == part]
        subs = {
            t.student_id: Transcript(
                student_id=t.student_id, question_number=t.question_number,
                lines=part_lines(t, part),
            )
            for t in transcripts
        }  # fmt: skip
        blank = sorted(s for s, t in subs.items() if t.is_blank)
        filled = [t for t in subs.values() if not t.is_blank]
        labels = {f"학생{n}": t for n, t in enumerate(filled, 1)}
        key_answer = key_answers.get(part, "")
        result = PartAnswerSides(
            part=part,
            key_answer=key_answer,
            candidates={c.id: c.answer for c in cands},
            blank=blank,
        )
        decided = {k: s for k, t in labels.items() if (s := prematch_side(t, key_answer, cands))}
        result.sides = list(decided.values())
        labels = {k: t for k, t in labels.items() if k not in decided}
        if labels:
            ids = {c.id for c in cands}

            def check(o: AnswerSideOutput, labels: dict[str, Transcript] = labels,
                      ids: set[str] = ids) -> list[str]:  # fmt: skip
                return check_sides(o, labels, ids)

            material = "\n".join(
                [
                    exam_question_text(q),
                    "",
                    f"[해설지 답] (소문항 {part or '전체'}) {key_answer or '(해설지에 따로 없음)'}",
                    "[정답 후보]",
                    *[f"- {c.id}: {c.answer}" for c in cands],
                ]
            )
            target = "\n\n".join(
                f"[{k}]\n" + transcript_text(t).split("\n", 1)[-1] for k, t in labels.items()
            )
            res = engine.run(
                dataclasses.replace(MATCH, validator=check),
                TaskInput(
                    variables={"level": scope.level, "part_label": part or "(전체)"},
                    scope=scope_layer(scope),
                    material=material,
                    target=target,
                ),
            )
            agent, model = res.agent, res.model
            result.sides += [
                StudentAnswerSide(
                    student=labels[s.student].student_id, side=s.side,
                    candidate_id=s.candidate_id, quote=s.quote,
                )
                for s in res.value.students
            ]  # fmt: skip
        parts.append(result)
    return CandidateMatches(question_number=q.number, parts=parts, agent=agent, model=model)


# ---------------------------------------------------------------- 채점


_QUOTE_WRAP = "\"'“”‘’「」『』`"
_ITEM_ERROR = re.compile(r"^(R[1-9][0-9]*): ")
_MISSING_ITEMS = re.compile(r"^루브릭 항목 \[(.*?)\]에 대한 판정이 없다")


def fix_grade(o: GradeOutput, rubric: RubricDraft) -> tuple[GradeOutput, list[str]]:
    """코드 선수리 (PLAN.md §2-5): AI에게 다시 묻기 전에 형식만 틀린 것을 고친다. 점수·근거의
    판단은 바꾸지 않는다 — 판단이 걸린 오류(인용이 답안에 없음, 배점 초과 등)는 그대로 둔다.

    - 항목 ID 대소문자·띄어쓰기 (`r1 ` → `R1`)
    - 같은 항목·같은 점수를 두 번 판정 → 하나 (점수가 다르면 그대로 — AI가 정해야 함)
    - 배점을 소수 끝자리만큼 넘은 점수 (2.0000001 → 2)
    - 인용을 감싼 따옴표 (`"V=IR"` → `V=IR`)
    - 루브릭에 없는 정답 후보 ID (대소문자는 고치고, 없는 것은 뺀다)"""
    ids = {i.id.casefold(): i for i in rubric.items}
    cands = {c.id.casefold(): c.id for c in rubric.candidates}
    fixes: list[str] = []
    out: list[ItemJudgement] = []
    for j in o.judgements:
        item = ids.get(j.rubric_item_id.strip().casefold())
        if item is not None and item.id != j.rubric_item_id:
            fixes.append(f"항목 ID '{j.rubric_item_id}' → {item.id}")
            j = j.model_copy(update={"rubric_item_id": item.id})
        if item is not None and item.points < j.awarded <= item.points + 0.01:
            fixes.append(f"{item.id}: 점수 {j.awarded} → {item.points:g}")
            j = j.model_copy(update={"awarded": item.points})
        prev = next((x for x in out if x.rubric_item_id == j.rubric_item_id), None)
        if prev is not None and abs(prev.awarded - j.awarded) < 1e-9:
            fixes.append(f"{j.rubric_item_id}: 같은 판정 두 번 → 하나")
            continue
        if j.evidence_quote:
            q = j.evidence_quote.strip()
            while len(q) > 1 and q[0] in _QUOTE_WRAP and q[-1] in _QUOTE_WRAP:
                q = q[1:-1].strip()
            if q != j.evidence_quote.strip():
                fixes.append(f"{j.rubric_item_id}: 인용의 따옴표 뺌")
            if q != j.evidence_quote:
                j = j.model_copy(update={"evidence_quote": q or None})
        out.append(j)
    matched: list[str] = []
    for c in o.matched_candidates:
        cid = cands.get(c.strip().casefold())
        if cid is None:
            fixes.append(f"루브릭에 없는 정답 후보 '{c}' 뺌")
        elif cid not in matched:
            matched.append(cid)
    if not fixes:
        return o, []
    return o.model_copy(update={"judgements": out, "matched_candidates": matched}), fixes


def grade_repair_items(o: GradeOutput, errors: list[str]) -> list[str] | None:
    """검증 오류가 모두 항목 하나하나의 것이면 그 항목 ID들 (부분 재요청). 아니면 None."""
    keys: list[str] = []
    for e in errors:
        if m := _ITEM_ERROR.match(e):
            keys.append(m.group(1))
        elif m := _MISSING_ITEMS.match(e):
            keys += re.findall(r"R[1-9][0-9]*", m.group(1))
        else:
            return None
    return list(dict.fromkeys(keys)) or None


def merge_grade(order: list[str]) -> Callable[[GradeOutput, GradeOutput, list[str]], GradeOutput]:
    def merge(base: GradeOutput, patch: GradeOutput, keys: list[str]) -> GradeOutput:
        new = {j.rubric_item_id: j for j in patch.judgements if j.rubric_item_id in keys}
        old = {j.rubric_item_id: j for j in base.judgements}
        rank = {k: i for i, k in enumerate(order)}
        ids = sorted({*old, *new}, key=lambda k: rank.get(k, len(rank)))
        return base.model_copy(
            update={
                "judgements": [new.get(k) or old[k] for k in ids],
                "comment": base.comment or patch.comment,
                "flags": list(dict.fromkeys([*base.flags, *patch.flags])),
                "matched_candidates": list(
                    dict.fromkeys([*base.matched_candidates, *patch.matched_candidates])
                ),
            }
        )

    return merge


@dataclass
class _Graded:
    out: GradeOutput
    agent: AgentName
    model: str | None
    attempts: int
    tokens: int | None
    duration_s: float


def _grade_once(
    engine: ContextEngine,
    q: ExamQuestion,
    rubric: Rubric,
    transcript: Transcript,
    scope: ScopeProfile,
    agent: AgentName | None,
    model: str | None,
    examples: Shrinkable | None = None,
    notes: list[str] | None = None,
) -> _Graded:
    def check(o: GradeOutput) -> list[str]:
        return checks.check_grade(o, rubric, transcript, scope)

    def fix(o: GradeOutput) -> tuple[GradeOutput, list[str]]:
        return fix_grade(o, rubric)

    task = dataclasses.replace(
        GRADE,
        validator=check,
        fix=fix,
        partial=PartialRepair(
            select=grade_repair_items,
            merge=merge_grade([i.id for i in rubric.items]),
            noun="채점 항목",
        ),
    )
    material = exam_question_text(q) + "\n\n" + rubric_text(rubric, q.parts)
    if notes:
        material += "\n\n[코드가 이미 채점한 소문항 — 판정하지 말 것]\n" + "\n".join(
            f"- {n}" for n in notes
        )
    res = engine.run(
        task,
        TaskInput(
            variables={"level": scope.level},
            scope=scope_layer(scope),
            material=material,
            examples=examples,
            target=transcript_text(transcript),
        ),
        agent=agent,
        model=model,
    )
    return _Graded(res.value, res.agent, res.model, res.attempts, res.total_tokens, res.duration_s)


def needs_second(first: GradeOutput, attempts: int) -> bool:
    """재채점할 채점 (PLAN.md §2-4 4번): 확신도가 낮거나 검증 수리가 있었던 것만.

    AI가 적은 확인할 점(`flags`)만으로는 다시 채점하지 않는다 — 검토 필요로 강사에게 간다.
    M4 실측(24명, flash-medium): flags로 다시 채점한 27건 중 점수가 달라진 것은 5건, 강사 점수와
    같은 비율은 오히려 낮았고(15/27, 안 한 것 17/18) 호출은 45 → 72번으로 늘었다."""
    return attempts > 1 or any(j.confidence < LOW_CONFIDENCE for j in first.judgements)


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

    대상 학생 자신의 답안은 예시로 넣지 않는다. 빈 답안·빈 소문항·답이 분명한 단답형 소문항은
    코드가 먼저 정하고(`pre_grade`) 나머지 항목만 AI가 채점한다 — 모두 정해지면 AI를 부르지
    않는다. `double`이 있으면 `needs_second`인 채점만 다른 세션(·AI)으로 한 번 더 본다
    (`double.when == "all"`이면 모두).
    """
    if example_students and transcript.student_id in example_students:
        raise PipelineError("채점 대상 학생의 답안을 기준 예시로 넣을 수 없습니다.")
    if rubric.status != "approved":
        raise PipelineError(f"문항 {q.number}: 루브릭이 아직 승인되지 않았습니다.")
    if transcript.unreadable:
        # 강사 확인 전 판독문도 채점한다 (2026-11-04 사용자 결정). 못 읽은 곳만 강사 입력을 기다린다
        raise PipelineError(
            f"{transcript.student_id} 문항 {q.number}: {UNREADABLE} {transcript.unreadable}곳을 "
            "강사가 채워야 채점할 수 있습니다."
        )
    if rubric.code_graded:  # 객관식·단답형: 코드로 (애매한 단답만 가벼운 도우미)
        return grade_by_code(engine, q, rubric, [transcript], scope)[0]
    item_points = {i.id: i.points for i in rubric.items}
    if transcript.is_blank:
        return blank_grade(q, rubric, transcript)
    pre = pre_grade(q, rubric, transcript)
    ai_items = [i for i in rubric.items if i.id not in pre.judgements]
    flags: list[str] = []
    second: GradeOutput | None = None
    diff: list[str] = []
    calls = repairs = 0
    tokens: int | None = None
    duration = 0.0
    used_agent: str = CODE_AGENT
    used_model: str | None = None
    comment = ""
    first_flags: list[str] = []
    matched_ids: list[str] = []
    ai_judgements: list[ItemJudgement] = []
    if ai_items:
        ai_rubric = rubric.model_copy(update={"items": ai_items}) if pre.judgements else rubric
        first = _grade_once(
            engine, q, ai_rubric, transcript, scope, agent, model, examples, pre.notes
        )
        runs = [first]
        flags += first.out.flags
        if double is not None and (double.when == "all" or needs_second(first.out, first.attempts)):
            again = _grade_once(
                engine, q, ai_rubric, transcript, scope, double.agent, double.model, examples,
                pre.notes,
            )  # fmt: skip
            runs.append(again)
            second = again.out
            diff = checks.disagreements(first.out, second)
            if diff:
                flags.append(f"이중 채점 불일치: {', '.join(diff)}")
        calls = sum(r.attempts for r in runs)
        repairs = sum(r.attempts - 1 for r in runs)
        if any(r.tokens is not None for r in runs):
            tokens = sum(r.tokens or 0 for r in runs)
        duration = sum(r.duration_s for r in runs)
        used_agent, used_model = first.agent, first.model
        comment = first.out.comment
        first_flags = first.out.flags
        matched_ids = first.out.matched_candidates
        ai_judgements = first.out.judgements
    by_id = {**pre.judgements, **{j.rubric_item_id: j for j in ai_judgements}}
    judgements = [by_id[i.id] for i in rubric.items if i.id in by_id]
    low = [j.rubric_item_id for j in judgements if j.confidence < LOW_CONFIDENCE]
    if low:
        flags.append(f"확신도 낮음: {', '.join(low)}")
    pending = {c.id: c for c in rubric.candidates if c.status == "proposed"}
    matched = [pending[c] for c in matched_ids if c in pending]
    flags += [
        f"학생 답이 강사 미확인 정답 후보 {c.id}({c.answer})와 같음 — 인정하면 "
        f"{', '.join(c.item_ids) or '해당 항목'} 점수를 주세요"
        for c in matched
    ]
    needs_review = bool(diff or low or first_flags or matched)
    return GradeResult(
        student_id=transcript.student_id,
        question_number=q.number,
        rubric_version=rubric.version,
        judgements=judgements,
        comment=comment,
        flags=flags,
        total=sum(j.awarded for j in judgements),
        max_points=rubric.total,
        status="needs_review" if needs_review else "auto",
        second_opinion=second.judgements if second else None,
        disagreements=diff,
        agent=used_agent,
        model=used_model,
        examples_used=list(example_students or []) if ai_items else [],
        item_points=item_points,
        code_items=list(pre.judgements),
        calls=calls,
        repairs=repairs,
        tokens=tokens,
        duration_s=duration,
    )


def blank_grade(q: ExamQuestion, rubric: Rubric, transcript: Transcript) -> GradeResult:
    """빈 답안(판독문에 글이 없음): AI 없이 0점. 판독이 놓쳤을 수 있으므로 강사가 판독본(원본
    그림)으로 확인하게 '검토 필요'로 둔다 (M3에서 남긴 것)."""
    return GradeResult(
        student_id=transcript.student_id,
        question_number=q.number,
        rubric_version=rubric.version,
        judgements=[
            ItemJudgement(
                rubric_item_id=i.id,
                awarded=0,
                evidence_quote=None,
                reason="답 없음",
                deduction_reason="답을 쓰지 않음",
                confidence=1.0,
            )
            for i in rubric.items
        ],
        comment="",
        flags=["빈 답안 — AI 없이 0점. 원본 그림에 답이 있으면 판독문을 채워 확정하세요"],
        total=0,
        max_points=rubric.total,
        status="needs_review",
        agent=CODE_AGENT,
        item_points={i.id: i.points for i in rubric.items},
        code_items=[i.id for i in rubric.items],
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


def spread_part_score(
    items: list[tuple[str, float, float]], score: float, step: float = 0.5
) -> dict[str, float]:
    """소문항 점수를 그 소문항의 루브릭 항목들에 나눈다. `items` = (ID, 배점, AI 점수).

    강사는 소문항 단위로 점수를 매긴다 (빨간 펜). AI 점수의 비율을 따라 나누고(배점을 넘지 않게,
    0.5점 단위), AI가 0점이면 앞 항목부터 채운다. 합은 정확히 `score`.
    """
    cap = sum(p for _, p, _ in items)
    score = max(0.0, min(score, cap))
    ai_total = sum(a for _, _, a in items)
    if abs(ai_total - score) < 1e-9:
        return {i: a for i, _, a in items}
    out: dict[str, float] = {}
    if ai_total > 0:
        for i, p, a in items:
            out[i] = min(p, round(a * score / ai_total / step) * step)
    else:
        left = score
        for i, p, _ in items:
            out[i] = min(p, left)
            left -= out[i]
    # 반올림으로 생긴 차이를 배점 안에서 맞춘다 (뒤 항목부터)
    diff = score - sum(out.values())
    for i, p, _ in reversed(items):
        if abs(diff) < 1e-9:
            break
        room = (p - out[i]) if diff > 0 else -out[i]
        move = min(diff, room) if diff > 0 else max(diff, room)
        out[i] += move
        diff -= move
    return out


def teacher_part_review(
    g: GradeResult,
    rubric: Rubric,
    q: ExamQuestion,
    part_scores: dict[str, float],
    *,
    part_notes: dict[str, str] | None = None,
    note: str | None = None,
    by: str | None = None,
) -> GradeResult:
    """강사가 소문항 단위로 확정 — 소문항 점수를 항목에 나눠 `teacher_review`로 저장한다.

    고친 소문항은 AI 판정이 `ai_judgements`에 남아 기준 예시·루브릭 개선 사례가 된다.
    """
    labels = {p.label: p for p in q.parts} or {"": None}
    unknown = sorted(set(part_scores) - set(labels))
    if unknown:
        raise PipelineError(f"문항 {q.number}에 없는 소문항: {unknown} (있는 것: {sorted(labels)})")
    current = {j.rubric_item_id: j.awarded for j in g.judgements}
    awarded: dict[str, float] = {}
    item_notes: dict[str, str] = {}
    notes = part_notes or {}
    for label, score in part_scores.items():
        part = labels[label]
        items = rubric.part_items(label)
        cap = part.points if part else q.points
        if not 0 <= score <= cap + 1e-9:
            raise PipelineError(
                f"소문항 {label or '전체'} 점수 {score:g}는 0~{cap:g} 사이여야 합니다"
            )
        awarded |= spread_part_score(
            [(i.id, i.points, current.get(i.id, 0.0)) for i in items], score
        )
        if notes.get(label):
            item_notes |= {i.id: f"[{label or '전체'}] {notes[label]}" for i in items}
    out = teacher_review(g, rubric, awarded, note=note, by=by, item_notes=item_notes)
    return out.model_copy(
        update={
            "teacher_part_scores": {**out.teacher_part_scores, **part_scores},
            "teacher_part_notes": {
                **out.teacher_part_notes,
                **{k: v for k, v in notes.items() if v},
            },
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
    s: RubricSuggestion,
    q: ExamQuestion,
    solution: SolutionDraft | None,
    case_students: set[str],
    *,
    key: AnswerKey | None = None,
    scope: ScopeProfile | None = None,
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
        # 소문항 배점 합은 지킨다 (해설지 부분 점수 구조는 강사 사례가 우선이라 보지 않음)
        problems += checks.check_rubric(s.revised, q, solution)
        if scope is not None:  # 보정 제안도 해설지 범위 안에서만 (§4-2)
            problems += checks.check_rubric_scope(s.revised, q, key, scope)
    return problems


def suggest_rubric(
    engine: ContextEngine,
    q: ExamQuestion,
    rubric: Rubric,
    solution: SolutionDraft | None,
    scope: ScopeProfile,
    corrections: str,
    case_students: set[str],
    key: AnswerKey | None = None,
) -> RubricSuggestion:
    """강사 수정 사례 → 루브릭 개선 제안. 적용은 강사가 고른 뒤 `edit_rubric` (다시 승인 필요).

    해설지 비교 결과(`checks`)·정답 후보(강사 결정 포함)·해설지 기준 확인은 제안이 바꾸지 못한다.
    해설지로 만든 루브릭(AI 풀이 없음)은 `solution=None`으로 해설지만 자료로 준다.
    """

    def check(v: RubricSuggestion) -> list[str]:
        return check_suggestion(v, q, solution, case_students, key=key, scope=scope)

    task = dataclasses.replace(SUGGEST, validator=check)
    out = engine.run(
        task,
        TaskInput(
            variables={"level": scope.level, "points": q.points, "parts": bool(q.parts)},
            scope=scope_layer(scope),
            material="\n\n".join(
                [
                    exam_question_text(q),
                    *([key_text(key, q)] if key else []),
                    *(
                        [solution_text(solution, "[AI 풀이]" if key else "[모범 해설]")]
                        if solution
                        else []
                    ),
                    rubric_text(rubric, q.parts),
                    "[강사가 확정한 채점 사례]\n" + corrections,
                ]
            ),
        ),
    ).value
    if out.revised is not None:
        data = out.revised.model_dump()
        data["never_deduct"] = checks.merge_never_deduct(out.revised, DEFAULT_NEVER_DEDUCT)
        data["checks"] = [c.model_dump() for c in rubric.checks]
        data["candidates"] = [c.model_dump() for c in rubric.candidates]
        data["key_conflicts"] = list(rubric.key_conflicts)
        out = out.model_copy(update={"revised": RubricDraft.model_validate(data)})
    return out
