"""여러 학생 답안지 PDF: 학생별 묶음, 표지(이름·반·학교·강사 점수·회전), 판독, 다시 하기.

실측(2026-10-28): 3쪽 × 24명 스캔 PDF, 모든 쪽이 옆으로 돌아가 있고, 표지에 강사가 문항별 점수를
빨간 펜으로 적었다. 반은 손글씨로 "1TSS", "M1J"처럼 적혀 있기도 했다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from PIL import Image
from tests_support import fake_prompts

from dawoni.cli import main
from dawoni.config import AppConfig, save_config
from dawoni.fsio import read_json
from dawoni.grading.store import ExamStore
from dawoni.models.grading import Exam
from dawoni.ocr import batch
from dawoni.ocr.pages import PageError
from dawoni.paths import AppPaths, WorkspacePaths
from dawoni.workspace import create_workspace

FIX = Path(__file__).parent / "fixtures" / "grading"
MakeFake = Callable[..., Path]


def _pdf(path: Path, n: int) -> Path:
    doc = pymupdf.open()
    for i in range(n):
        p = doc.new_page(width=842, height=595)  # 가로로 스캔된 쪽
        p.insert_text((60, 80), f"page {i + 1}", fontsize=20)  # pyright: ignore[reportUnknownMemberType]
    doc.save(str(path))  # pyright: ignore[reportUnknownMemberType]
    return path


def test_plan_sets() -> None:
    sets = batch.plan_sets(9, 3)
    assert [s.pages for s in sets] == [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
    assert [s.number for s in batch.plan_sets(9, 3, [2])] == [2]
    with pytest.raises(PageError, match="나누어떨어지지"):
        batch.plan_sets(10, 3)


def test_student_id_and_cover_check() -> None:
    assert batch.student_id_of("김태희 A", 1) == "김태희A"
    assert batch.student_id_of(None, 7) == "미확인07"
    exam = Exam.model_validate(
        {"title": "t", "scope_id": "s", "questions": read_json(FIX / "questions.json")[:1]}
    )
    c = batch.CoverRead(
        rotation="90", name="a", class_name="1TSS", school=None,
        teacher_scores=[batch.CoverScore(question="9", points=1)],
    )  # fmt: skip
    problems = batch.check_cover(c, exam, ["TSS1", "MSS1-J"])
    assert any("문항 ['9']" in p for p in problems)
    assert any("반 목록" in p for p in problems)
    ok = c.model_copy(update={"class_name": "TSS1", "teacher_scores": []})
    assert batch.check_cover(ok, exam, ["TSS1"]) == []


def test_rotate(tmp_path: Path) -> None:
    p = tmp_path / "x.png"
    Image.new("RGB", (40, 20), "white").save(p)
    batch.rotate([p], 90)
    with Image.open(p) as im:
        assert im.size == (20, 40)


def _cover(name: str, rotation: str = "90") -> dict[str, Any]:
    return {
        "rotation": rotation,
        "name": name,
        "class_name": "TSS1",
        "school": None,
        "teacher_scores": [{"question": "1", "points": 3}],
    }


def _lines(text: str) -> dict[str, Any]:
    return {"lines": [{"question_number": "1", "page": 1, "text": text, "confidence": 0.9}]}


def test_cli_scan_batch(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = create_workspace(tmp_path / "ws")
    responses = [_cover("김태희 A"), _lines("x=2일 때 최솟값 3"), _cover("김태희A"), _lines("모름")]
    exe = make_fake(
        "script", script=[{"response": json.dumps(r, ensure_ascii=False)} for r in responses]
    )
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.ocr.cross_check = False
    save_config(app_paths, cfg)
    w = ["--workspace", str(ws.root)]
    assert main(["scope", *w, "import", str(FIX / "scope.json")]) == 0
    one_q = tmp_path / "q1.json"
    one_q.write_text(json.dumps(read_json(FIX / "questions.json")[:1]), encoding="utf-8")
    assert main(["exam", *w, "create", "--title", "t", "--scope", "scope-quadratic-minmax",
                 "--questions", str(one_q)]) == 0  # fmt: skip
    found = re.search(r"exam-[0-9a-f]+", capsys.readouterr().out)
    assert found is not None
    eid = found.group(0)
    pdf = _pdf(tmp_path / "answers.pdf", 4)

    assert main(["exam", *w, "scan-batch", eid, str(pdf), "--per-student", "2",
                 "--classes", "TSS1,TSS2"]) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert "김태희A" in out and "표지 점수 1번 3" in out
    assert "다른 묶음" in out, "같은 이름이 두 묶음에 있으면 알린다"

    store = ExamStore(WorkspacePaths(ws.root), eid)
    recs = {r.student_id: r for r in store.student_records()}
    assert set(recs) == {"김태희A", "김태희A#2"}
    assert recs["김태희A"].teacher_scores == {"1": 3} and recs["김태희A"].class_name == "TSS1"
    t = store.transcript("김태희A", "1")
    assert t is not None and not t.confirmed and t.lines[0].text == "x=2일 때 최솟값 3"
    assert t.pages == ["scans/김태희A/page_01.png", "scans/김태희A/page_02.png"]
    with Image.open(store.root / t.pages[1]) as im:
        assert im.height > im.width, "표지의 회전 각도로 모든 쪽을 세운다"

    prompts = fake_prompts(exe)
    assert "TSS1, TSS2" in prompts[0], "반 목록을 표지 읽기에 넘긴다"

    assert main(["exam", *w, "scan-batch", eid, str(pdf), "--per-student", "2"]) == 0
    assert "이미 판독함" in capsys.readouterr().out
    assert len(fake_prompts(exe)) == 4, "다시 하면 끝난 학생은 건너뛴다"
