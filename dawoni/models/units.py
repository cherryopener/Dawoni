"""공통 단원표 — 과목마다 하나. 문제은행 문항·범위 프로필이 단원 ID로 가리킨다.

교재·문제집·기출은 목차가 서로 달라도 같은 단원표에 모인다. 단원 ID는 이름을 바꿔도
그대로라 분류가 깨지지 않는다. 합친 단원의 옛 ID는 `aliases`로 새 ID를 가리킨다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from pydantic import Field, model_validator

from dawoni.models.base import DawoniModel, utcnow

UNIT_ID = r"^[a-z0-9]+(\.[a-z0-9]+)+$"


class Unit(DawoniModel):
    id: str = Field(pattern=UNIT_ID, description="단원표 ID로 시작 (예: phy1.1.2)")
    title: str = Field(min_length=1)
    children: list[Unit] = Field(default_factory=lambda: list[Unit]())


class UnitTree(DawoniModel):
    id: str = Field(pattern=r"^[a-z0-9]+$", description="파일 이름 (예: phy1)")
    subject: str = Field(min_length=1, description="과목 이름 (예: 물리학 I)")
    group: str = Field(min_length=1, description="과목군 (예: 물리) — 화면에서 묶는 기준")
    curriculum: str | None = Field(default=None, description="예: 2015 개정")
    units: list[Unit] = Field(default_factory=lambda: list[Unit]())
    aliases: dict[str, str] = Field(
        default_factory=lambda: dict[str, str](), description="합친 단원의 옛 ID → 새 ID"
    )
    reviewed: bool = Field(default=False, description="강사가 검토했는가 (기본 초안은 False)")
    version: int = Field(default=1, ge=1)
    updated_at: datetime = Field(default_factory=utcnow)
    updated_by: str | None = None

    @model_validator(mode="after")
    def _ids(self) -> UnitTree:
        ids = [u.id for u, _ in self.walk()]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"단원 ID 중복: {dup}")
        foreign = [i for i in ids if not i.startswith(f"{self.id}.")]
        if foreign:
            raise ValueError(f"단원 ID는 '{self.id}.'으로 시작해야 함: {foreign}")
        live = set(ids)
        bad = {old: new for old, new in self.aliases.items() if old in live or new not in live}
        if bad:
            raise ValueError(
                f"합친 단원 표시가 맞지 않음 (옛 ID가 남아 있거나 새 ID가 없음): {bad}"
            )
        return self

    def walk(self) -> Iterator[tuple[Unit, tuple[str, ...]]]:
        """(단원, 위 단원 제목들) — 앞에서부터 깊이 우선."""

        def go(units: list[Unit], path: tuple[str, ...]) -> Iterator[tuple[Unit, tuple[str, ...]]]:
            for u in units:
                yield u, path
                yield from go(u.children, (*path, u.title))

        yield from go(self.units, ())

    def resolve(self, unit_id: str) -> str | None:
        """합친 단원이면 새 ID로. 없는 ID면 None."""
        seen: set[str] = set()
        while unit_id in self.aliases and unit_id not in seen:
            seen.add(unit_id)
            unit_id = self.aliases[unit_id]
        return unit_id if any(u.id == unit_id for u, _ in self.walk()) else None

    def label(self, unit_id: str) -> str | None:
        """예: 물리학 I > 역학과 에너지 > 힘과 운동."""
        rid = self.resolve(unit_id)
        for u, path in self.walk():
            if u.id == rid:
                return " > ".join((self.subject, *path, u.title))
        return None

    def descendants(self, unit_id: str) -> set[str]:
        """그 단원과 아래 단원 모두의 ID (단원을 고르면 아래 단원 문항도 포함)."""
        rid = self.resolve(unit_id)
        for u, _ in self.walk():
            if u.id == rid:
                out: set[str] = set()
                stack = [u]
                while stack:
                    x = stack.pop()
                    out.add(x.id)
                    stack.extend(x.children)
                return out
        return set()
