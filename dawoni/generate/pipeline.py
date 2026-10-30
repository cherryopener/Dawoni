"""문항 제작 파이프라인 (PLAN §9).

AI: 틀·초안 (수치형 틀 / 미지수형 / 조건 변형 / 신규 A / 신규 B)
 → 코드: 검사 (실패하면 수리 요청) · 수치형은 값 조합 탐색
 → AI: 독립 풀이 (범위 해설) → 코드: 기대한 답과 비교 (수치·sympy 동치·선지 번호)
 → AI: 루브릭 초안 (답이 맞을 때만)
 → 초안으로 저장 (강사 승인)
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from pydantic import BaseModel

from dawoni.context import ContextEngine, TaskDef, TaskFailed, TaskInput
from dawoni.export.fill import fill_item, with_values
from dawoni.generate import checks as gc
from dawoni.generate.search import SearchResult, search
from dawoni.grading import checks as gchecks
from dawoni.grading.pipeline import PipelineError, derive_rubric, solve_question
from dawoni.grading.render import question_text, scope_layer, solution_text
from dawoni.mathx.sym import ExprError, equivalent, parse, to_latex
from dawoni.models.generation import (
    ExtensionProposals,
    GeneratedSet,
    GenKind,
    NovelDraft,
    NumericTemplate,
    RewriteDraft,
    SourceRef,
    SymbolicTemplate,
)
from dawoni.models.grading import (
    ExamPart,
    ExamQuestion,
    FinalAnswer,
    RubricDraft,
    ScopedSolution,
    SolutionDraft,
)
from dawoni.models.item import ChoiceAnswer, ItemContent, ValueAnswer
from dawoni.models.scope import ExtensionConcept, ScopeProfile


def _task[T: BaseModel](name: str, template: str, output: type[T]) -> TaskDef[T]:
    return TaskDef(
        name=name,
        template=template,
        output=output,
        rule_target="variant",
        role="problem_maker",
    )


NUMERIC = _task("gen_numeric", "gen_numeric.md.j2", NumericTemplate)
SYMBOLIC = _task("gen_symbolic", "gen_symbolic.md.j2", SymbolicTemplate)
CONDITION = _task("gen_condition", "gen_condition.md.j2", RewriteDraft)
NOVEL_A = _task("gen_novel_a", "gen_novel_a.md.j2", NovelDraft)
NOVEL_B = _task("gen_novel_b", "gen_novel_b.md.j2", NovelDraft)
EXTENSIONS = _task("propose_extensions", "propose_extensions.md.j2", ExtensionProposals)


@dataclass
class Options:
    count: int = 2
    """수치형: 만들 문항 수."""
    max_den: int = 20
    allow_sqrt: bool = False
    max_overlap: float = 0.5
    """신규 A: 기준 문항과 겹쳐도 되는 기법 비율."""


@dataclass
class Draft:
    """독립 풀이로 확인하기 전의 제작 초안."""

    item: ItemContent
    note: str
    expected_label: int | None = None
    expected_expr: str | None = None
    expected_latex: str | None = None
    solution: SolutionDraft | None = None
    """신규 A·B: AI가 함께 쓴 해설 (독립 풀이와 답을 대조)."""
    values: dict[str, float] | None = None
    extension: ExtensionConcept | None = None
    skill_ids: list[str] = field(default_factory=lambda: list[str]())
    parts: list[ExamPart] = field(default_factory=lambda: list[ExamPart]())


def _material(src: ExamQuestion, src_sol: SolutionDraft | None) -> str:
    parts = [question_text(src.number, src.points, src.item)]
    if src_sol is not None:
        parts.append(solution_text(src_sol))
    parts.append("[기준 문항 JSON]\n" + src.item.model_dump_json(exclude_defaults=True))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- 동형 · 수치형


def _unrenderable(item: ItemContent, values: dict[str, float]) -> str | None:
    """이 값으로 파라미터 그림을 그릴 수 없으면 이유 (예: 경사각 85°)."""
    import tempfile
    from pathlib import Path

    from dawoni.figures.common import FigureError
    from dawoni.figures.render import render_spec

    for f in item.figures:
        if f.mode == "parametric" and f.spec is not None:
            with tempfile.TemporaryDirectory() as d:
                try:
                    render_spec(f.spec, values, Path(d) / "check.png")
                except FigureError as e:
                    return f"{f.id}: {e}"
    return None


def numeric_drafts(
    engine: ContextEngine,
    src: ExamQuestion,
    src_sol: SolutionDraft | None,
    scope: ScopeProfile,
    opts: Options,
) -> tuple[list[Draft], list[str]]:
    found: dict[str, SearchResult] = {}

    def check(t: NumericTemplate) -> list[str]:
        problems = gc.check_numeric(t, src.item, src_sol)
        if problems:
            return problems
        res = search(t, count=opts.count, max_den=opts.max_den, allow_sqrt=opts.allow_sqrt)
        if not res.found:
            return [
                f"답이 정수·기약분수(분모 {opts.max_den} 이하)로 나오는 값 조합이 없다: "
                f"{res.why_none()}. 범위를 넓히거나 constraints·answer_expr를 점검할 것"
            ]
        found["res"] = res
        return []

    t = engine.run(
        dataclasses.replace(NUMERIC, validator=check),
        TaskInput(
            variables={"level": scope.level},
            scope=scope_layer(scope),
            material=_material(src, src_sol),
        ),
    ).value
    res = found["res"]
    warnings: list[str] = []
    if len(res.found) < opts.count:
        warnings.append(f"깔끔한 답이 나오는 조합이 {len(res.found)}개뿐 (요청 {opts.count}개)")
    drafts: list[Draft] = []
    for c in res.found:
        bad = _unrenderable(t.item, c.values)
        if bad:
            warnings.append(f"값 {c.values}: 그림을 그릴 수 없어 제외 ({bad})")
            continue
        item = fill_item(with_values(t.item, c.values))
        if c.choice_label is not None:
            item = item.model_copy(
                update={"answer": ChoiceAnswer(kind="choice", labels=[c.choice_label])}
            )
        else:
            item = item.model_copy(
                update={
                    "answer": ValueAnswer(
                        kind="value", latex=to_latex(c.answer), expr=str(c.answer)
                    )
                }
            )
        changed = ", ".join(f"{k}={v:g}" for k, v in c.values.items())
        drafts.append(
            Draft(
                item=ItemContent.model_validate(item.model_dump()),
                note=f"{t.note} (값: {changed})",
                expected_label=c.choice_label,
                expected_expr=None if c.choice_label is not None else str(c.answer),
                expected_latex=to_latex(c.answer),
                values=c.values,
            )
        )
    return drafts, warnings


# ---------------------------------------------------------------- 동형 · 미지수형 / 조건 변형


def symbolic_draft(
    engine: ContextEngine, src: ExamQuestion, src_sol: SolutionDraft | None, scope: ScopeProfile
) -> Draft:
    t = engine.run(
        dataclasses.replace(SYMBOLIC, validator=gc.check_symbolic),
        TaskInput(
            variables={"level": scope.level},
            scope=scope_layer(scope),
            material=_material(src, src_sol),
        ),
    ).value
    # 문자 답식은 expr에 두지 않는다 (ItemContent는 expr의 문자를 '정의된 변수'로 요구)
    latex = sym_latex(t.answer_expr)
    item = t.item.model_copy(update={"answer": ValueAnswer(kind="value", latex=latex)})
    cond = f" (조건: {', '.join(t.assumptions)})" if t.assumptions else ""
    return Draft(
        item=ItemContent.model_validate(item.model_dump()),
        note=t.note + cond,
        expected_expr=t.answer_expr,
    )


def condition_draft(
    engine: ContextEngine, src: ExamQuestion, src_sol: SolutionDraft | None, scope: ScopeProfile
) -> Draft:
    def check(d: RewriteDraft) -> list[str]:
        return gc.check_rewrite(d, src.item, src_sol)

    d = engine.run(
        dataclasses.replace(CONDITION, validator=check),
        TaskInput(
            variables={"level": scope.level},
            scope=scope_layer(scope),
            material=_material(src, src_sol),
        ),
    ).value
    label = gc.choice_label_of(d.expected_answer) if d.item.item_type == "multiple_choice" else None
    item = d.item
    if label is not None:
        item = item.model_copy(update={"answer": ChoiceAnswer(kind="choice", labels=[label])})
    return Draft(
        item=ItemContent.model_validate(item.model_dump()),
        note=d.change_note,
        expected_label=label,
        expected_expr=d.expected_expr,
        expected_latex=None if label is not None else d.expected_answer,
    )


# ---------------------------------------------------------------- 신규 A · B


def novel_a_draft(
    engine: ContextEngine,
    src: ExamQuestion,
    src_sol: SolutionDraft | None,
    scope: ScopeProfile,
    others: list[tuple[str, ItemContent, set[str]]],
    opts: Options,
) -> Draft:
    src_skills = sorted(gc.skill_ids(src_sol, include_alternatives=True)) if src_sol else []

    def check(d: NovelDraft) -> list[str]:
        return gc.check_novel_a(d, scope, src.item, src_sol, others, max_overlap=opts.max_overlap)

    db = "\n".join(
        f"- {name}: {gc.item_text(it)[:160]} (기법 {sorted(sk)})" for name, it, sk in others[:20]
    )
    d = engine.run(
        dataclasses.replace(NOVEL_A, validator=check),
        TaskInput(
            variables={
                "level": scope.level,
                "points": src.points,
                "item_type": src.item.item_type,
                "source_skills": ", ".join(src_skills) or "(없음)",
            },
            scope=scope_layer(scope),
            material=_material(src, src_sol) + ("\n\n[같은 범위의 DB 문항]\n" + db if db else ""),
        ),
    ).value
    return Draft(
        item=d.item,
        note=d.differences,
        solution=d.solution,
        skill_ids=sorted(gc.skill_ids(d.solution)),
    )


def propose_extensions(
    engine: ContextEngine, src: ExamQuestion, src_sol: SolutionDraft | None, scope: ScopeProfile
) -> ExtensionProposals:
    taken = {x.id for x in scope.extensions} | scope.allowed_ids()

    def check(p: ExtensionProposals) -> list[str]:
        problems: list[str] = []
        ids = [c.id for c in p.candidates]
        if len(ids) != len(set(ids)) or set(ids) & taken:
            problems.append(f"후보 ID가 겹친다: {ids} (X1, X2 …로 새로 매길 것)")
        if p.rationale and len(p.rationale) != len(p.candidates):
            problems.append("rationale은 후보마다 하나씩, 같은 순서로")
        return problems

    return engine.run(
        dataclasses.replace(EXTENSIONS, validator=check),
        TaskInput(
            variables={"level": scope.level},
            scope=scope_layer(scope),
            material=_material(src, src_sol),
        ),
    ).value


def with_extension(scope: ScopeProfile, ext: ExtensionConcept) -> ScopeProfile:
    others = [x for x in scope.extensions if x.id != ext.id]
    return scope.model_copy(update={"extensions": [*others, ext]})


def novel_b_draft(
    engine: ContextEngine,
    src: ExamQuestion,
    src_sol: SolutionDraft | None,
    scope: ScopeProfile,
    ext: ExtensionConcept,
) -> Draft:
    ext_scope = with_extension(scope, ext)

    def check(d: NovelDraft) -> list[str]:
        return gc.check_novel_b(d, ext_scope, ext)

    d = engine.run(
        dataclasses.replace(NOVEL_B, validator=check),
        TaskInput(
            variables={
                "level": scope.level,
                "points": src.points,
                "ext_id": ext.id,
                "ext_name": ext.name,
                "ext_definition": ext.definition,
                "ext_assumptions": "; ".join(ext.assumptions),
                "ext_inference": ext.expected_inference or "",
            },
            scope=scope_layer(ext_scope, with_extensions=True),
            material=_material(src, src_sol),
        ),
    ).value
    return Draft(
        item=d.item,
        note=d.differences,
        solution=d.solution,
        extension=ext,
        skill_ids=sorted(gc.skill_ids(d.solution)),
    )


# ---------------------------------------------------------------- 마무리: 독립 풀이 → 비교 → 루브릭


def sym_latex(expr: str) -> str:
    try:
        return to_latex(parse(expr))
    except ExprError:
        return expr


def _agree(draft: Draft, final: FinalAnswer) -> tuple[bool | None, str]:
    if draft.expected_label is not None:
        got = gc.choice_label_of(final.latex)
        return got == draft.expected_label, f"{draft.expected_label}번 / 재풀이 {got}번"
    if draft.expected_expr and final.expr:
        same = equivalent(draft.expected_expr, final.expr)
        return same, f"{draft.expected_expr} / 재풀이 {final.expr}"
    if draft.expected_expr:
        # 재풀이가 답을 문장으로만 쓴 경우 ("x=3일 때 최솟값 10"): 답을 뜻하는 말 뒤의 값으로 비교
        got = gc.answer_values(final.latex)
        if got:
            same = any(equivalent(draft.expected_expr, g) for g in got)
            return (
                same,
                f"\\({draft.expected_latex or draft.expected_expr}\\) / 재풀이 \\({final.latex}\\)",
            )
    if draft.expected_latex:
        same = gchecks.normalize_latex(draft.expected_latex) == gchecks.normalize_latex(final.latex)
        return (
            True if same else None
        ), f"\\({draft.expected_latex}\\) / 재풀이 \\({final.latex}\\)"
    return None, "기대한 답 없음"


def finish(
    engine: ContextEngine,
    draft: Draft,
    kind: GenKind,
    src: ExamQuestion,
    scope: ScopeProfile,
    source: SourceRef,
) -> GeneratedSet:
    ext = draft.extension
    use_scope = with_extension(scope, ext) if ext else scope
    q = ExamQuestion(number=f"{src.number}′", points=src.points, item=draft.item, parts=draft.parts)
    indep = solve_question(engine, q, use_scope, with_extensions=ext is not None)
    notes: list[str] = []
    warnings: list[str] = []
    if indep.out_of_scope_note:
        warnings.append(f"범위 안에서 풀 수 없다는 판단: {indep.out_of_scope_note}")
    if draft.solution is not None and draft.expected_label is not None:
        same, detail = _agree(draft, indep.final_answer)  # 선지 번호로 비교 (③ / 3 / 3번)
        base = draft.solution
    elif draft.solution is not None:
        same = gchecks.answers_agree(draft.solution.final_answer, indep.final_answer)
        detail = (
            f"\\({draft.solution.final_answer.latex}\\) / 재풀이 \\({indep.final_answer.latex}\\)"
        )
        base = draft.solution
    else:
        same, detail = _agree(draft, indep.final_answer)
        base = indep
    if indep.out_of_scope_note and same:
        # 범위 밖이라는 판단과 함께 낸 답은 확인으로 치지 않는다 (범위 프로필이 문항과 다를 수 있음)
        same = None
        detail += ", 단 독립 풀이가 범위 밖이라고 판단"
    if same:
        notes.append(f"독립 풀이와 답 일치 ({detail})")
    elif same is None:
        warnings.append(f"독립 풀이 답을 자동 비교할 수 없음 ({detail}) — 강사 확인 필요")
    else:
        warnings.append(f"독립 풀이 답이 다름 ({detail}) — 문항 또는 정답에 오류 가능")
    solution = ScopedSolution(
        **base.model_dump(include=set(SolutionDraft.model_fields)),
        question_number=q.number,
        scope_version=scope.version,
        verified=same is True,
        check_notes=list(warnings),  # 확인 성공은 verified로 (화면은 check_notes를 경고로 표시)
    )
    rubric: RubricDraft | None = None
    if same is True:
        try:
            r = derive_rubric(engine, q, solution, use_scope, with_extensions=ext is not None)
            rubric = RubricDraft.model_validate(r.model_dump(include=set(RubricDraft.model_fields)))
        except (PipelineError, TaskFailed) as e:
            warnings.append(f"루브릭을 만들지 못함: {e}")
    else:
        warnings.append("답이 확인되지 않아 루브릭을 만들지 않았습니다")
    return GeneratedSet(
        kind=kind,
        source=source,
        scope_id=scope.id,
        points=src.points,
        item=draft.item,
        solution=solution,
        rubric=rubric,
        checks=notes,
        warnings=warnings,
        note=draft.note,
        values=draft.values,
        extension=ext,
        skill_ids=draft.skill_ids or sorted(gc.skill_ids(solution)),
        parts=draft.parts,
    )
