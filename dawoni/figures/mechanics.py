# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
"""역학 그림: 빗면·용수철·포물선 운동·도르래·충돌."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from matplotlib.axes import Axes
from matplotlib.patches import Circle, Polygon, Rectangle

from dawoni.figures.common import INK, LINE, Ctx, FigureError, arrow, new_figure, save
from dawoni.models.figure import CollisionSpec, InclineSpec, ProjectileSpec, PulleySpec, SpringSpec


def _hatch(
    ax: Axes, x0: float, x1: float, y: float, *, below: bool = True, step: float = 0.35
) -> None:
    """바닥·벽 빗금."""
    ax.plot([x0, x1], [y, y], color=INK, lw=LINE * 1.2)
    d = -step if below else step
    for x in np.arange(x0, x1, step):
        ax.plot([x, x + step * 0.8], [y + d * 0.9, y], color=INK, lw=LINE * 0.6)


def _block(ax: Axes, cx: float, cy: float, w: float, h: float, angle: float, label: str) -> None:
    t = math.radians(angle)
    c, s = math.cos(t), math.sin(t)
    corners = [(-w / 2, 0), (w / 2, 0), (w / 2, h), (-w / 2, h)]
    pts = [(cx + x * c - y * s, cy + x * s + y * c) for x, y in corners]
    ax.add_patch(Polygon(pts, closed=True, fc="#e6e6e6", ec=INK, lw=LINE))
    if label:
        mx, my = cx - (h / 2) * s, cy + (h / 2) * c
        ax.text(mx, my, label, ha="center", va="center", rotation=angle, fontsize=10)


# ---------------------------------------------------------------- 빗면


def render_incline(spec: InclineSpec, ctx: Ctx, out: Path, width_in: float) -> Path:
    deg = ctx.num(spec.angle_deg, "경사각")
    if not 0 < deg < 80:
        raise FigureError(f"경사각 {deg:g}°는 0°와 80° 사이여야 합니다")
    L = 6.0
    t = math.radians(deg)
    base, height = L * math.cos(t), L * math.sin(t)
    flip = -1 if spec.flip else 1
    lo = (0.0, 0.0)
    hi = (flip * base, height)
    corner = (flip * base, 0.0)
    fig, ax = new_figure(width_in, width_in * max(height / base, 0.3) * 0.9 + 0.4)
    ax.add_patch(Polygon([lo, corner, hi], closed=True, fc="#f2f2f2", ec=INK, lw=LINE * 1.2))
    _hatch(ax, min(0, flip * base) - 0.6, max(0, flip * base) + 0.6, 0)
    # 각 표시: 아래 꼭짓점(빗면이 시작하는 쪽)
    r = 0.9
    th = np.linspace(0, t, 30)
    ax.plot(flip * r * np.cos(th), r * np.sin(th), color=INK, lw=LINE * 0.8)
    if spec.angle_label:
        ax.text(
            flip * (r + 0.35) * math.cos(t / 2),
            (r + 0.35) * math.sin(t / 2),
            ctx.label(spec.angle_label),
            ha="center",
            va="center",
        )
    # 물체
    pos = spec.block_position
    bx, by = flip * pos * base, pos * height
    _block(ax, bx, by, 1.1, 0.75, flip * deg, ctx.label(spec.block_label))
    if spec.friction_label:
        ax.text(
            bx + flip * 0.9 * math.cos(t) + 0.3 * math.sin(t),
            by + 0.9 * math.sin(t) - 0.45,
            ctx.label(spec.friction_label),
            ha="center",
            va="top",
            fontsize=10,
        )
    u = (flip * math.cos(t), math.sin(t))  # 빗면 위쪽 방향
    nrm = (-flip * math.sin(t), math.cos(t))  # 빗면에 수직(바깥)
    if spec.pulley_at_top:
        pr = 0.3
        pc = (hi[0] + u[0] * 0.35 + nrm[0] * 0.35, hi[1] + u[1] * 0.35 + nrm[1] * 0.35)
        ax.add_patch(Circle(pc, pr, fill=False, ec=INK, lw=LINE))
        front = (bx + u[0] * 0.55 + nrm[0] * 0.375, by + u[1] * 0.55 + nrm[1] * 0.375)
        ax.plot(
            [front[0], pc[0] + nrm[0] * pr],
            [front[1], pc[1] + nrm[1] * pr],
            color=INK,
            lw=LINE * 0.8,
        )
        hx = pc[0] + flip * pr
        ax.plot([hx, hx], [pc[1], pc[1] - 1.6], color=INK, lw=LINE * 0.8)
        ax.add_patch(Rectangle((hx - 0.35, pc[1] - 2.2), 0.7, 0.6, fc="#e6e6e6", ec=INK, lw=LINE))
        if spec.hanging_block_label:
            ax.text(
                hx,
                pc[1] - 1.9,
                ctx.label(spec.hanging_block_label),
                ha="center",
                va="center",
                fontsize=10,
            )
    for f in spec.forces:
        cx, cy = bx + nrm[0] * 0.375, by + nrm[1] * 0.375
        n = nrm
        down_slope = (-u[0], -u[1])
        vec = {
            "gravity": (0.0, -1.4),
            "normal": (n[0] * 1.2, n[1] * 1.2),
            "friction": (-down_slope[0] * 1.1, -down_slope[1] * 1.1),
            "tension": (-down_slope[0] * 1.3, -down_slope[1] * 1.3),
            "acceleration": (down_slope[0] * 1.2, down_slope[1] * 1.2),
        }.get(f.force)
        if f.force == "applied":
            a = math.radians(ctx.num(f.angle_deg if f.angle_deg is not None else 0)) + (
                t if flip > 0 else math.pi - t
            )
            vec = (1.3 * math.cos(a), 1.3 * math.sin(a))
        assert vec is not None
        arrow(
            ax,
            cx,
            cy,
            cx + vec[0],
            cy + vec[1],
            label=ctx.label(f.label),
            offset=(vec[0] * 0.25, vec[1] * 0.25 + 0.05),
        )
    ax.set_xlim(
        min(0, flip * base) - 0.8, max(0, flip * base) + (1.5 if spec.pulley_at_top else 0.8)
    )
    ax.set_ylim(-0.6, height + 1.0)
    return save(fig, out)


# ---------------------------------------------------------------- 용수철


def _coil(
    x0: float, x1: float, y: float, n: int = 9, amp: float = 0.22
) -> tuple[np.ndarray, np.ndarray]:
    """x0→x1 방향의 지그재그 용수철 (양 끝은 짧은 직선)."""
    lead = (x1 - x0) * 0.1
    zx = np.linspace(x0 + lead, x1 - lead, 2 * n + 1)
    zy = y + amp * np.array([0.0] + [float((-1) ** k) for k in range(1, 2 * n)] + [0.0])
    return np.concatenate([[x0], zx, [x1]]), np.concatenate([[y], zy, [y]])


def render_spring(spec: SpringSpec, ctx: Ctx, out: Path, width_in: float) -> Path:
    L0 = ctx.num(spec.natural_length, "자연 길이")
    dx = ctx.num(spec.stretch, "늘어난 길이")
    L = L0 + dx
    if L <= 0.2 * L0:
        raise FigureError("용수철이 너무 많이 줄어들었습니다")
    bw, bh = 1.2, 0.9
    fig, ax = new_figure(width_in, width_in * (0.45 if spec.orientation == "horizontal" else 1.2))
    if spec.orientation == "horizontal":
        ax.plot([0, 0], [0, 2.2], color=INK, lw=LINE * 1.4)
        for y in np.arange(0, 2.2, 0.3):
            ax.plot([0, -0.25], [y, y + 0.25], color=INK, lw=LINE * 0.6)
        _hatch(ax, 0, L0 + abs(dx) + bw + 2, 0)
        xs, ys = _coil(0, L, bh / 2)
        ax.plot(xs, ys, color=INK, lw=LINE)
        ax.add_patch(Rectangle((L, 0), bw, bh, fc="#e6e6e6", ec=INK, lw=LINE))
        if spec.block_label:
            ax.text(L + bw / 2, bh / 2, ctx.label(spec.block_label), ha="center", va="center")
        if spec.spring_label:
            ax.text(L / 2, bh / 2 + 0.45, ctx.label(spec.spring_label), ha="center", va="bottom")
        if spec.show_equilibrium:
            ax.plot([L0, L0], [-0.1, bh + 0.9], "--", color=INK, lw=LINE * 0.7)
            ax.text(L0, bh + 0.95, "O", ha="center", va="bottom", fontsize=10)
        if spec.displacement_label and abs(dx) > 1e-9:
            ax.annotate(
                "",
                xy=(L, bh + 0.55),
                xytext=(L0, bh + 0.55),
                arrowprops={"arrowstyle": "<->", "lw": LINE * 0.8},
            )
            ax.text(
                (L + L0) / 2,
                bh + 0.62,
                ctx.label(spec.displacement_label),
                ha="center",
                va="bottom",
            )
        ax.set_xlim(-0.5, L0 + abs(dx) + bw + 1.5)
        ax.set_ylim(-0.5, bh + 1.6)
    else:
        top = L + bh + 1
        _hatch(ax, -1.2, 1.2, top, below=False)
        ys, xs = _coil(top, top - L, 0)
        ax.plot(xs, ys, color=INK, lw=LINE)
        ax.add_patch(Rectangle((-bw / 2, top - L - bh), bw, bh, fc="#e6e6e6", ec=INK, lw=LINE))
        if spec.block_label:
            ax.text(0, top - L - bh / 2, ctx.label(spec.block_label), ha="center", va="center")
        if spec.spring_label:
            ax.text(0.45, top - L / 2, ctx.label(spec.spring_label), ha="left", va="center")
        if spec.show_equilibrium:
            ax.plot([-1.2, 1.2], [top - L0, top - L0], "--", color=INK, lw=LINE * 0.7)
        ax.set_xlim(-1.6, 1.6)
        ax.set_ylim(top - L - bh - 0.5, top + 0.6)
    return save(fig, out)


# ---------------------------------------------------------------- 포물선 운동


def render_projectile(spec: ProjectileSpec, ctx: Ctx, out: Path, width_in: float) -> Path:
    v0, deg = ctx.num(spec.v0, "초속도"), ctx.num(spec.angle_deg, "발사각")
    h0, g = ctx.num(spec.height, "높이"), ctx.num(spec.g, "g")
    if v0 <= 0 or g <= 0 or h0 < 0:
        raise FigureError("초속도·g는 양수, 높이는 0 이상이어야 합니다")
    t = math.radians(deg)
    vx, vy = v0 * math.cos(t), v0 * math.sin(t)
    t_land = (vy + math.sqrt(vy * vy + 2 * g * h0)) / g
    if vx <= 0 or t_land <= 0:
        raise FigureError("앞으로 날아가는 발사각(0°~90°)이어야 합니다")
    ts = np.linspace(0, t_land, 200)
    xs, ys = vx * ts, h0 + vy * ts - 0.5 * g * ts**2
    R, H = float(xs[-1]), max(float(ys.max()), h0, 1e-6)
    u = R / 20  # 표시 크기 기준 (실제 비율로 그림)
    fig, ax = new_figure(width_in, width_in * min(max((H + 4 * u) / (R + 6 * u), 0.25), 1.2))
    _hatch(ax, -3 * u if h0 == 0 else 0.0, R + 2 * u, 0, step=u)
    if h0 > 0:
        ax.add_patch(Rectangle((-2.5 * u, 0), 2.5 * u, h0, fc="#eeeeee", ec=INK, lw=LINE))
        if spec.height_label:
            ax.annotate(
                "",
                xy=(-3.2 * u, h0),
                xytext=(-3.2 * u, 0),
                arrowprops={"arrowstyle": "<->", "lw": LINE * 0.8},
            )
            ax.text(-3.6 * u, h0 / 2, ctx.label(spec.height_label), ha="right", va="center")
    ax.plot(xs, ys, "--", color=INK, lw=LINE)
    L = 4 * u
    tip = (L * math.cos(t), h0 + L * math.sin(t))
    arrow(ax, 0, h0, tip[0], tip[1])
    if spec.v0_label:
        ax.text(
            tip[0] + 0.6 * u, tip[1] + 0.4 * u, ctx.label(spec.v0_label), ha="left", va="bottom"
        )
    if spec.angle_label:
        ax.plot([0, 3 * u], [h0, h0], ":", color=INK, lw=LINE * 0.7)
        th = np.linspace(0, t, 30)
        ax.plot(1.8 * u * np.cos(th), h0 + 1.8 * u * np.sin(th), color=INK, lw=LINE * 0.7)
        ax.text(
            2.6 * u * math.cos(t / 2),
            h0 + 2.6 * u * math.sin(t / 2),
            ctx.label(spec.angle_label),
            ha="left",
            va="center",
        )
    if spec.mark_top and vy > 0:
        tt = vy / g
        py = h0 + vy * tt - 0.5 * g * tt**2
        ax.plot([vx * tt], [py], "o", ms=3.5, color=INK)
        ax.text(vx * tt, py + 0.8 * u, ctx.label(spec.mark_top), ha="center", va="bottom")
    if spec.mark_landing:
        ax.plot([R], [0], "o", ms=3.5, color=INK)
        ax.text(R, -1.2 * u, ctx.label(spec.mark_landing), ha="center", va="top")
    ax.set_xlim(-6 * u if h0 > 0 else -3.5 * u, R + 2.5 * u)
    ax.set_ylim(-2.5 * u, H + 2.5 * u)
    return save(fig, out)


# ---------------------------------------------------------------- 도르래


def render_pulley(spec: PulleySpec, ctx: Ctx, out: Path, width_in: float) -> Path:
    fig, ax = new_figure(width_in, width_in * 0.8)
    r = 0.45

    def mass(x: float, y: float, m: float, label: str) -> None:
        s = 0.7 * m
        ax.add_patch(Rectangle((x - s / 2, y - s), s, s, fc="#e6e6e6", ec=INK, lw=LINE))
        ax.text(x, y - s / 2, label, ha="center", va="center")

    if spec.layout == "atwood":
        _hatch(ax, -1.6, 1.6, 4.6, below=False)
        ax.plot([0, 0], [4.6, 3.8 + r], color=INK, lw=LINE)
        ax.add_patch(Circle((0, 3.8), r, fill=False, ec=INK, lw=LINE))
        ax.plot([-r, -r], [3.8, 1.8], color=INK, lw=LINE * 0.8)
        ax.plot([r, r], [3.8, 2.6], color=INK, lw=LINE * 0.8)
        mass(-r, 1.8, spec.left.size, ctx.label(spec.left.label))
        mass(r, 2.6, spec.right.size, ctx.label(spec.right.label))
        if spec.arrow_label:
            arrow(
                ax,
                -r - 0.9,
                1.6,
                -r - 0.9,
                2.4,
                label=ctx.label(spec.arrow_label),
                offset=(-0.3, 0),
            )
        ax.set_xlim(-2.2, 2.2)
        ax.set_ylim(0.4, 5.0)
    else:
        ax.add_patch(Rectangle((0, 2.0), 4.0, 0.15, fc="#dddddd", ec=INK, lw=LINE))
        ax.plot([0.3, 0.3], [0, 2.0], color=INK, lw=LINE)
        ax.plot([3.7, 3.7], [0, 2.0], color=INK, lw=LINE)
        s = 0.8 * spec.left.size
        ax.add_patch(Rectangle((1.2, 2.15), s, s * 0.8, fc="#e6e6e6", ec=INK, lw=LINE))
        ax.text(1.2 + s / 2, 2.15 + s * 0.4, ctx.label(spec.left.label), ha="center", va="center")
        px, py = 4.15, 2.5
        ax.add_patch(Circle((px, py), 0.32, fill=False, ec=INK, lw=LINE))
        ax.plot([4.0, px], [2.15, py - 0.32 + 0.0], color=INK, lw=LINE * 0.6)
        ax.plot([1.2 + s, px], [2.15 + s * 0.4, py + 0.32 - 0.05], color=INK, lw=LINE * 0.8)
        ax.plot([px + 0.32, px + 0.32], [py, 1.2], color=INK, lw=LINE * 0.8)
        mass(px + 0.32, 1.2, spec.right.size, ctx.label(spec.right.label))
        if spec.friction_label:
            ax.text(
                1.2 + s / 2,
                1.95,
                ctx.label(spec.friction_label),
                ha="center",
                va="top",
                fontsize=10,
            )
        if spec.arrow_label:
            arrow(
                ax,
                1.2,
                2.15 + s + 0.3,
                2.2,
                2.15 + s + 0.3,
                label=ctx.label(spec.arrow_label),
                offset=(0.25, 0),
            )
        ax.set_xlim(-0.3, 5.2)
        ax.set_ylim(-0.3, 3.6)
    return save(fig, out)


# ---------------------------------------------------------------- 충돌


def render_collision(spec: CollisionSpec, ctx: Ctx, out: Path, width_in: float) -> Path:
    n = len(spec.panels)
    fig, ax = new_figure(width_in, width_in * 0.28 * n + 0.1)
    ax.set_aspect("auto")
    for k, panel in enumerate(spec.panels):
        y = (n - 1 - k) * 2.0
        _hatch(ax, 0, 11, y, step=0.4)
        if panel.title:
            ax.text(-0.3, y + 0.5, ctx.label(panel.title), ha="right", va="center", fontsize=10)
        for b in panel.bodies:
            x = ctx.num(b.x, "위치")
            ax.add_patch(
                Rectangle((x - b.width / 2, y), b.width, 0.8, fc="#e6e6e6", ec=INK, lw=LINE)
            )
            ax.text(x, y + 0.4, ctx.label(b.label), ha="center", va="center", fontsize=10)
            if b.velocity is not None:
                v = ctx.num(b.velocity, "속도")
                if abs(v) > 1e-12:
                    d = 1.0 if v > 0 else -1.0
                    arrow(ax, x, y + 1.15, x + d * 1.3, y + 1.15, head=0.08)
                    if b.velocity_label:
                        ax.text(
                            x + d * 0.65,
                            y + 1.3,
                            ctx.label(b.velocity_label),
                            ha="center",
                            va="bottom",
                            fontsize=10,
                        )
                elif b.velocity_label:
                    ax.text(
                        x,
                        y + 1.15,
                        ctx.label(b.velocity_label),
                        ha="center",
                        va="bottom",
                        fontsize=10,
                    )
    ax.set_xlim(-2.2, 11.3)
    ax.set_ylim(-0.4, (n - 1) * 2.0 + 1.9)
    return save(fig, out)
