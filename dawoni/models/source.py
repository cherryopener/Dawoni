"""원본 자료와 작업공간 메타데이터."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from dawoni.models.base import DawoniModel, utcnow

SourceKind = Literal["problem_bank", "textbook", "exam", "mock_exam", "handwriting", "other"]
"""문제은행 / 교재(개념+문제) / 시험지 / 모의고사 / 손글씨 답안 / 기타"""

IngestStatus = Literal["new", "ingesting", "ingested", "failed"]


class SourceIngestState(DawoniModel):
    status: IngestStatus = "new"
    item_count: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=utcnow)
    message: str | None = None


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


WorkspaceMode = Literal["personal", "team"]
"""personal: 혼자 쓰는 폴더 · team: 여러 강사가 공유 폴더로 함께 씀."""


class WorkspaceMeta(DawoniModel):
    schema_version: int = 1
    name: str
    mode: WorkspaceMode = "personal"
    created_at: datetime = Field(default_factory=utcnow)
