"""원본 자료 등록. 식별자 = 파일 내용의 SHA-256 → 같은 파일을 두 번 DB화하지 않는다."""

from __future__ import annotations

import shutil
from pathlib import Path

from dawoni.fsio import read_json, sha256_file, write_model_atomic
from dawoni.grading.store import safe_name
from dawoni.models.source import Source, SourceKind
from dawoni.paths import WorkspacePaths


def _meta_path(ws: WorkspacePaths, source_id: str) -> Path:
    return ws.sources / ".dawoni" / f"{source_id}.json"


def find_source(ws: WorkspacePaths, path: Path) -> Source | None:
    p = _meta_path(ws, sha256_file(path))
    return Source.model_validate(read_json(p)) if p.exists() else None


def register_source(
    ws: WorkspacePaths, path: Path, kind: SourceKind, *, title: str | None = None
) -> Source:
    """작업공간 sources/에 복사하고 등록. 이미 있으면 기존 기록을 돌려준다."""
    sid = sha256_file(path)
    meta = _meta_path(ws, sid)
    if meta.exists():
        return Source.model_validate(read_json(meta))
    dest = ws.sources / safe_name(path.name)
    if dest.exists() and sha256_file(dest) != sid:
        dest = ws.sources / f"{dest.stem}_{sid[:8]}{dest.suffix}"
    if not dest.exists():
        shutil.copy2(path, dest)
    src = Source(
        source_id=sid,
        filename=path.name,
        rel_path=dest.name,
        kind=kind,
        size_bytes=path.stat().st_size,
        title=title,
    )
    write_model_atomic(meta, src)
    return src


def update_source(ws: WorkspacePaths, src: Source) -> None:
    write_model_atomic(_meta_path(ws, src.source_id), src)
