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
    WORK_FRESH,
    WorkInProgress,
    cloud_provider,
    conflict_copies,
    machine_id,
    mark_in_use,
    others_using,
    release,
    working_on,
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


def test_working_on_blocks_other_pc(tmp_path: Path) -> None:
    import json

    ws = create_workspace(tmp_path / "ws", mode="team")
    with working_on(ws, "exam-1", "rubric", "2", "pc-a", "김선생"):
        mark = ws.exams / "exam-1" / ".locks" / "rubric-2.json"
        assert mark.is_file()
        with (
            pytest.raises(WorkInProgress, match="김선생 선생님이 지금 만들고"),
            working_on(ws, "exam-1", "rubric", "2", "pc-b", "이선생"),
        ):
            pass
        with working_on(ws, "exam-1", "rubric", "3", "pc-b", "이선생"):
            pass  # 다른 문항은 된다
        with working_on(ws, "exam-1", "solve", "2", "pc-b", None):
            pass  # 다른 작업도 된다
    assert not mark.exists(), "끝나면 지운다"

    # 멈춘 PC의 오래된 표식은 무시
    mark.parent.mkdir(parents=True, exist_ok=True)
    old = (utcnow() - WORK_FRESH - timedelta(minutes=1)).isoformat()
    mark.write_text(json.dumps({"machine": "pc-a", "task": "rubric", "at": old}))
    with working_on(ws, "exam-1", "rubric", "2", "pc-b", None):
        pass
    # 실패해도 지운다
    with pytest.raises(RuntimeError), working_on(ws, "exam-1", "rubric", "2", "pc-b", None):
        raise RuntimeError("AI 오류")
    assert not mark.exists()


def test_each_in_parallel() -> None:
    import contextvars
    import time

    from dawoni.grading.pipeline import PipelineError
    from dawoni.grading.workflow import each_in_parallel

    var: contextvars.ContextVar[str] = contextvars.ContextVar("v", default="없음")
    var.set("작업")
    seen: list[str] = []
    msgs: list[str] = []

    def slow(n: int) -> int:
        seen.append(var.get())
        time.sleep(0.3 if n == 1 else 0.1)
        return n * 10

    t0 = time.monotonic()
    out = each_in_parallel([1, 2, 3], slow, name=lambda n: f"문항 {n}", what="해설", workers=3,
                           progress=lambda d, t, m: msgs.append(m))  # fmt: skip
    assert out == [10, 20, 30], "입력 순서대로"
    assert time.monotonic() - t0 < 0.55, "동시에 돈다"
    assert seen == ["작업"] * 3, "작업의 ContextVar(취소·진행)가 스레드로 넘어간다"
    assert any("진행 중" in m for m in msgs)

    done: list[int] = []

    def flaky(n: int) -> int:
        if n == 2:
            raise ValueError("AI 응답 오류")
        done.append(n)
        return n

    with pytest.raises(PipelineError, match=r"1개 실패 \(나머지는 저장됨\) — 문항 2: AI 응답 오류"):
        each_in_parallel([1, 2, 3], flaky, name=lambda n: f"문항 {n}", what="해설", workers=2)
    assert sorted(done) == [1, 3], "실패한 문항이 있어도 나머지는 끝까지"
    with pytest.raises(ValueError, match="AI 응답 오류"):
        each_in_parallel([2], flaky, name=str, what="해설", workers=3)


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
