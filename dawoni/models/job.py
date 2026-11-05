"""백그라운드 작업 상태.

AIHelper에서 `current_step = idx`처럼 숫자만 덮어써 진행률이 깨졌던 문제를 막기 위해
진행 상황은 항상 `JobProgress(done, total, message)`로만 표현한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from dawoni.models.base import DawoniModel, new_id, utcnow

JobStatus = Literal["queued", "running", "paused", "succeeded", "failed", "cancelled"]
TERMINAL_STATUSES: frozenset[JobStatus] = frozenset({"succeeded", "failed", "cancelled"})


class JobProgress(DawoniModel):
    done: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0, description="0이면 전체 양을 아직 모름")
    message: str = ""

    @model_validator(mode="after")
    def _bounds(self) -> JobProgress:
        if self.total and self.done > self.total:
            raise ValueError(f"done({self.done}) > total({self.total})")
        return self

    @property
    def fraction(self) -> float | None:
        return self.done / self.total if self.total else None


class JobError(DawoniModel):
    code: str = Field(description="예: agent_timeout, agent_quota, invalid_output")
    message: str = Field(description="강사에게 보여 줄 한국어 안내")
    detail: str | None = None
    alternatives: list[str] = Field(
        default_factory=lambda: list[str](),
        description="AI를 쓸 수 없어 멈췄을 때 넘어갈 수 있는 다른 AI (강사가 고른다)",
    )


class Job(DawoniModel):
    id: str = Field(default_factory=lambda: new_id("job"))
    kind: str
    status: JobStatus = "queued"
    progress: JobProgress = Field(default_factory=JobProgress)
    params: dict[str, JsonValue] = Field(default_factory=lambda: dict[str, JsonValue]())
    result: dict[str, JsonValue] | None = None
    error: JobError | None = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES
