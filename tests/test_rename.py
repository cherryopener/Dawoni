"""이름 바꾸기(다워이 → 다워니, 2026-11-05).

예전 자료가 그대로 열리고, 옛 다워니와 섞이지 않는다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_export import LINES, _sheet  # pyright: ignore[reportPrivateUsage]

import dawoni.export.hwpx as hx
from dawoni.export.hwpx import build_bundle, build_student, count_heads, read_back
from dawoni.fsio import read_json
from dawoni.paths import APP_STAMP, LEGACY_WORKSPACE_MARKER, WORKSPACE_MARKER, settle_app_data
from dawoni.workspace import WorkspaceError, create_workspace, open_workspace


def _dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    return tmp_path / "Dawoni", tmp_path / "Dawoai", tmp_path / "Dawoni-old"


def test_old_dawoni_app_data_is_moved_aside_and_dawoai_data_moves_in(tmp_path: Path) -> None:
    root, legacy, old = _dirs(tmp_path)
    root.mkdir()
    (root / "auth.json").write_text("{}", encoding="utf-8")  # 옛 다워니의 로그인
    legacy.mkdir()
    (legacy / "machine-id").write_text("abc", encoding="utf-8")
    settle_app_data(root, legacy, old)
    assert (old / "auth.json").is_file(), "옛 다워니 것은 Dawoni-old로"
    moved = (root / "machine-id").read_text(encoding="utf-8")
    assert moved == "abc", "다워이 시절 것은 새 이름으로"
    assert not legacy.exists() and (root / APP_STAMP).is_file()
    # 두 번째부터는 아무것도 옮기지 않는다
    (root / "config.json").write_text("{}", encoding="utf-8")
    settle_app_data(root, legacy, old)
    assert (root / "config.json").is_file()


def test_old_dawoni_aside_does_not_overwrite(tmp_path: Path) -> None:
    root, legacy, old = _dirs(tmp_path)
    old.mkdir()
    root.mkdir()
    (root / "config.json").write_text("{}", encoding="utf-8")
    settle_app_data(root, legacy, old)
    assert (tmp_path / "Dawoni-old-2" / "config.json").is_file() and (root / APP_STAMP).is_file()


def test_fresh_pc(tmp_path: Path) -> None:
    root, legacy, old = _dirs(tmp_path)
    settle_app_data(root, legacy, old)
    assert (root / APP_STAMP).is_file() and not old.exists()


def test_dawoai_workspace_is_adopted(tmp_path: Path) -> None:
    ws_root = tmp_path / "ws"
    (ws_root / "sources" / ".dawoai").mkdir(parents=True)
    (ws_root / "sources" / ".dawoai" / "src1.json").write_text("{}", encoding="utf-8")
    meta = {"schema_version": 1, "name": "학원", "mode": "team", "subjects": ["물리"]}
    (ws_root / LEGACY_WORKSPACE_MARKER).write_text(json.dumps(meta), encoding="utf-8")
    ws, got = open_workspace(ws_root)
    assert got.name == "학원" and got.mode == "team" and got.subjects == ["물리"]
    assert read_json(ws.marker)["app"] == "dawoni"
    assert not (ws_root / LEGACY_WORKSPACE_MARKER).exists()
    assert (ws_root / "sources" / ".dawoni" / "src1.json").is_file()


def test_old_dawoni_workspace_is_refused(tmp_path: Path) -> None:
    """옛 다워니의 표식은 이름(dawoni-workspace.json)이 같지만 `app` 칸이 없다."""
    ws_root = tmp_path / "old"
    ws_root.mkdir()
    meta: dict[str, object] = {
        "schema_version": 1,
        "name": "temp",
        "mode": "personal",
        "subjects": [],
    }
    (ws_root / WORKSPACE_MARKER).write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(WorkspaceError, match="옛 다워니"):
        open_workspace(ws_root)
    with pytest.raises(WorkspaceError, match="옛 다워니"):
        create_workspace(ws_root)


def test_new_workspace_marks_app(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "new")
    assert read_json(ws.marker)["app"] == "dawoni"
    open_workspace(tmp_path / "new")


def test_dawoai_era_hwpx_still_reads_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """이름을 바꾸기 전에 만든 파일('다워이 머리' 스타일)도 확인 완료·묶음 세기가 된다."""
    for name in ("STYLE_HEAD", "STYLE_PART", "STYLE_GRADED"):
        monkeypatch.setattr(hx, name, hx.LEGACY_STYLES[getattr(hx, name)])
    old_file = build_student(tmp_path / "old.hwpx", _sheet())
    old_bundle = build_bundle(tmp_path / "(반별)_old.hwpx", [_sheet(), _sheet()])
    monkeypatch.undo()
    got = read_back(old_file)
    assert got.head == "10월 시험 · TSS1 · 김다원 · 구룡중"
    assert list(got.parts) == list(hx.expected_text(LINES))
    assert count_heads(old_bundle) == 2
