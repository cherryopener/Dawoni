# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
"""함수 그래프·운동 그래프 — 수능형: 화살표 좌표축, 원점 O, 눈금 대신 표시점과 점선."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from matplotlib.axes import Axes

from dawoni.figures.common import INK, LINE, Ctx, FigureError, arrow, ls, new_figure, save
from dawoni.mathx.sym import ExprError, numeric_function
from dawoni.models.figure import FunctionGraphSpec, MotionGraphSpec


def _axes(
    ax: Axes, x0: float, x1: float, y0: float, y1: float, xl: str, yl: str, origin: str
) -> None:
    w, h = x1 - x0, y1 - y0
    arrow(ax, x0, 0, x1, 0)
    arrow(ax, 0, y0, 0, y1)
    ax.text(x1, -0.06 * h, xl, ha="center", va="top", fontsize=11)
    ax.text(0.04 * w, y1, yl, ha="left", va="center", fontsize=11)
    if origin:
        ax.text(-0.03 * w, -0.03 * h, origin, ha="right", va="top", fontsize=11)


def render_function_graph(spec: FunctionGraphSpec, ctx: Ctx, out: Path, width_in: float) -> Path:
    x0, x1 = ctx.num(spec.x_range.min, "x 범위"), ctx.num(spec.x_range.max, "x 범위")
    y0, y1 = ctx.num(spec.y_range.min, "y 범위"), ctx.num(spec.y_range.max, "y 범위")
    if x0 >= x1 or y0 >= y1:
        raise FigureError("x·y 범위의 최솟값이 최댓값보다 작아야 합니다")
    w, h = x1 - x0, y1 - y0
    fig, ax = new_figure(width_in, width_in * h / w)
    ax.set_aspect("auto")
    ax.set_xlim(x0 - 0.02 * w, x1 + 0.06 * w)
    ax.set_ylim(y0 - 0.02 * h, y1 + 0.06 * h)
    _axes(
        ax,
        x0,
        x1,
        y0,
        y1,
        ctx.label(spec.x_label),
        ctx.label(spec.y_label),
        ctx.label(spec.origin_label),
    )
    fns: list[Any] = []
    for i, pf in enumerate(spec.functions):
        try:
            f = numeric_function(pf.expr, ctx.values)
        except ExprError as e:
            raise FigureError(f"함수 {i}: {e}") from e
        fns.append(f)
        d0 = ctx.num(pf.domain.min) if pf.domain else x0
        d1 = ctx.num(pf.domain.max) if pf.domain else x1
        xs = np.linspace(d0, d1, 800)
        ys = f(xs)
        ys = np.where((ys < y0) | (ys > y1), np.nan, ys)  # 범위 밖은 그리지 않음
        ys[np.abs(np.diff(ys, prepend=ys[0])) > h * 0.8] = np.nan  # 점근선 근처 끊기
        ax.plot(xs, ys, ls(pf.style), color=INK, lw=LINE * 1.2)
        if pf.label:
            k = int(len(xs) * 0.85)
            if np.isfinite(ys[k]):
                ax.text(xs[k], ys[k] + 0.04 * h, ctx.label(pf.label), ha="left", va="bottom")
    for sh in spec.shade:
        if sh.function >= len(fns) or (sh.other is not None and sh.other >= len(fns)):
            raise FigureError("색칠할 함수 번호가 없습니다")
        xs = np.linspace(ctx.num(sh.domain.min), ctx.num(sh.domain.max), 400)
        lower = fns[sh.other](xs) if sh.other is not None else np.zeros_like(xs)
        ax.fill_between(xs, fns[sh.function](xs), lower, color="#bbbbbb", alpha=0.5, lw=0)
    for a in spec.asymptotes:
        v = ctx.num(a.value, "점근선")
        if a.axis == "vertical":
            ax.plot([v, v], [y0, y1], "--", color=INK, lw=LINE * 0.8)
            if a.label:
                ax.text(v, y1, ctx.label(a.label), ha="left", va="top")
        else:
            ax.plot([x0, x1], [v, v], "--", color=INK, lw=LINE * 0.8)
            if a.label:
                ax.text(x1, v, ctx.label(a.label), ha="right", va="bottom")
    for p in spec.points:
        px = ctx.num(p.x, "점의 x")
        if p.y is not None:
            py = ctx.num(p.y, "점의 y")
        else:
            assert p.on_function is not None
            py = float(fns[p.on_function](np.array([px]))[0])
        if not np.isfinite(py):
            raise FigureError(f"점 x={px:g}에서 함수값이 정의되지 않습니다")
        if p.guide_lines:
            ax.plot([px, px], [0, py], ":", color=INK, lw=LINE * 0.8)
            ax.plot([0, px], [py, py], ":", color=INK, lw=LINE * 0.8)
        ax.plot([px], [py], "o", ms=4, color=INK if p.filled else "white", mec=INK, mew=1)
        if p.label:
            ax.text(px + 0.02 * w, py + 0.03 * h, ctx.label(p.label), ha="left", va="bottom")
    if spec.show_ticks:
        ax.axis("on")
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    return save(fig, out)


def render_motion_graph(spec: MotionGraphSpec, ctx: Ctx, out: Path, width_in: float) -> Path:
    series = [[(ctx.num(p.t, "t"), ctx.num(p.y, "값")) for p in s.points] for s in spec.series]
    ts = [t for s in series for t, _ in s] + [ctx.num(m.value) for m in spec.t_marks]
    ys = [y for s in series for _, y in s] + [ctx.num(m.value) for m in spec.y_marks] + [0.0]
    t1, y0, y1 = max(ts), min(ys), max(ys)
    if t1 <= 0 or y1 - y0 <= 0:
        raise FigureError("그래프 값의 범위가 비었습니다")
    w, h = t1 * 1.15, (y1 - y0) * 1.25 or 1
    fig, ax = new_figure(width_in, width_in * 0.62)
    ax.set_aspect("auto")
    ax.set_xlim(-0.06 * w, w * 1.04)
    ax.set_ylim(min(y0, 0) - 0.12 * h, max(y1, 0) + 0.12 * h)
    _axes(
        ax,
        0,
        w,
        min(y0, 0) - 0.05 * h,
        max(y1, 0) + 0.08 * h,
        ctx.label(spec.x_label),
        ctx.label(spec.y_label),
        ctx.label(spec.origin_label),
    )
    for s_spec, pts in zip(spec.series, series, strict=True):
        ax.plot(
            [t for t, _ in pts], [y for _, y in pts], ls(s_spec.style), color=INK, lw=LINE * 1.3
        )
        if s_spec.label:
            t, y = pts[-1]
            ax.text(t + 0.02 * w, y, ctx.label(s_spec.label), va="center")
    for sh in spec.shade:
        if sh.series >= len(series):
            raise FigureError("색칠할 그래프 번호가 없습니다")
        pts = series[sh.series]
        a, b = ctx.num(sh.t_from), ctx.num(sh.t_to)
        xs = np.linspace(a, b, 200)
        yv = np.interp(xs, [t for t, _ in pts], [y for _, y in pts])
        ax.fill_between(xs, yv, 0, color="#bbbbbb", alpha=0.55, lw=0)
    for m in spec.t_marks:
        t = ctx.num(m.value)
        ax.text(t, -0.04 * h, ctx.label(m.label) or f"{t:g}", ha="center", va="top")
        if m.guide:
            yv = [
                float(np.interp(t, [p[0] for p in s], [p[1] for p in s]))
                for s in series
                if s[0][0] <= t <= s[-1][0]
            ]
            if yv:
                ax.plot([t, t], [0, yv[0]], ":", color=INK, lw=LINE * 0.8)
    for m in spec.y_marks:
        y = ctx.num(m.value)
        ax.text(-0.02 * w, y, ctx.label(m.label) or f"{y:g}", ha="right", va="center")
        if m.guide:
            hits = [t for s in series for t, yy in s if abs(yy - y) < 1e-9]
            if hits:
                ax.plot([0, max(hits)], [y, y], ":", color=INK, lw=LINE * 0.8)
    return save(fig, out)
