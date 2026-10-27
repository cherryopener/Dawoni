"""문항 제작 AI 출력의 코드 검사 — 통과하지 못하면 문제 목록을 붙여 수리 요청한다."""

from __future__ import annotations

import re
import unicodedata

from dawoni.grading import checks as gchecks
from dawoni.grading.render import blocks_text
from dawoni.mathx.sym import ExprError, equivalent, exact, free_symbol_names
from dawoni.models.content import BoxBlock, MathRun, iter_runs
from dawoni.models.generation import NovelDraft, NumericTemplate, RewriteDraft, SymbolicTemplate
from dawoni.models.grading import SolutionDraft, SolutionStep
from dawoni.models.item import ChoiceAnswer, ItemContent
from dawoni.models.scope import ExtensionConcept, ScopeProfile

_CIRCLED = "①②③④⑤⑥⑦⑧⑨"
_GREEK = {
    "alpha", "beta", "gamma", "delta", "theta", "lambda", "mu", "omega", "phi", "sigma", "rho",
    "tau", "epsilon", "pi",
}  # fmt: skip


# ---------------------------------------------------------------- 공통 도우미


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\\[()[\]]|\$", "", s)
    return re.sub(r"\s+", "", s)


def shingles(s: str, n: int = 3) -> set[str]:
    t = normalize_text(s)
    return {t[i : i + n] for i in range(max(len(t) - n + 1, 1))}


def similarity(a: str, b: str) -> float:
    sa, sb = shingles(a), shingles(b)
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def item_text(item: ItemContent) -> str:
    return (
        blocks_text(item.stem)
        + " "
        + " ".join("".join(r.v for r in c.runs) or (c.note or "") for c in item.choices)
    )


_INEQ = (
    ("\\geq", ">="), ("\\ge", ">="), ("≥", ">="), ("\\leq", "<="), ("\\le", "<="),
    ("≤", "<="), ("\\neq", "!="), ("≠", "!="), ("\\gt", ">"), ("\\lt", "<"),
)  # fmt: skip


def normalize_ineq(s: str) -> str:
    """부등식 표기 통일 (`a \\ge 0`, `a ≥ 0`, `a>=0` → `a>=0`)."""
    t = normalize_text(s)
    for a, b in _INEQ:
        t = t.replace(a, b)
    return t


def stem_math(item: ItemContent) -> str:
    return " ".join(r.v for r in iter_runs(item.stem) if isinstance(r, MathRun))


def symbol_in_latex(name: str, latex: str) -> bool:
    if name in _GREEK:
        return f"\\{name}" in latex
    return re.search(rf"(?<![A-Za-z\\]){re.escape(name)}(?![A-Za-z])", latex) is not None


def choice_label_of(s: str) -> int | None:
    """'③', '3', '\\text{③}', '정답: 3번' → 3."""
    for i, ch in enumerate(_CIRCLED, start=1):
        if ch in s:
            return i
    m = re.search(r"(?<![\d.])([1-9])(?![\d.])", s)
    return int(m.group(1)) if m else None


def skill_ids(sol: SolutionDraft, *, include_alternatives: bool = False) -> set[str]:
    steps: list[SolutionStep] = list(sol.steps)
    if include_alternatives:
        steps += [s for a in sol.alternatives for s in a.steps]
    return {u for s in steps for u in s.uses if u.startswith("S")}


def source_answer_label(item: ItemContent, sol: SolutionDraft | None) -> int | None:
    if isinstance(item.answer, ChoiceAnswer) and len(item.answer.labels) == 1:
        return item.answer.labels[0]
    if sol is not None and item.item_type == "multiple_choice":
        return choice_label_of(sol.final_answer.latex)
    return None


# ---------------------------------------------------------------- 수치형


def check_numeric(
    t: NumericTemplate, source: ItemContent, source_sol: SolutionDraft | None
) -> list[str]:
    from dawoni.generate.search import choice_exprs, domain

    problems: list[str] = []
    item = t.item
    if not item.variables:
        problems.append("variables가 비었다. 바꿀 수치를 변수로 만들고 본문에 \\var{변수}로 쓸 것")
        return problems
    if all(len(domain(v)) < 2 for v in item.variables.values()):
        problems.append("모든 변수의 범위·후보가 값 하나뿐이다. min·max·step 또는 choices를 줄 것")
    try:
        names = free_symbol_names(t.answer_expr)
    except ExprError as e:
        return [*problems, f"answer_expr를 읽을 수 없다: {e}"]
    unknown = sorted(names - set(item.variables))
    if unknown:
        problems.append(f"answer_expr가 정의되지 않은 변수 {unknown}를 쓴다")
    original = {k: v.value for k, v in item.variables.items()}
    if not unknown and source_sol is not None and source_sol.final_answer.expr:
        want = source_sol.final_answer.expr
        try:
            got = str(exact(t.answer_expr, dict(original)))
            same = equivalent(got, want)
        except ExprError:
            got, same = "?", None
        if same is False:
            problems.append(
                f"원래 값으로 계산한 answer_expr({got})가 원래 정답({want})과 다르다. "
                "변수의 value는 원래 문항의 수치여야 하고 정답식이 맞아야 한다"
            )
    if item.item_type == "multiple_choice":
        cx = choice_exprs(t)
        if len(cx) < len(item.choices):
            problems.append(
                "선택형 수치형은 모든 선지를 \\var{식} 하나로 써야 한다 "
                "(정답 선지는 정답식, 나머지는 흔한 오답의 식)"
            )
    if source.has_figure_choices:
        problems.append("그림 선지 문항은 수치형으로 만들 수 없다 (조건 변형을 쓸 것)")
    return problems


# ---------------------------------------------------------------- 미지수형


def check_symbolic(t: SymbolicTemplate) -> list[str]:
    problems: list[str] = []
    if t.item.variables:
        problems.append("미지수형은 variables·\\var를 쓰지 않는다. 문자를 본문 수식에 직접 쓸 것")
    try:
        names = free_symbol_names(t.answer_expr)
    except ExprError as e:
        return [*problems, f"answer_expr를 읽을 수 없다: {e}"]
    if not names:
        problems.append("answer_expr에 문자가 없다. 답도 문자식이어야 한다")
    latex = stem_math(t.item)
    missing = sorted(n for n in names if not symbol_in_latex(n, latex))
    if missing:
        problems.append(f"정답식의 문자 {missing}가 본문 수식에 없다")
    text = normalize_ineq(blocks_text(t.item.stem))
    for a in t.assumptions:
        if normalize_ineq(a) not in text:
            problems.append(f"조건 '{a}'를 본문에 적을 것 (학생이 알 수 있게)")
    return problems


# ---------------------------------------------------------------- 조건 변형


def check_rewrite(
    d: RewriteDraft, source: ItemContent, source_sol: SolutionDraft | None
) -> list[str]:
    problems: list[str] = []
    if source.has_figure_choices:
        same = [(c.label, c.figure_id, c.note) for c in d.item.choices] == [
            (c.label, c.figure_id, c.note) for c in source.choices
        ]
        if not same or d.item.choices_figure_id != source.choices_figure_id:
            problems.append("그림 선지는 그대로 두고(번호·그림·note 같게) 문제 조건만 바꿀 것")
        src_figs = {f.id: f.orig for f in source.figures}
        for f in d.item.figures:
            if f.id in src_figs and f.orig != src_figs[f.id]:
                problems.append(f"그림 {f.id}의 원본(orig)을 바꾸지 말 것")
    if normalize_text(blocks_text(d.item.stem)) == normalize_text(blocks_text(source.stem)):
        problems.append("본문이 원래 문항과 같다. 조건을 바꿀 것")
    if d.item.item_type == "multiple_choice":
        label = choice_label_of(d.expected_answer)
        if label is None or label not in {c.label for c in d.item.choices}:
            problems.append(f"expected_answer '{d.expected_answer}'가 선지 번호가 아니다")
        src_label = source_answer_label(source, source_sol)
        if label is not None and src_label is not None and label == src_label:
            problems.append(
                f"정답이 원래와 같은 {label}번이다. 다른 선지가 정답이 되게 조건을 바꿀 것"
            )
    return problems


# ---------------------------------------------------------------- 신규 A·B


def check_novel_a(
    d: NovelDraft,
    scope: ScopeProfile,
    source: ItemContent,
    source_sol: SolutionDraft | None,
    others: list[tuple[str, ItemContent, set[str]]],
    *,
    max_overlap: float = 0.5,
    max_similarity: float = 0.55,
) -> list[str]:
    """others: (이름, 문항, 기법 ID 집합) — 같은 범위의 다른 문항(DB)."""
    problems = gchecks.check_solution_scope(d.solution, scope) + gchecks.check_solution_math(
        d.solution
    )
    new = skill_ids(d.solution)
    if not new:
        problems.append("해설 단계의 uses에 기법(S) ID가 없다. 쓰는 풀이 기법을 표시할 것")
    if source_sol is not None:
        src = skill_ids(source_sol, include_alternatives=True)
        if new and src:
            overlap = len(new & src) / len(new)
            if overlap > max_overlap or new <= src:
                problems.append(
                    f"기준 문항과 풀이 기법이 겹친다 (새 {sorted(new)}, 기준 {sorted(src)}). "
                    "같은 개념 범위에서 다른 기법으로 풀리는 문항을 만들 것"
                )
    text = item_text(d.item)
    if similarity(text, item_text(source)) > max_similarity:
        problems.append("본문이 기준 문항과 너무 비슷하다. 상황·구조를 새로 만들 것")
    for name, other, other_skills in others:
        if similarity(text, item_text(other)) > max_similarity:
            problems.append(f"본문이 DB의 {name}와 너무 비슷하다")
        elif other_skills and new and new == other_skills:
            problems.append(f"DB의 {name}와 풀이 기법 집합이 같다 ({sorted(new)})")
    return problems


def passage_text(item: ItemContent) -> str:
    boxes = [b for b in item.stem if isinstance(b, BoxBlock)]
    if boxes:
        return " ".join("".join(r.v for r in p) for b in boxes for p in b.paragraphs)
    return blocks_text(item.stem)


def check_novel_b(d: NovelDraft, scope: ScopeProfile, ext: ExtensionConcept) -> list[str]:
    problems = gchecks.check_solution_scope(
        d.solution, scope, with_extensions=True
    ) + gchecks.check_solution_math(d.solution)
    if not any(isinstance(b, BoxBlock) for b in d.item.stem):
        problems.append("제시문을 box 블록(제목 '제시문')으로 본문 앞에 둘 것")
    used = {u for s in d.solution.steps for u in s.uses if u.startswith("X")}
    if ext.id not in used:
        problems.append(f"해설이 확장 개념 {ext.id}를 쓰지 않는다 (uses에 표시할 것)")
    passage = normalize_text(passage_text(d.item))
    for xid in sorted(used):
        quote = d.definition_quotes.get(xid)
        if not quote:
            problems.append(f"definition_quotes에 {xid}의 정의 문장이 없다")
        elif normalize_text(quote) not in passage:
            problems.append(f"{xid}의 정의 인용 '{quote[:40]}'이 제시문에 그대로 없다")
    return problems


_ANSWER_WORDS = r"(?:최솟값|최댓값|최소값|최대값|값|답|넓이|길이|개수)"
_NUM = r"-?\s*(?:\\[dt]?frac\{-?\d+\}\{\d+\}|\d+(?:\.\d+)?(?:/\d+)?)"


def answer_values(latex: str) -> list[str]:
    """문장으로 쓴 답에서 '최솟값 10', '최댓값은 \\frac{3}{2}' 같은 값을 sympy 식으로 꺼낸다."""
    plain = re.sub(r"\\text\{([^{}]*)\}", r" \1 ", latex)
    out: list[str] = []
    for m in re.finditer(rf"{_ANSWER_WORDS}\s*(?:은|는|이|가|=)?\s*({_NUM})", plain):
        v = m.group(1).replace(" ", "")
        f = re.fullmatch(r"(-?)\\[dt]?frac\{(-?\d+)\}\{(\d+)\}", v)
        out.append(f"{f.group(1)}({f.group(2)})/({f.group(3)})" if f else v)
    return out
