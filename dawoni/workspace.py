"""작업공간 만들기·열기."""

from __future__ import annotations

from pathlib import Path

from dawoni.fsio import read_json, write_model_atomic
from dawoni.models.source import SUBJECT_GROUPS, WorkspaceMeta, WorkspaceMode
from dawoni.paths import LEGACY_WORKSPACE_MARKER, WorkspacePaths


class WorkspaceError(Exception):
    pass


def _check_subjects(subjects: list[str]) -> list[str]:
    bad = [s for s in subjects if s not in SUBJECT_GROUPS]
    if bad:
        raise WorkspaceError(f"알 수 없는 과목 {bad} — {', '.join(SUBJECT_GROUPS)} 중에서 고르세요")
    return list(dict.fromkeys(subjects))


OLD_DAWONI = (
    "옛 다워니(Dawoni-old)가 만든 작업공간입니다 — 새 다워니에서는 빈 폴더를 골라 새 작업공간을 "
    "만드세요: {root}"
)


def _adopt_legacy(ws: WorkspacePaths) -> None:
    """다워이 시절 작업공간(`dawoai-workspace.json`, `sources/.dawoai/`)을 새 이름으로 바꾼다."""
    legacy = ws.root / LEGACY_WORKSPACE_MARKER
    if not legacy.is_file() or ws.marker.exists():
        return
    meta = WorkspaceMeta.model_validate({**read_json(legacy), "app": "dawoni"})
    write_model_atomic(ws.marker, meta)
    old_sources = ws.sources / ".dawoai"
    if old_sources.is_dir() and not (ws.sources / ".dawoni").exists():
        old_sources.rename(ws.sources / ".dawoni")
    legacy.unlink()


def _check_not_old_dawoni(ws: WorkspacePaths) -> None:
    if not ws.marker.is_file():
        return
    data = read_json(ws.marker)
    if isinstance(data, dict) and "app" not in data:
        raise WorkspaceError(OLD_DAWONI.format(root=ws.root))


def create_workspace(
    root: Path,
    name: str | None = None,
    mode: WorkspaceMode = "personal",
    subjects: list[str] | None = None,
) -> WorkspacePaths:
    """폴더 구조와 표식 파일을 만든다. 이미 작업공간이면 빠진 폴더만 채운다.

    `subjects`: 과목 팀 작업공간 (예: ['물리']) — 그 과목 단원표만 깔린다."""
    ws = WorkspacePaths(root)
    if root.exists() and not root.is_dir():
        raise WorkspaceError(f"폴더가 아닙니다: {root}")
    subs = _check_subjects(subjects or [])
    _adopt_legacy(ws)
    _check_not_old_dawoni(ws)
    for d in ws.all_dirs():
        d.mkdir(parents=True, exist_ok=True)
    if not ws.marker.exists():
        write_model_atomic(
            ws.marker, WorkspaceMeta(name=name or root.name, mode=mode, subjects=subs)
        )
    return ws


def workspace_subjects(ws: WorkspacePaths) -> list[str]:
    """작업공간의 과목(팀). 비면 모든 과목."""
    if not ws.marker.exists():
        return []
    try:
        return WorkspaceMeta.model_validate(read_json(ws.marker)).subjects
    except ValueError:
        return []


def set_subjects(root: Path, subjects: list[str]) -> WorkspaceMeta:
    """과목(팀) 바꾸기. 이미 깔린 다른 과목 단원표는 그대로 둔다 (문항이 쓰고 있을 수 있음)."""
    ws, meta = open_workspace(root)
    meta = meta.model_copy(update={"subjects": _check_subjects(subjects)})
    write_model_atomic(ws.marker, meta)
    return meta


def open_workspace(root: Path) -> tuple[WorkspacePaths, WorkspaceMeta]:
    ws = WorkspacePaths(root)
    _adopt_legacy(ws)
    if not ws.marker.exists():
        raise WorkspaceError(f"다워니 작업공간이 아닙니다 (표식 파일 없음): {root}")
    _check_not_old_dawoni(ws)
    meta = WorkspaceMeta.model_validate(read_json(ws.marker))
    for d in ws.all_dirs():
        d.mkdir(parents=True, exist_ok=True)
    return ws, meta


def set_mode(root: Path, mode: WorkspaceMode) -> WorkspaceMeta:
    """혼자 쓰기 ↔ 함께 쓰기. 표식 파일에 저장되므로 같은 폴더를 쓰는 모든 PC에 적용된다."""
    ws, meta = open_workspace(root)
    meta = meta.model_copy(update={"mode": mode})
    write_model_atomic(ws.marker, meta)
    return meta
