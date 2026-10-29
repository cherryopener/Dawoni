"""원본 자료와 작업공간 메타데이터."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from dawoni.models.base import DawoniModel, utcnow

SourceKind = Literal["problem_bank", "textbook", "exam", "mock_exam", "handwriting", "other"]
"""문제은행 / 교재(개념+문제) / 시험지 / 모의고사 / 손글씨 답안 / 기타"""

IngestStatus = Literal["new", "ingesting", "ingested", "failed"]


class SourceIngestState(DawoniModel):
    status: IngestStatus = "new"
    item_count: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=utcnow)
    message: str | None = None


class SourceChunk(DawoniModel):
    """문제은행 DB화 구간: 두꺼운 책은 단원·쪽 범위로 나눠 넣는다 (끊겨도 이어 하기).

    교재에서는 구간 하나가 **챕터** 하나다: 개념 쪽(→ 범위 프로필) + 문제 쪽(→ 문제은행).
    """

    index: int = Field(ge=1)
    pages: list[int] = Field(
        default_factory=lambda: list[int](), description="문제 쪽 (1부터, 개념만 있는 챕터는 비움)"
    )
    key_pages: list[int] = Field(default_factory=lambda: list[int](), description="정답·해설 쪽")
    concept_pages: list[int] = Field(
        default_factory=lambda: list[int](), description="교재: 개념 설명 쪽 (→ 범위 프로필)"
    )
    unit_hint: str | None = Field(default=None, description="이 구간의 단원 ID (강사가 아는 경우)")
    label: str | None = Field(default=None, description="예: 1단원 힘과 운동")
    status: IngestStatus = "new"
    """문제 쪽 DB화 상태."""
    concept_status: IngestStatus = "new"
    concept_message: str | None = None
    scope_id: str | None = Field(default=None, description="교재: 이 챕터 개념의 범위 프로필")
    item_ids: list[str] = Field(default_factory=lambda: list[str]())
    message: str | None = None
    by: str | None = None
    machine: str | None = Field(
        default=None, description="DB화 중인 PC (팀 공유: 두 PC가 동시에 안 하게)"
    )
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _has_pages(self) -> SourceChunk:
        if not self.pages and not self.concept_pages:
            raise ValueError("문제 쪽이나 개념 쪽을 하나 이상 적어 주세요")
        return self


class Source(DawoniModel):
    """원본 파일. 식별자는 파일명이 아니라 내용 해시 → 같은 파일을 두 번 DB화하지 않는다."""

    source_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    filename: str
    rel_path: str = Field(description="작업공간 sources/ 기준 경로")
    kind: SourceKind = "other"
    size_bytes: int = Field(ge=0)
    pages: int | None = Field(default=None, ge=1)
    title: str | None = None
    subject: str | None = None
    added_at: datetime = Field(default_factory=utcnow)
    ingest: SourceIngestState = Field(default_factory=SourceIngestState)
    publisher: str | None = Field(default=None, description="출판사·저자·강사 (출처 표시)")
    year: int | None = None
    level: str | None = Field(default=None, description="교재: 학생 수준 (개념 범위 프로필에 씀)")
    key_attached_from: int | None = Field(
        default=None, ge=1, description="따로 받은 해설 PDF를 뒤에 붙였으면 그 첫 쪽 (기출 해설 등)"
    )
    unit_trees: list[str] = Field(
        default_factory=lambda: list[str](), description="문제은행: 분류할 단원표 ID (예: phy1)"
    )
    chunks: list[SourceChunk] = Field(default_factory=lambda: list[SourceChunk]())


WorkspaceMode = Literal["personal", "team"]
"""personal: 혼자 쓰는 폴더 · team: 여러 강사가 공유 폴더로 함께 씀."""


SUBJECT_GROUPS: tuple[str, ...] = ("물리", "화학", "생명과학", "지구과학")
"""과목 팀 단위 (단원표의 과목군과 같다)."""


class WorkspaceMeta(DawoniModel):
    schema_version: int = 1
    name: str
    mode: WorkspaceMode = "personal"
    subjects: list[str] = Field(
        default_factory=lambda: list[str](),
        description="이 작업공간의 과목(팀) — 예: ['물리']. 비면 모든 과목. 과목마다 작업공간을 "
        "따로 두면 그 과목 강사들끼리 폴더 하나를 공유한다",
    )
    created_at: datetime = Field(default_factory=utcnow)
