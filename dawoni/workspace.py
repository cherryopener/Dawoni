"""작업공간 만들기·열기."""

from __future__ import annotations

from pathlib import Path

from dawoni.fsio import read_json, write_model_atomic
from dawoni.models.source import WorkspaceMeta
from dawoni.paths import WorkspacePaths


class WorkspaceError(Exception):
    pass


def create_workspace(root: Path, name: str | None = None) -> WorkspacePaths:
    """폴더 구조와 표식 파일을 만든다. 이미 작업공간이면 빠진 폴더만 채운다."""
    ws = WorkspacePaths(root)
    if root.exists() and not root.is_dir():
        raise WorkspaceError(f"폴더가 아닙니다: {root}")
    for d in ws.all_dirs():
        d.mkdir(parents=True, exist_ok=True)
    if not ws.marker.exists():
        write_model_atomic(ws.marker, WorkspaceMeta(name=name or root.name))
    return ws


def open_workspace(root: Path) -> tuple[WorkspacePaths, WorkspaceMeta]:
    ws = WorkspacePaths(root)
    if not ws.marker.exists():
        raise WorkspaceError(f"Dawoni 작업공간이 아닙니다 (표식 파일 없음): {root}")
    meta = WorkspaceMeta.model_validate(read_json(ws.marker))
    for d in ws.all_dirs():
        d.mkdir(parents=True, exist_ok=True)
    return ws, meta
