"""메모리: 다워니가 강사의 수정·지적에서 배운 짧은 메모.

규칙은 강사가 정해 주는 것, 메모리는 다워니가 일하면서 배운 것. 둘 다 해당 작업의 요청마다
들어가므로 **작업마다 글자 수 상한**을 둔다(`LIMIT_CHARS`). 가득 차면 새로 배운 것은 기존 메모를
합치거나 바꿔야 들어간다 — 길어지기만 하는 전역 프롬프트가 되지 않게.

- 메모 1개 = 파일 1개 (`<작업공간>/memory/<id>.json`)
- 배워서 만든 메모는 `proposed`로 저장되고, 강사가 승인해야 적용된다
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Literal, get_args

from pydantic import Field, ValidationError

from dawoni.context.rules import RuleScope
from dawoni.context.tasks import RuleTarget
from dawoni.fsio import read_json, write_model_atomic
from dawoni.models.base import DawoniModel, new_id, utcnow

log = logging.getLogger(__name__)

LIMIT_CHARS = 600
"""작업 하나에 들어가는 메모 글자 수 합계 상한 (한글 기준 약 600토큰)."""
TARGETS: tuple[RuleTarget, ...] = get_args(RuleTarget)


class MemoryFull(ValueError):
    pass


class MemoryNote(DawoniModel):
    id: str = Field(default_factory=lambda: new_id("mem"))
    text: str = Field(min_length=1, max_length=200, description="AI에게 그대로 전달되는 한 문장")
    applies_to: list[RuleScope] = Field(min_length=1)
    enabled: bool = True
    proposed: bool = False
    replaces: list[str] = Field(
        default_factory=lambda: list[str](), description="승인하면 지울 기존 메모 ID (합치기)"
    )
    origin: Literal["learned", "teacher"] = "learned"
    request: str | None = Field(default=None, description="이 메모가 나온 강사의 말·수정 내용")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    created_by: str | None = None

    @property
    def live(self) -> bool:
        return self.enabled and not self.proposed

    def targets(self) -> tuple[RuleTarget, ...]:
        return (
            TARGETS if "all" in self.applies_to else tuple(t for t in self.applies_to if t != "all")
        )


class MemoryStore:
    def __init__(self, directory: Path, limit_chars: int = LIMIT_CHARS) -> None:
        self.dir = directory
        self.limit = limit_chars

    def _path(self, note_id: str) -> Path:
        return self.dir / f"{note_id}.json"

    def all(self) -> list[MemoryNote]:
        if not self.dir.is_dir():
            return []
        notes: list[MemoryNote] = []
        for p in sorted(self.dir.glob("mem-*.json")):
            try:
                notes.append(MemoryNote.model_validate(read_json(p)))
            except (ValueError, ValidationError) as e:
                log.warning("메모리 파일을 읽지 못함: %s (%s)", p, e)
        return sorted(notes, key=lambda n: n.created_at)

    def get(self, note_id: str) -> MemoryNote | None:
        p = self._path(note_id)
        return MemoryNote.model_validate(read_json(p)) if p.exists() else None

    def require(self, note_id: str) -> MemoryNote:
        note = self.get(note_id)
        if note is None:
            raise KeyError(f"메모 {note_id} 없음")
        return note

    def usage(self) -> dict[RuleTarget, int]:
        """작업별로 지금 들어가는 메모 글자 수."""
        used = dict.fromkeys(TARGETS, 0)
        for n in self.all():
            if n.live:
                for t in n.targets():
                    used[t] += len(n.text)
        return used

    def check_room(self, note: MemoryNote, *, without: Iterable[str] = ()) -> None:
        """`note`를 켜도 상한을 넘지 않는지. `without`: 함께 지워질 메모 ID."""
        drop = {note.id, *without}
        used = dict.fromkeys(TARGETS, 0)
        for n in self.all():
            if n.live and n.id not in drop:
                for t in n.targets():
                    used[t] += len(n.text)
        over = [t for t in note.targets() if used[t] + len(note.text) > self.limit]
        if over:
            raise MemoryFull(
                f"메모리가 가득 찼습니다 ({', '.join(over)}: 상한 {self.limit}자). "
                "안 쓰는 메모를 끄거나 지운 뒤 다시 승인하세요."
            )

    def save(self, note: MemoryNote) -> MemoryNote:
        if note.live:
            self.check_room(note)
        note.updated_at = utcnow()
        write_model_atomic(self._path(note.id), note)
        return note

    def delete(self, note_id: str) -> bool:
        p = self._path(note_id)
        if not p.exists():
            return False
        p.unlink()
        return True

    def approve(self, note_id: str) -> MemoryNote:
        """제안 승인. 합치기 제안이면 대신하는 메모들을 지운다."""
        note = self.require(note_id)
        active = note.model_copy(update={"proposed": False, "enabled": True, "replaces": []})
        self.check_room(active, without=note.replaces)
        for old in note.replaces:
            self.delete(old)
        return self.save(active)

    def active_for(self, targets: Iterable[RuleTarget]) -> list[MemoryNote]:
        ts = set(targets)
        return [n for n in self.all() if n.live and ts & set(n.targets())]
