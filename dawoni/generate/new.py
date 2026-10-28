"""새 문제 만들기: 강사의 아이디어(자연어) → 문항 + 모범답안 (+ 서술형 루브릭).

1. (제시문을 쓰면) 강사가 말로 적은 교과 밖 개념 → 확장 개념 하나로 정리 (강사가 확인·수정)
2. 아이디어 + 답 형태(객관식·ㄱㄴㄷ 합답형·단답형·서술형) + 범위로 문항과 모범답안을 쓴다
3. 코드 검사: 형태(선지 5개, 합답형 조합과 진술 참·거짓이 맞는지, 소문항 배점 합),
   범위 밖 개념, 제시문 인용·사용, 모범답안 답 = 표시한 정답
4. 정답을 가린 채 새 세션에서 독립으로 풀어 답을 비교 (같은 마무리: `pipeline.finish`)
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field

from dawoni.context import ContextEngine, TaskDef, TaskInput
from dawoni.generate import checks as gc
from dawoni.generate import pipeline as gp
from dawoni.grading import checks as gchecks
from dawoni.grading.render import question_text, scope_layer
from dawoni.mathx.sym import equivalent
from dawoni.models.content import BogiBlock, BoxBlock, iter_runs
from dawoni.models.generation import (
    ANSWER_FORMAT_LABELS,
    AnswerFormat,
    GeneratedSet,
    NewProblemDraft,
    SourceRef,
)
from dawoni.models.grading import ExamQuestion
from dawoni.models.item import ChoiceAnswer, ItemContent, ItemType, ValueAnswer
from dawoni.models.scope import ExtensionConcept, ScopeProfile

DRAFT_EXT: TaskDef[ExtensionConcept] = TaskDef(
    name="draft_extension", template="draft_extension.md.j2", output=ExtensionConcept,
    rule_target="variant", timeout_s=600,
)  # fmt: skip
NEW: TaskDef[NewProblemDraft] = TaskDef(
    name="gen_new", template="gen_new.md.j2", output=NewProblemDraft,
    rule_target="variant", timeout_s=900,
)  # fmt: skip

ITEM_TYPE: dict[AnswerFormat, ItemType] = {
    "choice5": "multiple_choice",
    "bogi": "multiple_choice",
    "short": "short_answer",
    "essay": "essay",
}
_JAMO = re.compile(r"[ㄱ-ㅎ]")


@dataclass
class NewRequest:
    idea: str
    answer_format: AnswerFormat
    points: float
    unit_labels: list[str] = field(default_factory=lambda: list[str]())
    unit_ids: list[str] = field(default_factory=lambda: list[str]())
    difficulty: int = 3
    n_parts: int = 1
    extension: ExtensionConcept | None = None
    references: list[ExamQuestion] = field(default_factory=lambda: list[ExamQuestion]())


def next_ext_id(scope: ScopeProfile) -> str:
    taken = scope.allowed_ids(with_extensions=True)
    n = 1
    while f"X{n}" in taken:
        n += 1
    return f"X{n}"


def draft_extension(
    engine: ContextEngine, scope: ScopeProfile, concept: str, idea: str = ""
) -> ExtensionConcept:
    """강사가 말로 적은 추가 개념 → 확장 개념 (정의·가정·유추할 내용)."""
    if not concept.strip():
        raise ValueError("추가할 개념을 적어 주세요")
    ext_id = next_ext_id(scope)

    def check(x: ExtensionConcept) -> list[str]:
        problems: list[str] = []
        if x.id != ext_id:
            problems.append(f"id는 {ext_id}로 쓸 것")
        if len(x.definition.strip()) < 5:
            problems.append("definition에 정의를 구체적으로 쓸 것")
        return problems

    return engine.run(
        dataclasses.replace(DRAFT_EXT, validator=check),
        TaskInput(
            variables={"level": scope.level, "ext_id": ext_id, "concept": concept, "idea": idea},
            scope=scope_layer(scope),
        ),
    ).value


# ---------------------------------------------------------------- 검사


def _labels(text: str) -> set[str]:
    return set(_JAMO.findall(text))


def _choice_text(item: ItemContent, label: int) -> str:
    c = next((c for c in item.choices if c.label == label), None)
    return "".join(r.v for r in c.runs) if c else ""


def check_new(d: NewProblemDraft, req: NewRequest, scope: ScopeProfile) -> list[str]:
    fmt = req.answer_format
    item = d.item
    ext = req.extension
    problems: list[str] = []
    want = ITEM_TYPE[fmt]
    if item.item_type != want:
        problems.append(f"item_type은 {want}여야 함 ({ANSWER_FORMAT_LABELS[fmt]})")

    if fmt in ("choice5", "bogi"):
        if len(item.choices) != 5:
            problems.append(f"선지는 5개여야 함 (지금 {len(item.choices)}개)")
        if d.answer_label is None:
            problems.append("answer_label에 정답 선지 번호를 쓸 것")
        got = gc.choice_label_of(d.solution.final_answer.latex)
        if d.answer_label is not None and got is not None and got != d.answer_label:
            problems.append(f"모범답안의 답({got}번)과 answer_label({d.answer_label}번)이 다름")
    if fmt == "bogi":
        bogi = next((b for b in item.stem if isinstance(b, BogiBlock)), None)
        if bogi is None:
            problems.append("본문에 bogi 블록(<보기> ㄱ, ㄴ, ㄷ)을 둘 것")
        else:
            labels = [x.label.strip(". ") for x in bogi.items]
            stated = [s.label.strip(". ") for s in d.statements]
            if sorted(stated) != sorted(labels):
                problems.append(f"statements의 기호 {stated}가 <보기> 기호 {labels}와 다름")
            combos = [frozenset(_labels(_choice_text(item, c.label))) for c in item.choices]
            bad = [c.label for c, k in zip(item.choices, combos, strict=True)
                   if not k or not k <= set(labels)]  # fmt: skip
            if bad:
                problems.append(f"선지 {bad}가 <보기> 기호의 조합이 아님 (예: 'ㄱ, ㄷ')")
            if len(set(combos)) != len(combos):
                problems.append("같은 조합의 선지가 두 번 나옴")
            true = frozenset(s.label.strip(". ") for s in d.statements if s.true)
            n = d.answer_label
            if n is not None and 1 <= n <= len(combos) and combos[n - 1] != true:
                problems.append(f"정답 선지 {n}번의 조합이 참인 진술 {sorted(true)}과 다름")
            if not true:
                problems.append("참인 진술이 하나도 없음 — 합답형은 참인 진술이 하나 이상")
    if fmt == "short":
        if not d.answer_latex:
            problems.append("answer_latex에 단답형 정답을 쓸 것")
        fa = d.solution.final_answer
        if d.answer_expr and fa.expr and equivalent(d.answer_expr, fa.expr) is False:
            problems.append(f"모범답안의 답({fa.expr})과 answer_expr({d.answer_expr})가 다름")
    if fmt == "essay":
        if req.n_parts > 1:
            if len(d.parts) != req.n_parts:
                problems.append(f"소문항을 {req.n_parts}개로 (지금 {len(d.parts)}개)")
            total = sum(p.points for p in d.parts)
            if d.parts and abs(total - req.points) > 1e-9:
                problems.append(f"소문항 배점 합({total:g})이 {req.points:g}점이어야 함")
        elif d.parts:
            problems.append("소문항 없이 하나로 쓸 것 (parts는 빈 목록)")
    elif d.parts:
        problems.append("서술형이 아니면 parts는 빈 목록")

    use_scope = gp.with_extension(scope, ext) if ext else scope
    problems += gchecks.check_solution_scope(d.solution, use_scope, with_extensions=ext is not None)
    problems += gchecks.check_solution_math(d.solution)
    if ext is not None:
        if not any(isinstance(b, BoxBlock) for b in item.stem):
            problems.append("제시문을 box 블록(제목 '제시문')으로 본문 앞에 둘 것")
        if not any(ext.id in s.uses for s in d.solution.steps):
            problems.append(f"모범답안이 제시문 개념 {ext.id}를 쓰지 않음 (uses에 표시할 것)")
        passage = gc.normalize_text(gc.passage_text(item))
        quote = d.definition_quotes.get(ext.id)
        if not quote:
            problems.append(f"definition_quotes에 {ext.id}의 정의 문장이 없다")
        elif gc.normalize_text(quote) not in passage:
            problems.append(f"{ext.id}의 정의 인용 '{quote[:40]}'이 제시문에 그대로 없다")
    if not any(r.v.strip() for r in iter_runs(item.stem)):
        problems.append("본문이 비어 있음")
    return problems


# ---------------------------------------------------------------- 만들기


def _material(req: NewRequest) -> str | None:
    if not req.references:
        return None
    return "[참고 문항] (형식·난이도만 참고)\n\n" + "\n\n".join(
        question_text(q.number, q.points, q.item) for q in req.references
    )


def new_draft(engine: ContextEngine, req: NewRequest, scope: ScopeProfile) -> gp.Draft:
    if not req.idea.strip():
        raise ValueError("문제 아이디어를 적어 주세요")
    ext = req.extension
    use_scope = gp.with_extension(scope, ext) if ext else scope

    def check(d: NewProblemDraft) -> list[str]:
        return check_new(d, req, scope)

    d = engine.run(
        dataclasses.replace(NEW, validator=check),
        TaskInput(
            variables={
                "level": scope.level,
                "units": ", ".join(req.unit_labels) or "(범위 프로필 전체)",
                "points": f"{req.points:g}",
                "difficulty": req.difficulty,
                "format": req.answer_format,
                "format_label": ANSWER_FORMAT_LABELS[req.answer_format],
                "n_parts": req.n_parts,
                "idea": req.idea,
                "ext_id": ext.id if ext else "",
                "ext_name": ext.name if ext else "",
                "ext_definition": ext.definition if ext else "",
                "ext_assumptions": "; ".join(ext.assumptions) if ext else "",
                "ext_inference": (ext.expected_inference or "") if ext else "",
            },
            scope=scope_layer(use_scope, with_extensions=ext is not None),
            material=_material(req),
        ),
    ).value
    answer: ChoiceAnswer | ValueAnswer | None = None
    if d.answer_label is not None and req.answer_format in ("choice5", "bogi"):
        answer = ChoiceAnswer(kind="choice", labels=[d.answer_label])
    elif req.answer_format == "short" and d.answer_latex:
        answer = ValueAnswer(kind="value", latex=d.answer_latex, expr=d.answer_expr)
    item = d.item.model_copy(update={"answer": answer, "difficulty": req.difficulty,
                                     "points": req.points})  # fmt: skip
    note = d.design_note
    if d.statements:
        note += "\n" + "\n".join(
            f"{s.label}: {'참' if s.true else '거짓'} — {s.reason}" for s in d.statements
        )
    return gp.Draft(
        item=item,
        note=note,
        expected_label=d.answer_label if req.answer_format in ("choice5", "bogi") else None,
        expected_expr=d.answer_expr,
        expected_latex=d.answer_latex,
        solution=d.solution,
        extension=ext,
        skill_ids=sorted(gc.skill_ids(d.solution)),
        parts=d.parts,
    )


def make_new(engine: ContextEngine, req: NewRequest, scope: ScopeProfile) -> GeneratedSet:
    """새 문제 하나 (초안). 저장은 부르는 쪽에서 (`GenStore`)."""
    draft = new_draft(engine, req, scope)
    src = ExamQuestion(number="새", points=req.points, item=draft.item, parts=draft.parts)
    s = gp.finish(engine, draft, "new", src, scope, SourceRef(question_number="새 문제"))
    return s.model_copy(update={"unit_ids": req.unit_ids, "idea": req.idea,
                                "answer_format": req.answer_format})  # fmt: skip
