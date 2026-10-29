"""실제 자료 회귀 테스트 (AI 없음, 레이아웃·챕터 제안만).

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
    assert [(r.label, sorted({b[0] for b in r.boxes})) for r in analyze(pdf, exclude=k).regions] == [
        ("1", [3]), ("2", [4]), ("3", [5])
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
