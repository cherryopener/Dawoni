"""채점 HWPX (dawoai/export) — 한글 없이: 스키마 검사, 다시 읽기, 수식 바꾸기."""

from __future__ import annotations

from pathlib import Path

import pytest
from hwpx.document import HwpxDocument

from dawoai.export.hwpx import (
    PartGrade,
    Sheet,
    build_bundle,
    build_student,
    canon,
    count_heads,
    expected_text,
    from_eqedit,
    prose_pieces,
    read_back,
    sheet_lines,
    to_eqedit,
)
from dawoai.models.grading import TranscriptLine

LINES = sheet_lines(
    "2",
    [
        TranscriptLine(
            part="(1)①", text=r"전류를 \(I_{\text{㉠}}\)라 하면 \(V=I_{\text{㉠}} R\)이야"
        ),
        TranscriptLine(part="(1)①", text="전류의 비는 \\(2:1\\)이다", uncertain=["비는"]),
        TranscriptLine(part="(1)②", text="저항이 적은 ㉠이 [판독불가] 밝아"),
        TranscriptLine(part="(1)②", text="P = VI", struck=True),
        TranscriptLine(part="(2)", math=r"\frac{3R}{3+R} : 6 \quad R=6\Omega \uparrow"),
        TranscriptLine(part=None, text="(여백 낙서)"),
    ],
)


def _sheet(head: str = "10월 시험 · TSS1 · 김다원 · 구룡중") -> Sheet:
    return Sheet(
        head=head,
        lines=LINES,
        grades={
            "2-(1)①": PartGrade(2, 2, good=[("전류의 비는", "직렬에서 전류가 같음")]),
            "2-(1)②": PartGrade(0, 2, bad=[("저항이 적은", "전구 밝기는 전력으로 비교")]),
        },
        comment="전력으로 밝기를 비교해 보자.",
        notes=["판독문 강사 확인 전 채점"],
    )


@pytest.mark.parametrize(
    "latex",
    [r"I_{\text{㉠}}", r"a \uparrow b \downarrow", r"\frac{3R}{3+R} : 6 \quad R=6\Omega", "2:1",
     # M3 실측(학생 24명): overline이 over + line으로 갈라지고, \Leftrightarrow를 변환기가 몰랐다
     r"\overline{op_1}=\overline{op_2}", r"(\Leftrightarrow 2\sin\alpha=\sin3\alpha)",
     # 식 안의 한글은 \text{}에 싸여, \ne는 \neq로 돌아온다 — 같은 식으로 본다
     r"i_ㄱ \ne i_{앞}", r"P_{병렬} > P_{직렬}", "「\\frac{1}{\\infty}」"],
)  # fmt: skip
def test_equation_round_trip(latex: str) -> None:
    """변환기가 받지 않는 ㉠·화살표·\\quad도 한글 수식이 되고, 다시 읽으면 같은 식이다."""
    assert canon(f"\\({from_eqedit(to_eqedit(latex))}\\)") == canon(f"\\({latex}\\)")


def test_student_file_reads_back(tmp_path: Path) -> None:
    path = build_student(tmp_path / "TSS1_김다원_2.hwpx", _sheet())
    assert not HwpxDocument.open(path).validate().issues
    got = read_back(path)
    assert got.head == "10월 시험 · TSS1 · 김다원 · 구룡중"
    want = expected_text(LINES)
    assert list(got.parts) == list(want) == ["2-(1)①", "2-(1)②", "2-(2)", "2"]
    for part, lines in want.items():
        assert [canon(x) for x in got.parts[part]] == [canon(x) for x in lines], part
    assert not any("[식:" in x for v in got.parts.values() for x in v), "모든 식이 한글 수식으로"
    breaks = [p for p in HwpxDocument.open(path).paragraphs if p.element.get("pageBreak") == "1"]
    assert len(breaks) == 1, "채점은 새 쪽에서"


def test_bundle_keeps_waiting_students(tmp_path: Path) -> None:
    """입력을 기다리는 학생은 안내 쪽으로 넣어 묶음을 먼저 만든다."""
    waiting = Sheet(head="10월 시험 · TSS1 · 박찬현", lines=[], waiting="입력 대기: [판독불가] 1곳")
    path = build_bundle(tmp_path / "(반별)_TSS1_2.hwpx", [_sheet(), waiting])
    assert not HwpxDocument.open(path).validate().issues
    assert count_heads(path) == 2
    assert "입력 대기" in HwpxDocument.open(path).text.plain()


@pytest.mark.parametrize(
    ("text", "want"),
    [
        # 실측 채점 이유: 식이 `$` 없이 글에 섞여 온다 (contract 6번)
        (r"정답은 2\Omega이나 4\Omega으로 작성함",
         [("정답은 ", False), (r"2\Omega", True), ("이나 ", False), (r"4\Omega", True),
          ("으로 작성함", False)]),
        (r"공식으로 T_1 = 2\sqrt{\frac{r}{g}}를 구함.",
         [("공식으로 ", False), (r"T_1 = 2\sqrt{\frac{r}{g}}", True), ("를 구함.", False)]),
        (r"각도(\theta)가 같다", [("각도(", False), (r"\theta", True), (")가 같다", False)]),
        (r"전력 \(P=VI\)와 $V^2/R$", [("전력 ", False), ("P=VI", True), ("와 ", False),
                                     ("V^2/R", True)]),
        ("식 없이 (1)과 1/2, V=IR", [("식 없이 (1)과 1/2, V=IR", False)]),
    ],
)  # fmt: skip
def test_prose_pieces(text: str, want: list[tuple[str, bool]]) -> None:
    assert prose_pieces(text) == want


def test_reasons_are_colored_text_with_equations_not_memos(tmp_path: Path) -> None:
    """○/✕ 이유는 파란·빨간 글씨로만 (한글 메모는 인쇄되지 않고 ✕ 줄과 겹친다), 이유 속 식은
    같은 색 한글 수식."""
    sheet = _sheet()
    sheet.grades = {
        "2-(1)①": PartGrade(2, 2, good=[("전류의 비는", r"비 2:1과 V=I_{\text{㉠}} R을 씀")]),
        "2-(1)②": PartGrade(0, 2, bad=[("저항이 적은", r"밝기는 P=I^2R로 비교해야 함")]),
    }
    path = build_student(tmp_path / "x.hwpx", sheet)
    doc = HwpxDocument.open(path)
    assert not doc.validate().issues
    assert not doc.notes.memos, "메모 없음"
    import zipfile

    xml = "".join(
        zipfile.ZipFile(path).read(n).decode("utf-8")
        for n in zipfile.ZipFile(path).namelist()
        if n.startswith("Contents/section")
    )
    assert "I^2R" not in xml.replace(" ", ""), "이유 속 식이 글자로 남지 않음"
    assert 'textColor="#C00000"' in xml and 'textColor="#1F4E9E"' in xml, "식도 이유와 같은 색"
    assert "[식:" not in xml
    assert read_back(path).parts.keys() == expected_text(LINES).keys(), "판독 다시 읽기는 그대로"


def test_equation_left_as_text_reads_back_as_equation(tmp_path: Path) -> None:
    """한글 수식으로 못 바꾼 식은 `[식: …]` 글자로 넣는다 — 다시 읽으면 식으로 되돌려, 강사가 고치지
    않았는데 고친 줄로 보이지 않게 한다."""
    lines = sheet_lines("1", [TranscriptLine(part="(1)", text=r"따라서 \(L \over v\)이다")])
    path = build_student(tmp_path / "x.hwpx", Sheet(head="t · 2반 · 김다원", lines=lines))
    assert "[식:" in "".join(p.text or "" for p in HwpxDocument.open(path).paragraphs)
    got = read_back(path).parts["1-(1)"]
    assert [canon(x) for x in got] == [canon(r"따라서 \(L \over v\)이다")]
