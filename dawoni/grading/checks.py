"""채점 파이프라인의 코드 검증. AI에게 부탁만 하지 않고, 결과를 여기서 확인한다.

각 함수는 문제 목록(한국어)을 돌려준다. 빈 목록 = 통과. 문제 문장은 그대로 수리 요청에 쓰인다.
"""

from __future__ import annotations

import re
import unicodedata

from dawoni.mathx.sym import equivalent
from dawoni.models.grading import (
    FinalAnswer,
    GradeOutput,
    RubricDraft,
    SolutionDraft,
    SolutionStep,
    Transcript,
)
from dawoni.models.scope import ScopeProfile

# 금지 기법 이름 → 식에서 그 기법을 쓴 흔적 (이름만으로는 못 잡는 경우)
_TECHNIQUE_TRACES: dict[str, tuple[str, ...]] = {
    "미분": (
        r"\\frac\{d",  # \frac{dy}{dx}
        r"\\frac\{\\partial",
        r"[A-Za-z]'+\s*(\(|=)",  # f'(x), y'=
        r"\\prime",
        r"\\dot\{",
        r"\bd[a-z]\s*/\s*d[a-z]\b",  # dy/dx
    ),
    "적분": (r"\\int",),
    "극한": (r"\\lim",),
    "로피탈": (r"\\lim",),
    "벡터": (r"\\vec\{", r"\\overrightarrow"),
    "행렬": (r"\\begin\{[pb]?matrix\}",),
}


def _forbidden_hits(text: str, scope: ScopeProfile) -> list[str]:
    hits: list[str] = []
    for f in scope.forbidden:
        if f.name and f.name in text:
            hits.append(f.name)
            continue
        for key, patterns in _TECHNIQUE_TRACES.items():
            if key in f.name and any(re.search(p, text) for p in patterns):
                hits.append(f.name)
                break
    return hits


# ---------------------------------------------------------------- 해설


def _check_steps(
    steps: list[SolutionStep], allowed: set[str], where: str, scope: ScopeProfile
) -> list[str]:
    problems: list[str] = []
    for n, step in enumerate(steps, start=1):
        bad = sorted(set(step.uses) - allowed)
        if bad:
            problems.append(
                f"{where} {n}단계가 범위 프로필에 없는 ID {bad}를 썼다. "
                f"허용 ID만 쓰고, 범위 안 개념으로 다시 풀 것: {sorted(allowed)}"
            )
        hits = _forbidden_hits(f"{step.explanation} {step.math or ''}", scope)
        if hits:
            problems.append(
                f"{where} {n}단계가 이 수준에서 쓰지 않는 기법({', '.join(hits)})을 썼다. "
                "범위 안의 방법으로 다시 풀 것"
            )
    return problems


def check_solution_scope(
    draft: SolutionDraft, scope: ScopeProfile, *, with_extensions: bool = False
) -> list[str]:
    allowed = scope.allowed_ids(with_extensions=with_extensions)
    problems = _check_steps(draft.steps, allowed, "해설", scope)
    for i, alt in enumerate(draft.alternatives, start=1):
        problems += _check_steps(alt.steps, allowed, f"다른 풀이 {i}의", scope)
    return problems


def normalize_latex(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    for cmd in (r"\left", r"\right", r"\displaystyle", r"\,", r"\;", r"\:", r"\!", r"\ "):
        s = s.replace(cmd, "")
    s = s.replace("−", "-").replace("×", r"\times").replace("·", r"\cdot")
    s = re.sub(r"\\dfrac|\\tfrac", r"\\frac", s)
    return re.sub(r"\s+", "", s).lower()


def answers_agree(a: FinalAnswer, b: FinalAnswer) -> bool | None:
    """두 최종 답이 같은가. 판단할 수 없으면 None.

    식이 둘 다 있으면 sympy 동치로, 아니면 LaTeX 정규화 후 문자열 비교.
    """
    if a.expr and b.expr:
        same = equivalent(a.expr, b.expr)
        if same is not None:
            return same
    if normalize_latex(a.latex) == normalize_latex(b.latex):
        return True
    return None


# ---------------------------------------------------------------- 루브릭


def check_rubric(draft: RubricDraft, question_points: float, solution: SolutionDraft) -> list[str]:
    problems: list[str] = []
    if abs(draft.total - question_points) > 1e-9:
        problems.append(
            f"루브릭 항목 배점의 합({draft.total:g})이 문항 배점({question_points:g})과 다르다. "
            "합이 정확히 같도록 배점을 나눌 것"
        )
    n_steps = len(solution.steps)
    for item in draft.items:
        bad = [s for s in item.solution_steps if not 1 <= s <= n_steps]
        if bad:
            problems.append(
                f"{item.id}의 solution_steps {bad}는 해설 단계 범위(1~{n_steps}) 밖이다"
            )
    return problems


def merge_never_deduct(draft: RubricDraft, defaults: tuple[str, ...]) -> list[str]:
    seen = set(draft.never_deduct)
    return list(draft.never_deduct) + [d for d in defaults if d not in seen]


# ---------------------------------------------------------------- 근거 인용


def normalize_for_compare(s: str) -> str:
    """판독·인용 비교용 정규화: 공백·LaTeX 간격 명령·중괄호·수식 구분자 제거."""
    s = s.replace("\\(", "").replace("\\)", "").replace("$", "")
    return normalize_latex(s).replace("{", "").replace("}", "")


def quote_in_transcript(quote: str, transcript: Transcript) -> bool:
    """인용이 판독문에 실제로 있는가. `…`로 이은 조각은 순서대로 모두 있어야 한다."""
    haystack = normalize_for_compare(" ".join(line.joined for line in transcript.lines))
    parts = [p for p in re.split(r"…|\.\.\.", quote) if normalize_for_compare(p)]
    if not parts:
        return False
    pos = 0
    for part in parts:
        found = haystack.find(normalize_for_compare(part), pos)
        if found < 0:
            return False
        pos = found + len(normalize_for_compare(part))
    return True


# ---------------------------------------------------------------- 채점


def check_grade(
    out: GradeOutput, rubric: RubricDraft, transcript: Transcript, scope: ScopeProfile
) -> list[str]:
    problems: list[str] = []
    items = {i.id: i for i in rubric.items}
    seen: list[str] = [j.rubric_item_id for j in out.judgements]
    missing = [i for i in items if i not in seen]
    unknown = [i for i in seen if i not in items]
    dups = sorted({i for i in seen if seen.count(i) > 1})
    if missing:
        problems.append(f"루브릭 항목 {missing}에 대한 판정이 없다. 모든 항목을 한 번씩 판정할 것")
    if unknown:
        problems.append(f"루브릭에 없는 항목 {unknown}을 판정했다")
    if dups:
        problems.append(f"항목 {dups}을 두 번 이상 판정했다")

    blank = transcript.is_blank
    for j in out.judgements:
        item = items.get(j.rubric_item_id)
        if item is None:
            continue
        if j.awarded > item.points + 1e-9:
            problems.append(f"{item.id}: 점수 {j.awarded:g}가 배점 {item.points:g}을 넘는다")
        if j.awarded > 0:
            if blank:
                problems.append(f"{item.id}: 답안이 비어 있는데 점수를 주었다")
            elif not j.evidence_quote:
                problems.append(
                    f"{item.id}: 점수를 주었으면 근거가 된 답안 부분을 그대로 인용할 것"
                )
            elif not quote_in_transcript(j.evidence_quote, transcript):
                problems.append(
                    f"{item.id}: 인용 '{j.evidence_quote}'가 학생 답안에 없다. "
                    "답안에 실제로 적힌 글·식만 그대로 인용할 것 (없으면 점수를 줄 수 없음)"
                )
        if j.awarded < item.points - 1e-9:
            if not j.deduction_reason:
                problems.append(f"{item.id}: 만점이 아니면 감점 이유(deduction_reason)를 적을 것")
            else:
                hits = _forbidden_hits(j.deduction_reason, scope)
                if hits:
                    problems.append(
                        f"{item.id}: 감점 이유가 이 수준에서 요구하지 않는 기법"
                        f"({', '.join(hits)})을 근거로 한다. "
                        "범위 밖 기법을 쓰지 않았다는 이유로 감점하지 말 것"
                    )
    return problems


def total_points(out: GradeOutput) -> float:
    return sum(j.awarded for j in out.judgements)


def disagreements(first: GradeOutput, second: GradeOutput, *, tolerance: float = 0.0) -> list[str]:
    """이중 채점에서 항목별 점수 차이가 tolerance를 넘는 항목 ID."""
    b = {j.rubric_item_id: j.awarded for j in second.judgements}
    return [
        j.rubric_item_id
        for j in first.judgements
        if abs(j.awarded - b.get(j.rubric_item_id, -1.0)) > tolerance + 1e-9
    ]
