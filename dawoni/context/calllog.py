"""AI 호출 기록 (`<앱 데이터>/logs/calls.jsonl`).

한 줄 = 시도 1회. 프롬프트를 고친 뒤 성공률·수리 횟수·토큰을 비교하는 근거가 된다.
프롬프트 본문은 남기지 않는다(학생 답안이 들어갈 수 있음). 필요하면 격리 폴더 보존 옵션을 쓴다.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from dawoni.models.base import utcnow

_lock = threading.Lock()


@dataclass
class CallRecord:
    task: str
    attempt: int
    ok: bool
    role: str | None = None
    tier: str | None = None
    agent: str | None = None
    model: str | None = None
    template_versions: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    rule_ids: list[str] = field(default_factory=lambda: list[str]())
    est_prompt_tokens: int = 0
    total_tokens: int | None = None
    duration_s: float | None = None
    schema_enforced: bool | None = None
    errors: list[str] = field(default_factory=lambda: list[str]())
    error_code: str | None = None
    at: datetime = field(default_factory=utcnow)


class CallLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, rec: CallRecord) -> None:
        d = asdict(rec)
        d["at"] = rec.at.isoformat()
        d["errors"] = rec.errors[:10]
        line = json.dumps(d, ensure_ascii=False)
        with _lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def read(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x]
