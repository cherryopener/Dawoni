"""문항.

- `ItemContent`: 문항의 내용. AI가 출력하는 범위이며 JSON 스키마로 강제된다.
- `Item`: 저장되는 문항. 내용 + ID·리비전·작성자·AI 기록 등 앱이 붙이는 정보.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from dawoni.models.base import DawoniModel, new_id, utcnow
from dawoni.models.content import Block, Run, figure_ids_in, iter_runs, placeholders_in
from dawoni.models.figure import Figure, SourceBox

ItemType = Literal["multiple_choice", "short_answer", "essay"]
"""선택형(5지선다 등) / 단답형 / 서술형"""

VariantLevel = Literal["L1", "L2", "L3", "L4"]
"""L1 수치 변형, L2 맥락 변형, L3 조건 확장, L4 역발상"""

ReviewStatus = Literal["draft", "needs_review", "approved"]

ChoiceLayout = Literal["auto", "row", "grid_3_2", "grid_2", "column"]
"""선지 배치: 자동 / 한 줄 5개 / 3+2 / 2열 / 세로 1열"""

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_VAR_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

# sympy에서 의미가 정해진 이름은 변수로 쓰지 않는다 (E=자연상수, I=허수단위 …)
RESERVED_NAMES = frozenset(
    {
        "pi", "e", "E", "I", "oo", "S", "N", "O", "Q",
        "sin", "cos", "tan", "cot", "sec", "csc",
        "asin", "acos", "atan", "sinh", "cosh", "tanh",
        "exp", "log", "ln", "sqrt", "Abs", "abs", "floor", "ceiling",
        "Min", "Max", "min", "max", "round", "deg", "rad", "x",
    }
)  # fmt: skip
"""`x`는 그래프 함수식의 독립변수로 예약."""

_EXPR_FUNCTIONS = RESERVED_NAMES - {"x"}


class Variable(DawoniModel):
    value: float
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = Field(default=None, gt=0)
    choices: list[float] | None = Field(default=None, min_length=1)
    decimals: int | None = Field(default=None, ge=0, le=6, description="표시 소수 자릿수")
    description: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Variable:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"min({self.min}) > max({self.max})")
        if self.min is not None and self.value < self.min:
            raise ValueError(f"value({self.value}) < min({self.min})")
        if self.max is not None and self.value > self.max:
            raise ValueError(f"value({self.value}) > max({self.max})")
        if self.choices is not None and self.value not in self.choices:
            raise ValueError(f"value({self.value})가 choices에 없음")
        return self


class Choice(DawoniModel):
    """선지 하나.

    그림 선지(그래프 등)는 동형문제에서 **변형하지 않는다**. 대신 문제 조건을 바꿔 다른 선지가
    정답이 되게 한다. 그러려면 각 그림 선지가 무엇을 나타내는지 `note`로 알아야 한다.
    """

    label: int = Field(ge=1, le=9, description="①=1, ②=2 …")
    runs: list[Run] = Field(default_factory=lambda: list[Run]())
    figure_id: str | None = None
    note: str | None = Field(
        default=None,
        description="그림 선지의 내용 설명 (예: 'y=2^x 그래프, (0,1)을 지나고 증가'). "
        "그림만 있는 선지에는 필수",
    )

    @property
    def is_label_only(self) -> bool:
        return not self.runs and self.figure_id is None


class ChoiceAnswer(DawoniModel):
    kind: Literal["choice"]
    labels: list[int] = Field(min_length=1, description="정답 선지 번호 (복수 정답 가능)")


class ValueAnswer(DawoniModel):
    kind: Literal["value"]
    latex: str
    expr: str | None = Field(default=None, description="변수로 정답을 계산하는 sympy 식")
    unit: str | None = None


class TextAnswer(DawoniModel):
    kind: Literal["text"]
    runs: list[Run] = Field(min_length=1)


Answer = Annotated[ChoiceAnswer | ValueAnswer | TextAnswer, Field(discriminator="kind")]


class SourceRef(DawoniModel):
    source_id: str = Field(pattern=r"^[0-9a-f]{64}$", description="원본 파일 SHA-256")
    number_label: str | None = Field(default=None, description="원본에서의 문항 번호 (예: 17)")
    boxes: list[SourceBox] = Field(
        default_factory=lambda: list[SourceBox](),
        description="원본 페이지 위 문항 영역 (페이지를 넘어가면 여러 개)",
    )


def _expr_identifiers(expr: str) -> set[str]:
    return {name for name in _IDENT.findall(expr) if name not in _EXPR_FUNCTIONS}


class ItemContent(DawoniModel):
    item_type: ItemType
    subject: str | None = None
    unit: str | None = Field(default=None, description="세부 단원")
    number_label: str | None = None
    points: float | None = Field(default=None, gt=0)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    stem: list[Block] = Field(min_length=1)
    choices: list[Choice] = Field(default_factory=lambda: list[Choice]())
    choice_layout: ChoiceLayout = "auto"
    choices_figure_id: str | None = Field(
        default=None,
        description="①~⑤가 그림 한 장에 함께 들어 있을 때 그 그림. 이때 선지는 번호와 note만",
    )
    answer: Answer | None = None
    solution: list[Block] = Field(default_factory=lambda: list[Block]())
    variables: dict[str, Variable] = Field(default_factory=lambda: dict[str, Variable]())
    constraints: list[str] = Field(
        default_factory=lambda: list[str](),
        description='변수 조합이 만족해야 할 sympy 부등식 (예: "mu < tan(theta*pi/180)")',
    )
    figures: list[Figure] = Field(default_factory=lambda: list[Figure]())
    tags: list[str] = Field(default_factory=lambda: list[str]())

    @field_validator("variables")
    @classmethod
    def _variable_names(cls, v: dict[str, Variable]) -> dict[str, Variable]:
        for name in v:
            if not _VAR_NAME.match(name):
                raise ValueError(f"변수 이름 '{name}'은 영문자로 시작하는 식별자여야 함")
            if name in RESERVED_NAMES:
                raise ValueError(f"변수 이름 '{name}'은 예약어 (다른 이름 사용)")
        return v

    @model_validator(mode="after")
    def _structure(self) -> ItemContent:
        errors: list[str] = []

        # 선지
        if self.item_type == "multiple_choice":
            if len(self.choices) < 2:
                errors.append("선택형 문항은 선지가 2개 이상 필요")
            labels = [c.label for c in self.choices]
            if labels != list(range(1, len(labels) + 1)):
                errors.append(f"선지 번호는 1부터 차례대로여야 함: {labels}")
        elif self.choices:
            errors.append(f"{self.item_type} 문항에는 선지가 없어야 함")
        errors += self._check_figure_choices()

        # 정답
        match self.answer:
            case ChoiceAnswer(labels=ans):
                if self.item_type != "multiple_choice":
                    errors.append("선지 번호 정답은 선택형 문항에만 사용")
                bad = sorted(set(ans) - {c.label for c in self.choices})
                if bad:
                    errors.append(f"정답 번호 {bad}에 해당하는 선지가 없음")
            case _:
                pass

        # 그림 참조
        fig_ids = [f.id for f in self.figures]
        if len(fig_ids) != len(set(fig_ids)):
            errors.append(f"그림 ID 중복: {fig_ids}")
        referenced = figure_ids_in(self.stem) + figure_ids_in(self.solution)
        referenced += [c.figure_id for c in self.choices if c.figure_id]
        if self.choices_figure_id:
            referenced.append(self.choices_figure_id)
        missing = sorted(set(referenced) - set(fig_ids))
        if missing:
            errors.append(f"정의되지 않은 그림 참조: {missing}")

        # 변수 자리표시
        known = set(self.variables)
        used: set[str] = set()
        all_runs = iter_runs(self.stem) + iter_runs(self.solution)
        for c in self.choices:
            all_runs += c.runs
        for r in all_runs:
            for ph in placeholders_in(r.v):
                used |= _expr_identifiers(ph)
        for expr in self.constraints:
            used |= _expr_identifiers(expr)
        if isinstance(self.answer, ValueAnswer) and self.answer.expr:
            used |= _expr_identifiers(self.answer.expr)
        undefined = sorted(used - known)
        if undefined:
            errors.append(f"정의되지 않은 변수: {undefined}")

        if errors:
            raise ValueError("; ".join(errors))
        return self

    @property
    def has_figure_choices(self) -> bool:
        """그림 선지 문항인가. 그렇다면 동형문제에서 선지를 고정하고 문제만 바꾼다."""
        return self.choices_figure_id is not None or any(c.figure_id for c in self.choices)

    def _check_figure_choices(self) -> list[str]:
        """그림 선지 규칙. 오류가 가장 적은 방식으로 제한한다.

        - 선지 그림은 원본 크롭(raster)을 그대로 쓴다. 다시 그리지 않으므로 재현 오류가 없다.
          (spec을 함께 둘 수는 있지만 의미 기록·정답 검증용일 뿐 출력에는 쓰지 않는다.)
        - 그림 선지 문항의 선지에는 변수 자리표시를 쓰지 않는다 (동형문제에서도 고정).
        - 그림만 있는 선지는 무엇을 그린 것인지 `note`가 있어야 한다.
        - 한 장짜리 선지 그림(choices_figure_id)을 쓰면 모든 선지는 번호+note만 가진다.
        """
        errors: list[str] = []
        if self.choices_figure_id and self.item_type != "multiple_choice":
            errors.append("choices_figure_id는 선택형 문항에만 사용")

        for c in self.choices:
            if self.choices_figure_id:
                if not c.is_label_only:
                    errors.append(
                        f"선지 {c.label}: 선지 그림이 한 장(choices_figure_id)이면 "
                        "선지에는 runs·figure_id를 넣지 않음"
                    )
            elif c.is_label_only:
                errors.append(f"선지 {c.label}: 내용(runs)이나 그림(figure_id)이 필요")
            if not c.runs and not c.note:
                errors.append(f"선지 {c.label}: 그림 선지에는 내용 설명(note)이 필요")

        if self.has_figure_choices:
            for c in self.choices:
                if any(placeholders_in(r.v) for r in c.runs):
                    errors.append(f"선지 {c.label}: 그림 선지 문항의 선지에는 변수 자리표시 금지")
            choice_fig_ids = {c.figure_id for c in self.choices if c.figure_id}
            if self.choices_figure_id:
                choice_fig_ids.add(self.choices_figure_id)
            for f in self.figures:
                if f.id in choice_fig_ids and f.mode != "raster":
                    errors.append(f"{f.id}: 선지 그림은 원본 그대로(mode=raster) 사용")
        return errors


class AgentStamp(DawoniModel):
    """이 문항을 만든/고친 AI 호출 기록."""

    agent: str
    model: str | None = None
    task: str
    template_version: str
    at: datetime = Field(default_factory=utcnow)


class Item(ItemContent):
    schema_version: int = 1
    id: str = Field(default_factory=lambda: new_id("it"))
    source: SourceRef | None = None
    parent_id: str | None = Field(default=None, description="동형문제의 원본 문항 ID")
    variant_level: VariantLevel | None = None
    status: ReviewStatus = "draft"
    revision: int = Field(default=1, ge=1, description="저장할 때마다 +1 (팀 공유 충돌 감지)")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    created_by: str | None = None
    updated_by: str | None = None
    ai: list[AgentStamp] = Field(default_factory=lambda: list[AgentStamp]())

    @model_validator(mode="after")
    def _variant(self) -> Item:
        if self.variant_level is not None and self.parent_id is None:
            raise ValueError("동형문제(variant_level)에는 parent_id가 필요")
        return self

    @classmethod
    def from_content(cls, content: ItemContent, **meta: object) -> Item:
        return cls.model_validate({**content.model_dump(), **meta})
