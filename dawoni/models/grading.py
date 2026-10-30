"""채점 파이프라인 데이터: 시험·범위 해설·루브릭·판독문·채점 결과.

AI 출력 모델(`*Draft`, `GradeOutput`)과 저장 모델을 나눈다. 승인·버전·합계처럼 앱이
정하는 값은 AI가 쓰지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from dawoni.models.base import DawoniModel, new_id, utcnow
from dawoni.models.content import fix_overescaped
from dawoni.models.item import ItemContent

# ---------------------------------------------------------------- 시험


PartKind = Literal["essay", "short_answer"]


class ExamPart(DawoniModel):
    """소문항: (1), (2), (1)① … 배점과 유형(서술형/단답형)은 채점 기준을 바꾼다."""

    label: str = Field(min_length=1, description="소문항 표시 그대로 (예: (1), (2), (1)①)")
    points: float = Field(gt=0)
    kind: PartKind | None = Field(
        default=None,
        description="essay=서술형(풀이 과정까지 채점), short_answer=단답형(최종 답만). 모르면 null",
    )


class ExamQuestion(DawoniModel):
    number: str = Field(min_length=1, description="문항 번호 (예: 3, 서술형1)")
    points: float = Field(gt=0)
    item: ItemContent
    parts: list[ExamPart] = Field(
        default_factory=lambda: list[ExamPart](), description="소문항 (없으면 빈 목록)"
    )
    answer_pages: list[int] = Field(
        default_factory=lambda: list[int](),
        description="학생 답안지 묶음에서 이 문항의 답이 있는 쪽 (표지가 1쪽). 없으면 모름",
    )

    @model_validator(mode="after")
    def _parts(self) -> ExamQuestion:
        labels = [p.label for p in self.parts]
        if len(labels) != len(set(labels)):
            raise ValueError(f"소문항 표시 중복: {labels}")
        total = sum(p.points for p in self.parts)
        if self.parts and abs(total - self.points) > 1e-9:
            raise ValueError(f"소문항 배점 합({total:g})이 문항 배점({self.points:g})과 다름")
        return self

    def part(self, label: str) -> ExamPart | None:
        return next((p for p in self.parts if p.label == label), None)


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

    @field_validator("math")
    @classmethod
    def _unescape(cls, v: str | None) -> str | None:
        return fix_overescaped(v) if v else v


class FinalAnswer(DawoniModel):
    latex: str = Field(description="최종 답 (LaTeX, $ 없이)")
    expr: str | None = Field(
        default=None, description="수치·식이면 sympy로 읽을 수 있는 식 (예: 3/2, (-1+sqrt(5))/2)"
    )

    @field_validator("latex")
    @classmethod
    def _unescape(cls, v: str) -> str:
        return fix_overescaped(v)


class PartAnswer(FinalAnswer):
    label: str = Field(description="소문항 표시 ([자료]에 주어진 그대로)")


class AlternativeSolution(DawoniModel):
    summary: str = Field(description="다른 풀이의 요지")
    steps: list[SolutionStep] = Field(min_length=1)


class SolutionDraft(DawoniModel):
    """AI 출력: 범위 안에서 쓴 해설."""

    steps: list[SolutionStep] = Field(min_length=1)
    final_answer: FinalAnswer
    part_answers: list[PartAnswer] = Field(
        default_factory=lambda: list[PartAnswer](),
        description="소문항이 있으면 소문항마다 최종 답 (해설지와 소문항별로 비교)",
    )
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
    created_by: str | None = None
    created_at: datetime | None = None
    agent: str | None = Field(default=None, description="만든 AI (agy·claude·codex)")
    model: str | None = Field(default=None, description="만든 모델 (예: gemini-3.8-flash-medium)")


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
    part: str | None = Field(default=None, description="이 항목이 속한 소문항 표시. 없으면 null")
    key_credit: int | None = Field(
        default=None,
        ge=1,
        description="해설지 부분 점수 표기의 몇 번째 항목에 해당하는가 (1부터). 표기가 없으면 null",
    )


class PartialCredit(DawoniModel):
    item_id: str
    condition: str = Field(description="예: 부호 실수 후 과정은 일관됨")
    points: float = Field(ge=0, description="이 경우 그 항목에서 주는 점수")
    from_key: bool = Field(
        default=False,
        description="해설지에 적혔거나 강사 채점 사례에 근거한 부분 점수면 true, AI 제안이면 false",
    )


Verdict = Literal["same", "ai_wrong", "other_valid", "key_doubt"]


class PartCheck(DawoniModel):
    """해설지 답과 AI 독립 풀이 답의 소문항별 비교."""

    part: str = Field(description="소문항 표시 (소문항이 없으면 빈 문자열)")
    key_answer: str = Field(description="해설지의 답")
    ai_answer: str = Field(description="AI 독립 풀이의 답")
    verdict: Verdict = Field(
        description="same=같은 답, ai_wrong=AI 풀이가 틀림(해설지가 맞음), "
        "other_valid=AI 답도 조건을 만족하는 다른 정답일 수 있음, "
        "key_doubt=해설지·문제 자체에 의문(오타·조건 모순 등)"
    )
    note: str = Field(default="", description="판단 근거 (AI가 틀렸다면 어디서 틀렸는지)")
    key_check: Literal["valid", "invalid", "unsure"] | None = Field(
        default=None, description="해설지 답의 독립 검산 (앱이 채움 — 두 답이 다를 때만)"
    )
    key_check_note: str | None = None
    ai_check: Literal["valid", "invalid", "unsure"] | None = Field(
        default=None, description="AI 답의 독립 검산 (앱이 채움 — 두 답이 다를 때만)"
    )
    ai_check_note: str | None = None


CandidateStatus = Literal["proposed", "accepted", "rejected"]


class AnswerCandidate(DawoniModel):
    """해설지에 없지만 정답일 수 있는 답. **강사가 인정하기 전에는 점수를 주지 않는다.**"""

    id: str = Field(pattern=r"^A[1-9][0-9]*$")
    part: str = Field(description="소문항 표시 (없으면 빈 문자열)")
    answer: str = Field(description="다른 정답 후보")
    argument: str = Field(description="왜 이것도 문제의 조건을 만족하는지 (검산 포함)")
    item_ids: list[str] = Field(
        default_factory=lambda: list[str](), description="이 답이면 점수를 받을 항목"
    )
    status: CandidateStatus = "proposed"
    check: Literal["valid", "invalid", "unsure"] | None = Field(
        default=None, description="독립 검산 결과 (앱이 채움)"
    )
    check_note: str | None = None


AnswerSide = Literal["key", "candidate", "other"]


class StudentAnswerSide(DawoniModel):
    """학생 한 명의 결론이 해설지 답·정답 후보 중 어느 쪽인지. 정오 판정이 아니다."""

    student: str = Field(description="[대상]의 학생 표시 그대로 (학생1, 학생2 …)")
    side: AnswerSide = Field(
        description="key: 해설지 답과 같은 결론 · candidate: 정답 후보와 같은 결론 · "
        "other: 둘 다 아님"
    )
    candidate_id: str | None = Field(default=None, description="side가 candidate일 때 후보 ID")
    quote: str = Field(description="결론을 담은 부분을 답안에 적힌 그대로 인용 (줄 번호 없이)")


class AnswerSideOutput(DawoniModel):
    """AI 출력: 학생마다 어느 답 쪽인지."""

    students: list[StudentAnswerSide]


class PartAnswerSides(DawoniModel):
    part: str
    key_answer: str
    candidates: dict[str, str] = Field(description="후보 ID → 분류할 때의 후보 답")
    sides: list[StudentAnswerSide] = Field(
        default_factory=lambda: list[StudentAnswerSide](), description="student는 실제 학생 ID"
    )
    blank: list[str] = Field(default_factory=lambda: list[str](), description="이 소문항이 빈 학생")

    def of(self, side: AnswerSide, candidate_id: str | None = None) -> list[StudentAnswerSide]:
        return [s for s in self.sides if s.side == side and s.candidate_id == candidate_id]


class CandidateMatches(DawoniModel):
    """정답 후보를 정하기 전에: 학생들 답이 해설지 답·후보 중 어느 쪽인지 (판독문 기준)."""

    question_number: str
    parts: list[PartAnswerSides]
    unconfirmed: list[str] = Field(
        default_factory=lambda: list[str](), description="판독문을 아직 확인하지 않은 학생"
    )
    agent: str | None = None
    model: str | None = None
    created_at: datetime = Field(default_factory=utcnow)

    def for_candidate(self, c: AnswerCandidate) -> PartAnswerSides | None:
        """그 후보를 같은 답으로 분류한 결과 (후보 답이 바뀌었으면 None)."""
        return next(
            (p for p in self.parts if p.part == c.part and p.candidates.get(c.id) == c.answer),
            None,
        )


class AcceptedAlternative(DawoniModel):
    description: str
    item_ids: list[str] = Field(min_length=1, description="이 풀이로도 점수를 받는 항목")
    id: str | None = Field(
        default=None, pattern=r"^E[1-9][0-9]*$", description="해설지 넓히기 제안 ID (앱이 채움)"
    )
    part: str | None = Field(default=None, description="소문항 표시 (앱이 채움)")
    status: CandidateStatus = Field(
        default="accepted",
        description="AI가 해설지를 넓혀 제안한 것은 proposed — 강사가 인정해야 채점에 쓴다",
    )


class KeyExpansion(DawoniModel):
    """해설지 답과 **같은 뜻**의 다른 표현·다른 풀이 (서술형). 강사가 인정해야 쓴다."""

    part: str = Field(description="소문항 표시 (소문항이 없으면 빈 문자열)")
    description: str = Field(
        description="해설지와 같은 근거·결론을 다르게 쓴 답 — 학생 답안에 나올 법한 문장으로"
    )
    item_ids: list[str] = Field(min_length=1, description="이렇게 써도 점수를 받는 항목")


class KeyRubricDraft(DawoniModel):
    """AI 출력: 강사 해설지를 넓혀 만든 루브릭 (AI 풀이 없이)."""

    items: list[RubricItem] = Field(min_length=1)
    partial: list[PartialCredit] = Field(default_factory=lambda: list[PartialCredit]())
    never_deduct: list[str] = Field(default_factory=lambda: list[str]())
    expansions: list[KeyExpansion] = Field(
        default_factory=lambda: list[KeyExpansion](),
        description="서술형 소문항에서 해설지와 같은 뜻으로 인정할 다른 표현 (강사 확인 전)",
    )
    key_conflicts: list[str] = Field(
        default_factory=lambda: list[str](),
        description="해설지의 기준이 문제의 요구와 맞지 않는 곳 — 무엇을 어떻게 기준으로 삼았는지",
    )

    def to_rubric_draft(self) -> RubricDraft:
        """넓힌 표현은 `accepted_alternatives`에 제안(proposed)으로 (E1, E2 …)."""
        return RubricDraft(
            items=[i.model_copy(update={"solution_steps": []}) for i in self.items],
            partial=self.partial,
            never_deduct=self.never_deduct,
            key_conflicts=self.key_conflicts,
            accepted_alternatives=[
                AcceptedAlternative(
                    id=f"E{i}", part=e.part, description=e.description,
                    item_ids=e.item_ids, status="proposed",
                )
                for i, e in enumerate(self.expansions, start=1)
            ],
        )  # fmt: skip


class RubricDraft(DawoniModel):
    """AI 출력: 해설에서 도출한 루브릭."""

    items: list[RubricItem] = Field(min_length=1)
    partial: list[PartialCredit] = Field(default_factory=lambda: list[PartialCredit]())
    accepted_alternatives: list[AcceptedAlternative] = Field(
        default_factory=lambda: list[AcceptedAlternative]()
    )
    never_deduct: list[str] = Field(default_factory=lambda: list[str]())
    checks: list[PartCheck] = Field(
        default_factory=lambda: list[PartCheck](),
        description="해설지가 있을 때: 소문항마다 해설지 답과 AI 풀이 답 비교",
    )
    candidates: list[AnswerCandidate] = Field(
        default_factory=lambda: list[AnswerCandidate](),
        description="해설지와 다르지만 정답일 수 있는 답 (강사 확인 전에는 점수 없음)",
    )
    key_conflicts: list[str] = Field(
        default_factory=lambda: list[str](),
        description="해설지의 기준이 문제의 요구와 맞지 않는 곳 (문제가 요구하지 않은 것을 "
        "0점 조건으로 요구하는 등) — 무엇을 어떻게 기준으로 삼았는지",
    )

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
        cids = [c.id for c in self.candidates]
        if len(cids) != len(set(cids)):
            raise ValueError(f"정답 후보 ID 중복: {cids}")
        for c in self.candidates:
            bad = sorted(set(c.item_ids) - set(points))
            if bad:
                raise ValueError(f"정답 후보 {c.id}가 없는 항목을 참조: {bad}")
        return self

    def part_items(self, label: str | None) -> list[RubricItem]:
        return [i for i in self.items if (i.part or "") == (label or "")]

    @property
    def total(self) -> float:
        return sum(i.points for i in self.items)


class RubricAudit(DawoniModel):
    """AI 출력: 루브릭이 해설지를 지키는지 검토 (만든 AI와 다른 새 세션)."""

    issues: list[str] = Field(
        default_factory=lambda: list[str](),
        description="고쳐야 할 점. 문제없으면 빈 목록",
    )


class AnswerCheck(DawoniModel):
    """AI 출력: 정답 후보 하나를 문제 조건에 대입해 검산."""

    verdict: Literal["valid", "invalid", "unsure"] = Field(
        description="valid=모든 조건 만족, invalid=어떤 조건을 어김, unsure=문제 해석에 따라 다름"
    )
    reasoning: str = Field(description="조건마다 확인한 과정")


class Rubric(RubricDraft):
    question_number: str
    based_on_key: bool = False
    """강사 해설지를 기준으로 만든 루브릭인가 (아니면 AI 풀이만으로 — 정답 확인 필요)."""
    review_notes: list[str] = Field(
        default_factory=lambda: list[str](), description="승인 전에 강사가 볼 점 (앱이 채움)"
    )
    version: int = Field(default=1, ge=1)
    status: Literal["draft", "approved"] = "draft"
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_by: str | None = None
    created_at: datetime | None = Field(
        default=None, description="AI 초안을 만든 때 (강사 편집은 판만 올림)"
    )
    agent: str | None = Field(default=None, description="초안을 만든 AI")
    model: str | None = Field(default=None, description="초안을 만든 모델")


# ---------------------------------------------------------------- 학생


class StudentRecord(DawoniModel):
    """시험 폴더 students/<학생>.json — 표지에서 읽은 학생 정보."""

    student_id: str
    name: str | None = None
    class_name: str | None = None
    school: str | None = None
    source: str = Field(description="어느 파일 몇 쪽")
    teacher_scores: dict[str, float] = Field(
        default_factory=lambda: dict[str, float](),
        description="표지에 강사가 적은 문항별 점수 (채점이 끝난 답안지를 넣었을 때)",
    )
    uncertain: list[str] = Field(default_factory=lambda: list[str]())
    rotation: int = Field(default=0, description="표지로 정한, 묶음의 쪽을 세울 각도 (시계 방향)")
    pages: int | None = Field(default=None, description="묶음의 쪽 수 (표지 포함)")


# ---------------------------------------------------------------- 판독문


class TranscriptLine(DawoniModel):
    text: str = Field(default="", description="학생이 쓴 글 (원문 그대로)")
    part: str | None = Field(default=None, description="소문항 표시 (답안지 칸에서). 모르면 null")
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
    pages: list[str] = Field(
        default_factory=lambda: list[str](), description="원본 페이지 이미지 (시험 폴더 기준)"
    )
    cross_checked: bool = False
    agreement: float | None = Field(default=None, ge=0, le=1, description="교차 판독 일치도")
    notes: list[str] = Field(default_factory=lambda: list[str]())

    @property
    def is_blank(self) -> bool:
        return not any(line.joined.strip() for line in self.lines if not line.struck)

    @property
    def needs_attention(self) -> bool:
        """강사가 특히 주의해서 확인할 판독문."""
        return any(line.uncertain or line.confidence < 0.7 for line in self.lines) or (
            self.agreement is not None and self.agreement < 0.9
        )


class TranscribedLine(DawoniModel):
    """AI 출력: 판독한 한 줄."""

    question_number: str | None = Field(description="이 줄이 속한 문항 번호. 알 수 없으면 null")
    part: str | None = Field(
        default=None, description="이 줄이 속한 소문항 표시 ((1), (1)② 등). 알 수 없으면 null"
    )
    page: int = Field(ge=1)
    text: str = Field(
        default="",
        description="글 (원문 그대로). 글과 식이 섞인 줄은 여기에 순서대로, 식만 \\( \\)로 감쌈",
    )
    math: str | None = Field(
        default=None, description="줄 전체가 식일 때만: LaTeX, $ 없이, 적힌 그대로"
    )

    @field_validator("math")
    @classmethod
    def _unescape(cls, v: str | None) -> str | None:
        return fix_overescaped(v) if v else v

    confidence: float = Field(ge=0, le=1)
    uncertain: list[str] = Field(
        default_factory=lambda: list[str](),
        description="읽기 불확실한 부분과 가능한 다른 읽기 (예: '7 또는 1')",
    )
    struck: bool = Field(default=False, description="학생이 지운(줄 그은) 부분")

    @property
    def joined(self) -> str:
        return " ".join(x for x in (self.text, self.math or "") if x)


class TranscriptionDraft(DawoniModel):
    """AI 출력: 한 학생 답안지 전체 판독."""

    lines: list[TranscribedLine] = Field(default_factory=lambda: list[TranscribedLine]())
    notes: list[str] = Field(
        default_factory=lambda: list[str](), description="판독 전반에 대해 강사가 알아야 할 점"
    )


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
    matched_candidates: list[str] = Field(
        default_factory=lambda: list[str](),
        description="학생 답이 루브릭의 '강사 미확인 정답 후보'(A1 …)와 같으면 그 ID",
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
    ai_judgements: list[ItemJudgement] | None = Field(
        default=None, description="강사가 점수를 고치기 전 AI 판정 (보정·일치도 측정용, M8)"
    )
    teacher_note: str | None = None
    teacher_item_notes: dict[str, str] = Field(
        default_factory=lambda: dict[str, str](), description="강사가 항목별로 남긴 판정 이유"
    )
    teacher_part_scores: dict[str, float] = Field(
        default_factory=lambda: dict[str, float](),
        description="강사가 소문항 단위로 준 점수 (빨간 펜처럼 소문항마다 매긴 경우)",
    )
    teacher_part_notes: dict[str, str] = Field(
        default_factory=lambda: dict[str, str](), description="강사가 소문항별로 남긴 이유"
    )
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    examples_used: list[str] = Field(
        default_factory=lambda: list[str](), description="채점에 넣은 기준 예시 (학생 ID)"
    )
    item_points: dict[str, float] = Field(
        default_factory=lambda: dict[str, float](),
        description="채점할 때 루브릭 항목별 배점 (루브릭이 바뀐 뒤 예시로 쓸 수 있는지 판단)",
    )
