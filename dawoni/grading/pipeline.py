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
from collections.abc import Callable
from dataclasses import dataclass

from rapidfuzz import fuzz

from dawoni.config import AgentName
from dawoni.context import ContextEngine, Shrinkable, TaskDef, TaskInput
from dawoni.grading import checks
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
    KeyRubricDraft,
    PartAnswerSides,
    PartCheck,
    Rubric,
    RubricAudit,
    RubricDraft,
    ScopedSolution,
    SolutionDraft,
    StudentAnswerSide,
    Transcript,
    TranscriptLine,
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
    role="rubric_builder",
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
        problems = checks.check_solution_math(d) + checks.check_part_answers(d, q)
        if d.out_of_scope_note:
            return problems  # 범위 안에서 못 푼다는 판단은 수리하지 않고 강사에게 알린다
        return problems + checks.check_solution_scope(d, scope, with_extensions=with_extensions)

    task = dataclasses.replace(SOLVE, validator=check)
    inp = TaskInput(
        variables={"level": scope.level, "has_parts": bool(q.parts)},
        scope=scope_layer(scope, with_extensions=with_extensions),
        material=exam_question_text(q),
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
        return checks.check_rubric(d.to_rubric_draft(), q, None, key)

    variables = {
        "level": scope.level,
        "points": q.points,
        "parts": bool(q.parts),
        "never_deduct": DEFAULT_NEVER_DEDUCT,
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
    notes += [f"해설지 기준 확인: {x}" for x in draft.key_conflicts]
    if draft.accepted_alternatives:
        notes.append(
            f"AI가 넓힌 표현 {len(draft.accepted_alternatives)}개 — 인정한 것만 채점에 씁니다"
        )
    notes.append("해설지를 넓혀 만든 루브릭 (AI 풀이 없음) — 해설지 오타가 걱정되면 'AI 풀기'")
    data = draft.model_dump()
    data["never_deduct"] = checks.merge_never_deduct(draft, DEFAULT_NEVER_DEDUCT)
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
        return checks.check_rubric(d, q, solution, key)

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
    notes += [f"해설지 기준 확인: {x}" for x in draft.key_conflicts]
    data = draft.model_dump()
    data["never_deduct"] = checks.merge_never_deduct(draft, DEFAULT_NEVER_DEDUCT)
    data["candidates"] = [c.model_dump() for c in candidates]
    data["checks"] = [c.model_dump() for c in part_checks]
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
    bump = 1 if previous.status == "approved" else 0
    return Rubric(
        **draft.model_dump(),
        question_number=previous.question_number,
        based_on_key=previous.based_on_key,
        review_notes=previous.review_notes,
        version=previous.version + bump,
        status="draft",
        created_by=previous.created_by,
        created_at=previous.created_at,
        agent=previous.agent,
        model=previous.model,
    )


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
            "status": "draft",
            "version": rubric.version + bump,
        }
    )


def part_lines(t: Transcript, part: str) -> list[TranscriptLine]:
    """판독문에서 그 소문항 줄 (지운 줄 제외). 소문항 표시가 하나도 없으면 전부."""
    lines = [x for x in t.lines if not x.struck]
    if not part or not any(x.part for x in lines):
        return lines
    return [x for x in lines if (x.part or "") == part]


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
            material=exam_question_text(q) + "\n\n" + rubric_text(rubric, q.parts),
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
    pending = {c.id: c for c in rubric.candidates if c.status == "proposed"}
    matched = [pending[c] for c in first.matched_candidates if c in pending]
    flags += [
        f"학생 답이 강사 미확인 정답 후보 {c.id}({c.answer})와 같음 — 인정하면 "
        f"{', '.join(c.item_ids) or '해당 항목'} 점수를 주세요"
        for c in matched
    ]
    needs_review = bool(diff or low or first.flags or matched)
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
        # 소문항 배점 합은 지킨다 (해설지 부분 점수 구조는 강사 사례가 우선이라 보지 않음)
        problems += checks.check_rubric(s.revised, q, solution)
    return problems


def suggest_rubric(
    engine: ContextEngine,
    q: ExamQuestion,
    rubric: Rubric,
    solution: SolutionDraft,
    scope: ScopeProfile,
    corrections: str,
    case_students: set[str],
    key: AnswerKey | None = None,
) -> RubricSuggestion:
    """강사 수정 사례 → 루브릭 개선 제안. 적용은 강사가 고른 뒤 `edit_rubric` (다시 승인 필요).

    해설지 비교 결과(`checks`)·정답 후보(강사 결정 포함)·해설지 기준 확인은 제안이 바꾸지 못한다.
    """

    def check(v: RubricSuggestion) -> list[str]:
        return check_suggestion(v, q, solution, case_students)

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
                    solution_text(solution, "[AI 풀이]" if key else "[모범 해설]"),
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
