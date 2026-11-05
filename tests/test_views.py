"""M7 화면용 API: 알림, 지금 단계, 채점 검토 한 장(근거 인용 자리), 배점 고치기, 총괄 AI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from conftest import MakeFake
from fastapi.testclient import TestClient
from test_grading import FIX, GRADE_A, RUBRIC_OK, SOL_OK
from test_server import _client, _wait  # pyright: ignore[reportPrivateUsage]

from dawoai.config import AppConfig, save_config
from dawoai.fsio import read_json
from dawoai.paths import AppPaths


def _exam(c: TestClient, title: str, questions: list[dict[str, Any]]) -> str:
    scope = c.post("/api/scopes", json=read_json(FIX / "scope.json")).json()
    body = {"title": title, "scope_id": scope["id"], "questions": questions}
    return c.post("/api/exams", json=body).json()["id"]


def test_notifications_step_and_review_sheet(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path
) -> None:
    exe = make_fake(
        "script",
        script=[
            {"response": json.dumps(r, ensure_ascii=False)} for r in (SOL_OK, RUBRIC_OK, GRADE_A)
        ],
    )
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    save_config(app_paths, cfg)
    c = _client(app_paths)
    assert c.get("/api/notifications").json() == [], "작업공간 전에는 알림 없음 (오류 아님)"
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    eid = _exam(c, "알림 시험", read_json(FIX / "questions.json")[:1])
    base = f"/api/exams/{eid}"
    assert c.get(base).json()["summary"]["step"] == "rubric"

    text = (FIX / "answers" / "A_q1.txt").read_text(encoding="utf-8")
    lines: list[dict[str, Any]] = []
    for s in ("A", "B"):
        lines = c.post(f"{base}/students/{s}/answers", json={"question": "1", "text": text}).json()[
            "lines"
        ]
    c.put(f"{base}/students/B/transcripts/1", json={"lines": [*lines, {"text": "[판독불가] 이다"}]})

    assert _wait(c, c.post(f"{base}/solve", json={}).json())["status"] == "succeeded"
    assert _wait(c, c.post(f"{base}/rubrics", json={}).json())["status"] == "succeeded"
    kinds = {n["kind"]: n for n in c.get("/api/notifications").json()}
    assert kinds["rubric"]["count"] == 1 and kinds["rubric"]["link"] == ["exams", eid, "rubric"]
    assert kinds["unreadable"]["count"] == 1 and "B 1번" in kinds["unreadable"]["detail"]
    assert kinds["job_done"]["exam"] == "알림 시험", "끝난 작업은 소식으로"

    c.post(f"{base}/questions/1/rubric/approve", json={"by": "김선생"})
    assert c.get(base).json()["summary"]["step"] == "scan", "아직 채점 전"
    job = _wait(c, c.post(f"{base}/grade", json={}).json())
    assert job["result"]["graded"] == 1
    assert c.get(base).json()["summary"]["step"] == "grade", "B가 입력 대기"
    kinds = {n["kind"] for n in c.get("/api/notifications").json()}
    assert "rubric" not in kinds and "unreadable" in kinds, "처리한 할 일은 사라진다"

    sheet = c.get(f"{base}/review/A/1").json()
    assert (sheet["total"], sheet["max"], sheet["status"]) == (4, 4, "auto")
    [part] = sheet["parts"]
    assert part["label"] == "1" and part["awarded"] == 4 and len(part["good"]) == 2
    kinds_on = [p["kind"] for line in part["lines"] for p in line["pieces"]]
    assert "good" in kinds_on, "근거 인용 자리를 칠한다"
    good_math = [p for line in part["lines"] for p in line["pieces"] if p["math"]]
    assert good_math and good_math[0]["kind"] == "good", "식에 걸친 인용은 식 통째로"
    waiting = c.get(f"{base}/review/B/1").json()
    assert waiting["total"] is None and waiting["unreadable"] == 1
    assert any(line["unreadable"] for line in waiting["parts"][0]["lines"])

    out = c.get(f"{base}/outputs").json()
    assert out["scores_exists"] and out["students"] == 2 and out["bundles"] == 1
    assert out["folder"].startswith("exports/") and out["edited"] == []


def test_points_fix_before_rubric(app_paths: AppPaths, tmp_path: Path) -> None:
    c = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    q = read_json(FIX / "questions.json")[0]
    q["points"] = 4
    q["parts"] = [{"label": "(1)", "points": 2}, {"label": "(2)", "points": 2}]
    eid = _exam(c, "배점", [q])
    path = f"/api/exams/{eid}/questions/1/points"

    bad = c.put(path, json={"parts": {"(1)": 3}})
    assert bad.status_code == 400 and "소문항 표시" in bad.json()["detail"]
    new = c.put(path, json={"parts": {"(1)": 3, "(2)": 5}}).json()
    assert new["points"] == 8 and [p["points"] for p in new["parts"]] == [3, 5]
    assert c.get(f"/api/exams/{eid}").json()["exam"]["questions"][0]["points"] == 8


def test_coordinator_plan_checks_and_runs(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path
) -> None:
    """총괄 AI: 부탁 → 작업 하나. 없는 문항을 지어내면 코드가 수리를 요청하고, 강사가 실행해야 돈다.
    AI에게는 시험 이름·문항 번호·반만 가고 학생 답안은 가지 않는다."""
    c0 = _client(app_paths)
    c0.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    eid = _exam(c0, "총괄 시험", read_json(FIX / "questions.json")[:1])
    text = (FIX / "answers" / "A_q1.txt").read_text(encoding="utf-8")
    c0.post(f"/api/exams/{eid}/students/A/answers", json={"question": "1", "text": text})

    wrong = {"kind": "action", "action": "grade", "exam_id": eid, "questions": ["7"],
             "summary": "7번 채점"}  # fmt: skip
    right = {**wrong, "questions": ["1"], "regrade": True, "summary": "1번을 다시 채점합니다"}
    exe = make_fake(
        "script", script=[{"response": json.dumps(x, ensure_ascii=False)} for x in (wrong, right)]
    )
    cfg = AppConfig.model_validate(read_json(app_paths.config_file))
    cfg.agents["agy"].executable = str(exe)
    save_config(app_paths, cfg)
    c = _client(app_paths)

    job = _wait(
        c, c.post("/api/coordinator/plan", json={"text": "1번 다시 채점해", "exam_id": eid}).json()
    )
    assert job["status"] == "succeeded", job
    plan = job["result"]["plan"]
    assert plan["questions"] == ["1"] and plan["regrade"], "없는 문항 7은 수리로 고쳐짐"
    prompts = sorted((exe.parent / (exe.name.removesuffix(".cmd") + ".state")).glob("prompt_*.txt"))
    first = prompts[0].read_text(encoding="utf-8")
    assert "총괄 시험" in first and "최솟값" not in first, "학생 답안 원문은 보내지 않는다"
    assert "['7']" in prompts[1].read_text(encoding="utf-8"), "수리 요청에 오류"

    bad = c.post("/api/coordinator/run", json={"plan": wrong})
    assert bad.status_code == 400 and "없다" in bad.json()["detail"], "실행 전에도 코드가 검사"
    r = c.post("/api/coordinator/run", json={"plan": plan, "by": "김선생"})
    assert r.status_code == 400 and "승인되지 않았습니다" in r.json()["detail"], "작업 관문 그대로"
    opened = c.post(
        "/api/coordinator/run",
        json={
            "plan": {"kind": "action", "action": "open", "screen": "students", "summary": "학생"}
        },
    ).json()
    assert opened == {"job": None, "link": ["students"]}
