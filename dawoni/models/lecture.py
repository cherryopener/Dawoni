"""유튜브 강의 요약 (M12): 자막 → 구간별 노트 → A4 요약본 + 교재 범위와 비교."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from dawoni.models.base import DawoniModel, new_id, utcnow
from dawoni.models.content import fix_overescaped

_DOUBLED_DELIM = re.compile(r"\\\\(?=[()\[\]])")


def _fix_loose(s: str) -> str:
    """`\\( … \\)` 안팎 문장: 구분자·명령의 겹 이스케이프를 푼다 (M12 실측: agy)."""
    return fix_overescaped(_DOUBLED_DELIM.sub(r"\\", s))


Latex = Annotated[str, AfterValidator(fix_overescaped)]
"""수식 하나 (LaTeX, $ 없이). AI가 `\\le`처럼 한 번 더 이스케이프하면 바로잡는다."""
Loose = Annotated[str, AfterValidator(_fix_loose)]
r"""`\( … \)` 수식이 섞인 문장."""


class CaptionLine(DawoniModel):
    start: float = Field(ge=0, description="초")
    text: str


class LectureSource(DawoniModel):
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    url: str
    title: str
    channel: str | None = None
    language: str
    generated: bool = Field(description="자동 생성 자막인가")
    duration_s: float
    lines: list[CaptionLine]
    fetched_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------- AI 출력


class ChunkTopic(DawoniModel):
    title: str = Field(min_length=1)
    start_s: float = Field(ge=0, description="이 내용이 시작하는 시각(초) — [자료]의 시각 표시에서")
    points: list[Loose] = Field(min_length=1, description="강의에서 설명한 핵심 (한 문장씩)")
    formulas: list[Latex] = Field(default_factory=lambda: list[str](), description="LaTeX, $ 없이")
    concept_ids: list[str] = Field(
        default_factory=lambda: list[str](), description="이 내용에 해당하는 [풀이 범위]의 C·S ID"
    )


class ChunkNotes(DawoniModel):
    topics: list[ChunkTopic] = Field(min_length=1)
    beyond_scope: list[str] = Field(
        default_factory=lambda: list[str](),
        description="[풀이 범위]에 없는 개념·기법을 강의에서 다뤘다면 그 이름 (예: 미분)",
    )


class SummarySection(DawoniModel):
    heading: str = Field(min_length=1)
    start_s: float = Field(ge=0, description="이 부분이 시작하는 시각(초) — 노트의 시각에서")
    bullets: list[Loose] = Field(min_length=1)
    formulas: list[Latex] = Field(default_factory=lambda: list[str]())
    concept_ids: list[str] = Field(default_factory=lambda: list[str]())


class SummaryDraft(DawoniModel):
    title: str = Field(min_length=1, description="요약본 제목 (강의 주제)")
    overview: Loose = Field(description="강의 전체를 한두 문장으로")
    sections: list[SummarySection] = Field(min_length=1)
    key_formulas: list[Latex] = Field(
        default_factory=lambda: list[str](), description="꼭 외울 식 (LaTeX)"
    )
    tips: list[Loose] = Field(
        default_factory=lambda: list[str](), description="강사가 강조한 실수·요령"
    )
    review_questions: list[Loose] = Field(
        default_factory=lambda: list[str](), max_length=4, description="스스로 확인할 짧은 질문"
    )


# ---------------------------------------------------------------- 저장

PageMode = Literal[1, 2]


class LectureSummary(SummaryDraft):
    id: str = Field(default_factory=lambda: new_id("lsum"))
    video_id: str
    url: str
    video_title: str
    channel: str | None = None
    duration_s: float
    scope_id: str | None = None
    pages: PageMode = 1
    covered: list[str] = Field(
        default_factory=lambda: list[str](), description="강의가 다룬 범위 ID"
    )
    missing: list[str] = Field(
        default_factory=lambda: list[str](), description="범위에 있는데 강의에 없는 개념"
    )
    beyond: list[str] = Field(default_factory=lambda: list[str](), description="범위 밖 내용")
    caption_generated: bool = False
    agent: str | None = None
    model: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
