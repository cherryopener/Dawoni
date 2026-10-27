# pyright: basic, reportMissingTypeStubs=false
"""sympy 사용은 이 파일에만 모은다 (sympy 타입 정보가 strict 검사와 맞지 않음).

밖으로는 타입이 분명한 함수만 내보낸다. AI가 준 식을 평가할 때도 `eval`은 쓰지 않고
sympy 파서만 쓴다.
"""

from __future__ import annotations

import cmath
import random

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
    except (SyntaxError, TypeError, ValueError, sympy.SympifyError, AttributeError) as e:
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
