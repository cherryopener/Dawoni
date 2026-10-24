"""그림: 원본 크롭 + 파라미터 스펙.

- `mode="parametric"`: 스펙에서 다시 그린 그림을 출력에 쓴다. 원본은 검수 비교용으로 보존.
- `mode="raster"`: 원본 크롭을 그대로 쓴다 (화학·생물·지구과학 사진·도판 등).

스펙의 수치 필드는 `Expr`: 숫자 또는 문항 변수를 쓴 식 문자열(`"theta"`, `"2*m"`).
라벨 필드는 텍스트이며 `{{식}}` 자리표시를 쓸 수 있다(`"{{m}} kg"`).

M0에서는 스펙 종류의 틀과 대표 2종(빗면, 함수 그래프)만 둔다. 나머지 종류(기하, 도르래,
충돌, 용수철, 포물선, 전기장·자기장, 회로, 운동 그래프)는 M4에서 렌더러와 함께 추가한다.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from dawoni.models.base import DawoniModel

Expr = Annotated[
    float | str,
    Field(description='숫자, 또는 문항 변수를 쓴 sympy 식 문자열 (예: 30, "theta", "2*m")'),
]


class ExprRange(DawoniModel):
    min: Expr
    max: Expr


LineStyle = Literal["solid", "dashed", "dotted"]


# ---------------------------------------------------------------- 빗면


class ForceArrow(DawoniModel):
    force: Literal["gravity", "normal", "friction", "tension", "applied", "acceleration"]
    label: str | None = None
    angle_deg: Expr | None = Field(
        default=None, description="applied 힘의 방향(빗면 기준 각). 나머지는 자동"
    )


class InclineSpec(DawoniModel):
    kind: Literal["incline"]
    angle_deg: Expr
    angle_label: str | None = "θ"
    flip: bool = Field(default=False, description="True면 높은 쪽이 왼쪽")
    block_label: str | None = None
    block_position: float = Field(default=0.5, ge=0, le=1, description="빗면 아래(0)~위(1)")
    friction_label: str | None = None
    pulley_at_top: bool = False
    hanging_block_label: str | None = Field(
        default=None, description="pulley_at_top일 때 매달린 물체 라벨"
    )
    forces: list[ForceArrow] = Field(default_factory=lambda: list[ForceArrow]())


# ---------------------------------------------------------------- 함수 그래프


class PlotFunction(DawoniModel):
    expr: str = Field(description='x에 대한 sympy 식. 문항 변수 사용 가능 (예: "a*x**2 - 4")')
    domain: ExprRange | None = None
    label: str | None = None
    style: LineStyle = "solid"


class MarkPoint(DawoniModel):
    x: Expr
    y: Expr | None = Field(default=None, description="없으면 on_function의 함수값")
    on_function: int | None = Field(default=None, ge=0, description="functions 목록의 번호")
    label: str | None = None
    filled: bool = True
    guide_lines: bool = Field(default=False, description="축까지 점선")

    @model_validator(mode="after")
    def _y_source(self) -> MarkPoint:
        if self.y is None and self.on_function is None:
            raise ValueError("y 또는 on_function 중 하나는 필요")
        return self


class Asymptote(DawoniModel):
    axis: Literal["vertical", "horizontal"]
    value: Expr
    label: str | None = None


class FunctionGraphSpec(DawoniModel):
    kind: Literal["function_graph"]
    functions: list[PlotFunction] = Field(min_length=1)
    x_range: ExprRange
    y_range: ExprRange
    points: list[MarkPoint] = Field(default_factory=lambda: list[MarkPoint]())
    asymptotes: list[Asymptote] = Field(default_factory=lambda: list[Asymptote]())
    x_label: str = "x"
    y_label: str = "y"
    origin_label: str | None = "O"
    show_ticks: bool = False
    """수능형 그래프는 눈금 없이 표시점만 쓰는 경우가 많다."""

    @model_validator(mode="after")
    def _function_refs(self) -> FunctionGraphSpec:
        n = len(self.functions)
        for p in self.points:
            if p.on_function is not None and p.on_function >= n:
                raise ValueError(f"on_function={p.on_function} 인데 함수는 {n}개")
        return self


FigureSpec = Annotated[InclineSpec | FunctionGraphSpec, Field(discriminator="kind")]


# ---------------------------------------------------------------- 그림 레코드


class SourceBox(DawoniModel):
    """원본 PDF 페이지 위의 영역 (PDF 포인트, 왼쪽 위 원점)."""

    page: int = Field(ge=1)
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def _ordered(self) -> SourceBox:
        if not (self.x0 < self.x1 and self.y0 < self.y1):
            raise ValueError("좌표 순서 오류: x0<x1, y0<y1 이어야 함")
        return self


class Figure(DawoniModel):
    id: str = Field(pattern=r"^F[1-9][0-9]*$", description="문항 안에서의 그림 번호 F1, F2…")
    mode: Literal["parametric", "raster"]
    orig: str | None = Field(default=None, description="문항 폴더 기준 원본 크롭 경로")
    spec: FigureSpec | None = None
    caption: str | None = None
    source_box: SourceBox | None = None

    @model_validator(mode="after")
    def _mode_requirements(self) -> Figure:
        if self.mode == "parametric" and self.spec is None:
            raise ValueError(f"{self.id}: parametric 그림에는 spec이 필요")
        if self.mode == "raster" and self.orig is None:
            raise ValueError(f"{self.id}: raster 그림에는 원본(orig)이 필요")
        return self
