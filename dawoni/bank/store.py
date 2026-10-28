"""문제은행 저장: 작업공간 `bank/<문항ID>/item.json` + 같은 폴더의 `figures/`.

팀 공유 폴더에서도 충돌이 적게: 여럿이 함께 고치는 파일(공용 목록 등)을 두지 않는다.
목록은 문항 파일에서 만들고, 빨리 읽으려고 PC별 캐시(앱 데이터 폴더)에만 요약을 둔다.
`bank/generated/`는 제작 초안(GenStore) 자리라 여기서 다루지 않는다.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import Field

from dawoni.bank import BankError
from dawoni.fsio import read_json, write_json_atomic, write_model_atomic
from dawoni.grading.render import blocks_text
from dawoni.models.bank import BankItem, ItemOrigin, OriginalLevel
from dawoni.models.base import DawoniModel, utcnow
from dawoni.models.item import ItemType
from dawoni.models.source import SourceKind
from dawoni.paths import WorkspacePaths

PREVIEW_CHARS = 120


class BankSummary(DawoniModel):
    """목록 한 줄."""

    id: str
    number: str
    item_type: ItemType
    points: float
    subject: str | None
    unit_ids: list[str]
    source: str = Field(description="보이는 출처 (예: OO 문제집 37쪽 12번)")
    source_id: str | None
    source_kind: SourceKind | Literal["dawoni"]
    origin: ItemOrigin
    original_level: OriginalLevel | None
    solution_by: Literal["source", "ai"] | None
    needs_check: bool
    reviewed: bool
    difficulty: int | None
    parent_id: str | None
    preview: str
    updated_by: str | None


def summarize(it: BankItem) -> BankSummary:
    text = " ".join(blocks_text(it.question.item.stem).split())
    return BankSummary(
        id=it.id,
        number=it.question.number,
        item_type=it.question.item.item_type,
        points=it.question.points,
        subject=it.subject,
        unit_ids=it.unit_ids,
        source=it.source.display(),
        source_id=it.source.source_id,
        source_kind=it.source.kind,
        origin=it.origin,
        original_level=it.original.level if it.original else None,
        solution_by=it.solution_by,
        needs_check=it.needs_check,
        reviewed=it.reviewed,
        difficulty=it.difficulty,
        parent_id=it.parent_id,
        preview=text[:PREVIEW_CHARS] + ("…" if len(text) > PREVIEW_CHARS else ""),
        updated_by=it.updated_by or it.created_by,
    )


class BankStore:
    def __init__(self, ws: WorkspacePaths, cache_dir: Path | None = None) -> None:
        self.ws = ws
        self.dir = ws.bank
        key = hashlib.sha256(str(ws.root.resolve()).encode()).hexdigest()[:16]
        self._cache = cache_dir / f"bank_{key}.json" if cache_dir else None

    def item_dir(self, item_id: str) -> Path:
        return self.ws.item_dir(item_id)

    def _path(self, item_id: str) -> Path:
        return self.item_dir(item_id) / "item.json"

    def exists(self, item_id: str) -> bool:
        return self._path(item_id).exists()

    def get(self, item_id: str) -> BankItem:
        p = self._path(item_id)
        if not p.exists():
            raise KeyError(f"문항 {item_id} 없음")
        return BankItem.model_validate(read_json(p))

    def save(
        self, it: BankItem, *, by: str | None = None, base_revision: int | None = None
    ) -> BankItem:
        """`base_revision`: 강사가 고치기 시작할 때 읽은 판. 그새 다른 강사가 고쳤으면 거부."""
        p = self._path(it.id)
        if p.exists():
            old = BankItem.model_validate(read_json(p))
            if base_revision is not None and old.revision != base_revision:
                who = f" ({old.updated_by})" if old.updated_by else ""
                raise BankError(
                    f"다른 강사가 먼저 이 문항을 고쳤습니다{who} — 다시 불러와 고쳐 주세요"
                )
            it = it.model_copy(
                update={"revision": old.revision + 1, "updated_at": utcnow(), "updated_by": by,
                        "created_at": old.created_at, "created_by": old.created_by}
            )  # fmt: skip
        elif by and not it.created_by:
            it = it.model_copy(update={"created_by": by, "updated_by": by})
        write_model_atomic(p, BankItem.model_validate(it.model_dump()))
        return it

    def add_figures(self, item_id: str, files: dict[str, Path]) -> dict[str, str]:
        """그림 파일을 문항 폴더로 복사 → 그림 ID → `bank/` 기준 경로."""
        out: dict[str, str] = {}
        d = self.item_dir(item_id) / "figures"
        for fid, f in files.items():
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, d / f"{fid}.png")
            out[fid] = f"{item_id}/figures/{fid}.png"
        return out

    def delete(self, item_id: str) -> None:
        d = self.item_dir(item_id)
        if not (d / "item.json").exists():
            raise KeyError(f"문항 {item_id} 없음")
        shutil.rmtree(d)

    def ids(self) -> list[str]:
        if not self.dir.is_dir():
            return []
        return sorted(p.parent.name for p in self.dir.glob("*/item.json"))

    def items(self, ids: Iterable[str] | None = None) -> list[BankItem]:
        return [self.get(i) for i in (self.ids() if ids is None else ids)]

    def summaries(self) -> list[BankSummary]:
        """목록. 문항 파일의 수정 시각이 같으면 PC별 캐시의 요약을 쓴다."""
        cache: dict[str, dict[str, object]] = {}
        if self._cache and self._cache.exists():
            try:
                cache = read_json(self._cache)
            except ValueError:
                cache = {}
        fresh: dict[str, dict[str, object]] = {}
        out: list[BankSummary] = []
        for item_id in self.ids():
            mtime = self._path(item_id).stat().st_mtime_ns
            hit = cache.get(item_id)
            if hit and hit.get("mtime") == mtime:
                s = BankSummary.model_validate(hit["summary"])
            else:
                s = summarize(self.get(item_id))
            fresh[item_id] = {"mtime": mtime, "summary": s.model_dump(mode="json")}
            out.append(s)
        if self._cache and fresh != cache:
            write_json_atomic(self._cache, fresh)
        return out

    def used_unit_ids(self) -> set[str]:
        return {u for s in self.summaries() for u in s.unit_ids}
