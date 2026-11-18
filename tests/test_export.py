"""채점 검토 화면·점수표의 소문항별 채점 (dawoni/export/sheet.py)과 근거 인용 자리 (quotes.py).

2026-11-18 채점 HWPX를 없애며 HWPX 만들기·다시 읽기 시험은 뺐다.
"""

from __future__ import annotations

from dawoni.export.quotes import Spots, locate, segments
from dawoni.export.sheet import PartGrade, grade_spots, part_grades, sheet_lines
from dawoni.models.grading import GradeResult, ItemJudgement, Rubric, RubricItem, TranscriptLine

# ---------------------------------------------------------------- 근거 인용 → 강조 자리 (M6)

REAL = sheet_lines(
    "1",
    [  # M5 실측 판독문 꼴: 글 사이 식, 식만 있는 줄, 같은 짧은 식이 두 소문항에
        TranscriptLine(part="(1)", text=r"중력가속도 \(g\), \(Ap\) 방향으로의 가속도 성분은"),
        TranscriptLine(part="(1)", text=r"\(g\cos\alpha\) (단, \(\alpha = \angle OAP\))"),
        TranscriptLine(part="(1)", math=r"\theta = \alpha"),
        TranscriptLine(part="(2)", text="각도는 같다", math=r"\theta = \alpha"),
        TranscriptLine(part="(2)", text="지운 줄", struck=True),
    ],
)


def _marked_text(spots: Spots, li: int, si: int) -> str:
    seg = segments(REAL[li])[si][0]
    return "".join(c for i, c in enumerate(seg) if i in spots.lines[li].chars[si])


def test_quote_across_lines_with_part_label_and_math() -> None:
    """채점 인용은 `(1) `을 붙이고, 여러 줄을 띄어 잇고, 식을 `\\( \\)` 없이 쓰기도 한다 (실측)."""
    quote = r"(1) 중력가속도 \(g\), \(Ap\) 방향으로의 가속도 성분은 g\cos\alpha (단,"
    spots = locate(REAL, [(quote, "good", "1-(1)")])
    assert spots.found == 1 and not spots.missed
    assert _marked_text(spots, 0, 0).replace(" ", "") == "중력가속도"
    assert spots.lines[0].maths == {1: {"good"}, 3: {"good"}}
    assert spots.lines[1].maths[1] == {"good"}, "식만 인용해도 그 식에 색"
    assert _marked_text(spots, 1, 2).replace(" ", "") == "(단,"
    assert 3 not in spots.lines[1].maths, "인용이 끝난 뒤 식은 칠하지 않음"


def test_repeated_quote_prefers_its_own_part() -> None:
    spots = locate(REAL, [(r"\theta = \alpha", "bad", "1-(2)")])
    assert list(spots.lines) == [3]
    first = locate(REAL, [(r"\theta = \alpha", "bad", None)])
    assert list(first.lines) == [2], "소문항을 모르면 처음 나온 곳"


def test_ellipsis_chunks_and_struck_lines() -> None:
    spots = locate(REAL, [("중력가속도 … 각도는 같다 … 없는 글", "good", None),
                          ("지운 줄", "bad", "1-(2)")])  # fmt: skip
    assert spots.found == 3 and spots.missed == ["없는 글"]
    assert 4 not in spots.lines, "지운 줄은 찾아도 칠하지 않음"


def test_bare_question_number_is_not_stripped_from_quotes() -> None:
    """소문항 없는 문항의 줄은 `part`가 문항 번호(`3`)뿐 — 인용에서 숫자를 지우면 안 된다."""
    lines = sheet_lines("3", [TranscriptLine(part=None, text="답은 3배이다")])
    assert locate(lines, [("3배", "good", "3")]).found == 1


def _grade(teacher_parts: dict[str, float] | None = None) -> tuple[GradeResult, Rubric]:
    rubric = Rubric.model_validate(
        {
            "question_number": "1",
            "version": 1,
            "items": [
                RubricItem(id="R1", points=2, criterion="가속도", evidence_hint="g", part="(1)"),
                RubricItem(id="R2", points=3, criterion="각도", evidence_hint="θ", part="(2)"),
            ],
        }
    )
    g = GradeResult(
        student_id="s1",
        question_number="1",
        rubric_version=1,
        judgements=[
            ItemJudgement(rubric_item_id="R1", awarded=2, evidence_quote="중력가속도",
                          reason="좋음", confidence=0.9),
            ItemJudgement(rubric_item_id="R2", awarded=0, evidence_quote=r"\theta = \alpha",
                          reason="근거 없음", deduction_reason="각도가 같은 이유가 없음",
                          confidence=0.9),
        ],
        comment="",
        total=2,
        max_points=5,
        item_points={"R1": 2, "R2": 3},
        teacher_part_scores=teacher_parts or {},
    )  # fmt: skip
    return g, rubric


def test_part_grades_and_spots_for_the_review_screen() -> None:
    """채점 항목 → 소문항별 점수·이유, 근거 인용은 판독문의 그 소문항 줄에 (검토 화면·점수표)."""
    g, rubric = _grade()
    grades = part_grades("1", g, rubric)
    assert grades["1-(1)"] == PartGrade(2, 2, good=[("중력가속도", "좋음")])
    assert grades["1-(2)"].awarded == 0 and grades["1-(2)"].points == 3
    assert grades["1-(2)"].bad == [(r"\theta = \alpha", "각도가 같은 이유가 없음")]
    spots = grade_spots(REAL, grades)
    assert sorted(spots.lines) == [0, 3], "맞은 곳은 (1) 첫 줄, 틀린 곳은 (2)의 식"


def test_teacher_part_score_overrides_ai_sum() -> None:
    g, rubric = _grade({"(2)": 1})
    assert part_grades("1", g, rubric)["1-(2)"].awarded == 1
