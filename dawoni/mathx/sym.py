# pyright: basic, reportMissingTypeStubs=false
"""sympy 사용은 이 파일에만 모은다 (sympy 타입 정보가 strict 검사와 맞지 않음).

밖으로는 타입이 분명한 함수만 내보낸다. AI가 준 식을 평가할 때도 `eval`은 쓰지 않고
sympy 파서만 쓴다.
"""

from __future__ import annotations

import cmath
import random
from tokenize import TokenError

import sympy
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

_TRANSFORMS = (*standard_transformations, implicit_multiplication_application, convert_xor)


class ExprError(ValueError):
    pass


def parse(expr: str) -> sympy.Expr:
    try:
        v = parse_expr(expr, transformations=_TRANSFORMS, evaluate=True)
    except (
        SyntaxError, TypeError, ValueError, sympy.SympifyError, AttributeError, TokenError,
    ) as e:  # fmt: skip
        raise ExprError(f"식을 읽을 수 없음: {expr!r} ({e})") from e
    if not isinstance(v, sympy.Expr):
        raise ExprError(f"식이 아님: {expr!r}")
    return v


def equivalent(a: str, b: str, *, samples: int = 8, seed: int = 0) -> bool | None:
    """두 식이 같은가. 읽을 수 없으면 None.

    차이를 단순화해 0이면 같음. 안 되면 무작위 양의 유리수를 여러 번 대입해 비교한다.
    """
    try:
        ea, eb = parse(a), parse(b)
    except ExprError:
        return None
    diff = sympy.simplify(ea - eb)
    if diff == 0:
        return True
    symbols = sorted(diff.free_symbols, key=str)
    if not symbols:
        try:
            return abs(complex(sympy.N(diff))) < 1e-9
        except TypeError, ValueError:
            return None
    rng = random.Random(seed)
    checked = 0
    for _ in range(samples * 3):
        point = {s: sympy.Rational(rng.randint(1, 97), rng.randint(1, 13)) for s in symbols}
        try:
            value = complex(sympy.N(diff.subs(list(point.items()))))
        except TypeError, ValueError, ZeroDivisionError:
            continue
        if value != value:  # NaN (정의역 밖)
            continue
        if abs(value) > 1e-9:
            return False
        checked += 1
        if checked >= samples:
            break
    return True if checked else None


def evaluate(expr: str, values: dict[str, float]) -> float:
    """변수 값을 넣어 수치로 계산. 남는 기호가 있거나 실수가 아니면 ExprError."""
    e = parse(expr)
    subs = {sympy.Symbol(k): sympy.nsimplify(v) for k, v in values.items()}
    v = e.subs(list(subs.items()))
    left = sorted(str(s) for s in v.free_symbols)
    if left:
        raise ExprError(f"값이 없는 변수: {left} ({expr!r})")
    try:
        c = complex(sympy.N(v))
    except (TypeError, ValueError) as err:
        raise ExprError(f"계산할 수 없음: {expr!r}") from err
    if not cmath.isfinite(c):
        raise ExprError(f"값이 정의되지 않음 (0으로 나눔 등): {expr!r}")
    if abs(c.imag) > 1e-12:
        raise ExprError(f"실수가 아님: {expr!r} = {c}")
    return c.real


# ---------------------------------------------------------------- 정확한 값 (문항 제작)


def exact(expr: str, values: dict[str, float | int | str]) -> sympy.Expr:
    """변수 값을 유리수로 넣어 **정확히** 계산 (부동소수 오차 없이). 남는 기호는 ExprError."""
    e = parse(expr)
    subs = [(sympy.Symbol(k), sympy.nsimplify(v, rational=True)) for k, v in values.items()]
    v = sympy.nsimplify(sympy.simplify(e.subs(subs)))
    if v.free_symbols:
        raise ExprError(f"값이 없는 변수: {sorted(str(s) for s in v.free_symbols)} ({expr!r})")
    if v.has(sympy.zoo, sympy.nan, sympy.oo, -sympy.oo):
        raise ExprError(f"값이 정의되지 않음 (0으로 나눔 등): {expr!r}")
    return v


def clean_number(v: sympy.Expr, max_den: int = 20, *, allow_sqrt: bool = False) -> bool:
    """정수, 또는 분모가 max_den 이하인 기약분수인가. allow_sqrt면 p/q·√n 꼴도."""
    if v.is_Integer:
        return True
    if v.is_Rational:
        return int(v.q) <= max_den  # pyright: ignore[reportAttributeAccessIssue]
    if allow_sqrt:
        coeff, rest = v.as_coeff_Mul()
        if rest.is_Pow and rest.exp == sympy.Rational(1, 2) and rest.base.is_Integer:  # pyright: ignore[reportAttributeAccessIssue]
            return bool(coeff.is_Rational) and _den(coeff) <= max_den
    return False


def _den(r: object) -> int:
    return int(getattr(r, "q", 10**9))


def holds(condition: str, values: dict[str, float | int | str]) -> bool | None:
    """부등식·등식 제약이 성립하는가. 판단할 수 없으면 None."""
    try:
        e = parse_expr(condition, transformations=_TRANSFORMS, evaluate=True)
    except SyntaxError, TypeError, ValueError, sympy.SympifyError, AttributeError:
        return None
    subs = [(sympy.Symbol(k), sympy.nsimplify(v, rational=True)) for k, v in values.items()]
    try:
        r = e.subs(subs)
        r = sympy.simplify(r)
    except TypeError, ValueError:
        return None
    if r is sympy.true or r is True:
        return True
    if r is sympy.false or r is False:
        return False
    return None


def to_latex(v: sympy.Expr) -> str:
    return sympy.latex(v)


def free_symbol_names(expr: str) -> set[str]:
    return {str(s) for s in parse(expr).free_symbols}


Value = sympy.Expr
"""정확한 값 (다른 모듈은 sympy를 직접 import하지 않고 이 이름을 쓴다)."""


def same_value(a: sympy.Expr, b: sympy.Expr) -> bool:
    return bool(sympy.simplify(a - b) == 0)
