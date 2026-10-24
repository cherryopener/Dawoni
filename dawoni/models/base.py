from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict


class DawoniModel(BaseModel):
    """모든 데이터 모델의 기반.

    `extra="forbid"`: AI가 스키마에 없는 필드를 지어내면 검증 실패 → 수리 루프에서 바로잡는다.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """시간순 정렬되는 ID (uuid7). 예: `it-019a1f…`"""
    return f"{prefix}-{uuid.uuid7().hex}"
