"""엑셀 점수표 (dawoni/export/xlsx.py, PLAN.md §9-2) — 2026-11-18 강사 성적표 모양: 전체 요약,
반마다 시트(소문항·문항 계·총점·석차·비고, 배점·평균·최고·최저), 채점 근거."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from dawoni.export.exam_files import export_scores, score_file, score_table
from dawoni.export.xlsx import (
    FILLS,
    NO_CLASS,
    SUM,
    TOTAL,
    Cell,
    Column,
    Reason,
    Row,
    build_scores,
)
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
from dawoni.students import Roster
from dawoni.workspace import create_workspace

COLS = [Column("1-(1)", 3, "1"), Column("1-(2)", 2, "1"), Column("2", 5, "2")]
HEAD = ["번호", "반", "이름", "1-(1)", "1-(2)", "1번 계", "2번", "총점", "석차", "비고"]


def _fill(sheet: Worksheet, row: int, column: int) -> str:
    fill = cast(Any, sheet.cell(row=row, column=column).fill)
    return str(fill.fgColor.rgb)[-6:]


def _ok(v: float) -> Cell:
    return Cell(v, "ok")


def _table(sheet: Worksheet) -> list[list[object]]:
    return [list(r) for r in sheet.iter_rows(values_only=True)]


def _students(sheet: Worksheet) -> list[list[object]]:
    """학생 줄 (머리·배점 줄 다음, 평균 줄 전)."""
    out: list[list[object]] = []
    for r in _table(sheet)[4:]:
        if r[2] == "평균" or r[0] is None:
            break
        out.append(r)
    return out


def _full(klass: str, name: str) -> Row:
    row = Row(klass, name, None)
    row.cells = {"1-(1)": _ok(3), "1-(2)": _ok(1), "2": _ok(4)}
    row.totals = {"1": _ok(4), "2": _ok(4)}
    return row


def test_sort_classes_naturally_then_names_ganada(tmp_path: Path) -> None:
    """한 시트(반 나누지 않음): 1반 < 2반 < 10반, TSS1 < TSS2, 반 없음은 맨 뒤 · 같은 반은
    가나다."""
    rows = [_full(k, n) for k, n in [
        ("10반", "김민준"), ("2반", "이서연"), ("TSS2", "김하늘"), ("2반", "김다원B"),
        (NO_CLASS, "가나다"), ("1반", "최유리"), ("2반", "박찬현"), ("TSS1", "한예성"),
        ("2반", "김다원A"),
    ]]  # fmt: skip
    path = build_scores(tmp_path / "s.xlsx", "10월 시험", COLS, rows, by_class=False)
    wb = load_workbook(path)
    assert wb.sheetnames == ["전체", "채점 근거"]
    got = [(r[1], r[2]) for r in _students(wb["전체"])]
    assert got == [
        ("1반", "최유리"), ("2반", "김다원A"), ("2반", "김다원B"), ("2반", "박찬현"),
        ("2반", "이서연"), ("10반", "김민준"), ("TSS1", "한예성"), ("TSS2", "김하늘"),
        (None, "가나다"),
    ]  # fmt: skip


def test_class_sheet_layout_with_formulas(tmp_path: Path) -> None:
    """번호·반·이름 | 소문항 | 문항 계 | 총점 | 석차 | 비고, 배점 줄, 평균·최고·최저·응시 —
    계·총점·석차는 수식이라 엑셀에서 점수를 고치면 다시 계산된다."""
    a = _full("2반", "김다원")
    a.notes = ["1-(2) 판독 불확실(4√33) — 감점과 관계있으면 원본 확인"]
    b = _full("2반", "박찬현")
    absent = Row("2반", "이서연", None, absent=True)
    wb = load_workbook(build_scores(tmp_path / "s.xlsx", "11월 월례", COLS, [b, absent, a]))
    sheet = wb["2반"]
    rows = _table(sheet)
    assert rows[0][0] == "11월 월례 성적 — 2반"
    assert rows[2] == HEAD
    assert rows[3][2:8] == ["배점", 3, 2, 5, 5, 10]
    assert rows[4] == [1, "2반", "김다원", 3, 1, "=SUM(D5:E5)", 4, "=F5+G5",
                       "=RANK(H5,$H$5:$H$7)", a.notes[0]]  # fmt: skip
    assert rows[5][2:8] == ["박찬현", 3, 1, "=SUM(D6:E6)", 4, "=F6+G6"]
    assert rows[6][2:] == ["이서연", None, None, None, None, None, None, "미응시 (답안지 없음)"]
    assert rows[7][2:4] == ["평균", '=IF(COUNT(D5:D7)=0,"",AVERAGE(D5:D7))']
    assert rows[8][2] == "최고" and rows[9][2] == "최저"
    assert rows[10][2:5] == ["응시", "=COUNT(H5:H7)", "명"]
    assert _fill(sheet, 5, 6) == SUM and _fill(sheet, 5, 8) == TOTAL
    assert sheet.freeze_panes == "D5"
    font = cast(Any, sheet.cell(row=5, column=3).font)
    assert font.name == "맑은 고딕" and font.b, "이름은 굵게"


def test_ungraded_cells_are_blank_and_review_cells_colored(tmp_path: Path) -> None:
    """미채점은 비우고(계·총점도), 검토 필요는 AI 점수를 넣어 노랑 (2026-11-18 사용자 결정)."""
    review = Row("2반", "박찬현", None, cells={"1-(1)": Cell(1, "review"),
                                             "1-(2)": Cell(2, "review"), "2": _ok(5)},
                 totals={"1": Cell(3, "review"), "2": _ok(5)})  # fmt: skip
    early = Row("2반", "이서연", None, cells={"1-(1)": Cell(2, "unconfirmed"),
                                            "1-(2)": Cell(2, "unconfirmed")},
                totals={"1": Cell(4, "unconfirmed"), "2": Cell(None, "ungraded")})  # fmt: skip
    path = build_scores(tmp_path / "s.xlsx", "t", COLS, [early, review])
    sheet = load_workbook(path)["2반"]
    got = _students(sheet)
    assert got[0][3:9] == [1, 2, "=SUM(D5:E5)", 5, "=F5+G5", "=RANK(H5,$H$5:$H$6)"]
    assert _fill(sheet, 5, 4) == FILLS["review"]
    assert got[1][3:9] == [2, 2, "=SUM(D6:E6)", None, None, None], "채점 전 문항이 있으면 총점 비움"
    assert _fill(sheet, 6, 4) == FILLS["unconfirmed"] and _fill(sheet, 6, 7) == FILLS["ungraded"]


def test_summary_and_reasons_sheets(tmp_path: Path) -> None:
    rows = [_full("10반", "김"), _full("2반", "이"), _full(NO_CLASS, "박"), _full("2반", "김")]
    rows[0].reasons = [Reason("1-(2)", 1, 2, "단위 없음")]
    wb = load_workbook(build_scores(tmp_path / "s.xlsx", "t", COLS, rows))
    assert wb.sheetnames == ["전체 요약", "2반", "10반", NO_CLASS, "채점 근거"]
    assert [r[2] for r in _students(wb["2반"])] == ["김", "이"]
    summary = _table(wb["전체 요약"])
    assert summary[0][0] == "t 반별 요약"
    assert summary[2] == [
        "반",
        "응시",
        "1번 평균(5)",
        "2번 평균(5)",
        "총점 평균(10)",
        "최고",
        "최저",
    ]
    assert summary[3][:3] == ["2반", "=COUNT('2반'!H5:H6)",
                              "=IF(COUNT('2반'!F5:F6)=0,\"\",AVERAGE('2반'!F5:F6))"]  # fmt: skip
    assert summary[3][3] == "=IF(COUNT('2반'!G5:G6)=0,\"\",AVERAGE('2반'!G5:G6))", "한 칸 문항"
    assert summary[6][0] == "전체" and summary[6][1] == (
        "=COUNT('2반'!H5:H6,'10반'!H5:H5,'반없음'!H5:H5)"
    )
    reasons = _table(wb["채점 근거"])
    assert reasons[0] == ["반", "이름", "문항", "득점", "배점", "감점 사유 / 메모"]
    assert reasons[1] == ["10반", "김", "1-(2)", 1, 2, "단위 없음"]


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
    ws, store = _store(tmp_path)
    Roster(ws).add("박서준", "2반")  # 명부에 있는데 답안지가 없는 학생 → 미응시
    Roster(ws).add("정하늘", "5반")  # 이 시험에 없는 반은 넣지 않는다
    g = store.grade("s10", "1")
    assert g is not None
    store.save_grade(g.model_copy(update={"flags": ["확신도 낮음: R1"]}))
    store.save_transcript(Transcript(student_id="s10", question_number="1", source="ocr",
                                     lines=[TranscriptLine(part="(1)", text="답 3",
                                                           uncertain=["3"])]))  # fmt: skip
    columns, rows = score_table(store)
    assert [(c.key, c.question) for c in columns] == [("1-(1)", "1"), ("1-(2)", "1"), ("2", "2")]
    by = {r.name: r for r in rows}
    assert by["김다원"].cells == {
        "1-(1)": Cell(3, "ok"),
        "1-(2)": Cell(1, "ok"),
        "2": Cell(4, "ok"),
    }
    assert by["김다원"].total() == Cell(8, "ok")
    assert by["김다원"].reasons == [Reason("1-(2)", 1, 2, "r")]
    assert by["이도윤"].cells["1-(1)"].state == "review"
    assert by["이도윤"].cells["1-(1)"].value == 2, "검토 필요도 AI 점수"
    assert by["이도윤"].notes == [
        "1-(1) 판독 불확실(3) — 감점과 관계있으면 원본 확인",
        "1번 AI 확신 낮음 (1-(1))",
    ], "항목 ID(R1)는 소문항으로"
    assert by["최"].total().state == "ungraded"
    assert by["박서준"].absent and by["박서준"].klass == "2반" and "정하늘" not in by


def test_scores_written_and_one_sheet_option(tmp_path: Path) -> None:
    ws, store = _store(tmp_path)
    res = export_scores(ws, store)
    path = score_file(ws, store)
    assert path in res.written and path.name == "(점수표)_10월_시험.xlsx"
    assert store.exam().sheets_per_class, "기본은 반마다 시트 (2026-11-18)"
    assert load_workbook(path).sheetnames == ["전체 요약", "1반", "2반", "10반", "채점 근거"]

    res = export_scores(ws, store, by_class=False)
    assert not store.exam().sheets_per_class and path in res.written
    wb = load_workbook(path)
    assert wb.sheetnames == ["전체", "채점 근거"]
    assert [r[2] for r in _students(wb["전체"])] == ["최", "김다원", "이도윤"]

    wb["전체"]["D5"] = 99  # 강사가 엑셀에서 고쳐 저장 → 덮어쓰지 않고 알린다
    wb.save(path)
    again = export_scores(ws, store)
    assert path in again.edited and any("점수표" in w for w in again.warnings)
    assert path in export_scores(ws, store, force=True).written
