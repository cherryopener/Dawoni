"""출력 전에 변수 자리표시(`\\var{식}`)를 값으로 채운다."""

from __future__ import annotations

import re
from typing import Any

from dawoni.mathx.sym import ExprError, evaluate
from dawoni.models.item import ItemContent

_PLACEHOLDER = re.compile(r"\\var\{([^{}]*)\}")
_SKIP_KEYS = frozenset({"variables", "constraints", "expr"})


class FillError(ValueError):
    pass


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

    def sub(s: str) -> str:
        def one(m: re.Match[str]) -> str:
            expr = m.group(1).strip()
            try:
                return format_value(evaluate(expr, values), decimals_for(expr))
            except ExprError as e:
                raise FillError(f"자리표시 \\var{{{expr}}}: {e}") from e

        return _PLACEHOLDER.sub(one, s)

    def walk(x: Any) -> Any:
        if isinstance(x, str):
            return sub(x) if "\\var{" in x else x
        if isinstance(x, list):
            return [walk(v) for v in x]  # pyright: ignore[reportUnknownVariableType]
        if isinstance(x, dict):
            return {
                k: (v if k in _SKIP_KEYS else walk(v))
                for k, v in x.items()  # pyright: ignore[reportUnknownVariableType]
            }
        return x

    data = item.model_dump(mode="json")
    if "\\var{" not in str(data):
        return item
    return ItemContent.model_validate(walk(data))
