"""엑셀 점수표 (PLAN.md §9-2) — 코드만, 토큰 0.

시험 1개 = 파일 1개 `(점수표)_<시험>.xlsx`(시험 폴더의 반별 묶음 옆, 이름순 맨 위). 열은
`반 | 이름 | 학교 | 1-(1) | 1-(2) | 2-(1)① | … | 총점` — 전화번호 열은 없다 (§15-5). 한 시트에 모두
담고, 시험에서 `scores_by_class`를 켜면 반마다 시트를 하나씩 둔다. 마지막 시트 `안내`에 색의 뜻과
열마다 배점을 적는다 (머리 줄 아래에 배점 줄을 두면 엑셀 정렬·필터에 섞인다).

- 정렬: 반은 자연 정렬(1반 < 2반 < 10반, TSS1 < TSS2, 반이 없으면 맨 뒤), 같은 반은 이름 가나다순 —
  한글 음절 코드 순서가 가나다순과 같다 (김 < 이, 김다원A < 김다원B).
- 소문항 점수는 채점 HWPX와 같은 값(`hwpx.part_grades` — 강사가 소문항으로 고친 점수 포함).
- 미채점(판독 전·입력 대기·채점 전) 칸과 검토 필요 칸은 **비우고** 색으로 표시한다. 판독문을 강사가
  확인하기 전에 채점한 칸은 값을 넣고 옅게 칠한다 (확정하면 다시 채점될 수 있음).
- 총점은 수식이 아니라 값(앱이 채점에 저장한 문항 총점의 합). 한 문항이라도 비면 총점도 비운다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from dawoai.students import natural_key

FONT = "맑은 고딕"
"""출력물 글꼴 (PLAN.md §11 — 화면과 따로, Windows 기본 탑재)."""
NO_CLASS = "반없음"

State = Literal["ok", "unconfirmed", "review", "ungraded"]
FILLS: dict[State, str | None] = {
    "ok": None,
    "unconfirmed": "DDEBF7",  # 옅은 파랑: 판독문 확인 전 채점 (값 있음)
    "review": "FFEB9C",  # 노랑: 검토 필요 (비움)
    "ungraded": "D9D9D9",  # 회색: 미채점 (비움)
}
MEANING: dict[State, str] = {
    "unconfirmed": "판독문을 강사가 확인하기 전에 채점 — 확인 완료 때 고친 줄이 있으면 다시 채점",
    "review": "검토 필요 — AI 채점이 엇갈리거나 확신이 낮음. 강사가 확정하면 점수가 들어감",
    "ungraded": "미채점 — 답안 없음·입력 대기·채점 전",
}
_WORST: list[State] = ["ungraded", "review", "unconfirmed", "ok"]
_BAD_SHEET = re.compile(r"[\[\]:*?/\\]")


@dataclass
class Cell:
    value: float | None
    state: State


@dataclass
class Row:
    klass: str
    """반 (없으면 `NO_CLASS`)."""
    name: str
    school: str | None
    cells: dict[str, Cell] = field(default_factory=lambda: dict[str, Cell]())
    """열 이름(`1-(1)`, 소문항이 없으면 `1`) → 칸."""
    totals: dict[str, Cell] = field(default_factory=lambda: dict[str, Cell]())
    """문항 번호 → 그 문항 총점 (총점 열을 셈)."""

    def total(self) -> Cell:
        if not self.totals:
            return Cell(None, "ungraded")
        seen = {c.state for c in self.totals.values()}
        state: State = next(s for s in _WORST if s in seen)
        if state == "ungraded" or state == "review":
            return Cell(None, state)
        return Cell(sum(c.value or 0 for c in self.totals.values()), state)


@dataclass
class Column:
    key: str
    points: float | None


def row_key(row: Row) -> tuple[bool, tuple[tuple[int, int | str], ...], str]:
    """반 자연 정렬(반 없음은 맨 뒤) → 이름 가나다순 (한글 음절 코드 순서 = 가나다순)."""
    return (row.klass == NO_CLASS, natural_key(row.klass), row.name)


def _number(v: float) -> int | float:
    return int(v) if float(v).is_integer() else round(v, 2)


def _sheet_title(s: str, taken: set[str]) -> str:
    base = _BAD_SHEET.sub("_", s)[:31] or "시트"
    title, n = base, 2
    while title in taken:
        title = f"{base[:28]}~{n}"
        n += 1
    taken.add(title)
    return title


def _font(bold: bool = False) -> Font:
    return Font(name=FONT, bold=bold)


_THIN = Side(style="thin", color="BFBFBF")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _write_table(sheet: Worksheet, columns: list[Column], rows: list[Row]) -> None:
    head = ["반", "이름", "학교", *[c.key for c in columns], "총점"]
    for ci, h in enumerate(head, start=1):
        cell = sheet.cell(row=1, column=ci, value=h)
        cell.font = _font(bold=True)
        cell.fill = PatternFill("solid", fgColor="F2F2F2")
        cell.alignment = Alignment(horizontal="center")
        cell.border = _BORDER
    for ri, row in enumerate(sorted(rows, key=row_key), start=2):
        info = ["" if row.klass == NO_CLASS else row.klass, row.name, row.school or ""]
        for ci, v in enumerate(info, start=1):
            cell = sheet.cell(row=ri, column=ci, value=v or None)
            cell.font = _font()
            cell.border = _BORDER
        scores = [row.cells.get(c.key, Cell(None, "ungraded")) for c in columns] + [row.total()]
        for ci, c in enumerate(scores, start=len(info) + 1):
            value = None  # 미채점·검토 필요는 비운다
            if c.value is not None and c.state not in ("ungraded", "review"):
                value = _number(c.value)
            cell = sheet.cell(row=ri, column=ci, value=value)
            cell.font = _font(bold=ci == len(head))
            cell.alignment = Alignment(horizontal="center")
            cell.border = _BORDER
            if color := FILLS[c.state]:
                cell.fill = PatternFill("solid", fgColor=color)
    sheet.freeze_panes = "D2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(head))}{max(1, len(rows) + 1)}"

    def fit(values: list[str], least: int) -> int:  # 한글은 글자 하나가 약 2칸
        return max([least, *(sum(2 if ord(ch) > 0x2E80 else 1 for ch in v) + 2 for v in values)])

    widths = [fit([r.klass for r in rows], 8), fit([r.name for r in rows], 10),
              fit([r.school or "" for r in rows], 10), *[max(8, len(c.key) + 4) for c in columns],
              8]  # fmt: skip
    for ci, w in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(ci)].width = w


def _write_notes(sheet: Worksheet, title: str, columns: list[Column]) -> None:
    bold: set[int] = set()

    def add(*values: object, strong: bool = False) -> int:
        sheet.append(list(values))
        if strong:
            bold.add(sheet.max_row)
        return sheet.max_row

    add(title, strong=True)
    add()
    add("색", "뜻", strong=True)
    for state, meaning in MEANING.items():
        r = add("", meaning)
        sheet.cell(row=r, column=1).fill = PatternFill("solid", fgColor=FILLS[state] or "FFFFFF")
    add("", "총점은 문항 총점의 합 (수식 아님). 한 문항이라도 비면 총점도 비움")
    add()
    add("열", "배점", strong=True)
    for c in columns:
        add(c.key, _number(c.points) if c.points is not None else None)
    add("총점", _number(sum(c.points or 0 for c in columns)), strong=True)
    for line in sheet.iter_rows():
        for cell in line:
            cell.font = _font(bold=cell.row in bold)
    sheet.column_dimensions["A"].width = 12
    sheet.column_dimensions["B"].width = 80


def build_scores(
    path: Path, title: str, columns: list[Column], rows: list[Row], *, by_class: bool = False
) -> Path:
    """점수표를 쓴다 (임시 파일에 쓰고 바꿔 넣는다 — 엑셀에서 열어 둔 파일이면 실패, 부른 쪽이
    알림)."""
    wb = Workbook()
    first = wb.active
    assert first is not None
    taken: set[str] = set()
    if by_class:
        classes = sorted({r.klass for r in rows}, key=lambda k: (k == NO_CLASS, natural_key(k)))
        wb.remove(first)
        for k in classes or [NO_CLASS]:
            sheet = wb.create_sheet(_sheet_title(k, taken))
            _write_table(sheet, columns, [r for r in rows if r.klass == k])
    else:
        first.title = _sheet_title("점수", taken)
        _write_table(first, columns, rows)
    _write_notes(wb.create_sheet(_sheet_title("안내", taken)), title, columns)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    wb.save(tmp)
    tmp.replace(path)
    return path
