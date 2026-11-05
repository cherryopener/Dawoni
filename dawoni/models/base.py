from __future__ import annotations

import secrets
import threading
import time
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict


class DawoniModel(BaseModel):
    """모든 데이터 모델의 기반.

    `extra="forbid"`: AI가 스키마에 없는 필드를 지어내면 검증 실패 → 수리 루프에서 바로잡는다.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def utcnow() -> datetime:
    return datetime.now(UTC)


_uuid7_lock = threading.Lock()
_uuid7_last = (0, 0)  # (밀리초, 74비트 난수 부분)


def _uuid7_hex() -> str:
    """RFC 9562 UUIDv7 (32자리 16진수). 같은 밀리초 안에서도 만든 순서대로 커진다.

    Python 3.14의 `uuid.uuid7()`을 3.13에서 쓰려고 직접 만든다 (PLAN.md §12).
    """
    global _uuid7_last
    with _uuid7_lock:
        ms = time.time_ns() // 1_000_000
        last_ms, last_rand = _uuid7_last
        if ms <= last_ms:  # 같은 밀리초(또는 시계가 뒤로 감): 앞의 값 + 1
            ms, rand = last_ms, last_rand + 1
            if rand >> 74:
                ms, rand = last_ms + 1, secrets.randbits(74)
        else:
            rand = secrets.randbits(74)
        _uuid7_last = (ms, rand)
    rand_a, rand_b = rand >> 62, rand & ((1 << 62) - 1)
    value = (ms & ((1 << 48) - 1)) << 80 | 0x7 << 76 | rand_a << 64 | 0b10 << 62 | rand_b
    return f"{value:032x}"


def new_id(prefix: str) -> str:
    """시간순 정렬되는 ID (uuid7). 예: `it-019a1f…`"""
    return f"{prefix}-{_uuid7_hex()}"
