"""시험 읽기 고치기: 소문항·문항·본문·해설을 강사가 고치기, 원본 영역을 지정해 다시 읽기 (가짜 AI).

실측 공통형3회(2026-11-06): 잘못 읽힌 시험지를 같은 방법으로 다시 읽혀도 같은 결과 — 강사가 정한
구조와 영역이 새 정보다."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from conftest import MakeFake
from fastapi.testclient import TestClient
from pdfgen import make_exam_pdf
from test_grading import FIX
from test_ingest import PAGE1, PAGE2
from test_server import _client, _wait  # pyright: ignore[reportPrivateUsage]
from tests_support import fake_prompts

from dawoni.config import AppConfig, save_config
from dawoni.fsio import read_json
from dawoni.ingest.layout import analyze
from dawoni.paths import AppPaths


@pytest.fixture(scope="module")
def exam_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_exam_pdf(tmp_path_factory.mktemp("pdf") / "exam.pdf")


def _ingest(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path, pdf: Path, extra: list[Any]
) -> tuple[TestClient, str, Path]:
    """가짜 AI로 시험지를 읽어 시험을 만든다 (해설지 없음). `extra`: 그 뒤 AI 응답들."""
    script = [{"response": json.dumps(d, ensure_ascii=False)} for d in (PAGE1, PAGE2, *extra)]
    exe = make_fake("script", script=script)
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.parallel_questions = 1  # 가짜 AI 응답 순서를 고정
    save_config(app_paths, cfg)
    c = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    scope = c.post("/api/scopes", json=read_json(FIX / "scope.json")).json()
    with pdf.open("rb") as f:
        job = c.post(
            "/api/exams/from-file",
            files={"file": ("exam.pdf", f, "application/pdf")},
            data={"title": "모의", "scope_id": scope["id"], "key_pages": "none"},
        ).json()
    done = _wait(c, job)
    assert done["status"] == "succeeded", done
    return c, done["result"]["exam_id"], exe


def _numbers(c: TestClient, base: str) -> list[str]:
    return [q["number"] for q in c.get(base).json()["exam"]["questions"]]


def test_teacher_fixes_parts_questions_stem_and_key(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path, exam_pdf: Path
) -> None:
    c, eid, _ = _ingest(app_paths, make_fake, tmp_path, exam_pdf, [])
    base = f"/api/exams/{eid}"
    q2 = f"{base}/questions/2"

    # 해설 직접 넣기 (해설지가 없던 문항) → 강사가 넣었으니 확인됨
    typed = [{"label": "", "answer": "\\(r=2\\)", "solution": "넓이로 구한다"}]
    key = c.put(f"{q2}/key-text", json={"parts": typed, "by": "김선생"}).json()
    assert key["confirmed"] and key["source"] == "강사 입력"
    assert c.put(f"{q2}/key-text", json={"parts": [{"label": "(9)"}]}).status_code == 400

    # 소문항 없던 문항 → (1)(2): 문항 전체 해설은 (1)로 따라가고 (2)는 빈 해설
    r = c.put(f"{q2}/structure", json={"parts": [
        {"label": "(1)", "points": 1, "kind": "short_answer", "was": ""},
        {"label": "(2)", "points": 2, "kind": "essay"},
    ]}).json()  # fmt: skip
    assert r["question"]["points"] == 3 and r["notes"] == []
    parts = c.get(q2).json()["key"]["parts"]
    assert [(p["label"], p["answer"]) for p in parts] == [("(1)", "\\(r=2\\)"), ("(2)", None)]

    # 표시 바꾸기 (해설이 따라감), 겹치는 표시는 거절
    c.put(f"{q2}/structure", json={"parts": [
        {"label": "(1)①", "points": 1, "was": "(1)"}, {"label": "(2)", "points": 2, "was": "(2)"},
    ]})  # fmt: skip
    detail = c.get(q2).json()
    assert [p["label"] for p in detail["question"]["parts"]] == ["(1)①", "(2)"]
    assert detail["key"]["parts"][0]["answer"] == "\\(r=2\\)"
    dup = [{"label": "(1)", "points": 1}, {"label": "(1)", "points": 2}]
    assert c.put(f"{q2}/structure", json={"parts": dup}).status_code == 400

    # 빠진 문항 더하기 (번호 순서 자리) → 본문 붙여 넣기 → 지우기
    new = c.post(f"{base}/questions", json={"number": "6", "points": 4}).json()
    assert new["number"] == "6" and _numbers(c, base) == ["1", "2", "3", "4", "5", "6", "서술형 1"]
    assert c.post(f"{base}/questions", json={"number": "6", "points": 4}).status_code == 400
    assert c.get(f"{base}/questions/6").json()["stem_missing"]
    c.put(
        f"{base}/questions/6/stem",
        json={"text": "물체의 속력 \\(v^2\\)를 구하시오.\n\\[\\frac{1}{2}mv^2\\]"},
    )
    q6 = c.get(f"{base}/questions/6").json()
    assert not q6["stem_missing"]
    assert q6["stem_text"] == "물체의 속력 \\(v^2\\)를 구하시오.\n\\[\\frac{1}{2}mv^2\\]"
    assert c.delete(f"{base}/questions/6").json() == {"ok": True}
    assert "6" not in _numbers(c, base)

    # 학생 답안을 판독한 뒤에는 표시를 바꿀 수 없다 (배점은 고칠 수 있음)
    c.post(f"{base}/students/A/answers", json={"question": "1", "text": "답은 5"})
    keep = [
        {"label": "(1)①", "points": 2, "was": "(1)①"},
        {"label": "(2)", "points": 2, "was": "(2)"},
    ]
    assert c.put(f"{q2}/structure", json={"parts": keep}).json()["question"]["points"] == 4
    renamed = [{"label": "(1)", "points": 2}, {"label": "(2)", "points": 2}]
    res = c.put(f"{q2}/structure", json={"parts": renamed})
    assert res.status_code == 400 and "판독" in res.json()["detail"]
    assert c.post(f"{base}/questions", json={"number": "7", "points": 4}).status_code == 400


def _q2_read(labels: list[str], *, figure: bool = True, stem: str | None = None) -> dict[str, Any]:
    q = copy.deepcopy(PAGE1["questions"][1])
    q["parts"] = [{"label": label, "points": 5} for label in labels]  # 배점은 강사 것으로 바뀐다
    if not figure:
        q["stem"] = [{"type": "paragraph", "runs": [{"t": "text", "v": stem or ""}]}]
        q.pop("figures")
    return {"questions": [q]}


KEY_Q2 = {"questions": [{"number": "2", "parts": [
    {"label": "(1)", "answer": "\\(r=2\\)", "solution": "", "credit_policy": "none"},
    {"label": "(2)", "answer": "\\(4\\pi\\)", "solution": "", "credit_policy": "unspecified"},
]}]}  # fmt: skip


def test_read_region_keeps_teacher_parts(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path, exam_pdf: Path
) -> None:
    pasted_stem = "붙여 넣은 그림에서 읽은 문장입니다"
    extra = [
        _q2_read(["1", "2"]),  # 강사가 정한 표시를 지키지 않음 → 수리
        _q2_read(["(1)", "(2)"]),
        KEY_Q2,
        _q2_read(["(1)", "(2)"], figure=False, stem=pasted_stem),
    ]
    c, eid, exe = _ingest(app_paths, make_fake, tmp_path, exam_pdf, extra)
    base = f"/api/exams/{eid}"
    q2 = f"{base}/questions/2"
    c.put(f"{q2}/structure", json={"parts": [
        {"label": "(1)", "points": 1, "kind": "short_answer"}, {"label": "(2)", "points": 2},
    ]})  # fmt: skip

    src = c.get(f"{base}/source").json()
    assert src["pdf"] and src["name"] == "exam.pdf" and len(src["pages"]) == 2
    img = c.get(f"{base}/source/pages/1")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"

    # 원본 1쪽에서 2번 영역을 끌어 지정 (쪽 비율)
    with pymupdf.open(exam_pdf) as doc:
        w, h = doc[0].rect.width, doc[0].rect.height
    page, rect = analyze(exam_pdf).region("2").boxes[0]
    box = {"page": page, "x0": rect.x0 / w, "y0": rect.y0 / h, "x1": rect.x1 / w, "y1": rect.y1 / h}
    job = c.post(f"{q2}/read-region", data={"what": "question", "boxes": json.dumps([box])}).json()
    done = _wait(c, job)
    assert done["status"] == "succeeded", done
    q = done["result"]["question"]
    assert [(p["label"], p["points"]) for p in q["parts"]] == [("(1)", 1), ("(2)", 2)], "강사 배점"
    assert q["points"] == 3 and q["source_pages"] == [1]
    assert [f["id"] for f in q["item"]["figures"]] == ["F1"]
    assert (tmp_path / "ws" / "exams" / eid / "figures" / "2" / "F1.png").is_file()
    prompts = fake_prompts(exe)
    assert "강사가 정한 소문항" in prompts[2] and "- (1): 1점, 단답형" in prompts[2]
    assert "강사가 정한 ['(1)', '(2)'] 그대로" in prompts[3], "표시를 지키지 않으면 수리 요청"

    # 해설 영역: 그 문항의 소문항 표시로 읽는다
    whole = {"page": 2, "x0": 0, "y0": 0, "x1": 1, "y1": 1}
    job = c.post(f"{q2}/read-region", data={"what": "key", "boxes": json.dumps([whole])}).json()
    done = _wait(c, job)
    assert done["status"] == "succeeded", done
    key = c.get(q2).json()["key"]
    assert [p["label"] for p in key["parts"]] == ["(1)", "(2)"] and "강사 지정 영역" in key[
        "source"
    ]
    assert (tmp_path / "ws" / "exams" / eid / key["pages"][0]).is_file()
    assert "잘라 준 것" in fake_prompts(exe)[-1], "해설지 쪽이 아니라 잘라 준 영역"

    # 캡처해 붙여 넣은 그림: PDF 글자가 없으니 근거 검사 없이 읽는다
    png = img.content
    job = c.post(f"{q2}/read-region", data={"what": "question"},
                 files=[("files", ("cap.png", png, "image/png"))]).json()  # fmt: skip
    done = _wait(c, job)
    assert done["status"] == "succeeded", done
    assert "붙여 넣은 그림" in " ".join(done["result"]["notes"])
    assert "PDF 글자: 없음" in fake_prompts(exe)[-1]
    detail = c.get(q2).json()
    assert detail["stem_text"] == pasted_stem and detail["question"]["item"]["figures"] == []
    assert len(fake_prompts(exe)) == 2 + len(extra), "수리 1번 말고는 한 번씩"
