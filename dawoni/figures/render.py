"""스펙 → PNG. 문항 변수 값으로 식을 계산하고, 같은 스펙·값이면 다시 그리지 않는다 (캐시)."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dawoni.figures.circuit import render_circuit
from dawoni.figures.common import Ctx, FigureError
from dawoni.figures.fields import render_field
from dawoni.figures.geometry import render_geometry
from dawoni.figures.graph import render_function_graph, render_motion_graph
from dawoni.figures.mechanics import (
    render_collision,
    render_incline,
    render_projectile,
    render_pulley,
    render_spring,
)
from dawoni.models.figure import Figure, FigureSpec
from dawoni.models.item import ItemContent

RENDERER_VERSION = "2026-10-27.1"
"""그리는 방식이 바뀌면 올린다 (캐시 무효화)."""

_RENDERERS: dict[str, Callable[[Any, Ctx, Path, float], Path]] = {
    "function_graph": render_function_graph,
    "motion_graph": render_motion_graph,
    "geometry": render_geometry,
    "incline": render_incline,
    "spring": render_spring,
    "projectile": render_projectile,
    "pulley": render_pulley,
    "collision": render_collision,
    "field": render_field,
    "circuit": render_circuit,
}
WIDTH_IN: dict[str, float] = {
    "function_graph": 2.6, "motion_graph": 2.8, "geometry": 2.4, "incline": 2.8, "spring": 3.0,
    "projectile": 3.0, "pulley": 2.4, "collision": 3.2, "field": 2.8, "circuit": 2.6,
}  # fmt: skip
_LOCK = threading.Lock()  # matplotlib rcParams는 프로세스 전역


def render_spec(
    spec: FigureSpec, values: dict[str, float], out: Path, *, width_in: float | None = None
) -> Path:
    fn = _RENDERERS[spec.kind]
    with _LOCK:
        return fn(spec, Ctx(dict(values)), out, width_in or WIDTH_IN[spec.kind])


def cache_key(spec: FigureSpec, values: dict[str, float]) -> str:
    data = {"v": RENDERER_VERSION, "spec": spec.model_dump(mode="json"), "values": values}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def item_values(item: ItemContent) -> dict[str, float]:
    return {k: v.value for k, v in item.variables.items()}


def default_cache() -> Path:
    """다시 그린 그림 캐시는 PC마다 (앱 데이터 폴더). 공유 작업공간에 두면 PC마다 동기화된다."""
    from dawoni.paths import AppPaths

    return AppPaths.default().cache / "figures"


def render_figure(fig: Figure, item: ItemContent, cache_dir: Path | None = None) -> Path:
    """파라미터 그림 → 캐시된 PNG 경로. 스펙이 없으면 FigureError."""
    cache_dir = cache_dir or default_cache()
    cache_dir.mkdir(parents=True, exist_ok=True)
    if fig.spec is None:
        raise FigureError(f"그림 {fig.id}: 스펙이 없습니다")
    values = item_values(item)
    out = cache_dir / f"{fig.id}_{cache_key(fig.spec, values)}.png"
    if out.is_file():
        return out
    tmp = out.with_suffix(".tmp.png")
    render_spec(fig.spec, values, tmp)
    tmp.replace(out)
    return out


__all__ = ["FigureError", "render_figure", "render_spec"]
