"""그림: 원본 크롭 + 파라미터 스펙.

- `mode="parametric"`: 스펙에서 다시 그린 그림을 출력에 쓴다. 원본은 검수 비교용으로 보존.
- `mode="raster"`: 원본 크롭을 그대로 쓴다 (화학·생물·지구과학 사진·도판 등).

스펙의 수치 필드는 `Expr`: 숫자 또는 문항 변수를 쓴 식 문자열(`"theta"`, `"2*m"`).
라벨 필드는 텍스트이며 `\\var{식}` 자리표시를 쓸 수 있다(`"\\var{m} kg"`).

종류 (M10): 함수 그래프, 운동 그래프, 기하(작도식 점), 빗면, 용수철, 포물선 운동, 도르래,
충돌, 전기장·자기장, 전기회로. AI는 이 JSON만 준다 — 코드를 주지 않는다 (PLAN §8-3 안전 원칙).
라벨 안의 수식은 `\\( … \\)`로 감싼다 (예: `"\\(\\theta\\)"`).
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, Field, model_validator

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


class ShadeBetween(DawoniModel):
    function: int = Field(ge=0)
    other: int | None = Field(default=None, ge=0, description="다른 함수 번호 (없으면 x축)")
    domain: ExprRange


class FunctionGraphSpec(DawoniModel):
    kind: Literal["function_graph"]
    functions: list[PlotFunction] = Field(min_length=1)
    shade: list[ShadeBetween] = Field(default_factory=lambda: list[ShadeBetween]())
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


# ---------------------------------------------------------------- 운동 그래프


class SeriesPoint(DawoniModel):
    t: Expr
    y: Expr


class MotionSeries(DawoniModel):
    points: list[SeriesPoint] = Field(min_length=2, description="꺾은선 꼭짓점 (시간 순)")
    label: str | None = None
    style: LineStyle = "solid"


class AxisMark(DawoniModel):
    value: Expr
    label: str | None = Field(default=None, description="축에 적을 글 (없으면 값)")
    guide: bool = Field(default=True, description="그래프까지 점선")


class ShadeInterval(DawoniModel):
    series: int = Field(default=0, ge=0)
    t_from: Expr
    t_to: Expr


class MotionGraphSpec(DawoniModel):
    kind: Literal["motion_graph"]
    series: list[MotionSeries] = Field(min_length=1)
    x_label: str = "t"
    y_label: str = "v"
    t_marks: list[AxisMark] = Field(default_factory=lambda: list[AxisMark]())
    y_marks: list[AxisMark] = Field(default_factory=lambda: list[AxisMark]())
    shade: list[ShadeInterval] = Field(default_factory=lambda: list[ShadeInterval]())
    origin_label: str | None = "O"


# ---------------------------------------------------------------- 기하

Pos = Literal["auto", "n", "s", "e", "w", "ne", "nw", "se", "sw"]


class Divide(DawoniModel):
    a: str
    b: str
    m: Expr
    n: Expr


class OnCircle(DawoniModel):
    center: str
    radius: Expr
    angle_deg: Expr


class Polar(DawoniModel):
    origin: str
    length: Expr
    angle_deg: Expr


class GeoPoint(DawoniModel):
    """점 하나: 좌표로 주거나, 다른 점으로 **작도**한다 (하나만)."""

    name: str = Field(min_length=1, max_length=8)
    label: str | None = Field(
        default=None, description="그림에 적을 이름 (없으면 name, 빈 문자열이면 안 적음)"
    )
    pos: Pos = "auto"
    at: list[Expr] | None = Field(default=None, min_length=2, max_length=2, description="[x, y]")
    midpoint: list[str] | None = Field(default=None, min_length=2, max_length=2)
    divide: Divide | None = Field(default=None, description="선분 ab를 m:n으로 내분")
    intersect: list[str] | None = Field(
        default=None, min_length=4, max_length=4, description="직선 AB와 CD의 교점 [A, B, C, D]"
    )
    foot: list[str] | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        description="점 P에서 직선 AB에 내린 수선의 발 [P, A, B]",
    )
    on_circle: OnCircle | None = None
    polar: Polar | None = None
    incenter: list[str] | None = Field(
        default=None, min_length=3, max_length=3, description="삼각형 [A, B, C]의 내심"
    )
    circumcenter: list[str] | None = Field(
        default=None, min_length=3, max_length=3, description="외심"
    )
    centroid: list[str] | None = Field(
        default=None, min_length=3, max_length=3, description="무게중심"
    )
    dot: bool = True

    @model_validator(mode="after")
    def _one(self) -> GeoPoint:
        given = [
            self.at,
            self.midpoint,
            self.divide,
            self.intersect,
            self.foot,
            self.on_circle,
            self.polar,
            self.incenter,
            self.circumcenter,
            self.centroid,
        ]
        if sum(x is not None for x in given) != 1:
            raise ValueError(f"점 {self.name}: 좌표(at) 또는 작도 방법 하나만")
        return self


class GeoSegment(DawoniModel):
    a: str
    b: str
    label: str | None = None
    style: LineStyle = "solid"
    ticks: int = Field(default=0, ge=0, le=3, description="같은 길이 표시 눈금 수")
    extend: Literal["none", "line", "ray"] = Field(
        default="none", description="직선·반직선으로 늘리기"
    )


class GeoCircle(DawoniModel):
    """원: 중심 + (반지름 또는 지나는 점), 또는 삼각형의 내접원·외접원 (코드가 계산)."""

    center: str | None = None
    radius: Expr | None = None
    through: str | None = None
    incircle: list[str] | None = Field(default=None, min_length=3, max_length=3)
    circumcircle: list[str] | None = Field(default=None, min_length=3, max_length=3)
    style: LineStyle = "solid"

    @model_validator(mode="after")
    def _r(self) -> GeoCircle:
        tri = (self.incircle is not None) + (self.circumcircle is not None)
        by_center = self.center is not None and (self.radius is None) != (self.through is None)
        if tri + by_center != 1 or (tri and (self.center or self.radius or self.through)):
            raise ValueError(
                "원: center + (radius 또는 through), 또는 incircle·circumcircle 중 하나"
            )
        return self


class GeoAngle(DawoniModel):
    a: str
    vertex: str
    b: str
    label: str | None = None
    right: bool = False


class GeoPolygon(DawoniModel):
    vertices: list[str] = Field(min_length=3)
    shade: bool = False


class GeometrySpec(DawoniModel):
    kind: Literal["geometry"]
    points: list[GeoPoint] = Field(min_length=1)
    segments: list[GeoSegment] = Field(default_factory=lambda: list[GeoSegment]())
    circles: list[GeoCircle] = Field(default_factory=lambda: list[GeoCircle]())
    angles: list[GeoAngle] = Field(default_factory=lambda: list[GeoAngle]())
    polygons: list[GeoPolygon] = Field(default_factory=lambda: list[GeoPolygon]())
    axes: bool = Field(default=False, description="좌표축 그리기")

    @model_validator(mode="after")
    def _refs(self) -> GeometrySpec:
        names = [p.name for p in self.points]
        if len(names) != len(set(names)):
            raise ValueError(f"점 이름 중복: {names}")
        known: set[str] = set()
        for p in self.points:  # 작도는 앞에서 정의한 점만 쓴다
            used: list[str] = []
            if p.midpoint:
                used += list(p.midpoint)
            if p.divide:
                used += [p.divide.a, p.divide.b]
            if p.intersect:
                used += list(p.intersect)
            if p.foot:
                used += list(p.foot)
            if p.on_circle:
                used.append(p.on_circle.center)
            if p.polar:
                used.append(p.polar.origin)
            for tri in (p.incenter, p.circumcenter, p.centroid):
                if tri:
                    used += tri
            bad = sorted(set(used) - known)
            if bad:
                raise ValueError(f"점 {p.name}: 앞에서 정의하지 않은 점 {bad}")
            known.add(p.name)
        refs: list[str] = []
        for s in self.segments:
            refs += [s.a, s.b]
        for c in self.circles:
            refs += (
                [x for x in (c.center, c.through) if x]
                + (c.incircle or [])
                + (c.circumcircle or [])
            )
        for a in self.angles:
            refs += [a.a, a.vertex, a.b]
        for pg in self.polygons:
            refs += pg.vertices
        bad = sorted(set(refs) - known)
        if bad:
            raise ValueError(f"정의되지 않은 점: {bad}")
        return self


# ---------------------------------------------------------------- 역학: 용수철·포물선·도르래·충돌


class SpringSpec(DawoniModel):
    kind: Literal["spring"]
    orientation: Literal["horizontal", "vertical"] = "horizontal"
    natural_length: Expr = 3
    stretch: Expr = Field(
        default=0, description="늘어난(+)·줄어든(−) 길이 (natural_length와 같은 단위)"
    )
    spring_label: str | None = "k"
    block_label: str | None = None
    show_equilibrium: bool = True
    displacement_label: str | None = Field(default=None, description="변위 화살표 라벨 (예: x)")
    friction: bool = False


class ProjectileSpec(DawoniModel):
    kind: Literal["projectile"]
    v0: Expr
    angle_deg: Expr
    height: Expr = 0
    g: Expr = 10
    v0_label: str | None = Field(default="\\(v_0\\)")
    angle_label: str | None = "\\(\\theta\\)"
    mark_top: str | None = Field(default=None, description="최고점 라벨")
    mark_landing: str | None = Field(default=None, description="떨어진 점 라벨")
    height_label: str | None = None


class HangingMass(DawoniModel):
    label: str
    size: float = Field(default=1.0, gt=0, le=2, description="상대 크기")


class PulleySpec(DawoniModel):
    kind: Literal["pulley"]
    layout: Literal["atwood", "table"] = Field(
        description="atwood: 고정 도르래 양쪽에 물체 / table: 책상 위 물체 + 모서리 도르래"
    )
    left: HangingMass
    right: HangingMass
    friction_label: str | None = None
    arrow_label: str | None = Field(
        default=None, description="가속도 등 화살표 라벨 (왼쪽→오른쪽 방향)"
    )


class Body(DawoniModel):
    label: str
    x: Expr = Field(description="0~10 사이 위치")
    width: float = Field(default=1.2, gt=0)
    velocity: Expr | None = Field(default=None, description="+는 오른쪽, −는 왼쪽, 0이면 정지")
    velocity_label: str | None = None


class CollisionPanel(DawoniModel):
    title: str | None = None
    bodies: list[Body] = Field(min_length=1)


class CollisionSpec(DawoniModel):
    kind: Literal["collision"]
    panels: list[CollisionPanel] = Field(min_length=1, max_length=3)


# ---------------------------------------------------------------- 전기장·자기장


class Charge(DawoniModel):
    x: Expr
    y: Expr
    sign: Literal["+", "-"] = "+"
    label: str | None = None
    velocity_angle_deg: Expr | None = None
    velocity_label: str | None = None


class Wire(DawoniModel):
    x1: Expr
    y1: Expr
    x2: Expr
    y2: Expr
    current_label: str | None = "I"
    current_toward: Literal["1", "2"] = Field(default="2", description="전류가 흐르는 쪽 끝")


class LoopConductor(DawoniModel):
    x0: Expr
    y0: Expr
    x1: Expr
    y1: Expr
    label: str | None = None
    velocity_angle_deg: Expr | None = None
    velocity_label: str | None = None


class FieldRegion(DawoniModel):
    x0: Expr = 0
    y0: Expr = 0
    x1: Expr = 6
    y1: Expr = 4
    direction: Literal["into", "out", "up", "down", "left", "right"]
    label: str | None = "B"


class FieldSpec(DawoniModel):
    kind: Literal["field"]
    regions: list[FieldRegion] = Field(min_length=1)
    charges: list[Charge] = Field(default_factory=lambda: list[Charge]())
    wires: list[Wire] = Field(default_factory=lambda: list[Wire]())
    loops: list[LoopConductor] = Field(default_factory=lambda: list[LoopConductor]())


# ---------------------------------------------------------------- 전기회로

ElementType = Literal[
    "wire", "resistor", "battery", "bulb", "switch", "capacitor", "inductor", "ammeter",
    "voltmeter", "diode",
]  # fmt: skip
Direction = Literal["right", "left", "up", "down"]


class CircuitElement(DawoniModel):
    type: ElementType
    label: str | None = None
    direction: Direction = "right"
    length: float = Field(default=3.0, gt=0, le=10)


class ParallelBlock(DawoniModel):
    """다음 칸에서 갈래 여러 개로 나뉘었다가 다시 합쳐지는 부분 (갈래마다 소자 하나)."""

    type: Literal["parallel"]
    branches: list[CircuitElement] = Field(min_length=2, max_length=4)
    direction: Direction = "right"
    length: float = Field(default=3.0, gt=0, le=10)


class CircuitSpec(DawoniModel):
    kind: Literal["circuit"]
    path: list[CircuitElement | ParallelBlock] = Field(
        min_length=3, description="출발점에서 한 바퀴 돌아 제자리로 오는 순서 (거북이 그리기)"
    )


FigureSpec = Annotated[
    InclineSpec
    | FunctionGraphSpec
    | MotionGraphSpec
    | GeometrySpec
    | SpringSpec
    | ProjectileSpec
    | PulleySpec
    | CollisionSpec
    | FieldSpec
    | CircuitSpec,
    Field(discriminator="kind"),
]
FIGURE_KINDS = (
    "incline", "function_graph", "motion_graph", "geometry", "spring", "projectile", "pulley",
    "collision", "field", "circuit",
)  # fmt: skip


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


_PLACEHOLDER = re.compile(r"\\var\{([^{}]*)\}")


def _flat_args(t: object) -> set[object]:
    args = get_args(t)
    if not args:
        return {t}
    out: set[object] = set()
    for a in args:
        out |= _flat_args(a)
    return out


def _is_expr_field(annotation: object) -> bool:
    """`Expr`(숫자 또는 식) 타입 필드인가 — float와 str을 함께 받는 필드."""
    flat = _flat_args(annotation)
    return float in flat and str in flat


def spec_expressions(spec: BaseModel) -> list[tuple[str, bool]]:
    """스펙 안의 식들: (식, 함수식인가). `Expr` 필드의 문자열, 함수식(`expr`), 글자 필드 안의
    `\\var{식}`. 점 이름·선택지 같은 글자 필드는 식이 아니다 (필드 타입으로 구별)."""
    out: list[tuple[str, bool]] = []

    def walk(m: BaseModel) -> None:
        for name, info in type(m).model_fields.items():
            v: object = getattr(m, name)
            expr_field = _is_expr_field(info.annotation)
            items: list[object] = list(v) if isinstance(v, list | tuple) else [v]  # pyright: ignore[reportUnknownArgumentType]
            for x in items:
                if isinstance(x, BaseModel):
                    walk(x)
                elif isinstance(x, str):
                    if name == "expr":
                        out.append((x, True))
                    elif expr_field:
                        out.append((x, False))
                    else:
                        out.extend((ph, False) for ph in _PLACEHOLDER.findall(x))

    walk(spec)
    return out
