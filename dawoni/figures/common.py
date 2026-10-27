# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
"""렌더러 공통: 한글 폰트, 식·라벨 계산, 그림 만들기·저장.

- pyplot 없이 객체 API(`Figure` + Agg 캔버스)만 쓴다 → 서버 작업 실행기의 여러 스레드에서 안전.
- 한글 라벨: 시스템 폰트(맑은 고딕 → Apple SD Gothic Neo → 나눔고딕 → Noto Sans CJK)를 찾아 쓴다.
  그림은 PNG(픽셀)로 문서에 들어가므로 폰트를 함께 배포하지 않아도 tofu가 생기지 않는다.
- 라벨 안의 `\\( … \\)`는 matplotlib 수식(mathtext)으로, `\\var{식}`은 변수 값으로 바꾼다.
"""

from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from matplotlib import font_manager, rcParams
from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure as MplFigure

from dawoni.export.fill import format_value
from dawoni.mathx.sym import ExprError, evaluate

LINE = 1.2
INK = "#111111"
_FONT_FILES = (
    Path("C:/Windows/Fonts/malgun.ttf"),
    Path("/Applications/Microsoft Word.app/Contents/Resources/DFonts/malgun.ttf"),
)
_FONT_NAMES = (
    "Malgun Gothic",
    "Apple SD Gothic Neo",
    "NanumGothic",
    "Noto Sans CJK KR",
    "Noto Sans KR",
)


class FigureError(ValueError):
    """스펙을 그릴 수 없음 (값 계산 실패, 작도 불가 등)."""


@cache
def korean_font() -> str:
    """쓸 수 있는 한글 폰트 이름 (없으면 DejaVu Sans — 한글이 □로 나올 수 있음)."""
    for f in _FONT_FILES:
        if f.is_file():
            try:
                font_manager.fontManager.addfont(str(f))
            except OSError, RuntimeError:
                continue
    names = {f.name for f in font_manager.fontManager.ttflist}
    for n in _FONT_NAMES:
        if n in names:
            return n
    print("경고: 한글 폰트를 찾지 못했습니다", file=sys.stderr)  # noqa: T201
    return "DejaVu Sans"


def setup() -> None:
    rcParams["font.family"] = [korean_font(), "DejaVu Sans"]
    rcParams["axes.unicode_minus"] = False
    rcParams["mathtext.fontset"] = "cm"
    rcParams["font.size"] = 11


@dataclass
class Ctx:
    """변수 값과 계산 도우미."""

    values: dict[str, float] = field(default_factory=lambda: dict[str, float]())

    def num(self, e: float | str, what: str = "값") -> float:
        if isinstance(e, (int, float)):
            return float(e)
        try:
            return evaluate(e, self.values)
        except ExprError as err:
            raise FigureError(f"{what} '{e}' 계산 불가: {err}") from err

    def label(self, s: str | None) -> str:
        if not s:
            return ""

        def var(m: re.Match[str]) -> str:
            try:
                return format_value(evaluate(m.group(1), self.values))
            except ExprError as err:
                raise FigureError(f"라벨 '{s}' 계산 불가: {err}") from err

        t = re.sub(r"\\var\{([^{}]*)\}", var, s)
        return re.sub(r"\\\((.+?)\\\)", lambda m: f"${m.group(1)}$", t)


def new_figure(width_in: float, height_in: float) -> tuple[MplFigure, Axes]:
    setup()
    fig = MplFigure(figsize=(width_in, height_in))
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(1, 1, 1)
    ax.set_aspect("equal")
    ax.axis("off")
    return fig, ax


def save(fig: MplFigure, out: Path, dpi: int = 300) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out), dpi=dpi, bbox_inches="tight", pad_inches=0.06, facecolor="white")
    return out


def arrow(
    ax: Axes, x0: float, y0: float, x1: float, y1: float, *, label: str = "", lw: float = LINE,
    color: str = INK, offset: tuple[float, float] = (0, 0), head: float = 0.18,
) -> None:  # fmt: skip
    ax.annotate(
        "", xy=(x1, y1), xytext=(x0, y0),
        arrowprops={"arrowstyle": f"-|>,head_length={head * 3},head_width={head * 1.5}",
                    "lw": lw, "color": color, "shrinkA": 0, "shrinkB": 0},
    )  # fmt: skip
    if label:
        ax.text(x1 + offset[0], y1 + offset[1], label, ha="center", va="center")


def rad(deg: float) -> float:
    return math.radians(deg)


_STYLE = {"solid": "-", "dashed": "--", "dotted": ":"}


def ls(style: str) -> str:
    return _STYLE.get(style, "-")
