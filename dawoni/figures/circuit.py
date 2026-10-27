# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportMissingTypeStubs=false, reportAttributeAccessIssue=false
"""전기회로: 소자를 순서대로 이어 한 바퀴 (거북이 그리기) — schemdraw. 병렬 칸은 갈래 2~4개."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import schemdraw
import schemdraw.elements as elm

from dawoni.figures.common import Ctx, FigureError, korean_font, setup
from dawoni.models.figure import CircuitElement, CircuitSpec, ParallelBlock

_ELEMENTS: dict[str, Any] = {
    "wire": elm.Line,
    "resistor": elm.Resistor,
    "battery": elm.Battery,
    "bulb": elm.Lamp,
    "switch": elm.Switch,
    "capacitor": elm.Capacitor,
    "inductor": elm.Inductor2,
    "ammeter": elm.MeterA,
    "voltmeter": elm.MeterV,
    "diode": elm.Diode,
}
_PERP = {"right": "up", "left": "down", "up": "left", "down": "right"}


def _place(d: schemdraw.Drawing, e: CircuitElement, ctx: Ctx, direction: str | None = None) -> Any:
    el = _ELEMENTS[e.type]()
    el = getattr(el, direction or e.direction)().length(e.length)
    if e.label:
        el = el.label(ctx.label(e.label))
    return d.add(el)


def render_circuit(spec: CircuitSpec, ctx: Ctx, out: Path, width_in: float) -> Path:
    setup()
    schemdraw.config(font=korean_font(), fontsize=11, lw=1.2)
    d = schemdraw.Drawing(show=False)
    start = None
    back = {"up": "down", "down": "up", "left": "right", "right": "left"}
    for part in spec.path:
        if isinstance(part, ParallelBlock):
            node = d.here
            d.add(elm.Dot())
            main_end: Any = None
            for k, br in enumerate(part.branches):
                d.here = node
                if k:
                    d.add(getattr(elm.Line(), _PERP[part.direction])().length(1.6 * k))
                _place(d, br, ctx, part.direction)
                if k:
                    d.add(getattr(elm.Line(), back[_PERP[part.direction]])().length(1.6 * k))
                else:
                    main_end = d.here
            d.here = main_end
            d.add(elm.Dot())
            if start is None:
                start = node
            continue
        el = _place(d, part, ctx)
        if start is None:
            start = el.start
    if start is None:
        raise FigureError("회로에 소자가 없습니다")
    out.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(out), dpi=300)
    _ = width_in  # 회로는 소자 길이로 크기가 정해진다
    return out
