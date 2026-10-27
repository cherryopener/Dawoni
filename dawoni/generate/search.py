"""수치형 동형문제: 변수 값 고르기는 **코드가** 한다 (PLAN §9-1, §9-2).

변수마다 후보(choices) 또는 범위(min·max·step)에서 값을 모아 조합을 만들고,
- 제약(constraints)이 성립하고
- 정답(과 중간값)을 유리수로 정확히 계산했을 때 정수이거나 분모가 상한 이하인 기약분수이며
- 선택형이면 선지 값이 서로 다르고 정답과 같은 선지가 정확히 하나인
조합만 채택한다. 원래 값 조합은 제외하고, 많이 바뀌고 답이 서로 다른 조합을 먼저 고른다.
"""

from __future__ import annotations

import itertools
import random
import re
from collections import Counter
from dataclasses import dataclass, field

from dawoni.mathx.sym import ExprError, Value, clean_number, exact, holds, same_value
from dawoni.models.content import iter_runs
from dawoni.models.generation import NumericTemplate
from dawoni.models.item import Variable

_PLACEHOLDER = re.compile(r"\\var\{([^{}]*)\}")
MAX_STEPS = 200


def domain(v: Variable) -> list[float]:
    if v.choices:
        return list(v.choices)
    if v.min is not None and v.max is not None:
        step = v.step
        ints = (v.min, v.max, v.value)
        if step is None and all(float(x).is_integer() for x in ints):
            step = 1.0
        if step:
            n = round((v.max - v.min) / step)
            if 0 < n <= MAX_STEPS:
                return [round(v.min + i * step, 10) for i in range(n + 1)]
    return [v.value]


@dataclass
class Candidate:
    values: dict[str, float]
    answer: Value
    choice_label: int | None = None
    changed: int = 0


@dataclass
class SearchResult:
    found: list[Candidate] = field(default_factory=lambda: list[Candidate]())
    tried: int = 0
    rejected: Counter[str] = field(default_factory=lambda: Counter[str]())

    def why_none(self) -> str:
        top = ", ".join(f"{k} {n}회" for k, n in self.rejected.most_common(3))
        return f"조합 {self.tried}개 중 조건에 맞는 것이 없음 ({top})"


def choice_exprs(t: NumericTemplate) -> dict[int, str]:
    """선지 번호 → 그 선지의 자리표시 식 (자리표시가 정확히 하나인 선지만)."""
    out: dict[int, str] = {}
    for c in t.item.choices:
        found = [m for r in c.runs for m in _PLACEHOLDER.findall(r.v)]
        if len(found) == 1:
            out[c.label] = found[0].strip()
    return out


def _clean(v: Value, max_den: int, allow_sqrt: bool) -> bool:
    return clean_number(v, max_den, allow_sqrt=allow_sqrt)


def evaluate_combo(
    t: NumericTemplate, values: dict[str, float], *, max_den: int, allow_sqrt: bool
) -> Candidate | str:
    """조합 하나 → 후보, 아니면 버린 이유."""
    for c in t.item.constraints:
        ok = holds(c, dict(values))
        if ok is None:
            return "제약 판단 불가"
        if not ok:
            return "제약 불성립"
    try:
        ans = exact(t.answer_expr, dict(values))
    except ExprError:
        return "정답 계산 불가"
    if not _clean(ans, max_den, allow_sqrt):
        return "답이 깔끔하지 않음"
    for e in t.intermediate_exprs:
        try:
            if not _clean(exact(e, dict(values)), max_den, allow_sqrt):
                return "중간값이 깔끔하지 않음"
        except ExprError:
            return "중간값 계산 불가"
    # 본문·선지의 자리표시도 모두 계산되어야 한다
    for r in iter_runs(t.item.stem):
        for ph in _PLACEHOLDER.findall(r.v):
            try:
                exact(ph, dict(values))
            except ExprError:
                return "본문 자리표시 계산 불가"
    label: int | None = None
    cx = choice_exprs(t)
    if t.item.item_type == "multiple_choice":
        vals: dict[int, Value] = {}
        for lab, e in cx.items():
            try:
                vals[lab] = exact(e, dict(values))
            except ExprError:
                return "선지 계산 불가"
        if len(set(vals.values())) != len(vals):
            return "선지 값이 겹침"
        hits = [lab for lab, v in vals.items() if same_value(v, ans)]
        if len(hits) != 1:
            return "정답과 같은 선지가 하나가 아님"
        label = hits[0]
    return Candidate(values, ans, label)


def search(
    t: NumericTemplate,
    *,
    count: int = 2,
    max_den: int = 20,
    allow_sqrt: bool = False,
    limit: int = 20_000,
    seed: int = 0,
) -> SearchResult:
    names = list(t.item.variables)
    doms = [domain(t.item.variables[n]) for n in names]
    original = {n: t.item.variables[n].value for n in names}
    total = 1
    for d in doms:
        total *= len(d)
    if total <= limit:
        combos = itertools.product(*doms)
    else:
        rng = random.Random(seed)
        combos = (tuple(rng.choice(d) for d in doms) for _ in range(limit))
    res = SearchResult()
    seen: set[tuple[float, ...]] = set()
    ok: list[Candidate] = []
    for combo in combos:
        if combo in seen:
            continue
        seen.add(combo)
        values = dict(zip(names, combo, strict=True))
        if values == original:
            continue
        res.tried += 1
        out = evaluate_combo(t, values, max_den=max_den, allow_sqrt=allow_sqrt)
        if isinstance(out, str):
            res.rejected[out] += 1
            continue
        out.changed = sum(values[n] != original[n] for n in names)
        ok.append(out)
    # 많이 바뀐 것, 답이 서로 다른 것, 선택형이면 정답 번호가 다른 것 먼저
    rng = random.Random(seed)
    rng.shuffle(ok)
    ok.sort(key=lambda c: -c.changed)
    used_answers: set[Value] = set()
    used_labels: set[int | None] = set()
    for c in ok:
        if len(res.found) >= count:
            break
        if c.answer in used_answers:
            continue
        if c.choice_label is not None and c.choice_label in used_labels and len(used_labels) < 5:
            continue
        res.found.append(c)
        used_answers.add(c.answer)
        used_labels.add(c.choice_label)
    for c in ok:  # 정답 번호를 다 썼으면 남은 것으로 채움
        if len(res.found) >= count:
            break
        if c not in res.found and c.answer not in used_answers:
            res.found.append(c)
            used_answers.add(c.answer)
    return res
