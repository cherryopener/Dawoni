"""학생 명부 (PLAN.md §7) — 작업공간 `students/`, 학생 1명 = 파일 1개."""

from dawoni.students.roster import (
    CoverMatch,
    ImportResult,
    Roster,
    RosterStudent,
    natural_key,
    read_table,
    same_class,
    same_name,
)

__all__ = [
    "CoverMatch",
    "ImportResult",
    "Roster",
    "RosterStudent",
    "natural_key",
    "read_table",
    "same_class",
    "same_name",
]
