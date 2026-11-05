"""엑셀 점수표 (dawoni/export/xlsx.py, PLAN.md §9-2) — 정렬 규칙, 빈 칸 색, 반별 시트, 시험
데이터에서."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from dawoni.export.exam_files import export_scores, score_file, score_table, write_files
from dawoni.export.xlsx import FILLS, NO_CLASS, Cell, Column, Row, build_scores
from dawoni.grading.store import ExamStore
from dawoni.models.grading import (
    Exam,
    GradeResult,
    ItemJudgement,
    Rubric,
    StudentRecord,
    Transcript,
    TranscriptLine,
)
from dawoni.paths import WorkspacePaths
from dawoni.workspace import create_workspace

COLS = [Column("1-(1)", 3), Column("1-(2)", 2), Column("2", 5)]


def _fill(sheet: Worksheet, row: int, column: int) -> str:
    fill = cast(Any, sheet.cell(row=row, column=column).fill)
    return str(fill.fgColor.rgb)[-6:]


def _ok(v: float) -> Cell:
    return Cell(v, "ok")


def _table(sheet: Worksheet) -> list[list[object]]:
    return [list(r) for r in sheet.iter_rows(values_only=True)]


def _full(klass: str, name: str) -> Row:
    row = Row(klass, name, None)
    row.cells = {"1-(1)": _ok(3), "1-(2)": _ok(1), "2": _ok(4)}
    row.totals = {"1": _ok(4), "2": _ok(4)}
    return row


def test_sort_classes_naturally_then_names_ganada(tmp_path: Path) -> None:
    """1반 < 2반 < 10반, TSS1 < TSS2, 반 없음은 맨 뒤 · 같은 반은 김 < 박 < 이,
    김다원A < 김다원B."""
    rows = [_full(k, n) for k, n in [
        ("10반", "김민준"), ("2반", "이서연"), ("TSS2", "김하늘"), ("2반", "김다원B"),
        (NO_CLASS, "가나다"), ("1반", "최유리"), ("2반", "박찬현"), ("TSS1", "한예성"),
        ("2반", "김다원A"),
    ]]  # fmt: skip
    path = build_scores(tmp_path / "s.xlsx", "10월 시험", COLS, rows)
    wb = load_workbook(path)
    assert wb.sheetnames == ["점수", "안내"]
    got = [(r[0], r[1]) for r in _table(wb["점수"])[1:]]
    assert got == [
        ("1반", "최유리"), ("2반", "김다원A"), ("2반", "김다원B"), ("2반", "박찬현"),
        ("2반", "이서연"), ("10반", "김민준"), ("TSS1", "한예성"), ("TSS2", "김하늘"),
        (None, "가나다"),
    ]  # fmt: skip
    assert _table(wb["점수"])[0] == ["반", "이름", "학교", "1-(1)", "1-(2)", "2", "총점"]
    assert _table(wb["점수"])[1][3:] == [3, 1, 4, 8], "총점은 값 (수식이 아님)"


def test_ungraded_and_review_cells_are_blank_and_colored(tmp_path: Path) -> None:
    done = _full("2반", "김다원")
    review = Row("2반", "박찬현", None, cells={"1-(1)": Cell(None, "review"),
                                             "1-(2)": Cell(None, "review"), "2": _ok(5)},
                 totals={"1": Cell(None, "review"), "2": _ok(5)})  # fmt: skip
    early = Row("2반", "이서연", None, cells={"1-(1)": Cell(2, "unconfirmed"),
                                            "1-(2)": Cell(2, "unconfirmed")},
                totals={"1": Cell(4, "unconfirmed"), "2": Cell(None, "ungraded")})  # fmt: skip
    path = build_scores(tmp_path / "s.xlsx", "t", COLS, [early, review, done])
    sheet = load_workbook(path)["점수"]
    rows = _table(sheet)
    assert rows[2][3:] == [None, None, 5, None], "검토 필요 칸과 총점은 비움"
    assert _fill(sheet, 3, 4) == _fill(sheet, 3, 7) == FILLS["review"]
    assert rows[3][3:] == [2, 2, None, None], "확인 전 채점은 값, 채점 전 문항은 비움"
    assert _fill(sheet, 4, 4) == FILLS["unconfirmed"]
    assert _fill(sheet, 4, 6) == FILLS["ungraded"]
    font = cast(Any, sheet.cell(row=1, column=1).font)
    assert font.name == "맑은 고딕"
    notes = _table(load_workbook(path)["안내"])
    assert ["1-(1)", 3] in notes and ["총점", 10] in notes, "배점은 안내 시트에"


def test_by_class_sheets(tmp_path: Path) -> None:
    rows = [_full("10반", "김"), _full("2반", "이"), _full(NO_CLASS, "박"), _full("2반", "김")]
    wb = load_workbook(build_scores(tmp_path / "s.xlsx", "t", COLS, rows, by_class=True))
    assert wb.sheetnames == ["2반", "10반", NO_CLASS, "안내"]
    assert [r[1] for r in _table(wb["2반"])[1:]] == ["김", "이"]


# ---------------------------------------------------------------- 시험 데이터에서


STEM = {"item_type": "essay", "stem": [{"type": "paragraph", "runs": [{"t": "text", "v": "문제"}]}]}


def _item(rid: str, part: str, points: float) -> dict[str, object]:
    return {"id": rid, "part": part, "criterion": "c", "evidence_hint": "h", "points": points}


def _store(tmp_path: Path) -> tuple[WorkspacePaths, ExamStore]:
    ws = create_workspace(tmp_path / "ws")
    exam = Exam.model_validate({
        "title": "10월 시험", "scope_id": "s",
        "questions": [
            {"number": "1", "points": 5, "item": STEM,
             "parts": [{"label": "(1)", "points": 3}, {"label": "(2)", "points": 2}]},
            {"number": "2", "points": 4, "item": STEM},
        ],
    })  # fmt: skip
    store = ExamStore.create(ws, exam)
    store.save_rubric(Rubric.model_validate({
        "question_number": "1", "version": 1, "status": "approved",
        "items": [_item("R1", "(1)", 3), _item("R2", "(2)", 2)],
    }))  # fmt: skip
    answer = [TranscriptLine(part="(1)", text="답")]
    for sid, klass, name in [
        ("s10", "10반", "이도윤"),
        ("s2", "2반", "김다원"),
        ("s1", "1반", "최"),
    ]:
        store.save_student(StudentRecord(student_id=sid, name=name, class_name=klass, source="t"))
        for q in ("1", "2"):
            t = Transcript(student_id=sid, question_number=q, source="ocr", lines=answer)
            store.save_transcript(t)
    _grade(store, "s2", "1", {"R1": 3, "R2": 1})
    _grade(store, "s2", "2", {}, total=4, max_points=4)
    _grade(store, "s10", "1", {"R1": 2, "R2": 2}, status="needs_review")
    return ws, store


def _grade(
    store: ExamStore,
    sid: str,
    q: str,
    awarded: dict[str, float],
    *,
    total: float | None = None,
    max_points: float = 5,
    status: str = "auto",
) -> None:
    store.save_grade(GradeResult.model_validate({
        "student_id": sid, "question_number": q, "rubric_version": 1, "comment": "",
        "judgements": [ItemJudgement.model_validate(
            {"rubric_item_id": k, "awarded": v, "reason": "r", "evidence_quote": "답",
             "confidence": 0.9}).model_dump() for k, v in awarded.items()],
        "total": sum(awarded.values()) if total is None else total, "max_points": max_points,
        "status": status, "item_points": {"R1": 3, "R2": 2},
    }))  # fmt: skip


def test_score_table_from_exam(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    columns, rows = score_table(store)
    assert [c.key for c in columns] == ["1-(1)", "1-(2)", "2"]
    by = {r.name: r for r in rows}
    assert by["김다원"].cells == {
        "1-(1)": Cell(3, "ok"),
        "1-(2)": Cell(1, "ok"),
        "2": Cell(4, "ok"),
    }
    assert by["김다원"].total() == Cell(8, "ok")
    assert by["이도윤"].cells["1-(1)"].state == "review" and by["이도윤"].total().value is None
    assert by["최"].total().state == "ungraded"


def test_scores_written_with_files_and_by_class_option(tmp_path: Path) -> None:
    ws, store = _store(tmp_path)
    res = write_files(ws, store, [("s2", "1")])
    path = score_file(ws, store)
    assert path in res.written and path.name == "(점수표)_10월_시험.xlsx"
    assert [r[1] for r in _table(load_workbook(path)["점수"])[1:]] == ["최", "김다원", "이도윤"]

    res = export_scores(ws, store, by_class=True)
    assert store.exam().scores_by_class and path in res.written
    assert load_workbook(path).sheetnames == ["1반", "2반", "10반", "안내"]

    wb = load_workbook(path)  # 강사가 엑셀에서 고쳐 저장 → 덮어쓰지 않고 알린다
    wb["2반"]["D2"] = 99
    wb.save(path)
    again = write_files(ws, store, [("s2", "1")])
    assert path in again.edited and any("점수표" in w for w in again.warnings)
    assert path in export_scores(ws, store, force=True).written
