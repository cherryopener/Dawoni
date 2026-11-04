"""M3 답안지 → HWPX: 학생 명부, 판독 직후 판독본 HWPX, 강사가 한글에서 고친 판독본 다시
읽기(확인 완료), 같은 반 동명이인 이름 바꾸기 (PLAN.md §5-1, §7)."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from hwpx.document import HwpxDocument
from openpyxl import Workbook
from PIL import Image
from tests_support import fake_prompts

from dawoai.cli import main
from dawoai.config import AppConfig, save_config
from dawoai.export.exam_files import bundle_file, is_edited, student_file, write_files
from dawoai.export.hwpx import count_heads, read_back
from dawoai.fsio import read_json
from dawoai.grading import workflow
from dawoai.grading.store import ExamStore, ScopeStore
from dawoai.models.grading import Exam, StudentRecord, Transcript, TranscriptLine
from dawoai.models.scope import ScopeProfile
from dawoai.paths import AppPaths
from dawoai.services import Services, open_services
from dawoai.students import Roster, natural_key, read_table
from dawoai.workspace import create_workspace

FIX = Path(__file__).parent / "fixtures" / "grading"
MakeFake = Callable[..., Path]


# ---------------------------------------------------------------- 명부


def test_read_table_csv_cp949_ignores_phone(tmp_path: Path) -> None:
    p = tmp_path / "명부.csv"
    rows = [["반", "성명", "학교", "전화번호"], ["2반", "김 다원", "구룡중", "010-1234-5678"],
            ["10반", "이서연", "", ""]]  # fmt: skip
    with p.open("w", encoding="cp949", newline="") as f:
        csv.writer(f).writerows(rows)
    got, notes = read_table(p)
    assert got == [
        {"name": "김 다원", "class_name": "2반", "school": "구룡중"},
        {"name": "이서연", "class_name": "10반", "school": None},
    ]
    assert any("전화번호" in n for n in notes)
    assert "010" not in json.dumps(got), "전화번호는 읽지 않는다"


def test_read_table_xlsx_without_header(tmp_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["TSS1", "박찬현", "구룡중"])
    ws.append(["TSS1", "김태희A", None])
    p = tmp_path / "명부.xlsx"
    wb.save(p)
    got, notes = read_table(p)
    assert [r["name"] for r in got] == ["박찬현", "김태희A"] and got[0]["class_name"] == "TSS1"
    assert any("반, 이름, 학교 순서" in n for n in notes)


def test_roster_import_and_match(tmp_path: Path) -> None:
    roster = Roster(create_workspace(tmp_path / "ws"))
    res = roster.import_rows([
        {"name": "김 다원", "class_name": "2반", "school": None},
        {"name": "김다원", "class_name": "2반", "school": None},  # 같은 반에 두 번
        {"name": "박찬현", "class_name": "10반", "school": "구룡중"},
        {"name": "이서연A", "class_name": "2반", "school": None},
        {"name": "이서연B", "class_name": "2반", "school": None},
    ])  # fmt: skip
    assert [s.name for s in res.added] == ["김다원", "박찬현", "이서연A", "이서연B"]
    assert [s.id for s in res.added] == ["S0001", "S0002", "S0003", "S0004"]
    assert len(res.problems) == 1 and "김다원A, 김다원B" in res.problems[0]
    again = roster.import_rows([{"name": "박찬현", "class_name": "10반", "school": "용산중"}])
    assert [s.school for s in again.updated] == ["용산중"] and not again.added, "학교만 고친다"
    assert roster.classes() == ["2반", "10반"], "자연 정렬"
    assert sorted(["10반", "2반", "1반"], key=natural_key) == ["1반", "2반", "10반"]

    m = roster.match("김 다원", "2")
    assert m.status == "linked" and m.student is not None and m.student.id == "S0001", "2 = 2반"
    assert roster.match("박찬현", "2반").status == "class_differs"
    lettered = roster.match("이서연", "2반")
    assert lettered.status == "ambiguous" and len(lettered.candidates) == 2
    assert lettered.note is not None and "알파벳" in lettered.note
    assert roster.match("최민준", "2반").status == "new"
    assert roster.match(None, "2반").status == "unknown"
    with pytest.raises(ValueError, match="이미 있습니다"):
        roster.add("김다원", "2반")


def test_cli_students(
    app_paths: AppPaths, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = create_workspace(tmp_path / "ws")
    p = tmp_path / "명부.csv"
    p.write_text("이름,반\n김다원,2반\n박찬현,TSS1\n", encoding="utf-8")
    w = ["students", "--workspace", str(ws.root)]
    assert main([*w, "import", str(p)]) == 0
    assert main([*w, "add", "이서연", "--class", "2반", "--school", "구룡중"]) == 0
    assert main([*w, "list", "--class", "2"]) == 0
    out = capsys.readouterr().out
    assert "추가 2명" in out and "S0003" in out and "박찬현" not in out.split("추가 2명")[1]


# ---------------------------------------------------------------- 판독본 HWPX · 확인 완료


def _services(app_paths: AppPaths, tmp_path: Path, exe: Path | None = None) -> Services:
    ws = create_workspace(tmp_path / "ws")
    cfg = AppConfig()
    if exe is not None:
        cfg.agents["agy"].executable = str(exe)
    cfg.ocr.cross_check = False
    save_config(app_paths, cfg)
    svc = open_services(ws.root)
    ScopeStore(svc.workspace).save(ScopeProfile.model_validate(read_json(FIX / "scope.json")))
    return svc


def _exam(svc: Services, n: int = 1) -> ExamStore:
    exam = Exam.model_validate(
        {"title": "10월 시험", "scope_id": "scope-quadratic-minmax",
         "questions": read_json(FIX / "questions.json")[:n]}
    )  # fmt: skip
    return ExamStore.create(svc.workspace, exam)


LINES = [
    TranscriptLine(part="(1)", text=r"전류를 \(I_{\text{㉠}}\)라 하면 \(V=IR\)이야"),
    TranscriptLine(part="(1)", text="저항이 적은 ㉠이 [판독불가] 밝아", uncertain=["적은"]),
    TranscriptLine(part="(2)", math=r"\frac{3R}{3+R} : 6 \quad R=6\Omega"),
]


def _student(store: ExamStore, sid: str, name: str, klass: str = "2반") -> None:
    store.save_student(StudentRecord(student_id=sid, name=name, class_name=klass, source="t"))
    scan = store.scan_dir(sid)
    scan.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 60), "white").save(scan / "page_02.png")
    store.save_transcript(Transcript(student_id=sid, question_number="1", lines=LINES,
                                     source="ocr", pages=[f"scans/{sid}/page_02.png"]))  # fmt: skip


def _edit(path: Path, change: Callable[[Any], None]) -> None:
    """강사가 한글에서 고친 것처럼 (python-hwpx로 문단을 고쳐 저장)."""
    doc = HwpxDocument.open(path)
    for p in doc.paragraphs:
        change(p)
    doc.save_to_path(path)


def _fill(p: Any) -> None:
    if "[판독불가]" in (p.text or ""):
        p.text = "저항이 작은 ㉠이 더 밝아"


def test_reading_file_written_and_confirmed_from_hwpx(app_paths: AppPaths, tmp_path: Path) -> None:
    svc = _services(app_paths, tmp_path)
    store = _exam(svc)
    _student(store, "김다원", "김다원")
    res = write_files(svc.workspace, store, [("김다원", "1")])
    path = student_file(svc.workspace, store, "김다원", "1")
    assert path in res.written and path.name == "2반_김다원_1.hwpx", "채점 전에도 판독본을 쓴다"
    assert bundle_file(svc.workspace, store, "2반", "1") in res.written
    back = read_back(path)
    assert back.head == "10월 시험 · 2반 · 김다원" and back.pictures == 1
    assert list(back.parts) == ["1-(1)", "1-(2)"], "머리 아래 안내 문단은 판독문에 섞이지 않는다"

    # [판독불가]가 남은 채로 확인 완료 → 받지만 확정하지 않는다
    run = workflow.confirm_from_files(svc, store, by="김선생")
    assert not run.confirmed and len(run.waiting) == 1
    t = store.transcript("김다원", "1")
    assert t is not None and not t.confirmed

    # 강사가 한글에서 채워 저장 → 앱이 그 파일을 덮어쓰지 않는다
    _edit(path, _fill)
    assert is_edited(store, path)
    again = write_files(svc.workspace, store, [("김다원", "1")])
    assert path in again.edited and path not in again.written
    assert "확인 완료" in again.warnings[0]

    run = workflow.confirm_from_files(svc, store, by="김선생")
    assert [c.changed for c in run.confirmed] == [1] and not run.problems
    t = store.transcript("김다원", "1")
    assert t is not None and t.confirmed and t.confirmed_by == "김선생"
    assert [x.text for x in t.lines][:2] == [LINES[0].text, "저항이 작은 ㉠이 더 밝아"]
    assert t.lines[0] == LINES[0] and t.lines[2] == LINES[2], "고치지 않은 줄은 원래 LaTeX 그대로"
    assert t.lines[1].part == "(1)" and not t.lines[1].uncertain
    assert any("1줄 고침" in n for n in t.notes)
    assert path in run.export.written, "확정 뒤 다시 써서 '확인 전' 표시를 지운다"
    assert not is_edited(store, path)
    assert "판독문 확인 전" not in "".join(p.text or "" for p in HwpxDocument.open(path).paragraphs)

    rerun = workflow.confirm_from_files(svc, store)
    assert rerun.unchanged == ["김다원 1"] and not rerun.confirmed, "해시가 같으면 다시 읽지 않는다"


def test_confirm_refuses_broken_marks_and_other_heads(app_paths: AppPaths, tmp_path: Path) -> None:
    svc = _services(app_paths, tmp_path)
    store = _exam(svc)
    _student(store, "김다원", "김다원")
    _student(store, "박찬현", "박찬현")
    write_files(svc.workspace, store, [("김다원", "1"), ("박찬현", "1")])
    mine = student_file(svc.workspace, store, "김다원", "1")
    other = student_file(svc.workspace, store, "박찬현", "1")

    def drop_mark(p: Any) -> None:
        if p.text == "■ 1-(2)":
            p.text = "1-(2)"
        _fill(p)

    _edit(mine, drop_mark)
    other.write_bytes(mine.read_bytes())  # 다른 학생 파일을 붙여 넣음
    run = workflow.confirm_from_files(svc, store)
    why = {p.student: p.reason for p in run.problems}
    assert "'■ 1-(2)' 표식이 없어" in why["김다원"]
    assert "머리" in why["박찬현"] and "박찬현" in why["박찬현"]
    assert not run.confirmed
    for sid in ("김다원", "박찬현"):
        t = store.transcript(sid, "1")
        assert t is not None and not t.confirmed and t.lines == LINES, "문제가 있으면 손대지 않는다"


def test_cli_confirm(
    app_paths: AppPaths, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    svc = _services(app_paths, tmp_path)
    store = _exam(svc)
    _student(store, "김다원", "김다원")
    w = ["exam", "--workspace", str(svc.workspace.root)]
    assert main([*w, "export", store.exam_id]) == 0
    _edit(student_file(svc.workspace, store, "김다원", "1"), _fill)
    assert main([*w, "export", store.exam_id]) == 1, "고친 파일은 두고 알린다"
    assert "--force" in capsys.readouterr().out
    assert main([*w, "confirm", store.exam_id, "--by", "김선생"]) == 0
    assert "✓ 김다원 문항 1: 확정 (1줄 고침)" in capsys.readouterr().out


# ---------------------------------------------------------------- 묶음 판독 + 명부 + 동명이인


def _pdf(path: Path, n: int) -> Path:
    doc = pymupdf.open()
    for i in range(n):
        p = doc.new_page(width=595, height=842)
        p.insert_text((60, 80), f"page {i + 1}", fontsize=20)  # pyright: ignore[reportUnknownMemberType]
    doc.save(str(path))  # pyright: ignore[reportUnknownMemberType]
    return path


def _cover(name: str, klass: str) -> dict[str, Any]:
    return {"rotation": "0", "name": name, "class_name": klass, "school": None,
            "teacher_scores": []}  # fmt: skip


def _lines(text: str) -> dict[str, Any]:
    return {"lines": [{"question_number": "1", "page": 1, "part": "(1)", "text": text,
                       "confidence": 0.9}]}  # fmt: skip


def test_batch_links_roster_and_holds_same_class_twins(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    covers = [_cover("김 다원", "2반"), _cover("박찬현", "2반"), _cover("박찬현", "2반"),
              _cover("최민준", "2반")]  # fmt: skip
    reads = [_lines(f"{k}번째 학생 답") for k in range(1, 5)]
    exe = make_fake("script", script=[{"response": json.dumps(r, ensure_ascii=False)}
                                      for r in covers + reads])  # fmt: skip
    svc = _services(app_paths, tmp_path, exe)
    roster = Roster(svc.workspace)
    roster.import_rows([{"name": "김다원", "class_name": "2반", "school": "구룡중"},
                        {"name": "박찬현", "class_name": "2반", "school": None}])  # fmt: skip
    store = _exam(svc)
    pdf = _pdf(tmp_path / "answers.pdf", 8)

    res = workflow.scan_batch(svc, store, pdf, per_student=2)
    assert "2반" in fake_prompts(exe)[0], "반 목록을 주지 않으면 명부의 반 중에서 고른다"
    recs = {r.student_id: r for r in res.students}
    assert set(recs) == {"김다원", "박찬현", "박찬현#3", "최민준"}
    kim = recs["김다원"]
    assert kim.roster_id == "S0001" and kim.class_name == "2반" and kim.school == "구룡중"
    assert kim.cover_name == "김 다원" and kim.roster_note is None
    assert recs["최민준"].roster_note is not None and "명부에 없음" in recs["최민준"].roster_note
    assert recs["박찬현"].needs_rename and recs["박찬현#3"].needs_rename
    saved = store.student_record("박찬현")
    assert saved is not None and saved.needs_rename

    names = sorted(p.name for p in res.export.written)
    assert names == ["(반별)_2반_1.hwpx", "2반_김다원_1.hwpx", "2반_최민준_1.hwpx"], (
        "판독이 끝나면 바로 판독본을 쓰고, 같은 반 동명이인은 이름을 바꿀 때까지 보류"
    )
    bundle = bundle_file(svc.workspace, store, "2반", "1")
    assert count_heads(bundle) == 4, "묶음에는 보류한 학생도 안내 쪽으로"

    # 강사가 이름을 바꾸면 그 학생 파일을 만들고, 짝도 풀린다
    w = ["exam", "--workspace", str(svc.workspace.root)]
    capsys.readouterr()
    assert main([*w, "student", store.exam_id, "박찬현#3", "--name", "박찬현B"]) == 0
    assert "2반_박찬현_1.hwpx" in capsys.readouterr().out, "짝(박찬현)도 이제 만든다"
    assert store.transcript("박찬현#3", "1") is None
    moved = store.transcript("박찬현B", "1")
    assert moved is not None and moved.lines[0].text == "3번째 학생 답"
    assert moved.pages == ["scans/박찬현B/page_02.png"]
    assert (store.root / moved.pages[0]).is_file()
    b = store.student_record("박찬현B")
    a = store.student_record("박찬현")
    assert b is not None and not b.needs_rename and a is not None and not a.needs_rename
    assert student_file(svc.workspace, store, "박찬현B", "1").is_file()

    # 명부에 없던 학생을 명부에 넣고 잇는다
    new = roster.add("최민준", "2반", "용산중")
    assert main([*w, "student", store.exam_id, "최민준", "--roster", new.id]) == 0
    linked = store.student_record("최민준")
    assert linked is not None and linked.roster_id == new.id and linked.roster_note is None
    assert linked.school == "용산중"
    assert main([*w, "student", store.exam_id, "최민준", "--name", "김다원"]) == 1, (
        "같은 반 같은 이름"
    )


def test_api_confirm_files_and_identity(app_paths: AppPaths, tmp_path: Path) -> None:
    from test_server import _client, _wait  # pyright: ignore[reportPrivateUsage]

    svc = _services(app_paths, tmp_path)
    cfg = AppConfig.model_validate(read_json(app_paths.config_file))
    cfg.workspace = svc.workspace.root
    save_config(app_paths, cfg)
    store = _exam(svc)
    _student(store, "김다원", "김다원")
    write_files(svc.workspace, store, [("김다원", "1")])
    _edit(student_file(svc.workspace, store, "김다원", "1"), _fill)
    c = _client(app_paths)
    r = c.post("/api/students", json={"name": "김다원", "class_name": "2반"})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert c.get("/api/students").json()["classes"] == ["2반"]
    job = c.post(f"/api/exams/{store.exam_id}/confirm-files", json={"by": "김선생"}).json()
    done = _wait(c, job)
    assert done["status"] == "succeeded", done
    assert done["result"]["confirmed"] == [{"student": "김다원", "question": "1", "changed": 1}]
    r = c.put(f"/api/exams/{store.exam_id}/students/김다원/identity", json={"roster_id": rid})
    assert r.status_code == 200, r.text
    assert r.json()["record"]["roster_id"] == rid
    rows = c.get(f"/api/exams/{store.exam_id}/students").json()
    assert rows[0]["record"]["roster_id"] == rid
    summary = c.get(f"/api/exams/{store.exam_id}").json()["summary"]
    assert summary["needs_rename"] == 0 and summary["pending_transcripts"] == 0


def test_blank_answer_is_not_read_again(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path
) -> None:
    """2번을 비워 낸 학생: 판독문은 1번만 생기지만, 다시 돌리면 이 학생은 건너뛴다
    (2026-11-04 실측에서 빈 답안이 있는 학생을 매번 다시 판독했다)."""
    replies = (_cover("가학생", "2반"), _lines("1번 답"))
    exe = make_fake(
        "script", script=[{"response": json.dumps(r, ensure_ascii=False)} for r in replies]
    )
    svc = _services(app_paths, tmp_path, exe)
    store = _exam(svc, 2)
    pdf = _pdf(tmp_path / "answers.pdf", 2)
    first = workflow.scan_batch(svc, store, pdf, per_student=2)
    assert [r.transcribed for r in first.students] == [["1", "2"]]
    assert store.transcript("가학생", "2") is None
    again = workflow.scan_batch(svc, store, pdf, per_student=2)
    assert again.skipped == ["가학생"] and len(fake_prompts(exe)) == 2
