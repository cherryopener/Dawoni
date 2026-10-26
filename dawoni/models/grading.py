"""채점 파이프라인 데이터: 시험·범위 해설·루브릭·판독문·채점 결과.

AI 출력 모델(`*Draft`, `GradeOutput`)과 저장 모델을 나눈다. 승인·버전·합계처럼 앱이
정하는 값은 AI가 쓰지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from dawoni.models.base import DawoniModel, new_id, utcnow
from dawoni.models.item import ItemContent

# ---------------------------------------------------------------- 시험


class ExamQuestion(DawoniModel):
    number: str = Field(min_length=1, description="문항 번호 (예: 3, 서술형1)")
    points: float = Field(gt=0)
    item: ItemContent


class Exam(DawoniModel):
    id: str = Field(default_factory=lambda: new_id("exam"))
    title: str
    scope_id: str
    questions: list[ExamQuestion] = Field(min_length=1)
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _numbers(self) -> Exam:
        nums = [q.number for q in self.questions]
        if len(nums) != len(set(nums)):
            raise ValueError(f"문항 번호 중복: {nums}")
        return self

    def question(self, number: str) -> ExamQuestion:
        for q in self.questions:
            if q.number == number:
                return q
        raise KeyError(f"문항 {number} 없음")


# ---------------------------------------------------------------- 범위 해설


class SolutionStep(DawoniModel):
    explanation: str = Field(min_length=1, description="이 단계에서 하는 일 (한국어 문장)")
    math: str | None = Field(default=None, description="이 단계의 식 (LaTeX, $ 없이)")
    uses: list[str] = Field(
        min_length=1, description="이 단계가 쓰는 개념·기법 ID (범위 프로필의 C·S·X 번호)"
    )


class FinalAnswer(DawoniModel):
    latex: str = Field(description="최종 답 (LaTeX, $ 없이)")
    expr: str | None = Field(
        default=None, description="수치·식이면 sympy로 읽을 수 있는 식 (예: 3/2, (-1+sqrt(5))/2)"
    )


class AlternativeSolution(DawoniModel):
    summary: str = Field(description="다른 풀이의 요지")
    steps: list[SolutionStep] = Field(min_length=1)


class SolutionDraft(DawoniModel):
    """AI 출력: 범위 안에서 쓴 해설."""

    steps: list[SolutionStep] = Field(min_length=1)
    final_answer: FinalAnswer
    alternatives: list[AlternativeSolution] = Field(
        default_factory=lambda: list[AlternativeSolution](),
        description="범위 안의 다른 정당한 풀이 (루브릭에서 인정)",
    )
    out_of_scope_note: str | None = Field(
        default=None, description="범위 안에서 풀 수 없다고 판단하면 그 이유. 풀 수 있으면 null"
    )


class ScopedSolution(SolutionDraft):
    question_number: str
    scope_version: int
    verified: bool = False
    """독립 재풀이와 최종 답이 일치함."""
    check_notes: list[str] = Field(default_factory=lambda: list[str]())


# ---------------------------------------------------------------- 루브릭

DEFAULT_NEVER_DEDUCT: tuple[str, ...] = (
    "범위 밖의 고급 기법(예: 미분)을 쓰지 않았다는 이유",
    "범위 안의 다른 정당한 풀이를 썼다는 이유",
    "분수·소수 등 같은 값을 다른 표기로 쓴 것",
    "풀이 순서가 해설과 다른 것 (논리가 맞으면)",
)


class RubricItem(DawoniModel):
    id: str = Field(pattern=r"^R[1-9][0-9]*$")
    points: float = Field(gt=0)
    criterion: str = Field(description="무엇이 답안에 있으면 이 점수를 주는가")
    evidence_hint: str = Field(description="답안에서 찾을 식·문장의 예")
    solution_steps: list[int] = Field(
        default_factory=lambda: list[int](), description="연결된 해설 단계 번호 (1부터)"
    )


class PartialCredit(DawoniModel):
    item_id: str
    condition: str = Field(description="예: 부호 실수 후 과정은 일관됨")
    points: float = Field(ge=0, description="이 경우 그 항목에서 주는 점수")


class AcceptedAlternative(DawoniModel):
    description: str
    item_ids: list[str] = Field(min_length=1, description="이 풀이로도 점수를 받는 항목")


class RubricDraft(DawoniModel):
    """AI 출력: 해설에서 도출한 루브릭."""

    items: list[RubricItem] = Field(min_length=1)
    partial: list[PartialCredit] = Field(default_factory=lambda: list[PartialCredit]())
    accepted_alternatives: list[AcceptedAlternative] = Field(
        default_factory=lambda: list[AcceptedAlternative]()
    )
    never_deduct: list[str] = Field(default_factory=lambda: list[str]())

    @model_validator(mode="after")
    def _refs(self) -> RubricDraft:
        ids = [i.id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError(f"루브릭 항목 ID 중복: {ids}")
        points = {i.id: i.points for i in self.items}
        for p in self.partial:
            if p.item_id not in points:
                raise ValueError(f"부분점수가 없는 항목을 참조: {p.item_id}")
            if p.points >= points[p.item_id]:
                raise ValueError(f"부분점수({p.points})는 항목 {p.item_id} 배점보다 작아야 함")
        for a in self.accepted_alternatives:
            bad = sorted(set(a.item_ids) - set(points))
            if bad:
                raise ValueError(f"다른 풀이가 없는 항목을 참조: {bad}")
        return self

    @property
    def total(self) -> float:
        return sum(i.points for i in self.items)


class Rubric(RubricDraft):
    question_number: str
    version: int = Field(default=1, ge=1)
    status: Literal["draft", "approved"] = "draft"
    approved_by: str | None = None
    approved_at: datetime | None = None


# ---------------------------------------------------------------- 판독문


class TranscriptLine(DawoniModel):
    text: str = Field(default="", description="학생이 쓴 글 (원문 그대로)")
    math: str | None = Field(default=None, description="학생이 쓴 식 (LaTeX, 원문 그대로)")
    confidence: float = Field(default=1.0, ge=0, le=1)
    uncertain: list[str] = Field(default_factory=lambda: list[str](), description="불확실한 부분")
    struck: bool = Field(default=False, description="지운 흔적")

    @property
    def joined(self) -> str:
        return " ".join(x for x in (self.text, self.math or "") if x)


class Transcript(DawoniModel):
    student_id: str = Field(min_length=1)
    question_number: str
    lines: list[TranscriptLine] = Field(default_factory=lambda: list[TranscriptLine]())
    source: Literal["typed", "ocr"] = "typed"
    confirmed: bool = False
    """강사 확인을 거친 판독문만 채점한다."""
    confirmed_by: str | None = None

    @property
    def is_blank(self) -> bool:
        return not any(line.joined.strip() for line in self.lines if not line.struck)


# ---------------------------------------------------------------- 채점


class ItemJudgement(DawoniModel):
    rubric_item_id: str
    awarded: float = Field(ge=0)
    evidence_quote: str | None = Field(
        default=None,
        description="점수 근거가 된 학생 답안의 부분을 판독문 그대로 인용 (줄 번호 없이). "
        "떨어진 부분은 … 로 이어 붙임. 0점이면 null 가능",
    )
    reason: str = Field(description="판정 이유")
    deduction_reason: str | None = Field(default=None, description="만점이 아니면 감점 이유")
    confidence: float = Field(ge=0, le=1)


class GradeOutput(DawoniModel):
    """AI 출력: 한 학생 한 문항의 채점. 총점은 코드가 계산한다."""

    judgements: list[ItemJudgement] = Field(min_length=1)
    comment: str = Field(description="학생에게 줄 짧은 첨삭")
    flags: list[str] = Field(
        default_factory=lambda: list[str](), description="강사가 확인할 점 (판독 불확실 영향 등)"
    )


GradeStatus = Literal["auto", "needs_review", "teacher_confirmed"]


class GradeResult(DawoniModel):
    student_id: str
    question_number: str
    rubric_version: int
    judgements: list[ItemJudgement]
    comment: str
    flags: list[str] = Field(default_factory=lambda: list[str]())
    total: float
    max_points: float
    status: GradeStatus = "auto"
    second_opinion: list[ItemJudgement] | None = None
    disagreements: list[str] = Field(
        default_factory=lambda: list[str](), description="이중 채점에서 차이가 난 항목 ID"
    )
    agent: str | None = None
    model: str | None = None
    graded_at: datetime = Field(default_factory=utcnow)
