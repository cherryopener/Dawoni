"""설정·파일 입출력·작업공간·명령줄."""

from __future__ import annotations

import hashlib
import json
import stat
import sys
from pathlib import Path

import pytest

from dawoni.cli import main
from dawoni.config import AppConfig, load_config, save_config
from dawoni.fsio import read_json, sha256_file, write_json_atomic
from dawoni.models import JobProgress
from dawoni.paths import AppPaths, WorkspacePaths
from dawoni.workspace import WorkspaceError, create_workspace, open_workspace


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "a.json"
    write_json_atomic(target, {"한글": "값"})
    write_json_atomic(target, {"한글": "새 값"})
    assert read_json(target) == {"한글": "새 값"}
    assert [p.name for p in target.parent.iterdir()] == ["a.json"]
    assert "한글" in target.read_text(encoding="utf-8")  # ensure_ascii=False


def test_sha256_file(tmp_path: Path) -> None:
    p = tmp_path / "x.bin"
    p.write_bytes(b"dawoni" * 1000)
    assert sha256_file(p, chunk_size=7) == hashlib.sha256(b"dawoni" * 1000).hexdigest()


def test_config_defaults_when_missing(app_paths: AppPaths) -> None:
    cfg = load_config(app_paths)
    assert cfg.default_agent == "agy"
    assert cfg.agent("agy").model
    assert "물리" in cfg.subjects


def test_config_roundtrip(app_paths: AppPaths, tmp_path: Path) -> None:
    cfg = AppConfig(workspace=tmp_path / "ws")
    cfg.agents["claude"].model = "claude-sonnet-5-5"
    cfg.ocr.clova_secret = "비밀"
    save_config(app_paths, cfg)
    assert load_config(app_paths) == cfg
    if sys.platform != "win32":
        mode = stat.S_IMODE(app_paths.config_file.stat().st_mode)
        assert mode == 0o600


def test_config_ignores_unknown_keys(app_paths: AppPaths) -> None:
    app_paths.config_file.write_text(
        json.dumps({"default_agent": "claude", "removed_option": 1}), encoding="utf-8"
    )
    assert load_config(app_paths).default_agent == "claude"


def test_broken_config_is_backed_up(app_paths: AppPaths) -> None:
    app_paths.config_file.write_text("{ 깨진", encoding="utf-8")
    cfg = load_config(app_paths)
    assert cfg == AppConfig()
    assert app_paths.config_file.with_name("config.json.broken").exists()


def test_job_progress_bounds() -> None:
    assert JobProgress(done=1, total=4).fraction == 0.25
    assert JobProgress().fraction is None
    with pytest.raises(ValueError, match="done"):
        JobProgress(done=5, total=4)


def test_workspace_create_and_open(tmp_path: Path) -> None:
    root = tmp_path / "학원 작업공간"
    ws = create_workspace(root)
    assert all(d.is_dir() for d in ws.all_dirs())
    ws2, meta = open_workspace(root)
    assert ws2 == WorkspacePaths(root)
    assert meta.name == "학원 작업공간"


def test_open_non_workspace_fails(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError):
        open_workspace(tmp_path)


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.startswith("dawoni ")


def test_cli_schema(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schema", "item-content"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["title"] == "ItemContent"


def test_cli_workspace_init(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["workspace", "init", str(tmp_path / "ws")]) == 0
    assert (tmp_path / "ws" / "dawoni-workspace.json").exists()
