"""M7 서버 API: 보호(호스트·토큰), 연결 마법사, 시험 1개 관통(가짜 AI), 강사 단계 관문, 출력."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import MakeFake
from fastapi.testclient import TestClient
from test_grading import FIX, GRADE_A, RUBRIC_OK, SOL_OK

from dawoai.config import AppConfig, save_config
from dawoai.export.hwpx import count_heads, read_back
from dawoai.fsio import read_json
from dawoai.paths import AppPaths
from dawoai.server import files
from dawoai.server.app import create_app

TOKEN = "test-token"
BASE = "http://127.0.0.1"


def _client(app_paths: AppPaths, *, login: bool = True) -> TestClient:
    c = TestClient(create_app(app_paths, TOKEN, web_dir=app_paths.root / "no-web"), base_url=BASE)
    if login:
        assert c.get(f"/login?t={TOKEN}").status_code == 200  # 리다이렉트 후 첫 화면
    return c


def _wait(c: TestClient, job: dict[str, Any], timeout: float = 60) -> dict[str, Any]:
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("succeeded", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"작업이 끝나지 않음: {job}")


def test_local_protection(app_paths: AppPaths) -> None:
    c = _client(app_paths, login=False)
    assert c.get("/api/status").status_code == 401, "토큰 쿠키 없으면 거부"
    assert c.get("/login?t=wrong").status_code == 403
    evil = TestClient(c.app, base_url="http://evil.example")
    assert evil.get("/login?t=" + TOKEN).status_code == 403, "다른 Host(DNS 리바인딩) 거부"
    c.get(f"/login?t={TOKEN}")
    assert c.get("/api/status").status_code == 200
    assert "화면 파일이 아직 없습니다" in c.get("/").text


def test_setup_wizard(app_paths: AppPaths, tmp_path: Path) -> None:
    c = _client(app_paths)
    st = c.get("/api/status").json()
    assert st["workspace"] is None and not st["workspace_ok"]
    r = c.get("/api/exams")
    assert r.status_code == 409 and r.json()["code"] == "needs_setup"

    assert c.post("/api/workspace", json={"path": str(tmp_path / "nope")}).status_code == 400
    st = c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True}).json()
    assert st["workspace_ok"] and st["workspace"].endswith("ws")
    assert c.get("/api/exams").json() == []

    cfg = c.patch(
        "/api/config",
        json={"default_agent": "claude", "agents": {"agy": {"model": "x-low"}},
              "ocr": {"mathpix_app_key": "secret-key"}},
    ).json()  # fmt: skip
    assert cfg["default_agent"] == "claude" and cfg["agents"]["agy"]["model"] == "x-low"
    assert cfg["ocr"]["mathpix_app_key"] == "••••", "비밀값은 화면에 보내지 않음"
    # 가려진 값을 그대로 다시 보내도 원래 키가 유지된다
    c.patch("/api/config", json={"ocr": cfg["ocr"]})
    assert AppConfig.model_validate(read_json(app_paths.config_file)).ocr.mathpix_app_key == (
        "secret-key"
    )
    detected = c.get("/api/agents/detect").json()
    assert [d["name"] for d in detected] == ["agy", "claude", "codex", "local"]


def test_exam_end_to_end(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = make_fake(
        "script",
        script=[
            {"response": json.dumps(r, ensure_ascii=False)} for r in (SOL_OK, RUBRIC_OK, GRADE_A)
        ],
    )
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.ocr.cross_check = False
    save_config(app_paths, cfg)
    c = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})

    scope = c.post("/api/scopes", json=read_json(FIX / "scope.json")).json()
    q1 = read_json(FIX / "questions.json")[:1]
    exam = c.post("/api/exams", json={"title": "화면 채점", "scope_id": scope["id"],
                                      "questions": q1}).json()  # fmt: skip
    eid = exam["id"]
    base = f"/api/exams/{eid}"

    job = _wait(c, c.post(f"{base}/solve", json={"by": "김선생"}).json())
    assert job["status"] == "succeeded", job
    assert job["progress"]["done"] == job["progress"]["total"] == 1
    assert (
        _wait(c, c.post(f"{base}/rubrics", json={"by": "이선생"}).json())["status"] == "succeeded"
    )
    made = c.get(f"{base}/questions/1").json()
    assert made["solution"]["created_by"] == "김선생" and made["solution"]["created_at"]
    assert made["rubric"]["created_by"] == "이선생", "팀원이 누가 만들었는지 본다"
    assert made["solution"]["agent"] == made["rubric"]["agent"] == "agy", "만든 AI 기록 (태그)"
    assert not (tmp_path / "ws" / "exams" / eid / ".locks").exists() or not any(
        (tmp_path / "ws" / "exams" / eid / ".locks").iterdir()
    ), "끝나면 만드는 중 표식을 지운다"

    detail = c.get(base).json()
    assert detail["questions"][0]["rubric"]["status"] == "draft"
    r = c.post(f"{base}/grade", json={})
    assert r.status_code == 400 and "승인되지 않았습니다" in r.json()["detail"]

    # 강사가 루브릭을 고침: 합계가 틀리면 거부, 맞으면 초안으로 저장
    rub = c.get(f"{base}/questions/1").json()["rubric"]
    draft = {k: rub[k] for k in ("items", "partial", "accepted_alternatives", "never_deduct")}
    draft["items"][0]["points"] = 3
    bad = c.put(f"{base}/questions/1/rubric", json=draft)
    assert bad.status_code == 400 and "합" in bad.json()["detail"]
    draft["items"][0]["points"] = 2
    draft["items"][0]["criterion"] = "표준형으로 변형 (강사 수정)"
    assert c.put(f"{base}/questions/1/rubric", json=draft).json()["version"] == 1
    approved = c.post(f"{base}/questions/1/rubric/approve", json={"by": "김선생"}).json()
    assert approved["status"] == "approved" and approved["approved_by"] == "김선생"

    text = (FIX / "answers" / "A_q1.txt").read_text(encoding="utf-8")
    t = c.post(f"{base}/students/A/answers", json={"question": "1", "text": text}).json()
    assert t["confirmed"] and len(t["lines"]) == 2

    job = _wait(c, c.post(f"{base}/grade", json={}).json())
    assert job["status"] == "succeeded" and job["result"]["graded"] == 1, job
    again = _wait(c, c.post(f"{base}/grade", json={}).json())
    assert (
        again["result"]["graded"] == 0 and "이미 채점됨" in again["result"]["skipped"][0]["reason"]
    )

    rows = c.get(f"{base}/review").json()
    assert rows[0]["student"] == "A" and rows[0]["total"] == 4
    # 강사 확인: R2를 1점으로
    bad = c.put(f"{base}/students/A/grades/1", json={"awarded": {"R2": 5}})
    assert bad.status_code == 400
    review = {"awarded": {"R2": 1}, "note": "결론 근거 부족", "by": "김선생"}
    g = c.put(f"{base}/students/A/grades/1", json=review).json()
    assert g["total"] == 3 and g["status"] == "teacher_confirmed"
    assert [j["awarded"] for j in g["ai_judgements"]] == [2, 2], "AI 원래 판정 보존"
    students = c.get(f"{base}/students").json()
    assert students[0]["cells"]["1"]["grade"]["status"] == "teacher_confirmed"

    opened: list[Path] = []
    monkeypatch.setattr(files, "open_file", opened.append)
    out_dir = tmp_path / "ws" / "exports"
    for name in ("점수표.xlsx", "김다원_채점.hwpx", "옛것.docx"):
        (out_dir / name).write_bytes(b"x")
    exports = c.get("/api/exports").json()
    names = sorted(e["name"] for e in exports)
    # 채점이 쓴 학생 파일·반별 묶음(시험 폴더 안)도 함께 보인다
    assert names == ["(반별)_반없음_1.hwpx", "김다원_채점.hwpx", "반없음_A_1.hwpx", "점수표.xlsx"]
    assert c.post("/api/files/open", json={"path": exports[0]["path"]}).json() == {"ok": True}
    assert opened and opened[0].suffix in (".hwpx", ".xlsx")
    out = c.post("/api/files/open", json={"path": "../../etc/passwd"})
    assert out.status_code == 400 and "작업공간 밖" in out.json()["detail"]
    assert c.get(f"{base}/file", params={"path": "../exam.json"}).status_code == 400


def test_transcript_confirm_and_scan_job(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path
) -> None:
    draft = {
        "lines": [
            {"question_number": "1", "page": 1, "text": "", "math": "y=(x-2)^2+3",
             "confidence": 0.95},
            {"question_number": "1", "page": 1, "text": "최솟값 3", "confidence": 0.5,
             "uncertain": ["3 또는 8"]},
        ]
    }  # fmt: skip
    exe = make_fake("script", script=[{"response": json.dumps(draft, ensure_ascii=False)}])
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.ocr.cross_check = False
    save_config(app_paths, cfg)
    c = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    scope = c.post("/api/scopes", json=read_json(FIX / "scope.json")).json()
    q1 = read_json(FIX / "questions.json")[:1]
    exam = c.post("/api/exams", json={"title": "판독", "scope_id": scope["id"], "questions": q1})
    exam = exam.json()
    base = f"/api/exams/{exam['id']}"

    img = (FIX / "scans" / "F_q1.jpg").read_bytes()
    job = c.post(f"{base}/students/F/scan", files=[("files", ("F_q1.jpg", img, "image/jpeg"))])
    assert job.status_code == 200, job.text
    done = _wait(c, job.json())
    assert done["status"] == "succeeded" and done["result"]["saved"] == ["1"], done

    tr = c.get(f"{base}/students/F/transcripts/1").json()
    assert not tr["transcript"]["confirmed"] and tr["needs_attention"]
    assert tr["page_images"] == ["scans/F/page_01.png"]
    assert c.get(f"{base}/file", params={"path": tr["page_images"][0]}).status_code == 200

    lines = tr["transcript"]["lines"]
    lines[1]["text"] = "따라서 최솟값 3"
    lines[1]["uncertain"] = []
    lines[1]["confidence"] = 1.0
    t = c.put(f"{base}/students/F/transcripts/1",
              json={"lines": lines, "confirm": True, "by": "김선생"}).json()  # fmt: skip
    assert (
        t["confirmed"]
        and t["confirmed_by"] == "김선생"
        and t["lines"][1]["text"].startswith("따라서")
    )


def test_one_unreadable_student_does_not_block_others(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path
) -> None:
    """학생 A는 판독이 끝나 바로 채점되고, B만 [판독불가] 입력을 기다린다 (알림 1). 강사가 B를 채워
    확정하면 B를 채점한다. [판독불가]가 남은 채로는 확정할 수 없다."""
    exe = make_fake(
        "script",
        script=[
            {"response": json.dumps(r, ensure_ascii=False)}
            for r in (SOL_OK, RUBRIC_OK, GRADE_A, GRADE_A)
        ],
    )
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    save_config(app_paths, cfg)
    c = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    scope = c.post("/api/scopes", json=read_json(FIX / "scope.json")).json()
    q1 = read_json(FIX / "questions.json")[:1]
    exam = c.post("/api/exams", json={"title": "자동 채점", "scope_id": scope["id"],
                                      "questions": q1}).json()  # fmt: skip
    base = f"/api/exams/{exam['id']}"
    text = (FIX / "answers" / "A_q1.txt").read_text(encoding="utf-8")

    # 판독 결과(미확인)를 흉내: A는 다 읽음, B는 한 곳을 못 읽음. 확정하지 않은 저장은 채점하지 않는다
    lines: list[dict[str, Any]] = []
    for s in ("A", "B"):
        made: dict[str, Any] = c.post(
            f"{base}/students/{s}/answers", json={"question": "1", "text": text}
        ).json()
        lines = made["lines"]
    unreadable = [*lines, {"text": "[판독불가] 이다"}]
    c.put(f"{base}/students/A/transcripts/1", json={"lines": lines, "confirm": False})
    c.put(f"{base}/students/B/transcripts/1", json={"lines": unreadable, "confirm": False})
    assert not [j for j in c.get("/api/jobs").json() if j["kind"] == "grade"]

    assert _wait(c, c.post(f"{base}/solve", json={}).json())["status"] == "succeeded"
    assert _wait(c, c.post(f"{base}/rubrics", json={}).json())["status"] == "succeeded"
    c.post(f"{base}/questions/1/rubric/approve", json={"by": "김선생"})

    job = _wait(c, c.post(f"{base}/grade", json={}).json())
    assert job["result"]["graded"] == 1, "A는 강사 확인 전에도 채점"
    assert job["result"]["skipped"] == [
        {"student": "B", "question": "1", "reason": "[판독불가] 1곳 입력 대기"}
    ]
    assert c.get(base).json()["summary"]["needs_input"] == 1, "알림 숫자"
    assert c.get(f"{base}/needs-input").json() == [{"student": "B", "question": "1",
                                                    "unreadable": 1}]  # fmt: skip
    a = c.get(f"{base}/students/A/grades/1").json()["grade"]
    assert a["total"] == 4 and a["transcript_confirmed"] is False

    # A는 채점본까지, B는 판독본만 (강사가 [판독불가]를 채울 파일). 묶음에는 둘 다
    out = next((tmp_path / "ws" / "exports").iterdir())
    assert out.name.endswith("_자동_채점")
    names = ["(반별)_반없음_1.hwpx", "반없음_A_1.hwpx", "반없음_B_1.hwpx"]
    assert sorted(p.name for p in out.iterdir()) == names
    assert job["result"]["files"] == ["반없음_A_1.hwpx", "(반별)_반없음_1.hwpx"]
    assert count_heads(out / "(반별)_반없음_1.hwpx") == 2
    a_file = read_back(out / "반없음_A_1.hwpx")
    assert a_file.head == "자동 채점 · 반없음 · A" and list(a_file.parts) == ["1"]
    assert any("[판독불가]" in x for x in read_back(out / "반없음_B_1.hwpx").parts["1"])
    listed = c.get("/api/exports").json()
    assert {e["name"] for e in listed} == set(names)
    assert {e["folder"] for e in listed} == {out.name}

    path = f"{base}/students/B/transcripts/1"
    got = c.get(path).json()
    assert got["unreadable"] == 1 and got["needs_attention"] and got["page_images"] == []
    r = c.put(path, json={"lines": unreadable, "confirm": True})
    assert r.status_code == 400 and "[판독불가] 1곳" in r.json()["detail"]
    assert not c.get(path).json()["transcript"]["confirmed"], "거부되면 저장하지 않는다"

    # 강사가 원본을 보고 채워 확정 → B만 채점
    t = c.put(path, json={"lines": lines, "confirm": True, "by": "김선생"}).json()
    assert t["confirmed"]
    auto = [j for j in c.get("/api/jobs").json() if j["params"].get("auto")]
    assert [j["params"] for j in auto] == [
        {"exam": exam["id"], "student": "B", "question": "1", "auto": True}
    ]
    done = _wait(c, auto[0])
    assert done["status"] == "succeeded" and done["result"]["graded"] == 1, done
    assert c.get(base).json()["summary"]["needs_input"] == 0
    b = c.get(f"{base}/students/B/grades/1").json()["grade"]
    assert b["total"] == 4 and b["transcript_confirmed"] is True
    assert (out / "반없음_B_1.hwpx").is_file() and done["result"]["files"] == [
        "반없음_B_1.hwpx", "(반별)_반없음_1.hwpx"
    ]  # fmt: skip
    assert read_back(out / "(반별)_반없음_1.hwpx").head.endswith("· A"), "묶음은 이름순"

    # A를 고친 것 없이 확정: AI를 부르지 않고 (가짜 응답이 다 떨어졌다) 표시만 켠다
    c.put(f"{base}/students/A/transcripts/1", json={"lines": lines, "confirm": True})
    assert len([j for j in c.get("/api/jobs").json() if j["params"].get("auto")]) == 1
    assert c.get(f"{base}/students/A/grades/1").json()["grade"]["transcript_confirmed"] is True


def test_failed_job_reports_korean_message(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path
) -> None:
    exe = make_fake("auth")
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    for n in ("claude", "codex"):
        cfg.agents[n].enabled = False  # pyright: ignore[reportArgumentType]
    save_config(app_paths, cfg)
    c = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    job = _wait(c, c.post("/api/rules/refine", json={"request": "존댓말로"}).json())
    assert job["status"] == "failed"
    assert job["error"]["code"] == "agent_not_logged_in" and "로그인" in job["error"]["message"]


def test_calibration_api(app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path) -> None:
    from test_calibration import SUGGESTION
    from test_grading import approved_rubric

    from dawoai.grading.store import ExamStore
    from dawoai.models.grading import ScopedSolution, SolutionDraft, Transcript, TranscriptLine

    exe = make_fake("script", script=[{"response": json.dumps(SUGGESTION, ensure_ascii=False)}])
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    save_config(app_paths, cfg)
    c = _client(app_paths)
    ws_root = tmp_path / "ws"
    c.post("/api/workspace", json={"path": str(ws_root), "create": True})
    scope = c.post("/api/scopes", json=read_json(FIX / "scope.json")).json()
    q1 = read_json(FIX / "questions.json")[:1]
    eid = c.post(
        "/api/exams", json={"title": "보정", "scope_id": scope["id"], "questions": q1}
    ).json()["id"]
    from dawoai.paths import WorkspacePaths

    store = ExamStore(WorkspacePaths(ws_root), eid)
    store.save_solution(ScopedSolution(**SolutionDraft.model_validate(SOL_OK).model_dump(),
                                       question_number="1", scope_version=1))  # fmt: skip
    store.save_rubric(approved_rubric())
    for st in ("A", "D"):
        store.save_transcript(Transcript(student_id=st, question_number="1", confirmed=True,
                                         lines=[TranscriptLine(math="y=(x-2)^2+11")]))  # fmt: skip
    base = f"/api/exams/{eid}"

    # 강사 직접 채점 → 기준 예시 후보
    bad = c.post(f"{base}/students/A/grades/1/manual", json={"awarded": {"R1": 2}})
    assert bad.status_code == 400 and "정하지 않은 항목" in bad.json()["detail"]
    g = c.post(f"{base}/students/A/grades/1/manual",
               json={"awarded": {"R1": 1, "R2": 0}, "item_notes": {"R1": "상수항 실수"}, "by": "김선생"}).json()  # fmt: skip
    assert g["status"] == "teacher_confirmed" and g["agent"] is None and g["total"] == 1

    view = c.get(f"{base}/calibration/1").json()
    assert [x["student"] for x in view["candidates"]] == ["A"] and view["candidates"][0][
        "kind"
    ] == "manual"
    assert view["candidates"][0]["order"] == 1 and view["agreement"]["n"] == 0

    s = view["settings"] | {"excluded": ["A"], "pinned": ["A"]}
    assert c.put(f"{base}/calibration/1/settings", json=s).status_code == 400, "고정·제외 동시 불가"
    s = view["settings"] | {"excluded": ["A"]}
    assert c.put(f"{base}/calibration/1/settings", json=s).json()["excluded"] == ["A"]
    view = c.get(f"{base}/calibration/1").json()
    assert view["candidates"] == [] and view["excluded"][0]["student"] == "A"
    r = c.post(f"{base}/calibration/1/eval", json={})
    assert r.status_code == 400 and "2개 이상" in r.json()["detail"]

    # 강사가 AI 점수를 고친 사례(D) → 개선 제안 → 새 루브릭 초안
    from test_calibration import ai_grade

    from dawoai.grading.pipeline import teacher_review

    store.save_grade(teacher_review(ai_grade("D", 2, 1), approved_rubric(), {"R1": 1, "R2": 0}))
    job = _wait(c, c.post(f"{base}/calibration/1/suggest").json())
    assert job["status"] == "succeeded", job
    sug = job["result"]["suggestion"]
    assert job["result"]["cases"] == ["D"] and sug["changes"][0]["item_id"] == "R2"
    new = c.post(f"{base}/calibration/1/apply", json={"revised": sug["revised"]}).json()
    assert new["version"] == 2 and new["status"] == "draft", "승인된 루브릭을 고치면 새 버전 초안"
    agree = c.get(f"{base}/agreement").json()
    assert agree["overall"]["n"] == 1 and agree["questions"]["1"]["bias"] == 2


def test_answer_key_fix_and_confirm(app_paths: AppPaths, tmp_path: Path) -> None:
    from dawoai.grading.store import ExamStore
    from dawoai.models.answer_key import AnswerKey
    from dawoai.paths import WorkspacePaths

    c = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    scope = c.post("/api/scopes", json=read_json(FIX / "scope.json")).json()
    q1 = read_json(FIX / "questions.json")[:1]
    eid = c.post(
        "/api/exams", json={"title": "해설지", "scope_id": scope["id"], "questions": q1}
    ).json()["id"]
    store = ExamStore(WorkspacePaths(tmp_path / "ws"), eid)
    store.save_key(
        AnswerKey(
            question_number="1",
            source="k.pdf 2쪽",
            parts=[{"label": "", "answer": "2/3", "credit_policy": "none"}],  # pyright: ignore[reportArgumentType]
            doubtful=[""],
        )
    )
    url = f"/api/exams/{eid}/questions/1/key"
    q = c.get(f"/api/exams/{eid}/questions/1").json()
    assert q["key"]["parts"][0]["answer"] == "2/3"
    bad = c.put(url, json={"parts": [{"label": "(1)", "credit_policy": "none"}]})
    assert bad.status_code == 400 and "소문항 표시" in bad.json()["detail"]
    fixed = c.put(
        url,
        json={"parts": [{"label": "", "answer": "1/2", "credit_policy": "none"}], "by": "김선생"},
    ).json()
    assert fixed["confirmed"] and fixed["confirmed_by"] == "김선생" and fixed["doubtful"] == []
    assert store.answer_key("1").parts[0].answer == "1/2"  # pyright: ignore[reportOptionalMemberAccess]


def test_candidate_match_api(app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path) -> None:
    from test_grading import approved_rubric

    from dawoai.grading.store import ExamStore
    from dawoai.models.grading import AnswerCandidate, PartCheck, Transcript, TranscriptLine
    from dawoai.paths import WorkspacePaths

    sides = {"students": [
        {"student": "학생1", "side": "candidate", "candidate_id": "A1", "quote": "최솟값은 11"},
    ]}  # fmt: skip
    exe = make_fake("script", script=[{"response": json.dumps(sides, ensure_ascii=False)}])
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    save_config(app_paths, cfg)
    c = _client(app_paths)
    ws_root = tmp_path / "ws"
    c.post("/api/workspace", json={"path": str(ws_root), "create": True})
    scope = c.post("/api/scopes", json=read_json(FIX / "scope.json")).json()
    q1 = read_json(FIX / "questions.json")[:1]
    eid = c.post(
        "/api/exams", json={"title": "후보", "scope_id": scope["id"], "questions": q1}
    ).json()["id"]
    store = ExamStore(WorkspacePaths(ws_root), eid)
    base = f"/api/exams/{eid}/questions/1"
    store.save_rubric(approved_rubric())
    no_cands = _wait(c, c.post(f"{base}/rubric/candidates/match").json())
    assert no_cands["status"] == "failed"

    r = approved_rubric()
    r = r.model_copy(update={
        "checks": [PartCheck(part="", key_answer="3", ai_answer="11", verdict="other_valid")],
        "candidates": [AnswerCandidate(id="A1", part="", answer="11", argument="다르게 읽으면")],
    })  # fmt: skip
    store.save_rubric(r)
    store.save_transcript(Transcript(student_id="홍길동", question_number="1",
                                     lines=[TranscriptLine(text="최솟값은 11")]))  # fmt: skip
    store.save_transcript(Transcript(student_id="빈칸", question_number="1"))
    assert c.get(base).json()["candidate_matches"] is None
    j = _wait(c, c.post(f"{base}/rubric/candidates/match").json())
    assert j["status"] == "succeeded", j
    m = c.get(base).json()["candidate_matches"]
    (p,) = m["parts"]
    assert p["sides"][0]["student"] == "홍길동" and p["blank"] == ["빈칸"]
    assert m["unconfirmed"] == ["빈칸", "홍길동"]


def test_same_pdf_opens_existing_exam(app_paths: AppPaths, tmp_path: Path) -> None:
    from pdfgen import make_exam_pdf

    from dawoai.ingest.sources import register_source, update_source
    from dawoai.models.source import SourceIngestState
    from dawoai.paths import WorkspacePaths

    c = _client(app_paths)
    ws_root = tmp_path / "ws"
    c.post("/api/workspace", json={"path": str(ws_root), "create": True})
    scope = c.post("/api/scopes", json=read_json(FIX / "scope.json")).json()
    q1 = read_json(FIX / "questions.json")[:1]
    eid = c.post(
        "/api/exams", json={"title": "중간고사", "scope_id": scope["id"], "questions": q1}
    ).json()["id"]
    pdf = make_exam_pdf(tmp_path / "exam.pdf")
    src = register_source(WorkspacePaths(ws_root), pdf, "exam", title="중간고사")
    src.ingest = SourceIngestState(status="ingested", item_count=1, message=eid)
    update_source(WorkspacePaths(ws_root), src)

    def upload() -> Any:
        with pdf.open("rb") as f:
            return c.post("/api/exams/from-pdf", files={"file": ("exam.pdf", f)},
                          data={"title": "다시", "scope_id": scope["id"]})  # fmt: skip

    r = upload()
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "already_ingested" and body["exam_id"] == eid
    assert "중간고사" in body["detail"]
    import shutil

    from dawoai.ingest.workflow import existing_exam
    from dawoai.services import open_services

    svc = open_services(ws_root, paths=app_paths)
    assert existing_exam(svc, pdf) is not None
    shutil.rmtree(ws_root / "exams" / eid)
    assert existing_exam(svc, pdf) is None, "시험 폴더가 지워졌으면 다시 만들 수 있다"


def test_levels_and_from_file_needs_scope_or_level(app_paths: AppPaths, tmp_path: Path) -> None:
    c = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    levels = c.get("/api/levels").json()
    h1 = next(lv for lv in levels if lv["id"] == "h1_phys")
    assert h1["label"] == "고1 물리" and "미분" in h1["forbidden"]
    assert len(levels) == 30

    def upload(**data: str) -> Any:
        return c.post("/api/exams/from-file", files={"file": ("t.hwpx", b"x")},
                      data={"title": "주말반", **data})  # fmt: skip

    assert upload().status_code == 400
    assert upload(level="대학 물리").status_code == 400


def test_rubric_with_key_is_one_call_and_ai_solve_keeps_old_path(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path
) -> None:
    from tests_support import fake_prompts

    from dawoai.grading.store import ExamStore
    from dawoai.models.answer_key import AnswerKey
    from dawoai.paths import WorkspacePaths

    expanded: dict[str, Any] = {
        "items": [{**i, "solution_steps": []} for i in RUBRIC_OK["items"]],
        "expansions": [{"part": "", "description": "꼭짓점이 (2, 3)이라 최솟값 3",
                        "item_ids": ["R2"]}],
    }  # fmt: skip
    exe = make_fake(
        "script",
        script=[{"response": json.dumps(r, ensure_ascii=False)}
                for r in (expanded, SOL_OK, RUBRIC_OK)],
    )  # fmt: skip
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    save_config(app_paths, cfg)
    c = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    scope = c.post("/api/scopes", json=read_json(FIX / "scope.json")).json()
    q1 = read_json(FIX / "questions.json")[:1]
    eid = c.post("/api/exams", json={"title": "해설지", "scope_id": scope["id"],
                                     "questions": q1}).json()["id"]  # fmt: skip
    ExamStore(WorkspacePaths(tmp_path / "ws"), eid).save_key(
        AnswerKey(
            question_number="1",
            source="key.pdf 1쪽",
            parts=[{"label": "", "answer": "3", "credit_policy": "unspecified"}],  # type: ignore[list-item]
        )
    )
    base = f"/api/exams/{eid}"
    job = _wait(c, c.post(f"{base}/rubrics", json={}).json())
    assert job["status"] == "succeeded", job
    made = c.get(f"{base}/questions/1").json()
    assert made["solution"] is None, "해설지가 있으면 풀이 없이"
    assert made["rubric"]["based_on_key"]
    assert made["rubric"]["accepted_alternatives"][0]["status"] == "proposed"
    assert len(fake_prompts(exe)) == 1

    # AI 풀기 (해설지 검산): 먼저 풀고, 예전 경로 (루브릭 → 검토 → …). 가짜 응답이 바닥나
    # 검토 단계에서 멈추지만 호출 순서만 본다.
    _wait(c, c.post(f"{base}/rubrics", json={"ai_solve": True}).json())
    prompts = fake_prompts(exe)
    assert "[AI 독립 풀이]" in prompts[2] and "Role: solver / verifier." in prompts[1]
    assert c.get(f"{base}/questions/1").json()["solution"] is not None


def test_diagnostics_bundle(app_paths: AppPaths, tmp_path: Path) -> None:
    import zipfile

    c = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    app_paths.logs.mkdir(parents=True, exist_ok=True)
    (app_paths.logs / "error.log").write_text("boom", encoding="utf-8")
    (tmp_path / "ws" / "memory" / "n1.md").write_text("메모", encoding="utf-8")
    fail = app_paths.sandboxes / "20261102-grade-abc"
    (fail / ".dawoai").mkdir(parents=True)
    (fail / ".dawoai" / "stdout.jsonl").write_text("{}", encoding="utf-8")

    plan = c.get("/api/diagnostics/plan").json()
    keys = {g["key"]: g for g in plan["groups"]}
    assert keys["logs"]["count"] >= 1 and keys["failed_calls"]["count"] == 1
    assert "workspace/memory/n1.md" in keys["learning"]["sample"]
    assert c.get("/api/diagnostics/plan?exam_id=../x").status_code == 400

    r = c.post(
        "/api/diagnostics/bundle",
        json={"note": "취소가 안 됨", "include": ["logs", "learning"], "teacher": "김"},
    ).json()
    assert r["path"].startswith("진단묶음/") and r["path"].endswith(".zip")
    with zipfile.ZipFile(tmp_path / "ws" / r["path"]) as z:
        names = z.namelist()
        assert "logs/error.log" in names and "workspace/memory/n1.md" in names
        assert not any(n.startswith("failed_calls/") for n in names), "고르지 않은 묶음은 빠짐"
        assert "취소가 안 됨" in z.read("README.md").decode()
        assert "config" in json.loads(z.read("context.json"))


def test_multiple_choice_rubric_without_ai(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path
) -> None:
    """객관식: 루브릭을 코드로 만들고(AI 0회), 강사가 정답(복수 정답)을 고친 뒤 승인한다."""
    from tests_support import fake_prompts

    exe = make_fake("script", script=[])
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    save_config(app_paths, cfg)
    c = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    scope = c.post("/api/scopes", json=read_json(FIX / "scope.json")).json()
    item = {
        "item_type": "multiple_choice", "subject": "수학",
        "stem": [{"type": "paragraph", "runs": [{"t": "text", "v": "옳은 것은?"}]}],
        "choices": [{"label": n, "runs": [{"t": "text", "v": str(n * 10)}]} for n in range(1, 6)],
    }  # fmt: skip
    eid = c.post("/api/exams", json={"title": "객관식", "scope_id": scope["id"],
                                     "questions": [{"number": "1", "points": 3, "item": item}]}
                 ).json()["id"]  # fmt: skip
    base = f"/api/exams/{eid}"
    assert _wait(c, c.post(f"{base}/rubrics", json={}).json())["status"] == "succeeded"
    r = c.get(f"{base}/questions/1").json()["rubric"]
    assert r["answer_rules"][0]["answers"] == [] and fake_prompts(exe) == []
    assert c.post(f"{base}/questions/1/rubric/approve", json={}).status_code >= 400, "정답 없음"
    r = c.put(f"{base}/questions/1/rubric/answers", json={"answers": ["②", "4"]}).json()
    assert r["answer_rules"][0]["answers"] == ["2", "4"]
    assert c.post(f"{base}/questions/1/rubric/approve", json={}).json()["status"] == "approved"
