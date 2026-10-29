"""문제 만들기 — 문제은행 문항을 기준으로 동형·신규 제작, 승인하면 문제은행에 넣기.

범위는 강사가 고른 범위 프로필, 고르지 않으면 기준 문항 단원의 단원표 범위(자동)다.
단원표 범위도 범위 프로필로 저장해 둔다 (제작물 → 시험으로 묶을 때 같은 범위를 찾게).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from dawoni.bank import BankError
from dawoni.bank.export import bank_answer
from dawoni.bank.ingest import unit_scope
from dawoni.bank.store import BankStore
from dawoni.bank.units import UnitStore
from dawoni.generate import pipeline as gp
from dawoni.generate import workflow as gw
from dawoni.grading.pipeline import solve_question
from dawoni.grading.store import ScopeStore
from dawoni.models.bank import AnswerComparison, BankItem, BankSourceRef
from dawoni.models.figure import Figure
from dawoni.models.generation import (
    AnswerFormat,
    ExtensionProposals,
    GeneratedSet,
    SourceRef,
    VariantKind,
)
from dawoni.models.grading import ExamQuestion, ScopedSolution
from dawoni.models.scope import ExtensionConcept, ScopeProfile
from dawoni.services import Services


def item_trees(svc: Services, it: BankItem) -> list[str]:
    ids = [u.split(".", 1)[0] for u in [*it.unit_ids, *it.concept_units.values()]]
    return list(dict.fromkeys(ids))


def auto_scope(svc: Services, tree_ids: list[str]) -> tuple[ScopeProfile, dict[str, str]]:
    """단원표 범위를 범위 프로필로 저장 (같은 단원표 조합이면 같은 ID)."""
    units = UnitStore(svc.workspace)
    trees = [units.get(t) for t in tree_ids]
    scope, mapping = unit_scope(trees)
    scope = scope.model_copy(update={"title": f"{scope.title} (자동)"})
    ScopeStore(svc.workspace).save(scope)
    return scope, mapping


def base_for(
    svc: Services, it: BankItem, scope_id: str | None
) -> tuple[ScopeProfile, ScopedSolution | None, list[str]]:
    """(범위, 기준 문항 풀이, 경고). 풀이 ID가 범위와 맞지 않으면 그 범위로 다시 푼다.

    아직 풀지 않은 문항(풀이 없이 DB화)이면 먼저 풀어 문제은행에 저장한다 (원본 답과 비교 포함).
    """
    if it.solution is None and it.answer_check is None:
        from dawoni.bank.ingest import solve_items
        from dawoni.sharing import machine_id

        it = solve_items(svc, [it.id], machine=machine_id(svc.paths)).solved[0]
    if it.needs_check:
        raise BankError(
            "답 확인이 필요한 문항입니다 — 문제은행에서 원본 답과 AI 답 중 맞는 쪽을 먼저 고르세요"
        )
    warnings: list[str] = []
    if scope_id:
        scope = ScopeStore(svc.workspace).get(scope_id)
        mapping = None
    else:
        trees = item_trees(svc, it)
        if not trees:
            raise BankError("단원이 없는 문항입니다 — 단원을 정하거나 범위 프로필을 고르세요")
        scope, mapping = auto_scope(svc, trees)

    sol = it.solution
    wrong = (
        it.answer_side() == "original"
        and it.answer_check is not None
        and it.answer_check.verdict != "same"
    )
    if wrong:
        warnings.append("기준 문항의 AI 풀이가 원본 답과 달라 기준 풀이 없이 만듭니다")
        return scope, None, warnings
    same_ids = (
        sol is not None
        and mapping is not None
        and all(mapping.get(c) == u for c, u in it.concept_units.items())
    )
    if sol is not None and same_ids:
        return scope, sol, warnings
    sol = solve_question(svc.engine, it.question, scope)
    if sol.out_of_scope_note:
        warnings.append(f"고른 범위로 기준 문항을 풀 수 없다는 판단: {sol.out_of_scope_note}")
    return scope, sol, warnings


def generate_from_bank(
    svc: Services,
    item_id: str,
    kind: VariantKind,
    *,
    scope_id: str | None = None,
    opts: gp.Options | None = None,
    extension: ExtensionConcept | None = None,
    progress: gw.Progress = gw._noop,  # pyright: ignore[reportPrivateUsage]
) -> list[GeneratedSet]:
    it = BankStore(svc.workspace).get(item_id)
    progress(0, 1, "기준 문항 풀이 확인")
    scope, sol, warnings = base_for(svc, it, scope_id)
    src = _src_question(it)
    others = gw.same_scope_items(svc.workspace, scope.id) if kind == "novel_a" else []
    sets = gw.generate_from(
        svc, src, sol, scope,
        SourceRef(question_number=it.question.number, bank_item_id=it.id), kind,
        opts=opts, extension=extension, others=others, progress=progress,
    )  # fmt: skip
    if warnings:
        gs = gw.GenStore(svc.workspace)
        sets = [gs.save(s.model_copy(update={"warnings": [*warnings, *s.warnings]})) for s in sets]
    return sets


def _src_question(it: BankItem) -> ExamQuestion:
    """정답을 문항에 넣어 준다 (조건 변형 등이 원래 답을 알게). 고르는 규칙은 DOCX와 같다."""
    item = it.question.item.model_copy(update={"answer": bank_answer(it)})
    return it.question.model_copy(update={"item": item})


def propose_from_bank(svc: Services, item_id: str, scope_id: str | None) -> ExtensionProposals:
    it = BankStore(svc.workspace).get(item_id)
    scope, sol, _ = base_for(svc, it, scope_id)
    return gp.propose_extensions(svc.engine, _src_question(it), sol, scope)


def copy_figures(
    svc: Services, figures: list[Figure], src_root: Path, item_id: str
) -> list[Figure]:
    bank = BankStore(svc.workspace)
    out: list[Figure] = []
    for f in figures:
        if f.orig and (src_root / f.orig).is_file():
            dest = bank.item_dir(item_id) / "figures" / Path(f.orig).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_root / f.orig, dest)
            f = f.model_copy(update={"orig": f"{item_id}/figures/{dest.name}"})
        out.append(f)
    return out


def approve_to_bank(svc: Services, gen_id: str, by: str | None = None) -> BankItem:
    """제작물 승인 → 문제은행 문항 (기준 문항의 단원·출처를 이어받는다)."""
    gs = gw.GenStore(svc.workspace)
    s = gw.review(gs, gen_id, "approved", by)
    bank = BankStore(svc.workspace)
    parent = bank.get(s.source.bank_item_id) if s.source.bank_item_id else None
    if s.bank_item_id and bank.exists(s.bank_item_id):
        return bank.get(s.bank_item_id)
    from dawoni.models.base import new_id

    item_id = new_id("bk")
    src_root = svc.workspace.bank if s.source.bank_item_id else (
        svc.workspace.exams / (s.source.exam_id or "")
    )  # fmt: skip
    item = s.item.model_copy(
        update={"figures": copy_figures(svc, s.item.figures, src_root, item_id)}
    )
    concept_units: dict[str, str] = {}
    if s.scope_id.startswith("units-"):
        trees = [
            UnitStore(svc.workspace).get(t) for t in s.scope_id.removeprefix("units-").split("-")
        ]
        _, mapping = unit_scope(trees)
        used = {u for st in s.solution.steps for u in st.uses}
        concept_units = {c: u for c, u in mapping.items() if c in used}
    title = "다워니 제작" + (f" · 기준 {parent.source.display()}" if parent else " · 새 문제")
    verified = s.solution.verified
    it = BankItem(
        id=item_id,
        question=ExamQuestion(number="1", points=s.points, item=item, parts=s.parts),
        subject=parent.subject if parent else None,
        unit_ids=list(parent.unit_ids) if parent else list(s.unit_ids),
        origin="new" if s.kind == "new" else "variant",
        source=BankSourceRef(title=title, kind="dawoni"),
        parent_id=parent.id if parent else None,
        gen_kind=s.kind,
        solution=s.solution,
        solution_by="ai",
        concept_units=concept_units,
        answer_check=AnswerComparison(
            verdict="no_original" if verified else "unsure",
            ai_answer=s.solution.final_answer.latex,
            notes=[] if verified else list(s.warnings) or ["독립 풀이로 답이 확인되지 않음"],
        ),
        rubric=s.rubric,
        note=s.note,
        reviewed=True,
        reviewed_by=by,
    )
    it = bank.save(it, by=by)
    gs.save(s.model_copy(update={"bank_item_id": it.id}))
    return it


# ---------------------------------------------------------------- 새 문제


def scope_for_units(svc: Services, scope_id: str | None, unit_ids: list[str]) -> ScopeProfile:
    if scope_id:
        return ScopeStore(svc.workspace).get(scope_id)
    trees = list(dict.fromkeys(u.split(".", 1)[0] for u in unit_ids))
    if not trees:
        raise BankError("단원을 고르거나 범위 프로필을 고르세요")
    return auto_scope(svc, trees)[0]


def draft_extension_for(
    svc: Services, scope_id: str | None, unit_ids: list[str], concept: str, idea: str = ""
) -> ExtensionConcept:
    from dawoni.generate.new import draft_extension

    return draft_extension(svc.engine, scope_for_units(svc, scope_id, unit_ids), concept, idea)


def make_new_problem(
    svc: Services,
    *,
    idea: str,
    answer_format: AnswerFormat,
    points: float,
    unit_ids: list[str],
    scope_id: str | None = None,
    difficulty: int = 3,
    n_parts: int = 1,
    extension: ExtensionConcept | None = None,
    reference_ids: list[str] | None = None,
    progress: gw.Progress = gw._noop,  # pyright: ignore[reportPrivateUsage]
) -> GeneratedSet:
    from dawoni.generate.new import NewRequest, make_new

    units = UnitStore(svc.workspace)
    for u in unit_ids:
        if units.find(u) is None or units.label(u).startswith("("):
            raise BankError(f"없는 단원: {u}")
    scope = scope_for_units(svc, scope_id, unit_ids)
    bank = BankStore(svc.workspace)
    refs = [_src_question(bank.get(i)) for i in reference_ids or []]
    req = NewRequest(
        idea=idea, answer_format=answer_format, points=points, difficulty=difficulty,
        n_parts=n_parts, extension=extension, references=refs, unit_ids=unit_ids,
        unit_labels=[units.label(u) for u in unit_ids],
    )  # fmt: skip
    progress(0, 2, "AI가 문항·모범답안 작성")
    s = make_new(svc.engine, req, scope)
    progress(2, 2, "독립 풀이 확인 완료 — 검토 후 승인하세요")
    return gw.GenStore(svc.workspace).save(s)
