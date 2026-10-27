"""강사 해설지(모범 답안): 소문항별 답·해설·부분 점수 표기.

강사는 서술형 채점 전에 늘 해설지에 "무엇을 쓰면 몇 점"을 적어 둔다. 루브릭은 이것을
기준으로 만들고, AI 독립 풀이는 해설지가 놓친 다른 정답·풀이를 찾는 데 쓴다.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from dawoni.models.base import DawoniModel

CreditPolicy = Literal["listed", "none", "range", "unspecified"]


class KeyCredit(DawoniModel):
    """부분 점수 표기 하나: "[부분 점수: θ 2점 / T₂ 3점]"의 "θ 2점"."""

    what: str = Field(min_length=1, description="해설지에 적힌 대상 그대로 (예: θ, 과정, 답, (가))")
    points: float = Field(gt=0)


class KeyPart(DawoniModel):
    label: str = Field(
        description="소문항 표시 ([자료]에 주어진 그대로). 소문항이 없으면 빈 문자열"
    )
    answer: str | None = Field(
        default=None, description="해설지의 최종 답 (글은 그대로, 식은 \\( \\)로 감쌈)"
    )
    answer_expr: str | None = Field(
        default=None, description="답이 값·식이면 sympy로 읽을 수 있는 식 (예: (n+1)*R*cos(alpha))"
    )
    solution: str = Field(default="", description="해설 본문 (적힌 그대로, 식은 \\( \\)로 감쌈)")
    credit_policy: CreditPolicy = Field(
        description="listed=항목별 점수가 적혀 있음, none='부분 점수 없음'(맞으면 만점·아니면 0), "
        "range='1~5점'처럼 범위만, unspecified=표기 없음"
    )
    credits: list[KeyCredit] = Field(
        default_factory=lambda: list[KeyCredit](), description="listed일 때 항목별 점수"
    )
    credit_min: float | None = Field(default=None, ge=0, description="range일 때 최소 점수")
    credit_max: float | None = Field(default=None, gt=0, description="range일 때 최대 점수")
    zero_if: list[str] = Field(
        default_factory=lambda: list[str](),
        description="해설지에 적힌 0점 조건 (예: '밝기 표현 없으면 0점')",
    )
    credit_text: str | None = Field(
        default=None,
        description="해설지의 부분 점수 표기 원문 그대로 (예: '[부분 점수: 과정 4점/ 답 2점]')",
    )

    @model_validator(mode="after")
    def _policy(self) -> KeyPart:
        if self.credit_policy == "listed" and not self.credits:
            raise ValueError(f"소문항 '{self.label}': listed인데 credits가 비어 있음")
        if self.credit_policy != "listed" and self.credits:
            raise ValueError(f"소문항 '{self.label}': credits는 listed일 때만")
        return self

    @property
    def credit_total(self) -> float:
        return sum(c.points for c in self.credits)


_NUMBER_WRAP = re.compile(r"\[?\s*(?:문항\s*)?(.+?)\s*(?:번)?\s*\]?")


class KeyQuestion(DawoniModel):
    number: str = Field(description="문항 번호만 (예: 1, 서술형 2)")
    parts: list[KeyPart] = Field(min_length=1)

    @field_validator("number")
    @classmethod
    def _bare(cls, v: str) -> str:
        """실측: "[문항 1]"처럼 자료의 머리 표기를 그대로 옮김 — 번호만 남긴다."""
        m = _NUMBER_WRAP.fullmatch(v.strip())
        return m.group(1) if m else v.strip()

    notes: list[str] = Field(
        default_factory=lambda: list[str](), description="해설지에 대해 강사가 알아야 할 점"
    )


class KeyExtraction(DawoniModel):
    """AI 출력: 해설지 쪽에서 읽은 문항별 모범 답안."""

    questions: list[KeyQuestion] = Field(default_factory=lambda: list[KeyQuestion]())


class AnswerKey(DawoniModel):
    question_number: str
    parts: list[KeyPart] = Field(min_length=1)
    source: str = Field(description="어느 파일 몇 쪽에서 읽었나")
    pages: list[str] = Field(
        default_factory=lambda: list[str](), description="해설지 쪽 이미지 (시험 폴더 기준)"
    )
    confirmed: bool = False
    """강사가 원본과 비교해 확인함 (AI가 수식의 숫자를 바꿔 읽은 실측 사례가 있다)."""
    confirmed_by: str | None = None
    doubtful: list[str] = Field(
        default_factory=lambda: list[str](),
        description="두 번 읽은 결과가 달랐던 소문항 — 특히 원본과 비교할 곳",
    )
    notes: list[str] = Field(default_factory=lambda: list[str]())
    warnings: list[str] = Field(
        default_factory=lambda: list[str](), description="앱이 찾은 점 (부분 점수 합 ≠ 배점 등)"
    )

    def part(self, label: str | None) -> KeyPart | None:
        return next((p for p in self.parts if p.label == (label or "")), None)
