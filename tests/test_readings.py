"""M3 답안지: 학생 명부, 판독문을 화면에서 고쳐 확인 완료, 같은 반 동명이인 이름 바꾸기
(PLAN.md §5-1, §7). 2026-11-18까지는 판독본 HWPX를 한글에서 고쳐 다시 읽었다."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from openpyxl import Workbook
from PIL import Image
from tests_support import fake_prompts

from dawoni.cli import main
from dawoni.config import AppConfig, save_config
from dawoni.export.exam_files import score_file
from dawoni.fsio import read_json
from dawoni.grading import workflow
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.models.grading import Exam, StudentRecord, Transcript, TranscriptLine
from dawoni.models.scope import ScopeProfile
from dawoni.paths import AppPaths
from dawoni.server.api_views import exam_notices
from dawoni.services import Services, open_services
from dawoni.students import Roster, natural_key, read_table
from dawoni.workspace import create_workspace

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


def test_roster_edit_and_remove(tmp_path: Path) -> None:
    """화면에서 명부 학생 한 명을 고치고 뺀다 (엑셀을 다시 넣지 않고)."""
    roster = Roster(create_workspace(tmp_path / "ws"))
    a = roster.add("김다은", "2반")
    b = roster.add("박찬현", " 3반 ", " ")
    assert (b.class_name, b.school) == ("3반", None), "빈칸은 지운다"
    fixed = roster.update(a.id, "김 다원", "2반", "구룡중")
    assert (fixed.id, fixed.name, fixed.school) == (a.id, "김다원", "구룡중"), "ID는 그대로"
    assert roster.match("김다원", "2").student == fixed
    assert roster.update(a.id, "김다원", "2", None).class_name == "2", "자기 자신과는 겹치지 않는다"
    with pytest.raises(ValueError, match="이미 있습니다"):
        roster.update(b.id, "김다원", "2반", None)
    with pytest.raises(ValueError, match="이름을 적어"):
        roster.update(b.id, " ", "3반", None)
    assert roster.remove(b.id).name == "박찬현"
    assert [s.id for s in roster.all()] == [a.id]
    with pytest.raises(KeyError):
        roster.remove(b.id)
    assert roster.add("최민준").id == "S0002", "뺀 뒤 새 학생은 남은 번호 다음"


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
    assert main([*w, "edit", "S0003", "--name", "이서윤"]) == 0
    assert main([*w, "remove", "S0002"]) == 0
    assert main([*w, "remove", "S0002"]) == 1
    roster = Roster(ws)
    assert [(s.name, s.school) for s in roster.all()] == [("김다원", None), ("이서윤", "구룡중")]


# ---------------------------------------------------------------- 판독본 HWPX · 확인 완료


def _services(app_paths: AppPaths, tmp_path: Path, exe: Path | None = None) -> Services:
    ws = create_workspace(tmp_path / "ws")
    cfg = AppConfig()
    cfg.parallel_students = 1  # 가짜 CLI가 정해진 순서로 답한다
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


def _client_for(app_paths: AppPaths, svc: Services) -> Any:
    from test_server import _client  # pyright: ignore[reportPrivateUsage]

    cfg = AppConfig.model_validate(read_json(app_paths.config_file))
    cfg.workspace = svc.workspace.root
    save_config(app_paths, cfg)
    return _client(app_paths)


def test_screen_fills_unreadable_then_confirms(app_paths: AppPaths, tmp_path: Path) -> None:
    """판독문 고치기 화면: 그 문항의 원본 쪽과 판독문을 열고, [판독불가]가 남으면 확인하지 않고,
    채워 확인하면 확정 판독문이 된다 (2026-11-18 한글 대신 화면)."""
    svc = _services(app_paths, tmp_path)
    store = _exam(svc)
    _student(store, "김다원", "김다원")
    Image.new("RGB", (40, 60), "white").save(store.scan_dir("김다원") / "page_01.png")  # 표지
    c = _client_for(app_paths, svc)
    url = f"/api/exams/{store.exam_id}/students/김다원/transcripts/1"

    got = c.get(url).json()
    assert got["page_images"] == ["scans/김다원/page_02.png"], "표지는 빼고 그 문항을 읽은 쪽만"
    assert got["name"] == "김다원" and got["class_name"] == "2반" and got["unreadable"] == 1
    assert got["question"]["number"] == "1" and got["transcript"]["lines"][1]["uncertain"]

    lines = [ln.model_dump(mode="json") for ln in LINES]
    r = c.put(url, json={"lines": lines, "confirm": True, "by": "김선생"})
    assert r.status_code == 400 and "[판독불가]" in r.json()["detail"], "남으면 확정 안 함"

    lines[1] |= {"text": "저항이 작은 ㉠이 더 밝아", "uncertain": [], "confidence": 1}
    r = c.put(url, json={"lines": lines, "confirm": False})
    assert r.status_code == 200 and not r.json()["confirmed"], "저장만 하면 확인 전 그대로"
    r = c.put(url, json={"lines": lines, "confirm": True, "by": "김선생"})
    assert r.status_code == 200, r.text
    t = store.transcript("김다원", "1")
    assert t is not None and t.confirmed and t.confirmed_by == "김선생"
    assert t.lines[1].text == "저항이 작은 ㉠이 더 밝아" and t.lines[0] == LINES[0]
    assert t.lines[2] == LINES[2], "고치지 않은 줄은 원래 LaTeX 그대로"
    assert score_file(svc.workspace, store).is_file(), "확인하면 점수표의 '확인 전' 표시를 맞춘다"
    exports = score_file(svc.workspace, store).parent
    assert not list(exports.rglob("*.hwpx")), "HWPX는 쓰지 않는다"


def test_erased_and_illegible_spots_confirm_without_new_grading(
    app_paths: AppPaths, tmp_path: Path
) -> None:
    """[판독불가]를 '학생이 지운 자리'·'원본도 못 읽음'으로 바꾸면 확인할 수 있고, 줄 안의 지운
    부분(~~…~~)만 지운 것으로 본다 (2026-11-19 사용자: 지운 부분만 지웠는데 줄 전체가 지운 줄이 됨).
    '맞음'으로 애매한 곳 표시만 지운 확인은 다시 채점하지 않는다."""
    from test_grading import approved_rubric

    from dawoni.grading.render import transcript_text
    from dawoni.models.grading import GradeResult, ItemJudgement

    svc = _services(app_paths, tmp_path)
    store = _exam(svc)
    _student(store, "김다원", "김다원")
    c = _client_for(app_paths, svc)
    url = f"/api/exams/{store.exam_id}/students/김다원/transcripts/1"
    lines = [ln.model_dump(mode="json") for ln in LINES]
    lines[1] |= {"text": "저항이 ~~적은~~ 작은 ㉠이 [지움] 밝아 [못 읽음]"}
    r = c.put(url, json={"lines": lines, "confirm": True, "by": "김선생"})
    assert r.status_code == 200, r.text
    t = store.transcript("김다원", "1")
    assert t is not None and t.confirmed and t.unreadable == 0 and not t.lines[1].struck
    assert t.lines[1].kept == "저항이 작은 ㉠이 밝아 [못 읽음]", (
        "지운 부분·지운 자리는 채점에서 뺀다"
    )
    text = transcript_text(t)
    assert "(지운 흔적: 적은)" in text and "(지운 자리)" in text and "(강사도 못 읽음)" in text
    erased = TranscriptLine(text="~~다 지움~~ [지움]")
    assert Transcript(student_id="x", question_number="1", lines=[erased]).is_blank

    # 채점이 있는 답안: 애매한 곳 표시만 지우고 확인하면 AI를 부르지 않는다 (내용이 같다)
    store.save_rubric(approved_rubric())
    j = ItemJudgement(rubric_item_id="R1", awarded=1, reason="r", confidence=0.9)
    store.save_grade(GradeResult(student_id="김다원", question_number="1", rubric_version=1,
                                 judgements=[j], comment="", total=1, max_points=1,
                                 item_points={"R1": 1}))  # fmt: skip
    before = t.model_copy(update={"confirmed": False})
    flagged = t.model_copy(update={"lines": [ln.model_copy(update={"uncertain": ["?"],
                                                                    "confidence": 0.5})
                                             for ln in t.lines]})  # fmt: skip
    assert not workflow.wants_grading(store, flagged.model_copy(update={"confirmed": False}), t)
    changed = t.model_copy(update={"lines": [*t.lines[:1], t.lines[1].model_copy(
        update={"text": "저항이 작은 ㉠이 더 밝아"}), *t.lines[2:]]})  # fmt: skip
    assert workflow.wants_grading(store, before, changed), "글을 고쳤으면 다시 채점"


def test_older_exam_readings_are_accepted_in_one_go(app_paths: AppPaths, tmp_path: Path) -> None:
    """자동 확인 전에 판독한 시험: [판독불가]가 없는 확인 전 판독문을 한 번에 AI 확인으로 (AI 호출
    없음). [판독불가]가 남은 판독문은 그대로, 빈 답안이라 검토 필요였던 0점은 끝낸다."""
    from dawoni.grading.pipeline import BLANK_FLAG
    from dawoni.models.grading import GradeResult, ItemJudgement

    svc = _services(app_paths, tmp_path)
    store = _exam(svc)
    _student(store, "김다원", "김다원")  # LINES에 [판독불가]
    _student(store, "박서준", "박서준")
    clean = [ln.model_copy(update={"text": ln.text.replace("[판독불가]", "더")}) for ln in LINES]
    store.save_transcript(Transcript(student_id="박서준", question_number="1", lines=clean))
    _student(store, "이하늘", "이하늘")
    store.save_transcript(Transcript(student_id="이하늘", question_number="1", lines=[]))
    j = ItemJudgement(rubric_item_id="R1", awarded=0, reason="답 없음", confidence=1)
    store.save_grade(GradeResult(student_id="이하늘", question_number="1", rubric_version=1,
                                 judgements=[j], comment="", total=0, max_points=1,
                                 flags=[BLANK_FLAG], status="needs_review",
                                 transcript_confirmed=False, item_points={"R1": 1}))  # fmt: skip
    c = _client_for(app_paths, svc)
    r = c.post(f"/api/exams/{store.exam_id}/transcripts/accept").json()
    assert r == {"readings": 2, "blanks": 1}
    kim, park = store.transcript("김다원", "1"), store.transcript("박서준", "1")
    assert kim is not None and not kim.confirmed, "[판독불가]는 강사가"
    assert park is not None and park.confirmed and park.confirmed_by == "AI"
    g = store.grade("이하늘", "1")
    assert g is not None and g.status == "auto" and g.transcript_confirmed and not g.flags
    assert c.post(f"/api/exams/{store.exam_id}/transcripts/accept").json() == {
        "readings": 0,
        "blanks": 0,
    }


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
    assert "2반: 김다원, 박찬현" in fake_prompts(exe)[0], "표지 이름도 명부 안에서 고르게"
    recs = {r.student_id: r for r in res.students}
    assert set(recs) == {"김다원", "박찬현", "박찬현#3", "최민준"}
    kim = recs["김다원"]
    assert kim.roster_id == "S0001" and kim.class_name == "2반" and kim.school == "구룡중"
    assert kim.cover_name == "김 다원" and kim.roster_note is None
    assert recs["최민준"].roster_note is not None and "명부에 없음" in recs["최민준"].roster_note
    assert recs["박찬현"].needs_rename and recs["박찬현#3"].needs_rename
    saved = store.student_record("박찬현")
    assert saved is not None and saved.needs_rename

    assert [p.suffix for p in res.export.written] == [".xlsx"], "점수표만 (채점 전이라 비어 있음)"
    assert store.transcript("최민준", "1") is not None, "명부에 없어도 판독은 한다"
    notices = {n["id"].split(":")[0]: n for n in exam_notices(store)}
    assert notices["roster"]["title"] == "명부에 없는 이름 1명"
    assert "2반 최민준" in notices["roster"]["detail"]

    # 강사가 이름을 바꾸면 짝도 풀린다
    w = ["exam", "--workspace", str(svc.workspace.root)]
    capsys.readouterr()
    assert main([*w, "student", store.exam_id, "박찬현#3", "--name", "박찬현B"]) == 0
    assert "점수표" in capsys.readouterr().out
    assert store.transcript("박찬현#3", "1") is None
    moved = store.transcript("박찬현B", "1")
    assert moved is not None and moved.lines[0].text == "3번째 학생 답"
    assert moved.pages == ["scans/박찬현B/page_02.png"]
    assert (store.root / moved.pages[0]).is_file()
    b = store.student_record("박찬현B")
    a = store.student_record("박찬현")
    assert b is not None and not b.needs_rename and a is not None and not a.needs_rename
    assert b.roster_note is not None, "명부에는 박찬현 한 명뿐 — 박찬현B는 명부 확인"
    assert b.name_pending
    assert main([*w, "student", store.exam_id, "박찬현B", "--off-roster"]) == 0
    b = store.student_record("박찬현B")
    assert b is not None and not b.name_pending

    # 명부에 없던 학생을 명부에 넣고 잇는다
    new = roster.add("최민준", "2반", "용산중")
    assert main([*w, "student", store.exam_id, "최민준", "--roster", new.id]) == 0
    linked = store.student_record("최민준")
    assert linked is not None and linked.roster_id == new.id and linked.roster_note is None
    assert linked.school == "용산중" and not linked.name_pending
    assert "roster" not in {n["id"].split(":")[0] for n in exam_notices(store)}
    assert main([*w, "student", store.exam_id, "최민준", "--name", "김다원"]) == 1, (
        "같은 반 같은 이름"
    )


def test_name_fix_rematches_roster_or_accepts_off_roster(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path
) -> None:
    """표지 오독(이태오 → 이타요): 강사가 이름을 고치면 명부와 다시 맞춰 잇는다. 명부에 없는
    학생(전학 온 학생 등)은 그대로 확정할 수 있다."""
    covers = [_cover("이타요", "2반"), _cover("최민준", "2반")]
    reads = [_lines("답 1"), _lines("답 2")]
    exe = make_fake("script", script=[{"response": json.dumps(r, ensure_ascii=False)}
                                      for r in covers + reads])  # fmt: skip
    svc = _services(app_paths, tmp_path, exe)
    roster = Roster(svc.workspace)
    roster.import_rows([{"name": "이태오", "class_name": "2반", "school": "포항제철중"}])
    store = _exam(svc)
    workflow.scan_batch(svc, store, _pdf(tmp_path / "a.pdf", 4), per_student=2)
    pending = store.student_record("이타요")
    assert pending is not None and pending.name_pending

    fixed = workflow.rename_student(svc, store, "이타요", name="이태오")
    assert fixed.record.roster_id == "S0001" and fixed.record.school == "포항제철중"
    assert not fixed.record.name_pending and fixed.record.student_id == "이태오"
    assert store.transcript("이태오", "1") is not None

    still = workflow.rename_student(svc, store, "최민준", name="최민준")
    assert still.record.roster_note is not None, "명부에 없는 이름으로 고치면 확인이 남는다"
    kept = workflow.rename_student(svc, store, "최민준", off_roster=True)
    assert kept.record.roster_note is None and kept.record.roster_id is None
    assert not [n for n in exam_notices(store) if n["id"].startswith(("roster", "rename"))]


def test_name_fix_unlinks_wrong_roster_student(app_paths: AppPaths, tmp_path: Path) -> None:
    """표지 김다원을 명부의 다른 학생 김다은으로 읽어 잘못 이어졌을 때: 명부 확인 표시가 없어도
    이름을 고칠 수 있고, 명부에 없는 이름으로 고치면 옛 연결(김다은)은 끊는다."""
    svc = _services(app_paths, tmp_path)
    roster = Roster(svc.workspace)
    wrong = roster.add("김다은", "2반")
    store = _exam(svc)
    _student(store, "김다은", "김다은")
    rec = store.student_record("김다은")
    assert rec is not None
    store.save_student(rec.model_copy(update={"roster_id": wrong.id}))

    res = workflow.rename_student(svc, store, "김다은", name="김다원")
    assert res.record.roster_id is None and res.record.roster_note is not None
    right = roster.add("김다원", "2반", "구룡중")  # 이름 확인 창의 '명부에 넣고 잇기'
    res = workflow.rename_student(svc, store, "김다원", roster_id=right.id)
    got = (res.record.roster_id, res.record.school, res.record.roster_note)
    assert got == (right.id, "구룡중", None)


def test_api_identity_and_history(app_paths: AppPaths, tmp_path: Path) -> None:
    svc = _services(app_paths, tmp_path)
    store = _exam(svc)
    _student(store, "김다원", "김다원")
    lines = [ln.model_copy(update={"text": "채움"}) if ln.uncertain else ln for ln in LINES]
    store.save_transcript(Transcript(student_id="김다원", question_number="1", lines=lines,
                                     source="ocr", confirmed=True))  # fmt: skip
    c = _client_for(app_paths, svc)
    r = c.post("/api/students", json={"name": "김다원", "class_name": "2반"})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert c.get("/api/students").json()["classes"] == ["2반"]
    r = c.put(f"/api/exams/{store.exam_id}/students/김다원/identity", json={"roster_id": rid})
    assert r.status_code == 200, r.text
    assert r.json()["record"]["roster_id"] == rid
    rows = c.get(f"/api/exams/{store.exam_id}/students").json()
    assert rows[0]["record"]["roster_id"] == rid
    summary = c.get(f"/api/exams/{store.exam_id}").json()["summary"]
    assert summary["needs_rename"] == 0 and summary["pending_transcripts"] == 0

    edit = {"name": "김다원", "class_name": "2반", "school": "구룡중"}
    r = c.put(f"/api/students/{rid}", json=edit)
    assert r.status_code == 200 and r.json()["school"] == "구룡중", r.text
    assert c.post("/api/students", json={"name": "김 다원", "class_name": "2"}).status_code == 400
    assert c.delete("/api/students/S9999").status_code == 404
    assert c.delete(f"/api/students/{rid}").status_code == 200
    who = [p for p in c.get("/api/students/history").json() if p["name"] == "김다원"]
    assert len(who) == 1 and who[0]["roster_id"] is None, "명부에서 빼면 시험 기록은 남고 명부 밖"
    assert len(who[0]["exams"]) == 1
    assert who[0]["exams"][0]["numbers"] == ["1"], "학생 검색에서 문항마다 채점 검토로"


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
    blank = store.transcript("가학생", "2")
    assert blank is not None and blank.is_blank and blank.pages, "빈 판독문 + 원본 쪽 (M4)"
    again = workflow.scan_batch(svc, store, pdf, per_student=2)
    assert again.skipped == ["가학생"] and len(fake_prompts(exe)) == 2
