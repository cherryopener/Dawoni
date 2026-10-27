"""출력 전에 변수 자리표시(`\\var{식}`)를 값으로 채운다."""

from __future__ import annotations

import re
from typing import Any, cast

from dawoni.mathx.sym import ExprError, evaluate, exact
from dawoni.models.item import ItemContent

_PLACEHOLDER = re.compile(r"\\var\{([^{}]*)\}")
_SKIP_KEYS = frozenset({"variables", "constraints", "expr"})


class FillError(ValueError):
    pass


def rational_parts(v: object) -> tuple[int, int]:
    return int(getattr(v, "p", 0)), int(getattr(v, "q", 1))


def terminating(q: int) -> bool:
    """분모가 2·5로만 이루어져 유한소수로 끝나는가."""
    for f in (2, 5):
        while q % f == 0:
            q //= f
    return q == 1


def format_value(x: float, decimals: int | None = None) -> str:
    if decimals is not None:
        return f"{x:.{decimals}f}"
    if abs(x - round(x)) < 1e-9:
        return str(round(x))
    return f"{x:.4f}".rstrip("0").rstrip(".")


def fill_item(item: ItemContent) -> ItemContent:
    """자리표시를 변수의 현재 값으로 바꾼 사본. 자리표시가 없으면 그대로."""
    values = {k: v.value for k, v in item.variables.items()}

    def decimals_for(expr: str) -> int | None:
        ds = [
            v.decimals
            for k, v in item.variables.items()
            if v.decimals is not None and re.search(rf"\b{re.escape(k)}\b", expr)
        ]
        return max(ds) if ds else None

    def decimal_inputs(expr: str) -> bool:
        """식에 소수(9.8)가 있거나 쓰는 변수 값이 정수가 아니면 소수 표기가 자연스럽다."""
        if re.search(r"\d\.\d", expr):
            return True
        used = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr))
        return any(float(v) != int(v) for k, v in values.items() if k in used)

    def value_text(expr: str, in_math: bool) -> str:
        d = decimals_for(expr)
        if d is None:
            try:
                v = exact(expr, dict(values))
            except ExprError:
                v = None
            if v is not None and v.is_Rational:
                p, q = rational_parts(v)
                if q == 1:
                    return str(p)
                if decimal_inputs(expr) and terminating(q):
                    return format_value(p / q)
                if q <= 1000:
                    # 정확한 분수: 수식 안에서는 \frac (1/3을 0.3333으로 쓰지 않음)
                    sign = "-" if p < 0 else ""
                    return f"{sign}\\frac{{{abs(p)}}}{{{q}}}" if in_math else f"{p}/{q}"
        return format_value(evaluate(expr, values), d)

    def sub(s: str, in_math: bool) -> str:
        def one(m: re.Match[str]) -> str:
            expr = m.group(1).strip()
            try:
                return value_text(expr, in_math)
            except ExprError as e:
                raise FillError(f"자리표시 \\var{{{expr}}}: {e}") from e

        return _PLACEHOLDER.sub(one, s)

    def walk(x: Any, in_math: bool = False) -> Any:
        if isinstance(x, str):
            return sub(x, in_math) if "\\var{" in x else x
        if isinstance(x, list):
            return [walk(v, in_math) for v in x]  # pyright: ignore[reportUnknownVariableType]
        if isinstance(x, dict):
            d = cast(dict[str, Any], x)
            is_math = d.get("t") == "math"
            return {
                k: (v if k in _SKIP_KEYS else walk(v, (is_math and k == "v") or k == "latex"))
                for k, v in d.items()
            }
        return x

    data = item.model_dump(mode="json")
    if "\\var{" not in str(data):
        return item
    return ItemContent.model_validate(walk(data))


def with_values(item: ItemContent, values: dict[str, float]) -> ItemContent:
    """변수 값을 바꾼 사본 (자리표시는 그대로 — 출력·제작에서 `fill_item`으로 채움)."""
    data = item.model_dump(mode="json")
    for k, v in values.items():
        data["variables"][k]["value"] = v
    return ItemContent.model_validate(data)
