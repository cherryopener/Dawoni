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
    label: int = Field(ge=1, le=9, description="①=1, ②=2 …")
    runs: list[Run] = Field(default_factory=lambda: list[Run]())
    figure_id: str | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> Choice:
        if not self.runs and self.figure_id is None:
            raise ValueError(f"선지 {self.label}: 내용(runs)이나 그림(figure_id)이 필요")
        return self


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
