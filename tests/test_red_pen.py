# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
"""채점 답안지 PDF (dawoni/export/red_pen.py): 표 찾기, 점수 자리, 이유 메모, PDF 만들기 — AI 없이.

답안지 그림은 실제 학원 답안지(`real_data/docs/m1j.pdf`, 2026-11 월례평가)의 표 자리를 본떠 그리고
0.4° 기울인다 (복합기 스캔처럼).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from PIL import Image, ImageDraw

from dawoni.export.red_pen import (
    Box,
    Scan,
    Slot,
    find_cover_cells,
    find_table,
    layout,
    short,
    slot_note,
    table_slots,
    write_marked,
)
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.models.grading import (
    Exam,
    ExamPart,
    ExamQuestion,
    GradeResult,
    ItemJudgement,
    Rubric,
    RubricItem,
    StudentRecord,
    Transcript,
    TranscriptLine,
)
from dawoni.models.scope import ScopeProfile
from dawoni.workspace import create_workspace

FIX = Path(__file__).parent / "fixtures" / "grading"
W, H = 2024, 2866
COLS = (139, 272, 1648, 1782, 1916)
# (y, 시작 x): 139 = 문항 경계, 272 = 소문항 경계, 369 = 소문항 안 (실험결과 1·2·3)
RULES = ((268, 139), (363, 139), (558, 272), (754, 139), (1069, 272), (1384, 139), (1735, 272),
         (2053, 369), (2371, 369), (2687, 139))  # fmt: skip


def _answer_sheet(path: Path) -> Path:
    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    for x in COLS:
        d.line([(x, 268), (x, 2687)], fill=60, width=3)
    d.line([(369, 1384), (369, 2687)], fill=60, width=3)
    for y, x0 in RULES:
        d.line([(x0, y), (COLS[-1], y)], fill=60, width=3)
    d.line([(400, 1472), (1648, 1472)], fill=60, width=3)  # 3-(1) 안의 ㉠㉡ 칸: 점수 칸까지 안 감
    d.text((420, 420), "W / sqrt 6", fill=0)  # 학생 글씨 자리
    im.rotate(0.4, fillcolor=255).convert("RGB").save(path)
    return path


def _cover(path: Path) -> Path:
    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    for x0, x1, y in ((205, 646, 731), (205, 646, 869), (760, 1250, 729), (760, 1250, 867)):
        d.line([(x0, y), (x1, y)], fill=60, width=3)  # 성명·반명 칸
    for y in (1049, 1132, 1215, 1297):  # 1번·2번·3번 점수 칸
        d.line([(1595, y), (1932, y)], fill=60, width=3)
    for x in (1595, 1715, 1932):
        d.line([(x, 1049), (x, 1297)], fill=60, width=3)
    im.rotate(-0.3, fillcolor=255).convert("RGB").save(path)
    return path


def _questions() -> list[ExamQuestion]:
    base = json.loads((FIX / "questions.json").read_text(encoding="utf-8"))[0]
    raw: list[dict[str, Any]] = [
        {**base, "number": "1", "points": 9,
         "parts": [{"label": "1)", "points": 4}, {"label": "2)", "points": 5}]},
        {**base, "number": "2", "points": 8, "parts": []},
        {**base, "number": "3", "points": 13,
         "parts": [{"label": "(1)", "points": 4}, {"label": "(2)", "points": 9}]},
    ]  # fmt: skip
    return [ExamQuestion.model_validate(q) for q in raw]


def test_table_slots_follow_question_and_part_rules(tmp_path: Path) -> None:
    """번호 칸에서 시작하는 줄 = 문항, 번호 칸 오른쪽 = 소문항. 2번(소문항 없음)은 A·B 두 칸을,
    3-(2)는 실험결과 세 칸을 한 덩어리로."""
    scan = Scan.open(_answer_sheet(tmp_path / "p.png"))
    assert abs(scan.angle - (-0.4)) <= 0.1 or abs(scan.angle - 0.4) <= 0.1
    table = find_table(scan)
    assert table is not None
    slots, used = table_slots(table, _questions())
    assert used == 3
    assert [(s.question, s.parts, len(s.cells)) for s in slots] == [
        ("1", ["1)"], 1), ("1", ["2)"], 1), ("2", [], 2), ("3", ["(1)"], 1), ("3", ["(2)"], 3),
    ]  # fmt: skip
    first = scan.back(slots[0].cells[0])
    assert 1770 < first.x0 < 1800 and 1900 < first.x1 < 1930, "맨 오른쪽 점수 칸"
    assert 350 < first.y0 < 380 and 545 < first.y1 < 575
    ans = scan.back(slots[0].answer)
    assert 255 < ans.x0 < 290 and 1630 < ans.x1 < 1665, "답안 칸: 번호 칸 오른쪽 ~ 배점 칸 왼쪽"


def test_parts_that_do_not_match_rows_become_one_slot(tmp_path: Path) -> None:
    scan = Scan.open(_answer_sheet(tmp_path / "p.png"))
    table = find_table(scan)
    assert table is not None
    qs = _questions()
    q1 = qs[0].model_copy(update={"parts": [*qs[0].parts[:1]], "points": 4})
    slots, _ = table_slots(table, [q1, *qs[1:]])
    assert (slots[0].question, slots[0].parts, len(slots[0].cells)) == ("1", [], 2)


def test_each_part_gets_its_own_score(tmp_path: Path) -> None:
    """소문항이 여럿인 문항도 소문항마다 점수 (2026-11-19 사용자 요청 — 실제 답안지에서 2번 A·B와
    3번이 문항 총점 하나로 나왔다). 3-(2)[실험결과 1]·[실험결과 2]·[실험결과 3]처럼 소문항이 표의
    소문항 칸보다 많으면 맨 앞 표시((2))로 묶고, 그 칸이 점수 칸까지 셋으로 나뉘었으면 하나씩.
    시험에 소문항이 없는 2번은 루브릭의 A·B로."""
    scan = Scan.open(_answer_sheet(tmp_path / "p.png"))
    table = find_table(scan)
    assert table is not None
    qs = _questions()
    labs = ["(1)", "(2)[실험결과 1]", "(2)[실험결과 2]", "(2)[실험결과 3]"]
    q3 = qs[2].model_copy(update={"parts": [ExamPart(label=x, points=3 if "실험" in x else 4)
                                            for x in labs]})  # fmt: skip
    slots, _ = table_slots(table, [*qs[:2], q3], {"2": ["A", "B"]})
    assert [(s.question, s.parts, len(s.cells)) for s in slots] == [
        ("1", ["1)"], 1), ("1", ["2)"], 1), ("2", ["A"], 1), ("2", ["B"], 1), ("3", ["(1)"], 1),
        ("3", ["(2)[실험결과 1]"], 1), ("3", ["(2)[실험결과 2]"], 1), ("3", ["(2)[실험결과 3]"], 1),
    ]  # fmt: skip
    # 3-(2) 칸이 셋으로 나뉘지 않았으면(실험결과가 넷) 그 소문항들의 합 한 자리
    four = [*labs, "(2)[실험결과 4]"]
    q3b = q3.model_copy(update={"parts": [ExamPart(label=x, points=2) for x in four]})
    slots, _ = table_slots(table, [*qs[:2], q3b])
    assert (slots[-1].parts, len(slots[-1].cells)) == (four[1:], 3)
    assert (slots[2].question, slots[2].parts) == ("2", []), "루브릭 표시가 없으면 문항 전체"

    r = _rubric("3", [("R1", 2, "(2)[실험결과 1]"), ("R2", 1, "(2)[실험결과 1]"),
                      ("R3", 3, "(2)[실험결과 2]")])  # fmt: skip
    g = _grade(
        "가", "3", [("R1", 2, 2, None), ("R2", 0, 1, "근거 없음"), ("R3", 1, 3, "척력 빠짐")]
    )
    one = Slot("3", ["(2)[실험결과 1]"], [Box(0, 0, 1, 1)], Box(0, 0, 1, 1))
    both = Slot("3", ["(2)[실험결과 1]", "(2)[실험결과 2]"], [Box(0, 0, 1, 1)], Box(0, 0, 1, 1))
    assert slot_note(one, g, r) == (2, 3, "2점 (−1: 근거 없음)")
    assert slot_note(both, g, r)[:2] == (3, 6)


def test_cover_score_cells(tmp_path: Path) -> None:
    scan = Scan.open(_cover(tmp_path / "c.png"))
    cells = find_cover_cells(scan, 3)
    assert cells is not None and len(cells) == 3
    assert all(1680 < c.x0 < 1740 and c.x1 > 1900 for c in cells), "표의 오른쪽(점수) 칸"
    assert [round(c.y0 / 10) * 10 for c in cells] == pytest.approx([1050, 1130, 1220], abs=20)
    assert find_cover_cells(scan, 4) is None, "문항 수와 칸 수가 다르면 찾지 못함"


def test_short_pen_note_keeps_math_whole() -> None:
    exact = r"정답은 \(T_a = \frac{5}{\sqrt{33}}W\)"
    assert short(exact) == exact
    long = "파란 빛이 더 많이 꺾여서 초점거리가 짧아진다는 결론을 명시하지 않았다. 또한 배율도 없다"
    got = short(long)
    assert got.endswith("…") and len(got) <= 30 and "." not in got
    assert short("같은 전하 사이 척력 필요 — 그림의 ++ 표시만 있음") == "같은 전하 사이 척력 필요"


def test_layout_draws_math_as_math() -> None:
    lay = layout(r"0점 (−4: 정답 \(\frac{W}{\sqrt{6}}\))", 10, 400, 2)
    runs = [r for line in lay.lines for _, r in line]
    assert any(r.math for r in runs) and len(lay.lines) == 1
    assert lay.h > 10 * 1.1, "분수는 글 한 줄보다 높다"
    narrow = layout("아주 긴 감점 이유 " * 20, 10, 120, 2)
    assert narrow.cut and len(narrow.lines) == 2


def _grade(student: str, q: str, items: list[tuple[str, float, float, str | None]]) -> GradeResult:
    js = [
        ItemJudgement(
            rubric_item_id=i,
            awarded=a,
            reason="r",
            confidence=0.9,
            deduction_reason=f"{i} 감점 이유가 길게 적힌 문장이다" if a < p else None,
            pen=pen,
        )
        for i, a, p, pen in items
    ]
    return GradeResult(
        student_id=student, question_number=q, rubric_version=1, judgements=js, comment="",
        total=sum(j.awarded for j in js), max_points=sum(p for _, _, p, _ in items),
        item_points={i: p for i, _, p, _ in items},
    )  # fmt: skip


def _rubric(q: str, items: list[tuple[str, float, str | None]]) -> Rubric:
    return Rubric.model_validate(
        {"question_number": q, "status": "approved",
         "items": [RubricItem(id=i, points=p, criterion="c", evidence_hint="e", part=part)
                   for i, p, part in items]}
    )  # fmt: skip


def test_slot_note_uses_pen_memo() -> None:
    scan_slots = [("1", "1)"), ("1", "2)")]
    r = _rubric("1", [("R1", 4, "1)"), ("R2", 5, "2)")])
    g = _grade("가", "1", [("R1", 4, 4, None), ("R2", 0, 5, r"정답 \(5W/\sqrt{33}\)")])
    notes = [
        slot_note(Slot(q, [p], [Box(0, 0, 1, 1)], Box(0, 0, 1, 1)), g, r) for q, p in scan_slots
    ]
    assert notes[0] == (4, 4, "정답 → 4점")
    assert notes[1] == (0, 5, r"0점 (−5: 정답 \(5W/\sqrt{33}\))")


def test_write_marked_pdf(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    ScopeStore(ws).save(
        ScopeProfile.model_validate(json.loads((FIX / "scope.json").read_text(encoding="utf-8")))
    )
    exam = Exam(title="월례", scope_id="scope-quadratic-minmax", questions=_questions())
    store = ExamStore.create(ws, exam)
    store.save_student(StudentRecord(student_id="가", name="가", class_name="2반", source="t"))
    scans = store.scan_dir("가")
    scans.mkdir(parents=True)
    _cover(scans / "page_01.png")
    _answer_sheet(scans / "page_02.png")
    store.save_transcript(Transcript(student_id="가", question_number="1",
                                     lines=[TranscriptLine(text="x")]))  # fmt: skip
    rubrics = {
        "1": _rubric("1", [("R1", 4, "1)"), ("R2", 5, "2)")]),
        "2": _rubric("2", [("R1", 4, None), ("R2", 4, None)]),
        "3": _rubric("3", [("R1", 4, "(1)"), ("R2", 9, "(2)")]),
    }
    grades = [
        _grade("가", "1", [("R1", 4, 4, None), ("R2", 0, 5, r"정답 \(\frac{5}{\sqrt{33}}W\)")]),
        _grade("가", "2", [("R1", 4, 4, None), ("R2", 2, 4, "배율 설명 없음")]),
        _grade("가", "3", [("R1", 4, 4, None), ("R2", 7, 9, None)]),
    ]
    for r in rubrics.values():
        store.save_rubric(r)
    for g in grades:
        store.save_grade(g)

    res = write_marked(store, tmp_path / "out")
    assert [p.name for p in res.written] == ["2반_채점답안지.pdf"] and res.placed == 5
    assert not [w for w in res.warnings if "찾지 못함" in w], res.warnings
    doc = pymupdf.open(res.written[0])
    assert doc.page_count == 2
    cover = str(doc[0].get_text()).replace("\xa0", " ")
    answers = str(doc[1].get_text()).replace("\xa0", " ")
    assert "21 / 30" in cover, "총점 (4 + 6 + 11)"
    assert cover.split("\n")[1:4] == ["4", "6", "11"], "표지 점수 칸에 문항 점수"
    assert "정답 → 4점" in answers and "배율 설명 없음" in answers
    assert "(−2: 3-(2)" not in answers and "R2 감점 이유" in answers, (
        "pen이 없으면 감점 이유를 줄여"
    )
