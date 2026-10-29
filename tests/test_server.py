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

from dawoni.config import AppConfig, save_config
from dawoni.fsio import read_json
from dawoni.paths import AppPaths
from dawoni.server import files
from dawoni.server.app import create_app

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
    assert [d["name"] for d in detected] == ["agy", "claude", "codex"]


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
    for kind in ("paper", "key", "report", "scores"):
        res = c.post(f"{base}/export", json={"kind": kind, "with_rubric": True}).json()
        assert res["ok"], res
    exports = c.get("/api/exports").json()
    assert len(exports) == 4
    assert c.post("/api/files/open", json={"path": exports[0]["path"]}).json() == {"ok": True}
    assert opened and opened[0].suffix == ".docx"
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

    from dawoni.grading.store import ExamStore
    from dawoni.models.grading import ScopedSolution, SolutionDraft, Transcript, TranscriptLine

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
    from dawoni.paths import WorkspacePaths

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

    from dawoni.grading.pipeline import teacher_review

    store.save_grade(teacher_review(ai_grade("D", 2, 1), approved_rubric(), {"R1": 1, "R2": 0}))
    job = _wait(c, c.post(f"{base}/calibration/1/suggest").json())
    assert job["status"] == "succeeded", job
    sug = job["result"]["suggestion"]
    assert job["result"]["cases"] == ["D"] and sug["changes"][0]["item_id"] == "R2"
    new = c.post(f"{base}/calibration/1/apply", json={"revised": sug["revised"]}).json()
    assert new["version"] == 2 and new["status"] == "draft", "승인된 루브릭을 고치면 새 버전 초안"
    agree = c.get(f"{base}/agreement").json()
    assert agree["overall"]["n"] == 1 and agree["questions"]["1"]["bias"] == 2


def test_generate_api(app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path) -> None:
    from test_generate import Q1_TEMPLATE, solved, template

    from dawoni.generate.search import search
    from dawoni.grading.store import ExamStore
    from dawoni.models.grading import ScopedSolution, SolutionDraft
    from dawoni.paths import WorkspacePaths

    ans = str(search(template(), count=1).found[0].answer)
    script = [Q1_TEMPLATE, solved(ans, ans), RUBRIC_OK]
    exe = make_fake(
        "script", script=[{"response": json.dumps(r, ensure_ascii=False)} for r in script]
    )
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    save_config(app_paths, cfg)
    c = _client(app_paths)
    ws_root = tmp_path / "ws"
    c.post("/api/workspace", json={"path": str(ws_root), "create": True})
    scope = c.post("/api/scopes", json=read_json(FIX / "scope.json")).json()
    q1 = read_json(FIX / "questions.json")[:1]
    eid = c.post(
        "/api/exams", json={"title": "원본", "scope_id": scope["id"], "questions": q1}
    ).json()["id"]
    ExamStore(WorkspacePaths(ws_root), eid).save_solution(
        ScopedSolution(
            **SolutionDraft.model_validate(SOL_OK).model_dump(),
            question_number="1",
            scope_version=1,
        )
    )
    bad = c.post(f"/api/exams/{eid}/generate", json={"question": "1", "kind": "novel_b"})
    assert bad.status_code == 200, "확장 개념 없음은 작업 안에서 실패로 알림"
    assert "확장 개념이 필요" in _wait(c, bad.json())["error"]["message"]

    job = _wait(
        c,
        c.post(
            f"/api/exams/{eid}/generate", json={"question": "1", "kind": "numeric", "count": 1}
        ).json(),
    )
    assert job["status"] == "succeeded" and job["result"]["verified"] == 1, job
    sets = c.get(f"/api/exams/{eid}/generated").json()
    assert len(sets) == 1 and sets[0]["status"] == "draft" and sets[0]["rubric"]
    gid = sets[0]["id"]
    r = c.post("/api/generated/to-exam", json={"ids": [gid], "title": "동형"})
    assert r.status_code == 400 and "승인하지 않은" in r.json()["detail"]
    assert (
        c.put(f"/api/generated/{gid}/review", json={"status": "approved", "by": "김선생"}).json()[
            "status"
        ]
        == "approved"
    )
    new = c.post("/api/generated/to-exam", json={"ids": [gid], "title": "동형"}).json()
    detail = c.get(f"/api/exams/{new['id']}").json()
    assert (
        detail["questions"][0]["solution"]["verified"]
        and detail["questions"][0]["rubric"]["status"] == "draft"
    )


def test_figure_api(app_paths: AppPaths, tmp_path: Path) -> None:
    from test_figures import SAMPLES

    c = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    scope = c.post("/api/scopes", json=read_json(FIX / "scope.json")).json()
    q = {"number": "1", "points": 3, "item": {
        "item_type": "short_answer",
        "stem": [{"type": "paragraph", "runs": [{"t": "text", "v": "가속도를 구하시오."}]}, {"type": "figure", "figure_id": "F1"}],
        "variables": {"theta": {"value": 30}, "m": {"value": 2}},
        "figures": [{"id": "F1", "mode": "raster", "orig": "figures/1/F1.png"}]}}  # fmt: skip
    eid = c.post(
        "/api/exams", json={"title": "그림", "scope_id": scope["id"], "questions": [q]}
    ).json()["id"]
    base = f"/api/exams/{eid}/questions/1/figures/F1"
    assert c.get(f"{base}/render").status_code == 400, "스펙 없음"
    bad = c.put(base, json={"mode": "parametric"})
    assert bad.status_code == 400, "스펙 없이 parametric 불가"
    fig = c.put(base, json={"spec": SAMPLES["incline"]["spec"], "mode": "parametric"}).json()
    assert fig["mode"] == "parametric" and fig["spec"]["kind"] == "incline"
    img = c.get(f"{base}/render")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert c.put(base, json={"mode": "raster"}).json()["mode"] == "raster"
    r = c.post(f"{base}/extract")
    assert r.status_code == 400 and "원본 이미지가 없습니다" in r.json()["detail"]


def test_answer_key_fix_and_confirm(app_paths: AppPaths, tmp_path: Path) -> None:
    from dawoni.grading.store import ExamStore
    from dawoni.models.answer_key import AnswerKey
    from dawoni.paths import WorkspacePaths

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

    from dawoni.grading.store import ExamStore
    from dawoni.models.grading import AnswerCandidate, PartCheck, Transcript, TranscriptLine
    from dawoni.paths import WorkspacePaths

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

    from dawoni.ingest.sources import register_source, update_source
    from dawoni.models.source import SourceIngestState
    from dawoni.paths import WorkspacePaths

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

    from dawoni.ingest.workflow import existing_exam
    from dawoni.services import open_services

    svc = open_services(ws_root, paths=app_paths)
    assert existing_exam(svc, pdf) is not None
    shutil.rmtree(ws_root / "exams" / eid)
    assert existing_exam(svc, pdf) is None, "시험 폴더가 지워졌으면 다시 만들 수 있다"
