# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
"""기하: 점(좌표 또는 작도) → 선분·직선·원·각·다각형. 작도는 코드가 계산한다 (교점·수선의 발 등)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from matplotlib.patches import Arc, Circle, Polygon

from dawoni.figures.common import INK, LINE, Ctx, FigureError, arrow, ls, new_figure, save
from dawoni.models.figure import GeoCircle, GeometrySpec

P = tuple[float, float]


def _intersect(a: P, b: P, c: P, d: P) -> P:
    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = a, b, c, d
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-12:
        raise FigureError("평행한 두 직선은 교점이 없습니다")
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def _foot(p: P, a: P, b: P) -> P:
    ax_, ay = a
    dx, dy = b[0] - ax_, b[1] - ay
    d2 = dx * dx + dy * dy
    if d2 < 1e-12:
        raise FigureError("직선을 정하는 두 점이 같습니다")
    t = ((p[0] - ax_) * dx + (p[1] - ay) * dy) / d2
    return (ax_ + t * dx, ay + t * dy)


def incenter(a: P, b: P, c: P) -> tuple[P, float]:
    la, lb, lc = math.dist(b, c), math.dist(a, c), math.dist(a, b)
    s = la + lb + lc
    if s < 1e-12:
        raise FigureError("삼각형이 아닙니다")
    i = ((la * a[0] + lb * b[0] + lc * c[0]) / s, (la * a[1] + lb * b[1] + lc * c[1]) / s)
    area = abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2
    if area < 1e-12:
        raise FigureError("세 점이 한 직선 위에 있습니다")
    return i, 2 * area / s


def circumcenter(a: P, b: P, c: P) -> tuple[P, float]:
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-12:
        raise FigureError("세 점이 한 직선 위에 있습니다")
    ux = (
        (a[0] ** 2 + a[1] ** 2) * (b[1] - c[1])
        + (b[0] ** 2 + b[1] ** 2) * (c[1] - a[1])
        + (c[0] ** 2 + c[1] ** 2) * (a[1] - b[1])
    ) / d
    uy = (
        (a[0] ** 2 + a[1] ** 2) * (c[0] - b[0])
        + (b[0] ** 2 + b[1] ** 2) * (a[0] - c[0])
        + (c[0] ** 2 + c[1] ** 2) * (b[0] - a[0])
    ) / d
    return (ux, uy), math.dist((ux, uy), a)


def circle_of(c: GeoCircle, pts: dict[str, P], ctx: Ctx) -> tuple[P, float]:
    if c.incircle:
        return incenter(*(pts[x] for x in c.incircle))
    if c.circumcircle:
        return circumcenter(*(pts[x] for x in c.circumcircle))
    assert c.center is not None
    o = pts[c.center]
    return o, ctx.num(c.radius) if c.radius is not None else math.dist(
        o, pts[c.through or c.center]
    )


def compute_points(spec: GeometrySpec, ctx: Ctx) -> dict[str, P]:
    pts: dict[str, P] = {}
    for p in spec.points:
        if p.at is not None:
            pts[p.name] = (ctx.num(p.at[0], f"{p.name}의 x"), ctx.num(p.at[1], f"{p.name}의 y"))
        elif p.midpoint is not None:
            a, b = pts[p.midpoint[0]], pts[p.midpoint[1]]
            pts[p.name] = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        elif p.divide is not None:
            a, b = pts[p.divide.a], pts[p.divide.b]
            m, n = ctx.num(p.divide.m), ctx.num(p.divide.n)
            if m + n == 0:
                raise FigureError(f"점 {p.name}: 내분비의 합이 0")
            pts[p.name] = ((n * a[0] + m * b[0]) / (m + n), (n * a[1] + m * b[1]) / (m + n))
        elif p.intersect is not None:
            a, b, c, d = (pts[x] for x in p.intersect)
            pts[p.name] = _intersect(a, b, c, d)
        elif p.foot is not None:
            pts[p.name] = _foot(pts[p.foot[0]], pts[p.foot[1]], pts[p.foot[2]])
        elif p.on_circle is not None:
            o = pts[p.on_circle.center]
            r, t = ctx.num(p.on_circle.radius), math.radians(ctx.num(p.on_circle.angle_deg))
            pts[p.name] = (o[0] + r * math.cos(t), o[1] + r * math.sin(t))
        elif p.polar is not None:
            o = pts[p.polar.origin]
            r, t = ctx.num(p.polar.length), math.radians(ctx.num(p.polar.angle_deg))
            pts[p.name] = (o[0] + r * math.cos(t), o[1] + r * math.sin(t))
        elif p.incenter is not None:
            pts[p.name] = incenter(*(pts[x] for x in p.incenter))[0]
        elif p.circumcenter is not None:
            pts[p.name] = circumcenter(*(pts[x] for x in p.circumcenter))[0]
        elif p.centroid is not None:
            a, b, c = (pts[x] for x in p.centroid)
            pts[p.name] = ((a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3)
    return pts


def label_fraction(a: P, b: P, others: list[P], unit: float) -> float:
    """선분 라벨을 놓을 자리 (a→b 비율). 가운데에 다른 점(중점·수선의 발 등)이 있으면 비킨다."""

    def room(f: float) -> float:
        x, y = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        return min((math.dist((x, y), q) for q in others), default=math.inf)

    cands = (0.5, 0.35, 0.65, 0.2, 0.8)
    return next((c for c in cands if room(c) > unit * 1.8), max(cands, key=room))


_DIRS = {
    "n": (0, 1),
    "s": (0, -1),
    "e": (1, 0),
    "w": (-1, 0),
    "ne": (1, 1),
    "nw": (-1, 1),
    "se": (1, -1),
    "sw": (-1, -1),
}


def render_geometry(spec: GeometrySpec, ctx: Ctx, out: Path, width_in: float) -> Path:
    pts = compute_points(spec, ctx)
    xs = [p[0] for p in pts.values()]
    ys = [p[1] for p in pts.values()]
    for c in spec.circles:
        o, r = circle_of(c, pts, ctx)
        xs += [o[0] - r, o[0] + r]
        ys += [o[1] - r, o[1] + r]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    size = max(x1 - x0, y1 - y0, 1e-6)
    pad = 0.12 * size
    fig, ax = new_figure(width_in, width_in * max((y1 - y0 + 2 * pad) / (x1 - x0 + 2 * pad), 0.35))
    ax.set_xlim(x0 - pad, x1 + pad)
    ax.set_ylim(y0 - pad, y1 + pad)
    unit = size / 20  # 표시 크기 기준
    cx, cy = float(np.mean(xs)), float(np.mean(ys))

    if spec.axes:
        arrow(ax, x0 - pad * 0.8, 0, x1 + pad * 0.8, 0)
        arrow(ax, 0, y0 - pad * 0.8, 0, y1 + pad * 0.8)
        ax.text(x1 + pad * 0.8, -unit, "x", ha="center", va="top")
        ax.text(unit, y1 + pad * 0.8, "y", ha="left", va="center")

    for pg in spec.polygons:
        vs = [pts[v] for v in pg.vertices]
        ax.add_patch(
            Polygon(
                vs,
                closed=True,
                fill=pg.shade,
                fc="#cccccc" if pg.shade else "none",
                ec=INK,
                lw=LINE,
            )
        )

    for s in spec.segments:
        a, b = pts[s.a], pts[s.b]
        if s.extend != "none":
            d = np.subtract(b, a)
            n = float(np.hypot(*d)) or 1.0
            far = 3 * size / n
            start = a if s.extend == "ray" else (a[0] - d[0] * far, a[1] - d[1] * far)
            b = (a[0] + d[0] * far, a[1] + d[1] * far)
            a = start
        ax.plot([a[0], b[0]], [a[1], b[1]], ls(s.style), color=INK, lw=LINE)
        ma, mb = pts[s.a], pts[s.b]
        mx, my = (ma[0] + mb[0]) / 2, (ma[1] + mb[1]) / 2
        dx, dy = mb[0] - ma[0], mb[1] - ma[1]
        n = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / n, dx / n
        if (mx - cx) * nx + (my - cy) * ny < 0:  # 라벨은 도형 바깥쪽으로
            nx, ny = -nx, -ny
        for k in range(s.ticks):
            off = (k - (s.ticks - 1) / 2) * unit * 0.5
            tx, ty = mx + dx / n * off, my + dy / n * off
            ax.plot(
                [tx - nx * unit * 0.4, tx + nx * unit * 0.4],
                [ty - ny * unit * 0.4, ty + ny * unit * 0.4],
                color=INK,
                lw=LINE,
            )
        if s.label:
            others = [q for name, q in pts.items() if name not in (s.a, s.b)]
            f = label_fraction(ma, mb, others, unit)
            lx, ly = ma[0] + dx * f, ma[1] + dy * f
            ax.text(
                lx + nx * unit * 1.2,
                ly + ny * unit * 1.2,
                ctx.label(s.label),
                ha="center",
                va="center",
            )

    for c in spec.circles:
        o, r = circle_of(c, pts, ctx)
        ax.add_patch(Circle(o, r, fill=False, ec=INK, lw=LINE, ls=ls(c.style)))

    for a in spec.angles:
        v, pa, pb = pts[a.vertex], pts[a.a], pts[a.b]
        t1 = math.degrees(math.atan2(pa[1] - v[1], pa[0] - v[0]))
        t2 = math.degrees(math.atan2(pb[1] - v[1], pb[0] - v[0]))
        if (t2 - t1) % 360 > 180:
            t1, t2 = t2, t1
        r = unit * 1.6
        if a.right:
            u1 = np.array([math.cos(math.radians(t1)), math.sin(math.radians(t1))]) * r * 0.7
            u2 = np.array([math.cos(math.radians(t2)), math.sin(math.radians(t2))]) * r * 0.7
            sq = [v + u1, v + u1 + u2, v + u2]
            ax.plot([p[0] for p in sq], [p[1] for p in sq], color=INK, lw=LINE * 0.9)
        else:
            ax.add_patch(Arc(v, 2 * r, 2 * r, theta1=t1, theta2=t2, ec=INK, lw=LINE * 0.9))
        if a.label:
            mid = math.radians(t1 + ((t2 - t1) % 360) / 2)
            ax.text(
                v[0] + math.cos(mid) * r * 1.8,
                v[1] + math.sin(mid) * r * 1.8,
                ctx.label(a.label),
                ha="center",
                va="center",
            )

    for p in spec.points:
        x, y = pts[p.name]
        if p.dot:
            ax.plot([x], [y], "o", ms=3, color=INK)
        text = p.name if p.label is None else ctx.label(p.label)
        if not text:
            continue
        if p.pos == "auto":
            dx, dy = x - cx, y - cy
            n = math.hypot(dx, dy) or 1.0
            dx, dy = (dx / n, dy / n) if n > 1e-9 else (0.0, 1.0)
        else:
            dx, dy = _DIRS[p.pos]
            n = math.hypot(dx, dy)
            dx, dy = dx / n, dy / n
        ax.text(
            x + dx * unit * 1.3, y + dy * unit * 1.3, text, ha="center", va="center", fontsize=11
        )
    return save(fig, out)
