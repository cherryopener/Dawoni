"""강사 보정: 기준 예시 설정·일치도·보정 효과 측정·루브릭 개선 제안 (M8, PLAN §2-6).

기준 예시는 따로 저장하지 않는다. 강사가 확정한 채점 결과(`GradeResult.status ==
"teacher_confirmed"`)가 곧 예시 후보이고, 여기에는 강사가 고른 고정·제외 목록만 둔다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from dawoni.models.base import DawoniModel, new_id, utcnow
from dawoni.models.grading import RubricDraft


class CalibrationSettings(DawoniModel):
    """문항 하나의 기준 예시 설정 (작업공간 `exams/<시험>/calibration/<문항>.json`)."""

    question_number: str
    use_examples: bool = True
    max_examples: int = Field(default=3, ge=0, le=6)
    pinned: list[str] = Field(
        default_factory=lambda: list[str](), description="항상 넣을 예시 (학생 ID)"
    )
    excluded: list[str] = Field(
        default_factory=lambda: list[str](), description="예시로 쓰지 않을 답안 (학생 ID)"
    )


class AgreementStats(DawoniModel):
    """AI 점수와 강사 점수의 일치도."""

    n: int = 0
    exact: float | None = Field(default=None, description="총점이 같은 비율")
    within1: float | None = Field(default=None, description="총점 차이 1점 이내 비율")
    item_agreement: float | None = Field(default=None, description="항목 점수가 같은 비율")
    mean_abs_diff: float | None = None
    bias: float | None = Field(default=None, description="AI − 강사 평균 (양수 = AI가 후함)")
    item_disagreements: dict[str, int] = Field(
        default_factory=lambda: dict[str, int](), description="항목별 불일치 수"
    )


class EvalCase(DawoniModel):
    student_id: str
    teacher_total: float
    before_total: float | None = None
    after_total: float | None = None
    teacher_items: dict[str, float]
    before_items: dict[str, float] = Field(default_factory=lambda: dict[str, float]())
    after_items: dict[str, float] = Field(default_factory=lambda: dict[str, float]())
    examples_used: list[str] = Field(default_factory=lambda: list[str]())
    error: str | None = None


class CalibrationEval(DawoniModel):
    """보정 효과 측정: 강사가 확정한 답안을 예시 없이 / 예시와 함께(자기 답안은 빼고) 다시 채점."""

    id: str = Field(default_factory=lambda: new_id("caleval"))
    question_number: str
    rubric_version: int
    agent: str | None = None
    model: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    cases: list[EvalCase] = Field(default_factory=lambda: list[EvalCase]())
    before: AgreementStats = Field(default_factory=AgreementStats)
    after: AgreementStats = Field(default_factory=AgreementStats)


# ---------------------------------------------------------------- 루브릭 개선 제안 (AI 출력)

ChangeKind = Literal[
    "clarify_criterion", "add_partial", "add_never_deduct", "add_alternative", "adjust_points",
    "split_item", "other",
]  # fmt: skip


class RubricChange(DawoniModel):
    item_id: str | None = Field(default=None, description="바꾸는 항목 ID (새 규정이면 null)")
    kind: ChangeKind
    summary: str = Field(min_length=1, description="무엇을 왜 바꾸는가 (한국어 한두 문장)")
    evidence: list[str] = Field(
        min_length=1, description="이 제안의 근거가 된 강사 수정 사례 (학생 ID)"
    )


class RubricSuggestion(DawoniModel):
    changes: list[RubricChange] = Field(default_factory=lambda: list[RubricChange]())
    revised: RubricDraft | None = Field(
        default=None, description="제안을 반영한 루브릭 전체. 바꿀 것이 없으면 null"
    )
    no_change_reason: str | None = Field(
        default=None, description="바꿀 것이 없다고 판단하면 그 이유"
    )
