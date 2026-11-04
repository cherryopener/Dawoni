"""채점 파이프라인의 코드 검증. AI에게 부탁만 하지 않고, 결과를 여기서 확인한다.

각 함수는 문제 목록(한국어)을 돌려준다. 빈 목록 = 통과. 문제 문장은 그대로 수리 요청에 쓰인다.
"""

from __future__ import annotations

import re
import unicodedata

from dawoai.mathx.sym import equivalent
from dawoai.models.answer_key import AnswerKey
from dawoai.models.grading import (
    ExamQuestion,
    FinalAnswer,
    GradeOutput,
    RubricDraft,
    SolutionDraft,
    SolutionStep,
    Transcript,
)
from dawoai.models.scope import ScopeProfile

# 금지 기법 → (그 기법을 가리키는 낱말, 식에서 그 기법을 쓴 흔적). 이름만으로는 못 잡는 경우를 위해.
# 금지 목록의 이름에 들어 있는 가장 긴 열쇠를 쓴다 ("벡터 외적"은 "벡터"가 아니라 "벡터 외적" —
# 물리에서 \vec{F}는 범위 안이다). 열쇠가 없는 이름(강사가 넣은 것)은 이름 그대로만 찾는다.
_TRACES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "미분": (
        ("미분", "도함수", "derivative"),
        (
            r"\\frac\{\s*d",  # \frac{dy}{dx}
            r"\\frac\{\\partial",
            r"\b[fghy]'+\s*[(=]",  # f'(x), y'= (물리의 v'는 충돌 뒤 속도라 뺌)
            r"\\prime",
            r"\\d?dot\{",
            r"\bd[a-zA-Z]\s*/\s*d[a-zA-Z]\b",  # dy/dx
        ),
    ),
    "편미분": (("편미분", "partial derivative"), (r"\\partial", r"\\nabla")),
    "적분": (("적분", "integral"), (r"\\i+nt(?![A-Za-z])", r"\\oint", "∫")),
    "극한": (("극한", "limit"), (r"\\lim(?![A-Za-z])", r"\blim\s*_")),
    "로피탈": (("로피탈", "hôpital", "hospital"), ()),
    "테일러": (("테일러", "매클로린", "taylor"), ()),
    "삼각함수": (("삼각함수",), (r"\\(?:sin|cos|tan)(?![A-Za-z])",)),
    "삼각비": (("삼각비",), (r"\\(?:sin|cos|tan)(?![A-Za-z])",)),
    "로그": (("상용로그", "자연로그"), (r"\\(?:log|ln)(?![A-Za-z])", r"(?<![가-힣])로그")),
    "벡터": (("벡터",), (r"\\vec\{", r"\\overrightarrow")),
    "벡터 외적": (
        ("외적", "cross product"), (r"\\vec\{[^{}]*\}\s*\\times\s*\\vec", r"\\times\s*\\vec\{")
    ),
    "행렬": (("행렬", "matrix"), (r"\\begin\{[pbvBV]?matrix\}",)),
    "판별식": (("판별식",), (r"b\^\{?2\}?\s*-\s*4\s*ac",)),
    "라그랑지안": (("라그랑지안", "라그랑주", "lagrangian"), (r"\\mathcal\{L\}",)),
}  # fmt: skip


def _traces(name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    keys = [k for k in _TRACES if k in name]
    if not keys:
        return (name,), ()
    return _TRACES[max(keys, key=len)]  # 이름 그대로는 찾지 않음 ("로그" ⊂ "프로그램")


def _forbidden_hits(text: str, scope: ScopeProfile) -> list[str]:
    hits: list[str] = []
    low = text.lower()
    for f in scope.forbidden:
        if not f.name:
            continue
        words, patterns = _traces(f.name)
        if any(w.lower() in low for w in words) or any(re.search(p, text) for p in patterns):
            hits.append(f.name)
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


def check_solution_math(d: SolutionDraft) -> list[str]:
    """해설의 모든 식이 깨지지 않았는가 (출력 시점 실패 방지)."""
    from dawoai.mathx.latex import latex_problems
    from dawoai.models.content import MathRun, runs_from_text

    latex: list[str] = [d.final_answer.latex]
    for steps in [d.steps, *(a.steps for a in d.alternatives)]:
        for st in steps:
            if st.math:
                latex.append(st.math)
            latex += [r.v for r in runs_from_text(st.explanation) if isinstance(r, MathRun)]
    return latex_problems(latex)


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
    # 여러 값으로 된 답: "q=\\frac{A}{2B}일 때 최댓값 \\frac{A^2}{4B}" ↔ "q=\\frac{A}{2B}, R=…"
    va, vb = latex_values(a.latex), latex_values(b.latex)
    if va and vb and same_values(va, vb):
        return True
    return None


_SPACES = re.compile(r"\\(?:left|right|displaystyle|quad|qquad|,|;|:|!|\s)")


def latex_to_expr(s: str) -> str:
    """간단한 LaTeX 식 → sympy 식 (분수·근호·거듭제곱·곱 기호). 완전한 변환기는 아님."""
    t = _SPACES.sub(" ", s)
    t = t.replace("\\cdot", "*").replace("\\times", "*").replace("\\pi", "pi")
    for _ in range(10):  # 안쪽부터 \frac{…}{…}, \sqrt{…}
        n = re.sub(r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}", r"((\1)/(\2))", t)
        n = re.sub(r"\\sqrt\{([^{}]*)\}", r"sqrt(\1)", n)
        n = re.sub(r"\^\{([^{}]*)\}", r"**(\1)", n)
        if n == t:
            break
        t = n
    t = re.sub(r"\^(\w)", r"**\1", t)
    return t.replace("{", "(").replace("}", ")")


def latex_values(latex: str) -> list[str]:
    """답에 들어 있는 값들: 글(\\text·한글)·쉼표로 나눈 조각마다 마지막 '=' 오른쪽."""
    plain = re.sub(r"\\text\{[^{}]*\}", " , ", latex)
    plain = re.sub(r"[가-힣]+", " , ", plain)
    out: list[str] = []
    for seg in re.split(r",|\\quad|\\qquad|;", plain):
        seg = seg.strip()
        if not seg:
            continue
        rhs = seg.split("=")[-1].strip()
        if not rhs or re.search(r"\\(?:le|ge|lt|gt|neq)|[<>]", rhs):
            continue
        expr = latex_to_expr(rhs)
        try:
            from dawoai.mathx.sym import parse

            parse(expr)
        except ValueError:
            continue
        out.append(expr)
    return out


def same_values(a: list[str], b: list[str]) -> bool:
    if len(a) != len(b):
        return False
    rest = list(b)
    for x in a:
        hit = next((y for y in rest if equivalent(x, y) is True), None)
        if hit is None:
            return False
        rest.remove(hit)
    return True


# ---------------------------------------------------------------- 루브릭


def check_part_answers(d: SolutionDraft, q: ExamQuestion) -> list[str]:
    """소문항이 있으면 소문항마다 답 (해설지와 소문항별로 비교하기 위해)."""
    if not q.parts:
        return []
    want = [p.label for p in q.parts]
    got = [a.label for a in d.part_answers]
    missing = [x for x in want if x not in got]
    extra = [x for x in got if x not in want]
    out: list[str] = []
    if missing:
        out.append(f"소문항 {missing}의 답(part_answers)이 없다. 소문항마다 최종 답을 적을 것")
    if extra:
        out.append(f"part_answers의 소문항 표시 {extra}는 문항에 없다. {want} 중에서 쓸 것")
    return out


def ai_part_answer(solution: SolutionDraft, label: str) -> FinalAnswer | None:
    if not label:
        return solution.final_answer
    return next((a for a in solution.part_answers if a.label == label), None)


def key_answer_agrees(key_answer: str | None, key_expr: str | None, ai: FinalAnswer) -> bool | None:
    """해설지 답과 AI 답이 같은가 (코드로 판단할 수 없으면 None)."""
    if not key_answer and not key_expr:
        return None
    latex = (key_answer or "").replace("\\(", "").replace("\\)", "")
    return answers_agree(FinalAnswer(latex=latex or key_expr or "", expr=key_expr), ai)


def check_rubric(
    draft: RubricDraft,
    q: ExamQuestion,
    solution: SolutionDraft | None,
    key: AnswerKey | None = None,
    *,
    strict: bool = True,
) -> list[str]:
    """`strict`: AI가 만든 루브릭 — 소문항 배점·해설지 부분 점수 구조까지 지켜야 한다.
    강사가 고친 루브릭(`strict=False`)은 합계와 소문항 표시만 본다 (강사가 기준을 정함).
    `solution`이 없으면(해설지 넓히기) 해설 단계·AI 답 비교 검사를 건너뛴다."""
    problems: list[str] = []
    if abs(draft.total - q.points) > 1e-9:
        problems.append(
            f"루브릭 항목 배점의 합({draft.total:g})이 문항 배점({q.points:g})과 다르다. "
            "합이 정확히 같도록 배점을 나눌 것"
        )
    n_steps = len(solution.steps) if solution else 0
    for item in draft.items if solution else []:
        bad = [s for s in item.solution_steps if not 1 <= s <= n_steps]
        if bad:
            problems.append(
                f"{item.id}의 solution_steps {bad}는 해설 단계 범위(1~{n_steps}) 밖이다"
            )
    labels = [p.label for p in q.parts]
    for item in draft.items:
        if labels and item.part not in labels:
            problems.append(
                f"{item.id}의 part '{item.part}'는 소문항 표시 {labels} 중 하나여야 한다"
            )
        if not labels and item.part:
            problems.append(f"{item.id}: 이 문항은 소문항이 없다. part는 null로 둘 것")
    if not strict:
        return problems
    for part in q.parts:
        items = draft.part_items(part.label)
        got = sum(i.points for i in items)
        if not items:
            problems.append(f"소문항 {part.label}의 채점 항목이 없다")
        elif abs(got - part.points) > 1e-9:
            problems.append(
                f"소문항 {part.label} 항목 배점의 합({got:g})이 "
                f"소문항 배점({part.points:g})과 다르다"
            )
    if key is not None:
        problems += _check_against_key(draft, q, solution, key)
    return problems


def _check_against_key(
    draft: RubricDraft, q: ExamQuestion, solution: SolutionDraft | None, key: AnswerKey
) -> list[str]:
    problems: list[str] = []
    by_part = {c.part: c for c in draft.checks}
    partial_items = {p.item_id for p in draft.partial}
    for kp in key.parts:
        name = f"소문항 {kp.label}" if kp.label else "문항"
        items = draft.part_items(kp.label)
        part = q.part(kp.label)
        points = part.points if part else q.points
        if solution is not None and (kp.answer or kp.solution):
            check = by_part.get(kp.label)
            if check is None:
                problems.append(
                    f"{name}: checks에 해설지 답과 AI 풀이 답의 비교가 없다 (part '{kp.label}')"
                )
            else:
                ai = ai_part_answer(solution, kp.label)
                same = key_answer_agrees(kp.answer, kp.answer_expr, ai) if ai else None
                if same is True and check.verdict != "same":
                    problems.append(
                        f"{name}: 코드로 비교하면 해설지 답과 AI 답이 같다. verdict는 same"
                    )
                if same is False and check.verdict == "same":
                    problems.append(
                        f"{name}: 코드로 비교하면 해설지 답({kp.answer})과 "
                        f"AI 답({ai.latex if ai else ''})이 다르다. 어느 쪽이 맞는지 검토해 "
                        "ai_wrong / other_valid / key_doubt 중에서 고를 것"
                    )
                if check.verdict == "other_valid" and not any(
                    c.part == kp.label for c in draft.candidates
                ):
                    problems.append(
                        f"{name}: other_valid라면 그 답을 candidates에 후보로 올릴 것 (검산 포함)"
                    )
        if kp.credit_policy == "none":
            if len(items) != 1:
                problems.append(
                    f"{name}: 해설지에 '부분 점수 없음' — 항목을 하나로 "
                    f"(맞으면 {points:g}점, 아니면 0점)"
                )
            bad = sorted(i.id for i in items if i.id in partial_items)
            if bad:
                problems.append(f"{name}: '부분 점수 없음'인데 {bad}에 부분점수(partial)를 넣었다")
        elif kp.credit_policy == "listed" and abs(kp.credit_total - points) < 1e-9:
            n = len(kp.credits)
            for i in items:
                if i.key_credit is None or not 1 <= i.key_credit <= n:
                    problems.append(
                        f"{i.id}: {name}의 해설지 부분 점수 표기 1~{n}번 중 어디에 해당하는지 "
                        "key_credit에 적을 것"
                    )
            for k, c in enumerate(kp.credits, start=1):
                got = sum(i.points for i in items if i.key_credit == k)
                if abs(got - c.points) > 1e-9:
                    problems.append(
                        f"{name}: 해설지 부분 점수 {k}번 '{c.what} {c.points:g}점'에 해당하는 "
                        f"항목 배점의 합이 {got:g}점이다. {c.points:g}점이 되도록"
                    )
    for a in draft.accepted_alternatives:
        if a.part is not None and key.part(a.part) is None:
            problems.append(f"expansions {a.id}의 part '{a.part}'는 해설지 소문항에 없다")
        elif a.part is not None and not set(a.item_ids) <= {i.id for i in draft.part_items(a.part)}:
            problems.append(f"expansions {a.id}의 item_ids는 소문항 '{a.part}'의 항목이어야 한다")
    for c in draft.candidates:
        if key.part(c.part) is None:
            problems.append(f"정답 후보 {c.id}의 part '{c.part}'는 해설지 소문항에 없다")
        elif by_part.get(c.part) and by_part[c.part].verdict == "same":
            problems.append(
                f"정답 후보 {c.id}: 소문항 '{c.part}'는 해설지와 같은 답(same)이다 "
                "— 다른 답만 후보로"
            )
    return problems


# ---------------------------------------------------------------- 범위 (PLAN.md §4-2)

_CONCEPT_NOISE = re.compile(r"[\s·,()\-_]|의$|법칙$|원리$|정리$|공식$")


def _concept_key(name: str) -> str:
    s = unicodedata.normalize("NFKC", name).lower()
    for _ in range(3):  # "옴의 법칙" → "옴"
        s = _CONCEPT_NOISE.sub("", s)
    return s


def concept_in(name: str, allowed: list[str]) -> bool:
    """개념 이름이 해설지 개념 목록에 있는가 — 띄어쓰기·'법칙' 같은 꼬리·짧은 줄임은 봐준다."""
    from rapidfuzz import fuzz

    n = _concept_key(name)
    if not n:
        return True
    for a in allowed:
        k = _concept_key(a)
        if not k:
            continue
        if n == k or (min(len(n), len(k)) >= 2 and (n in k or k in n)):
            return True
        if fuzz.ratio(n, k) >= 85:
            return True
    return False


def key_scope_text(key: AnswerKey | None, q: ExamQuestion) -> str:
    """해설지·문항이 이미 쓰는 글 — 여기 있는 기법은 프리셋이 금지해도 이 시험의 범위다."""
    from dawoai.grading.render import exam_question_text

    parts = [exam_question_text(q)]
    for p in key.parts if key else []:
        parts += [p.answer or "", p.solution, p.credit_text or "", *p.zero_if, *p.concepts]
    return "\n".join(parts)


def _conclusions(latex: str) -> dict[str, str]:
    """식의 '좌변 = 우변' 결론: 정규화한 좌변 → sympy 식 (읽을 수 있는 것만)."""
    from dawoai.mathx.sym import parse

    out: dict[str, str] = {}
    for seg in re.split(r",|\\quad|\\qquad|;|\\text\{[^{}]*\}|[가-힣]+", latex):
        if seg.count("=") < 1:
            continue
        sides = [x.strip() for x in seg.split("=")]
        lhs, rhs = normalize_for_compare(sides[0]), sides[-1]
        if not lhs or not rhs or re.search(r"\\(?:le|ge|lt|gt|neq|approx)|[<>]", seg):
            continue
        expr = latex_to_expr(rhs)
        try:
            parse(expr)
        except ValueError:
            continue
        out[lhs] = expr
    return out


def _math_of(text: str) -> list[str]:
    from dawoai.models.content import MathRun, runs_from_text

    return [r.v for r in runs_from_text(text) if isinstance(r, MathRun)]


def _conclusion_conflict(text: str, key_answer: str | None) -> str | None:
    """넓힌 표현이 해설지 답과 같은 좌변에 다른 값을 쓰면 그 식 (sympy로 분명히 다를 때만)."""
    if not key_answer:
        return None
    want = _conclusions(key_answer.replace("\\(", "").replace("\\)", ""))
    for m in _math_of(text):
        for lhs, expr in _conclusions(m).items():
            if lhs in want and equivalent(expr, want[lhs]) is False:
                return m
    return None


def check_rubric_scope(
    draft: RubricDraft, q: ExamQuestion, key: AnswerKey | None, scope: ScopeProfile
) -> list[str]:
    """채점 기준이 해설지의 범위 안인가.

    1. 수준 프리셋의 금지 기법(미분·적분 …)이 기준·부분 점수·넓힌 표현·정답 후보에 나오면 수리.
       해설지·문항이 그 기법을 이미 쓰면 이 시험의 범위다 (강사가 정함).
    2. 기준·넓힌 표현이 적은 개념(`concepts`)은 해설지 개념 목록 안이어야 한다 (목록이 있을 때).
    3. 넓힌 표현의 결론이 해설지 답과 다르면 수리 (같은 좌변, 다른 값)."""
    problems: list[str] = []
    exempt = set(_forbidden_hits(key_scope_text(key, q), scope))

    def forbidden(where: str, *texts: str) -> None:
        hits = [h for h in _forbidden_hits(" ".join(texts), scope) if h not in exempt]
        if hits:
            problems.append(
                f"{where}: 이 수준({scope.level})에서 쓰지 않는 기법({', '.join(hits)})이 채점 "
                "기준에 들어 있다. 해설지가 쓴 방법만으로 기준을 쓸 것 — 범위 밖 기법을 "
                "요구하지도, 넓힌 표현에 넣지도 않는다"
            )

    def allowed_for(part: str | None) -> list[str]:
        if key is None:
            return []
        kp = key.part(part)
        return (kp.concepts if kp and kp.concepts else None) or key.concepts

    def outside(where: str, part: str | None, concepts: list[str]) -> None:
        allowed = allowed_for(part)
        bad = [c for c in concepts if allowed and not concept_in(c, allowed)]
        if bad:
            problems.append(
                f"{where}: 해설지 개념 목록에 없는 개념 {bad}을 쓴다. 해설지 개념"
                f"({', '.join(allowed)}) 안에서만 기준을 세우고 넓힐 것 — 다른 개념의 풀이는 "
                "넣지 않는다"
            )

    for i in draft.items:
        forbidden(i.id, i.criterion, i.evidence_hint, *i.concepts)
        outside(i.id, i.part, i.concepts)
    for p in draft.partial:
        forbidden(f"{p.item_id}의 부분점수", p.condition)
    for n, a in enumerate(draft.accepted_alternatives, start=1):
        where = a.id or f"다른 풀이 {n}"
        forbidden(where, a.description, *a.concepts)
        outside(where, a.part, a.concepts)
        kp = key.part(a.part) if key and a.part is not None else None
        if a.status == "proposed" and kp and (m := _conclusion_conflict(a.description, kp.answer)):
            problems.append(
                f"{where}: 결론 \\({m}\\)이 해설지 답({kp.answer})과 다르다. 값·결론이 다른 답은 "
                "넓힌 표현에 넣지 않는다 — 같은 결론을 다르게 쓴 말만"
            )
    for c in draft.candidates:
        forbidden(f"정답 후보 {c.id}", c.answer, c.argument)
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
    # 판독문을 보여 줄 때 줄 앞에 소문항 표시를 붙이므로, 인용에 그 표시가 들어가도 된다.
    # (표시를 판독문 쪽에 끼워 넣으면 여러 줄에 걸친 인용이 깨진다 — 실측)
    for label in sorted({x.part for x in transcript.lines if x.part}, key=len, reverse=True):
        quote = quote.replace(label, " ")
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
    candidates = {c.id: c for c in rubric.candidates}
    unknown_c = [c for c in out.matched_candidates if c not in candidates]
    if unknown_c:
        problems.append(f"matched_candidates {unknown_c}는 루브릭의 정답 후보가 아니다")
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
                elif term := unrequired_method(j.deduction_reason, rubric):
                    problems.append(
                        f"{item.id}: 감점 이유 '{term}을(를) 쓰지 않음'은 루브릭에 없는 요구다. "
                        "루브릭 기준에 있는 것으로만 감점할 것 — 해설지가 요구하지 않은 "
                        "방법·개념을 쓰지 않았다고 감점하지 않는다"
                    )
    return problems


_NOT_USED = re.compile(
    r"((?:[가-힣A-Za-z0-9]+\s){0,2}[가-힣A-Za-z0-9]+?)\s*(?:을|를)\s*"
    r"(?:쓰지|사용하지|이용하지|적용하지|활용하지)\s*않"
)
_GENERIC = {
    "풀이", "과정", "식", "단위", "답", "근거", "그림", "부호", "기호", "문장", "설명", "결론",
}  # fmt: skip
"""답안의 모양을 말하는 낱말 — "단위를 쓰지 않아"는 방법을 요구하는 감점이 아니다."""
_FILLER = {"그래서", "따라서", "그리고", "또한", "이를", "이", "그", "및"}


def unrequired_method(reason: str, rubric: RubricDraft) -> str | None:
    """감점 이유가 "○○을 쓰지 않아서"이고 ○○이 루브릭 어디에도 없으면 ○○ (다워니 never_deduct).

    ○○ 전체가 루브릭 글에 있거나 루브릭 개념 표시와 같은 개념이면 루브릭의 요구다."""
    m = _NOT_USED.search(reason)
    if not m:
        return None
    words = m.group(1).split()
    while words and words[0] in _FILLER:
        words = words[1:]
    if not words or all(w in _GENERIC for w in words):
        return None
    term = " ".join(words)
    hay = " ".join(
        [i.criterion + " " + i.evidence_hint for i in rubric.items]
        + [p.condition for p in rubric.partial]
        + [a.description for a in rubric.accepted_alternatives if a.status == "accepted"]
    ).replace(" ", "")
    tags = [c for i in rubric.items for c in i.concepts]
    if term.replace(" ", "") in hay or (tags and concept_in(term, tags)):
        return None
    return term


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
