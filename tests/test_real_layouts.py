"""실제 자료 회귀 테스트 (AI 없음, 레이아웃만).

`real_data/`(git 제외 — 저작권·학생 자료)에 파일이 있을 때만 돈다. 실측으로 확인한 결과를
기대값으로 고정해, 알고리즘을 고칠 때 다른 강사 자료가 깨지는 것을 바로 잡는다.
새 자료를 실측하면 여기에 더한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dawoni.ingest.layout import analyze, find_key_pages

REAL = Path(__file__).resolve().parent.parent / "real_data"
KIM = REAL / "2027 중등 영재 물리학 1(역학)_김승규.pdf"
CHO_BASIC = REAL / "2028학년도 영재학교 대비 기초물리학(교사용)_조태환.pdf"
EXAM = REAL / "test.pdf"


def need(path: Path) -> Path:
    if not path.is_file():
        pytest.skip(f"실제 자료 없음: {path.name}")
    return path


def labels(pdf: Path, lo: int, hi: int, total: int) -> list[str]:
    lay = analyze(pdf, exclude=[p for p in range(1, total + 1) if not lo <= p <= hi])
    return [r.label for r in lay.regions]


def test_real_exam_three_questions() -> None:
    pdf = need(EXAM)
    k = find_key_pages(pdf)
    assert k == [6]
    assert [
        (r.label, sorted({b[0] for b in r.boxes})) for r in analyze(pdf, exclude=k).regions
    ] == [("1", [3]), ("2", [4]), ("3", [5])]


def test_real_exam_list_inside_question() -> None:
    """공통형3회: 3번 안의 [실험 단서] 1.~4.가 문항이 아님. 배점은 조각난 "총 점 [ 8 ]"에서."""
    pdf = need(REAL / "공통형3회.pdf")
    k = find_key_pages(pdf)
    assert k == [6, 7]
    assert [(r.label, r.pages, r.points) for r in analyze(pdf, exclude=k).regions] == [
        ("1", [3], 8),
        ("2", [4], 9),
        ("3", [5], 8),
    ]


@pytest.mark.parametrize(
    ("lo", "hi", "first", "last"),
    [(4, 20, 1, 30), (23, 34, 31, 48), (38, 70, 49, 108), (72, 85, 109, 131),
     (88, 125, 132, 197), (127, 140, 198, 223), (143, 157, 224, 249)],
)  # fmt: skip
def test_kim_chapters_continuous_numbering(lo: int, hi: int, first: int, last: int) -> None:
    """김승규 역학: 번호가 책 전체에서 1~249로 이어짐, 2단, 짝수 쪽 머리말 "7. 원운동"."""
    got = labels(need(KIM), lo, hi, 192)
    assert got == [str(n) for n in range(first, last + 1)]


@pytest.mark.parametrize(
    ("lo", "hi", "count"),
    [(4, 7, 7), (7, 9, 10), (11, 15, 9), (15, 17, 10)],
)
def test_cho_basic_problem_sets(lo: int, hi: int, count: int) -> None:
    """조태환 기초: 강마다 '기본 문제'와 '학습 과제'가 각각 1번부터, 한 쪽 안에서 묶음이 바뀜,
    쪽 위쪽 색 띠와 같은 자리의 번호 줄."""
    assert labels(need(CHO_BASIC), lo, hi, 105) == [str(n) for n in range(1, count + 1)]


WAVE = REAL / "2026 영재대비 파동(조태환).pdf"
SUNEUNG1 = REAL / "2011학년도 수능 물리1.pdf"
SUNEUNG2 = REAL / "2011학년도 수능 물리2.pdf"


@pytest.mark.parametrize("pdf", [SUNEUNG1, SUNEUNG2])
def test_suneung_twenty_questions(pdf: Path) -> None:
    lay = analyze(need(pdf))
    assert [r.label for r in lay.regions] == [str(n) for n in range(1, 21)]


def test_wave_short_left_column() -> None:
    """파동: 왼쪽 단이 짧은 쪽 — 단 경계를 빈 띠 가운데로 (12번이 들여쓴 줄로 빠지던 문제)."""
    assert labels(need(WAVE), 58, 75, 96) == [str(n) for n in range(1, 34)]


def test_cho_basic_doubled_number_is_reported() -> None:
    """회로 이론 기본 문제: 교재에 10번이 두 번 인쇄돼 11번이 없다 → 7번은 찾고(2단 경계), 11만 빠짐."""
    got = labels(need(CHO_BASIC), 50, 60, 105)
    assert got == [str(n) for n in range(1, 24) if n != 11]
