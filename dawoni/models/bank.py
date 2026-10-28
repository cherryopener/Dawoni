"""문제은행 문항: 교재·문제집·기출에서 DB화했거나 다워니가 만든 문항 하나.

원본 답·해설은 지우지 않고 `original`에 둔다. 원본 해설이 없거나 짧으면 AI가 해설을 쓰고
(`solution_by="ai"`), 원본 답과 AI 답을 비교한 결과를 `answer_check`에 남긴다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from dawoni.models.answer_key import KeyPart
from dawoni.models.base import DawoniModel, new_id, utcnow
from dawoni.models.grading import ExamQuestion, RubricDraft, ScopedSolution
from dawoni.models.source import SourceKind

ItemOrigin = Literal["ingested", "variant", "new"]
"""자료에서 DB화 / 동형 제작 / 새로 만듦"""

OriginalLevel = Literal["none", "answer_only", "brief", "full"]
"""원본에 있던 것: 없음 / 답만 / 짧은 해설 / 충분한 해설"""

ORIGINAL_LEVEL_LABELS: dict[OriginalLevel, str] = {
    "none": "원본 해설 없음",
    "answer_only": "원본은 답만",
    "brief": "원본 해설 짧음",
    "full": "원본 해설 있음",
}

CheckVerdict = Literal["same", "differs", "unsure", "no_original"]


class BankSourceRef(DawoniModel):
    source_id: str | None = Field(default=None, description="원본 파일 SHA-256 (제작 문항은 없음)")
    title: str = Field(description="보이는 출처 이름 (예: 2025학년도 6월 모평 물리학I)")
    kind: SourceKind | Literal["dawoni"] = "other"
    pages: list[int] = Field(default_factory=lambda: list[int]())
    number_label: str | None = Field(default=None, description="원본에서의 문항 번호")

    def display(self) -> str:
        """ "OO 문제집 37쪽 12번"."""
        bits = [self.title]
        if self.pages:
            bits.append(f"{self.pages[0]}쪽")
        if self.number_label:
            bits.append(f"{self.number_label}번")
        return " ".join(bits)


class OriginalSolution(DawoniModel):
    level: OriginalLevel
    parts: list[KeyPart] = Field(default_factory=lambda: list[KeyPart]())
    pages: list[int] = Field(default_factory=lambda: list[int](), description="해설이 있던 쪽")


class AnswerComparison(DawoniModel):
    verdict: CheckVerdict
    original_answer: str | None = None
    ai_answer: str | None = None
    notes: list[str] = Field(default_factory=lambda: list[str]())
    resolved: Literal["original", "ai"] | None = Field(
        default=None, description="강사가 고른 쪽 (답 확인 필요를 풀었을 때)"
    )
    resolved_by: str | None = None


class BankItem(DawoniModel):
    schema_version: int = 1
    id: str = Field(default_factory=lambda: new_id("bk"), pattern=r"^[a-z]+-[0-9a-z-]+$")
    question: ExamQuestion
    subject: str | None = None
    unit_ids: list[str] = Field(
        default_factory=lambda: list[str](), description="공통 단원표 ID (앞이 대표 단원)"
    )
    origin: ItemOrigin = "ingested"
    source: BankSourceRef
    parent_id: str | None = Field(default=None, description="동형·새 문제의 기준 문항")
    gen_kind: str | None = Field(default=None, description="제작 종류 (numeric, new …)")
    original: OriginalSolution | None = None
    solution: ScopedSolution | None = None
    solution_by: Literal["source", "ai"] | None = None
    concept_units: dict[str, str] = Field(
        default_factory=lambda: dict[str, str](),
        description="풀이 단계의 개념 ID(C…) → 단원 ID (단원표로 푼 경우)",
    )
    answer_check: AnswerComparison | None = None
    rubric: RubricDraft | None = None
    difficulty: int | None = Field(default=None, ge=1, le=5)
    tags: list[str] = Field(default_factory=lambda: list[str]())
    note: str | None = None
    reviewed: bool = False
    reviewed_by: str | None = None
    revision: int = Field(default=1, ge=1, description="저장할 때마다 +1 (팀 공유 충돌 감지)")
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str | None = None
    updated_at: datetime = Field(default_factory=utcnow)
    updated_by: str | None = None

    @property
    def needs_check(self) -> bool:
        """답 확인 필요: 원본 답과 AI 답이 다르거나 비교할 수 없음."""
        c = self.answer_check
        return c is not None and c.resolved is None and c.verdict in ("differs", "unsure")

    def answer_side(self) -> Literal["original", "ai"]:
        """정답으로 쓸 쪽: 강사가 고른 쪽 → 원본 답이 있으면 원본 → AI."""
        c = self.answer_check
        if c and c.resolved:
            return c.resolved
        has_original = any(
            p.answer or p.answer_expr for p in (self.original.parts if self.original else [])
        )
        return "original" if has_original else "ai"
