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
    assert t.pages == ["scans/김태희A/page_02.png"], "판독문에는 답안 쪽만 (표지는 page_01)"
    assert (store.scan_dir("김태희A") / "page_01.png").exists()
    with Image.open(store.root / t.pages[0]) as im:
        assert im.height > im.width, "표지의 회전 각도로 모든 쪽을 세운다"

    prompts = fake_prompts(exe)
    assert "TSS1, TSS2" in prompts[0], "반 목록을 표지 읽기에 넘긴다"

    assert main(["exam", *w, "scan-batch", eid, str(pdf), "--per-student", "2"]) == 0
    assert "이미 판독함" in capsys.readouterr().out
    assert len(fake_prompts(exe)) == 4, "다시 하면 끝난 학생은 건너뛴다"


# ---------------------------------------------------------------- 강사별 문항 분담


def _two_question_exam(app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path, script: list[Any]):
    exe = make_fake(
        "script", script=[{"response": json.dumps(r, ensure_ascii=False)} for r in script]
    )
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.ocr.cross_check = False
    save_config(app_paths, cfg)
    ws = create_workspace(tmp_path / "ws")
    w = ["--workspace", str(ws.root)]
    assert main(["scope", *w, "import", str(FIX / "scope.json")]) == 0
    qs = read_json(FIX / "questions.json")[:2]
    qfile = tmp_path / "qs.json"
    qfile.write_text(json.dumps(qs, ensure_ascii=False), encoding="utf-8")
    assert main(["exam", *w, "create", "--title", "t", "--scope", "scope-quadratic-minmax",
                 "--questions", str(qfile)]) == 0  # fmt: skip
    store = ExamStore(WorkspacePaths(ws.root), ExamStore.list_ids(WorkspacePaths(ws.root))[0])
    return exe, w, store


def _q_lines(q: str, text: str) -> dict[str, Any]:
    return {"lines": [{"question_number": q, "page": 1, "text": text, "confidence": 0.9}]}


def test_one_question_per_teacher(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """표지 1쪽 + 답안 2쪽(문항마다 한 쪽). 강사 A는 1번, 강사 B는 2번만 — 표지는 한 번만 읽는다."""
    exe, w, store = _two_question_exam(
        app_paths, make_fake, tmp_path,
        [_cover("가학생"), _q_lines("1", "1번 답"), _q_lines("2", "2번 답")],
    )  # fmt: skip
    q1, q2 = (q.number for q in store.exam().questions)
    pdf = _pdf(tmp_path / "answers.pdf", 3)
    assert main(["exam", *w, "answer-pages", store.exam_id, f"{q1}=2", f"{q2}=3"]) == 0
    assert [q.answer_pages for q in store.exam().questions] == [[2], [3]]

    assert main(["exam", *w, "scan-batch", store.exam_id, str(pdf), "--per-student", "3",
                 "-q", q1]) == 0  # fmt: skip
    assert main(["exam", *w, "scan-batch", store.exam_id, str(pdf), "--per-student", "3",
                 "-q", q2]) == 0  # fmt: skip
    prompts = fake_prompts(exe)
    assert len(prompts) == 3, "표지 1번 + 문항마다 판독 1번"
    first, second = prompts[1], prompts[2]
    assert f"[문항 {q1}]" in first and f"[문항 {q2}]" not in first, "맡은 문항만 자료로"
    assert f"[문항 {q2}]" in second and f"[문항 {q1}]" not in second
    assert "input_02" not in first and "input_02" not in second, "답안 한 쪽만 첨부"
    t1, t2 = store.transcript("가학생", q1), store.transcript("가학생", q2)
    assert t1 is not None and t1.pages == ["scans/가학생/page_02.png"]
    assert t2 is not None and t2.pages == ["scans/가학생/page_03.png"]
    rec = store.student_record("가학생")
    assert rec is not None and rec.rotation == 90 and rec.pages == 3

    assert main(["exam", *w, "scan-batch", store.exam_id, str(pdf), "--per-student", "3",
                 "-q", q1]) == 0  # fmt: skip
    assert "이미 판독함" in capsys.readouterr().out and len(fake_prompts(exe)) == 3


def test_question_scan_needs_pages_and_covers_only(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exe, w, store = _two_question_exam(app_paths, make_fake, tmp_path, [_cover("나학생")])
    q1 = store.exam().questions[0].number
    pdf = _pdf(tmp_path / "answers.pdf", 3)
    assert main(["exam", *w, "scan-batch", store.exam_id, str(pdf), "--per-student", "3",
                 "-q", q1]) == 1  # fmt: skip
    assert "답안 쪽을 모릅니다" in capsys.readouterr().err
    assert main(["exam", *w, "answer-pages", store.exam_id, f"{q1}=1"]) == 1, "1쪽은 표지"
    assert main(["exam", *w, "scan-batch", store.exam_id, str(pdf), "--per-student", "3",
                 "--covers-only"]) == 0  # fmt: skip
    assert len(fake_prompts(exe)) == 1, "표지만"
    assert store.student_record("나학생") is not None and store.transcript("나학생", q1) is None


# ---------------------------------------------------------------- 복합기 폴더


def test_scanner_addresses() -> None:
    from dawoni.config import ScannerSettings
    from dawoni.scanner import finder_url, windows_unc

    s = ScannerSettings(smb_url="smb://192.168.0.100/scan/", username="Administrator")
    assert s.smb_url == "smb://192.168.0.100/scan"
    assert finder_url(s) == "smb://Administrator@192.168.0.100/scan"
    assert windows_unc(s) == "\\\\192.168.0.100\\scan"
    bare = ScannerSettings(smb_url="\\\\192.168.0.100")
    assert bare.smb_url == "smb://192.168.0.100" and windows_unc(bare) == "\\\\192.168.0.100"
    with pytest.raises(ValueError, match="smb://"):
        ScannerSettings(smb_url="http://192.168.0.100")


def test_open_scan_folders_mac(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    import sys

    from dawoni import scanner
    from dawoni.config import ScannerSettings

    calls: list[list[str]] = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", lambda argv, **_: calls.append(list(argv)))  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    inbox = tmp_path / "ws" / "스캔본"
    out = scanner.open_scan_folders(
        ScannerSettings(smb_url="smb://192.168.0.100", username="Administrator"), inbox
    )
    assert inbox.is_dir() and out.scanner == "smb://Administrator@192.168.0.100"
    assert calls[0] == ["open", "smb://Administrator@192.168.0.100"]
    assert calls[1][0] == "osascript" and str(inbox) in calls[1][2], "작업공간 창도 연다"
    assert not any("7654321" in " ".join(c) for c in calls), "비밀번호는 다루지 않는다"


def test_open_scanner_without_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    """작업공간을 정하기 전에도 설정 화면에서 복합기 연결만 시험해 볼 수 있다."""
    import subprocess
    import sys

    from dawoni import scanner
    from dawoni.config import ScannerSettings

    calls: list[list[str]] = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", lambda argv, **_: calls.append(list(argv)))  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    out = scanner.open_scan_folders(ScannerSettings(smb_url="smb://192.168.0.100"), None)
    assert out.inbox is None and calls == [["open", "smb://192.168.0.100"]]
