"""M12 함께 쓰기: 클라우드 폴더 짐작, 충돌 사본, 사용 중 표식, 모드 API."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from test_grading import FIX
from test_server import _client  # pyright: ignore[reportPrivateUsage]

from dawoni.config import AppConfig, save_config
from dawoni.fsio import read_json
from dawoni.grading.store import ExamStore
from dawoni.models.base import utcnow
from dawoni.models.grading import Exam, ExamQuestion
from dawoni.paths import AppPaths
from dawoni.sharing import (
    FRESH,
    cloud_provider,
    conflict_copies,
    machine_id,
    mark_in_use,
    others_using,
    release,
)
from dawoni.workspace import create_workspace, open_workspace, set_mode


@pytest.mark.parametrize(
    ("path", "name"),
    [
        ("~/Library/CloudStorage/GoogleDrive-a@b.com/내 드라이브/다워니", "Google Drive"),
        ("G:/My Drive/Dawoni", "Google Drive"),
        ("C:/Users/kim/OneDrive - 학원/다워니", "OneDrive"),
        ("~/Dropbox/dawoni", "Dropbox"),
        ("~/Library/Mobile Documents/com~apple~CloudDocs/다워니", "iCloud Drive"),
        ("~/Library/CloudStorage/Box-Box/다워니", "클라우드 폴더"),
        ("~/Documents/다워니", None),
    ],
)
def test_cloud_provider(path: str, name: str | None) -> None:
    assert cloud_provider(Path(path)) == name


def test_conflict_copies(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    d = ws.exams / "exam-1"
    d.mkdir(parents=True)
    names = [
        "exam.json",
        "exam (1).json",
        "grade.json",
        "grade (김선생의 충돌 사본 2026-10-27).json",
    ]
    names += ["rubric.json", "rubric 2.json", "score.json", "score-DESKTOP-AB12.json"]
    names += ["photo (1).png", "lonely (1).json"]
    for n in names:
        (d / n).write_text("{}", encoding="utf-8")
    (d / ".inuse").mkdir()
    (d / ".inuse" / "a.json").write_text("{}")
    (d / ".inuse" / "a (1).json").write_text("{}")
    found = {c.path.split("/")[-1]: c.original.split("/")[-1] for c in conflict_copies(ws)}
    assert found == {
        "exam (1).json": "exam.json",
        "grade (김선생의 충돌 사본 2026-10-27).json": "grade.json",
        "rubric 2.json": "rubric.json",
        "score-DESKTOP-AB12.json": "score.json",
    }, "원본이 없으면(lonely, photo) 사본으로 보지 않음 · .inuse는 건너뜀"


def test_presence(tmp_path: Path, app_paths: AppPaths) -> None:
    ws = create_workspace(tmp_path / "ws", mode="team")
    me = machine_id(app_paths)
    assert machine_id(app_paths) == me, "PC ID는 고정"
    mark_in_use(ws, "exam-1", me, "나")
    mark_in_use(ws, "exam-1", "other-pc", "김선생")
    others = others_using(ws, "exam-1", me)
    assert [o.teacher for o in others] == ["김선생"], "내 표식은 빼고"
    assert others_using(ws, "exam-1", me, now=utcnow() + FRESH + timedelta(seconds=5)) == []
    release(ws, "exam-1", "other-pc")
    assert others_using(ws, "exam-1", me) == []


def test_mode_and_presence_api(app_paths: AppPaths, tmp_path: Path) -> None:
    root = tmp_path / "Library" / "CloudStorage" / "GoogleDrive-x" / "ws"
    c = _client(app_paths)
    assert c.get("/api/workspace/sharing", params={"path": str(root)}).json() == {
        "provider": "Google Drive"
    }
    st = c.post("/api/workspace", json={"path": str(root), "create": True, "mode": "team"}).json()
    assert st["workspace_mode"] == "team" and st["machine"]
    sh = c.get("/api/workspace/sharing").json()
    assert sh == {"mode": "team", "provider": "Google Drive", "conflicts": []}

    ws, _ = open_workspace(root)
    q = ExamQuestion.model_validate(read_json(FIX / "questions.json")[0])
    store = ExamStore.create(ws, Exam(title="중간", scope_id="s", questions=[q]))
    mark_in_use(ws, store.exam_id, "other-pc", "김선생")
    r = c.post(f"/api/exams/{store.exam_id}/presence", json={"teacher": "나"}).json()
    assert r["mode"] == "team" and [o["teacher"] for o in r["others"]] == ["김선생"]
    assert (store.root / ".inuse" / f"{st['machine']}.json").exists()
    c.delete(f"/api/exams/{store.exam_id}/presence")
    assert not (store.root / ".inuse" / f"{st['machine']}.json").exists()

    assert c.put("/api/workspace/mode", json={"mode": "personal"}).json()["mode"] == "personal"
    r = c.post(f"/api/exams/{store.exam_id}/presence", json={}).json()
    assert r == {"mode": "personal", "others": []}, "혼자 쓰기면 표식을 남기지 않음"
    set_mode(root, "team")
    assert c.get("/api/status").json()["workspace_mode"] == "team", (
        "모드는 폴더에 저장 (모든 PC 공통)"
    )


def test_app_state_files_stay_local(app_paths: AppPaths, tmp_path: Path) -> None:
    """설정·로그·캐시·PC ID는 앱 데이터 폴더에 — 작업공간(공유 폴더)에는 생기지 않는다."""
    ws = create_workspace(tmp_path / "ws", mode="team")
    cfg = AppConfig()
    cfg.workspace = ws.root
    save_config(app_paths, cfg)
    c = _client(app_paths)
    c.get("/api/status")
    names = {p.name for p in ws.root.rglob("*")}
    assert not names & {
        "config.json",
        "logs",
        "cache",
        "machine-id",
        "index.sqlite3",
        "calls.jsonl",
    }
    assert (app_paths.root / "machine-id").exists()
