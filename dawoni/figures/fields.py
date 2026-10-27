# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
"""전기장·자기장: 영역(⊗ ⊙ 또는 균일장 화살표), 대전 입자와 속도, 도선과 전류, 도선 고리."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from matplotlib.patches import Circle, Rectangle

from dawoni.figures.common import INK, LINE, Ctx, FigureError, arrow, new_figure, save
from dawoni.models.figure import FieldSpec

_ARROW_DIR = {"up": (0, 1), "down": (0, -1), "left": (-1, 0), "right": (1, 0)}


def render_field(spec: FieldSpec, ctx: Ctx, out: Path, width_in: float) -> Path:
    boxes = []
    for r in spec.regions:
        x0, y0, x1, y1 = (ctx.num(v, "영역") for v in (r.x0, r.y0, r.x1, r.y1))
        if x0 >= x1 or y0 >= y1:
            raise FigureError("영역의 x0<x1, y0<y1 이어야 합니다")
        boxes.append((x0, y0, x1, y1))
    xs = [b[0] for b in boxes] + [b[2] for b in boxes]
    ys = [b[1] for b in boxes] + [b[3] for b in boxes]
    for c in spec.charges:
        xs.append(ctx.num(c.x))
        ys.append(ctx.num(c.y))
    for w in spec.wires:
        xs += [ctx.num(w.x1), ctx.num(w.x2)]
        ys += [ctx.num(w.y1), ctx.num(w.y2)]
    X0, X1, Y0, Y1 = min(xs) - 0.8, max(xs) + 0.8, min(ys) - 0.8, max(ys) + 0.8
    fig, ax = new_figure(width_in, width_in * (Y1 - Y0) / (X1 - X0))
    ax.set_xlim(X0, X1)
    ax.set_ylim(Y0, Y1)
    for r, (x0, y0, x1, y1) in zip(spec.regions, boxes, strict=True):
        ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, ec=INK, lw=LINE, ls="--"))
        step = max(min(x1 - x0, y1 - y0) / 4, 0.6)
        gx = np.arange(x0 + step / 2, x1, step)
        gy = np.arange(y0 + step / 2, y1, step)
        for x in gx:
            for y in gy:
                if r.direction == "into":
                    ax.add_patch(Circle((x, y), 0.14, fill=False, ec=INK, lw=LINE * 0.7))
                    ax.plot([x - 0.1, x + 0.1], [y - 0.1, y + 0.1], color=INK, lw=LINE * 0.7)
                    ax.plot([x - 0.1, x + 0.1], [y + 0.1, y - 0.1], color=INK, lw=LINE * 0.7)
                elif r.direction == "out":
                    ax.add_patch(Circle((x, y), 0.14, fill=False, ec=INK, lw=LINE * 0.7))
                    ax.plot([x], [y], "o", ms=1.8, color=INK)
        if r.direction in _ARROW_DIR:
            dx, dy = _ARROW_DIR[r.direction]
            if dx:
                for y in gy:
                    arrow(
                        ax,
                        x0 + 0.2 if dx > 0 else x1 - 0.2,
                        y,
                        x1 - 0.2 if dx > 0 else x0 + 0.2,
                        y,
                        lw=LINE * 0.7,
                        head=0.08,
                    )
            else:
                for x in gx:
                    arrow(
                        ax,
                        x,
                        y0 + 0.2 if dy > 0 else y1 - 0.2,
                        x,
                        y1 - 0.2 if dy > 0 else y0 + 0.2,
                        lw=LINE * 0.7,
                        head=0.08,
                    )
        if r.label:
            ax.text(
                x1 - 0.15,
                y1 - 0.15,
                ctx.label(r.label),
                ha="right",
                va="top",
                fontsize=11,
                bbox={"fc": "white", "ec": "none", "pad": 1},
            )
    for w in spec.wires:
        p1 = (ctx.num(w.x1), ctx.num(w.y1))
        p2 = (ctx.num(w.x2), ctx.num(w.y2))
        ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color=INK, lw=LINE * 2)
        a, b = (p1, p2) if w.current_toward == "2" else (p2, p1)
        m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        d = (b[0] - a[0], b[1] - a[1])
        n = math.hypot(*d) or 1.0
        arrow(
            ax,
            m[0] - d[0] / n * 0.3,
            m[1] - d[1] / n * 0.3,
            m[0] + d[0] / n * 0.3,
            m[1] + d[1] / n * 0.3,
            head=0.1,
        )
        if w.current_label:
            ax.text(
                m[0] - d[1] / n * 0.4,
                m[1] + d[0] / n * 0.4,
                ctx.label(w.current_label),
                ha="center",
                va="center",
            )
    for lp in spec.loops:
        x0, y0, x1, y1 = (ctx.num(v) for v in (lp.x0, lp.y0, lp.x1, lp.y1))
        ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, ec=INK, lw=LINE * 1.8))
        if lp.label:
            ax.text((x0 + x1) / 2, y0 - 0.25, ctx.label(lp.label), ha="center", va="top")
        if lp.velocity_angle_deg is not None:
            t = math.radians(ctx.num(lp.velocity_angle_deg))
            cx, cy = x1 + 0.2, (y0 + y1) / 2
            arrow(
                ax,
                cx,
                cy,
                cx + math.cos(t) * 0.9,
                cy + math.sin(t) * 0.9,
                label=ctx.label(lp.velocity_label),
                offset=(0.25, 0.2),
            )
    for c in spec.charges:
        x, y = ctx.num(c.x), ctx.num(c.y)
        ax.add_patch(Circle((x, y), 0.2, fc="white", ec=INK, lw=LINE))
        ax.text(x, y, "+" if c.sign == "+" else "−", ha="center", va="center", fontsize=10)
        if c.label:
            ax.text(x, y - 0.35, ctx.label(c.label), ha="center", va="top", fontsize=10)
        if c.velocity_angle_deg is not None:
            t = math.radians(ctx.num(c.velocity_angle_deg))
            arrow(
                ax,
                x + math.cos(t) * 0.22,
                y + math.sin(t) * 0.22,
                x + math.cos(t) * 1.1,
                y + math.sin(t) * 1.1,
                label=ctx.label(c.velocity_label),
                offset=(math.cos(t) * 0.3, math.sin(t) * 0.3 + 0.15),
            )
    return save(fig, out)
