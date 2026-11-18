"""학생 한 명 × 문항 하나의 채점을 소문항별로 (채점 검토 화면·엑셀 점수표) — 코드만, 토큰 0.

2026-11-18까지는 채점 HWPX를 쓰는 `export/hwpx.py`에 있었다. 앱이 HWPX를 쓰지 않게 되어 화면과
점수표가 함께 쓰는 것만 남겼다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dawoni.export.quotes import Quote, Spots, locate
from dawoni.models.grading import GradeResult, Rubric, TranscriptLine


@dataclass
class PartGrade:
    awarded: float
    points: float
    good: list[tuple[str, str]] = field(default_factory=lambda: list[tuple[str, str]]())
    """(판독문 인용, 맞은 이유) — 인용은 채점이 쓴 그대로 (`…`로 이은 조각, `quotes.locate`)."""
    bad: list[tuple[str, str]] = field(default_factory=lambda: list[tuple[str, str]]())
    """(판독문 인용, 감점 이유). 일부만 받은 항목은 인용 없이 이유만 (근거는 맞은 곳으로 칠함)."""


def sheet_lines(question: str, lines: list[TranscriptLine]) -> list[TranscriptLine]:
    """판독문의 소문항 표시 `(1)`에 문항 번호를 붙인다 → `1-(1)` (소문항이 없으면 `1`)."""
    return [
        x.model_copy(update={"part": f"{question}-{x.part}" if x.part else question}) for x in lines
    ]


def part_grades(question: str, g: GradeResult, rubric: Rubric) -> dict[str, PartGrade]:
    """채점 결과(채점 항목별) → 소문항별 점수·근거. 강사가 고친 점수가 있으면 그것을 쓴다."""
    part_of = {i.id: i.part for i in rubric.items}
    out: dict[str, PartGrade] = {}
    for j in g.judgements:
        part = part_of.get(j.rubric_item_id)
        pg = out.setdefault(f"{question}-{part}" if part else question, PartGrade(0, 0))
        pg.awarded += j.awarded
        pg.points += g.item_points.get(j.rubric_item_id, 0)
        quote = j.evidence_quote or ""
        if j.awarded > 0:
            pg.good.append((quote, j.reason))
        if j.deduction_reason:  # 일부만 받은 항목은 근거를 맞은 곳으로 칠하고 이유만 단다
            pg.bad.append((quote if j.awarded == 0 else "", j.deduction_reason))
    for part, score in g.teacher_part_scores.items():
        key = f"{question}-{part}" if part else question
        if key in out:
            out[key].awarded = score
    return out


def grade_spots(lines: list[TranscriptLine], grades: dict[str, PartGrade]) -> Spots:
    """채점 근거 인용들이 판독문(`sheet_lines`로 소문항 표시를 붙인 줄)의 어디인가."""
    quotes: list[Quote] = []
    for part, g in grades.items():
        good: list[Quote] = [(q, "good", part) for q, _ in g.good if q]
        bad: list[Quote] = [(q, "bad", part) for q, _ in g.bad if q]
        quotes += good + bad
    return locate(lines, quotes)
