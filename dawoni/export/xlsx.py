"""엑셀 점수표 (PLAN.md §9-2) — 코드만, 토큰 0.

시험 1개 = 파일 1개 `(점수표)_<시험>.xlsx`. 2026-11-18 강사가 보내 준 성적표 모양으로 바꿨다
(채팅 AI가 만든 `11월_월례평가_물리_성적.xlsx`):

- `전체 요약`: 반마다 응시 수, 문항별 평균, 총점 평균·최고·최저 + 전체 줄 (반 시트를 가리키는 수식)
- 반마다 시트: 제목 / 머리 줄 `번호 | 반 | 이름 | (학교) | 1-(1) | 1-(2) | 1번 계 | 2번 | … | 총점 |
  석차 | 비고` / 배점 줄 / 학생 (이름 가나다순, 명부에 있는데 답안지가 없는 학생은 '미응시') /
  평균·최고·최저·응시 / 안내. 문항 계·총점·석차·평균은 **수식** — 엑셀에서 점수를 고치면 다시
  계산된다 (앱은 엑셀을 읽지 않는다). 한 문항이라도 채점 전이면 그 학생의 계·총점은 비운다.
- `채점 근거`: 만점이 아닌 소문항마다 한 줄 (반 | 이름 | 문항 | 득점 | 배점 | 감점 사유·메모).
- 비고: 원본을 다시 볼 곳(판독불가·판독 불확실), 검토 필요 이유, 강사가 고친 이유, 이름 확인.
- 칸 색: 검토 필요 = 노랑(AI 점수를 넣음, 2026-11-18 사용자 결정), 판독문 확인 전 채점 = 옅은 파랑,
  채점 전 = 회색(비움). 문항 계 = 옅은 초록, 총점 = 옅은 주황.
- 반을 나누지 않으면(`by_class=False`) `전체` 시트 하나에 모든 반 (요약 시트 없음).
- 정렬: 반은 자연 정렬(1반 < 2반 < 10반, 반이 없으면 맨 뒤), 같은 반은 이름 가나다순.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from dawoni.students import natural_key

FONT = "맑은 고딕"
"""출력물 글꼴 (PLAN.md §11 — 화면과 따로, Windows 기본 탑재)."""
NO_CLASS = "반없음"
ABSENT = "미응시 (답안지 없음)"

State = Literal["ok", "unconfirmed", "review", "ungraded"]
FILLS: dict[State, str | None] = {
    "ok": None,
    "unconfirmed": "DDEBF7",  # 옅은 파랑: 판독문 확인 전 채점 (값 있음)
    "review": "FFEB9C",  # 노랑: 검토 필요 (AI 점수를 넣고 칠함)
    "ungraded": "D9D9D9",  # 회색: 미채점 (비움)
}
MEANING: dict[State, str] = {
    "review": "검토 필요 — AI 채점이 엇갈리거나 확신이 낮음 (AI 점수). 비고에 이유",
    "unconfirmed": "판독문을 강사가 확인하기 전에 채점 — 확인할 때 고친 줄이 있으면 다시 채점",
    "ungraded": "채점 전 — 답안 없음·입력 대기",
}
HEAD = "D9E1F2"
BAND = "F2F2F2"
"""배점 줄·평균·최고·최저."""
SUM = "E2EFDA"
TOTAL = "FCE4D6"
_WORST: list[State] = ["ungraded", "review", "unconfirmed", "ok"]
_BAD_SHEET = re.compile(r"[\[\]:*?/\\']")
HEAD_ROW, POINTS_ROW, FIRST_ROW = 3, 4, 5
type Value = str | int | float | None


@dataclass
class Cell:
    value: float | None
    state: State


@dataclass
class Reason:
    """채점 근거 한 줄: 만점이 아닌 소문항."""

    key: str
    awarded: float
    points: float
    text: str


@dataclass
class Row:
    klass: str
    """반 (없으면 `NO_CLASS`)."""
    name: str
    school: str | None
    cells: dict[str, Cell] = field(default_factory=lambda: dict[str, Cell]())
    """열 이름(`1-(1)`, 소문항이 없으면 `1`) → 칸."""
    totals: dict[str, Cell] = field(default_factory=lambda: dict[str, Cell]())
    """문항 번호 → 그 문항 총점."""
    notes: list[str] = field(default_factory=lambda: list[str]())
    """비고 — 애매한 곳 (원본 확인, 검토 필요 이유, 강사가 고친 이유, 이름 확인)."""
    reasons: list[Reason] = field(default_factory=lambda: list[Reason]())
    absent: bool = False
    """명부에 있는데 이 시험 답안지가 없음."""

    def total(self) -> Cell:
        if not self.totals:
            return Cell(None, "ungraded")
        seen = {c.state for c in self.totals.values()}
        state: State = next(s for s in _WORST if s in seen)
        if state == "ungraded":
            return Cell(None, state)
        return Cell(sum(c.value or 0 for c in self.totals.values()), state)


@dataclass
class Column:
    key: str
    points: float | None
    question: str = ""
    """문항 번호 — 같은 문항의 소문항은 이어서 두고 뒤에 `N번 계`. 비면 `key`."""


@dataclass
class _Col:
    """시트의 점수 열 하나: 소문항(`part`) 또는 문항 계(`sum`)."""

    kind: Literal["part", "sum"]
    key: str
    head: str
    points: float | None
    parts: list[str] = field(default_factory=lambda: list[str]())


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


def _font(bold: bool = False, size: float | None = None, color: str | None = None) -> Font:
    return Font(name=FONT, bold=bold, size=size, color=color)


_THIN = Side(style="thin", color="BFBFBF")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _layout(columns: list[Column]) -> tuple[list[_Col], dict[str, str]]:
    """점수 열 차례와 문항마다 그 문항 점수 열의 key (소문항이 둘 이상이면 계)."""
    groups: dict[str, list[Column]] = {}
    for c in columns:
        groups.setdefault(c.question or c.key, []).append(c)
    out: list[_Col] = []
    score_of: dict[str, str] = {}
    for q, cols in groups.items():
        if len(cols) == 1:
            c = cols[0]
            head = f"{q}번" if c.key == q else c.key
            out.append(_Col("part", c.key, head, c.points))
            score_of[q] = c.key
            continue
        out += [_Col("part", c.key, c.key, c.points) for c in cols]
        total = (
            sum(c.points or 0 for c in cols) if all(c.points is not None for c in cols) else None
        )
        out.append(_Col("sum", f"{q}#계", f"{q}번 계", total, [c.key for c in cols]))
        score_of[q] = f"{q}#계"
    return out, score_of


def _style(cell: Any, *, bold: bool = False, fill: str | None = None, border: bool = True,
           align: Alignment | None = _CENTER) -> None:  # fmt: skip
    cell.font = _font(bold=bold)
    if fill:
        cell.fill = PatternFill("solid", fgColor=fill)
    if border:
        cell.border = _BORDER
    if align is not None:
        cell.alignment = align


@dataclass
class _Placed:
    """반 시트에서 요약이 가리킬 곳."""

    sheet: str
    first: int
    last: int
    question_cols: dict[str, str]
    """문항 번호 → 열 글자."""
    total_col: str


def _write_class(
    sheet: Worksheet, title: str, columns: list[Column], rows: list[Row]
) -> _Placed | None:
    specs, score_of = _layout(columns)
    schools = any(r.school for r in rows)
    fixed = ["번호", "반", "이름", *(["학교"] if schools else [])]
    first_score = len(fixed) + 1
    letter: dict[str, str] = {
        s.key: get_column_letter(first_score + i) for i, s in enumerate(specs)
    }
    total_ci = first_score + len(specs)
    total_col = get_column_letter(total_ci)
    note_ci = total_ci + 2
    heads = [*fixed, *[s.head for s in specs], "총점", "석차", "비고"]

    sheet.cell(row=1, column=1, value=title).font = _font(bold=True, size=13)
    for ci, h in enumerate(heads, start=1):
        _style(sheet.cell(row=HEAD_ROW, column=ci, value=h), bold=True, fill=HEAD)
    band = {"이름": "배점"}
    for ci, h in enumerate(heads, start=1):
        cell = sheet.cell(row=POINTS_ROW, column=ci, value=band.get(h))
        _style(cell, bold=True, fill=BAND)
    points = [s.points for s in specs]
    for i, p in enumerate(points):
        if p is not None:
            sheet.cell(row=POINTS_ROW, column=first_score + i, value=_number(p))
    full = [s.points for s in specs if s.kind == "sum" or s.key in score_of.values()]
    if all(p is not None for p in full):
        sheet.cell(row=POINTS_ROW, column=total_ci, value=_number(sum(p or 0 for p in full)))

    ordered = sorted(rows, key=row_key)
    last = FIRST_ROW + len(ordered) - 1
    for i, row in enumerate(ordered):
        r = FIRST_ROW + i
        klass = "" if row.klass == NO_CLASS else row.klass
        info: list[Value] = [i + 1, klass or None, row.name, *([row.school] if schools else [])]
        for ci, v in enumerate(info, start=1):
            _style(sheet.cell(row=r, column=ci, value=v), bold=ci == 3)
        numeric: set[str] = set()
        for k, s in enumerate(specs):
            value: Value = None
            if s.kind == "part":
                c = row.cells.get(s.key, Cell(None, "ungraded"))
                if not row.absent and c.value is not None and c.state != "ungraded":
                    value = _number(c.value)
                    numeric.add(s.key)
                fill = None if row.absent else FILLS[c.state]
            else:
                if all(p in numeric for p in s.parts):
                    a, b = letter[s.parts[0]], letter[s.parts[-1]]
                    value = f"=SUM({a}{r}:{b}{r})"
                    numeric.add(s.key)
                fill = SUM
            _style(sheet.cell(row=r, column=first_score + k, value=value), fill=fill)
        total: Value = None
        rank: Value = None
        if score_of and all(k in numeric for k in score_of.values()):
            total = "=" + "+".join(f"{letter[k]}{r}" for k in score_of.values())
            rank = f"=RANK({total_col}{r},${total_col}${FIRST_ROW}:${total_col}${last})"
        _style(sheet.cell(row=r, column=total_ci, value=total), bold=True, fill=TOTAL)
        _style(sheet.cell(row=r, column=total_ci + 1, value=rank))
        notes = [ABSENT] if row.absent else []
        note = sheet.cell(row=r, column=note_ci, value=" / ".join([*notes, *row.notes]) or None)
        _style(note, align=Alignment(horizontal="left", vertical="center", wrap_text=True))

    end = last + 1
    if ordered:
        score_cols = [first_score + k for k in range(len(specs))] + [total_ci]
        for k, (label, fn) in enumerate((("평균", "AVERAGE"), ("최고", "MAX"), ("최저", "MIN"))):
            r = end + k
            for ci in range(1, note_ci + 1):
                _style(sheet.cell(row=r, column=ci), bold=True, fill=BAND)
            sheet.cell(row=r, column=3, value=label)
            for ci in score_cols:
                col = get_column_letter(ci)
                rng = f"{col}{FIRST_ROW}:{col}{last}"
                cell = sheet.cell(row=r, column=ci, value=f'=IF(COUNT({rng})=0,"",{fn}({rng}))')
                cell.number_format = "0.0" if fn == "AVERAGE" else "0.##"
        r = end + 3
        sheet.cell(row=r, column=3, value="응시").font = _font(bold=True)
        count = f"=COUNT({total_col}{FIRST_ROW}:{total_col}{last})"
        sheet.cell(row=r, column=4, value=count).font = _font(bold=True)
        sheet.cell(row=r, column=5, value="명").font = _font(bold=True)
        end = r + 2
    lines = [
        "※ 평균·최고·최저·석차는 응시자 기준입니다. 점수를 고치면 문항 계·총점·석차가 다시 "
        "계산됩니다 (엑셀에서 고친 점수는 다워니에 돌아가지 않습니다).",
        "※ 비고: 원본을 다시 볼 곳(판독불가·판독 불확실), 검토 필요 이유, 강사가 고친 이유, "
        "이름 확인.",
    ]
    for k, text in enumerate(lines):
        sheet.cell(row=end + k, column=1, value=text).font = _font()
    r = end + len(lines)
    for state, meaning in MEANING.items():
        _style(sheet.cell(row=r, column=1), fill=FILLS[state])
        sheet.cell(row=r, column=2, value=meaning).font = _font()
        r += 1

    sheet.freeze_panes = f"{get_column_letter(first_score)}{FIRST_ROW}"
    sheet.row_dimensions[HEAD_ROW].height = 30
    widths = [5, 9, 11, *([12] if schools else [])]
    widths += [8 if s.kind == "part" and len(s.head) > 6 else 7 for s in specs] + [7, 6, 62]
    for ci, w in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(ci)].width = w
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True  # pyright: ignore[reportOptionalMemberAccess]
    if not ordered:
        return None
    qcols = {q: letter[k] for q, k in score_of.items()}
    return _Placed(sheet.title, FIRST_ROW, last, qcols, total_col)


def _ref(sheet: str, col: str, p: _Placed) -> str:
    quoted = "'" + sheet.replace("'", "''") + "'"
    return f"{quoted}!{col}{p.first}:{col}{p.last}"


def _write_summary(
    sheet: Worksheet, title: str, columns: list[Column], placed: list[tuple[str, _Placed]]
) -> None:
    _, score_of = _layout(columns)
    points: dict[str, float | None] = {}
    for c in columns:
        q = c.question or c.key
        prev = points.get(q, 0.0)
        points[q] = None if prev is None or c.points is None else prev + c.points
    full = sum(p or 0 for p in points.values()) if all(points.values()) else None
    sheet.cell(row=1, column=1, value=f"{title} 반별 요약").font = _font(bold=True, size=13)

    def pts(p: float | None) -> str:
        return f"({_number(p)})" if p is not None else ""

    heads = ["반", "응시", *[f"{q}번 평균{pts(points[q])}" for q in score_of],
             f"총점 평균{pts(full)}", "최고", "최저"]  # fmt: skip
    for ci, h in enumerate(heads, start=1):
        _style(sheet.cell(row=3, column=ci, value=h), bold=True, fill=HEAD)

    def write(r: int, label: str, parts: list[tuple[str, _Placed]], bold: bool = False) -> None:
        def refs(col_of: str | None) -> str:
            return ",".join(_ref(n, col_of or p.total_col, p) for n, p in parts)

        def guard(fn: str, rng: str) -> str:
            return f'=IF(COUNT({rng})=0,"",{fn}({rng}))'

        values: list[Value] = [label, f"=COUNT({refs(None)})"]
        values += [guard("AVERAGE", ",".join(_ref(n, p.question_cols[q], p) for n, p in parts))
                   for q in score_of]  # fmt: skip
        values += [guard("AVERAGE", refs(None)), guard("MAX", refs(None)), guard("MIN", refs(None))]
        for ci, v in enumerate(values, start=1):
            cell = sheet.cell(row=r, column=ci, value=v)
            _style(cell, bold=bold or ci == 1, fill=BAND if bold else None)
            if 3 <= ci <= len(values) - 2:
                cell.number_format = "0.0"

    for k, (klass, p) in enumerate(placed):
        write(4 + k, klass, [(p.sheet, p)])
    if len(placed) > 1:
        write(4 + len(placed), "전체", [(p.sheet, p) for _, p in placed], bold=True)
    widths = [14, 7, *[13 for _ in score_of], 15, 7, 7]
    for ci, w in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(ci)].width = w


def _write_reasons(sheet: Worksheet, columns: list[Column], rows: list[Row]) -> None:
    order = {c.key: i for i, c in enumerate(columns)}
    heads = ["반", "이름", "문항", "득점", "배점", "감점 사유 / 메모"]
    for ci, h in enumerate(heads, start=1):
        _style(sheet.cell(row=1, column=ci, value=h), bold=True, fill=HEAD)
    r = 2
    for row in sorted(rows, key=row_key):
        klass = "" if row.klass == NO_CLASS else row.klass
        for x in sorted(row.reasons, key=lambda x: order.get(x.key, 999)):
            values: list[Value] = [klass or None, row.name, x.key, _number(x.awarded),
                                    _number(x.points), x.text]  # fmt: skip
            for ci, v in enumerate(values, start=1):
                left = Alignment(horizontal="left", vertical="center", wrap_text=True)
                _style(sheet.cell(row=r, column=ci, value=v), align=left if ci == 6 else _CENTER)
            r += 1
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:F{max(1, r - 1)}"
    for ci, w in enumerate([9, 11, 13, 6, 6, 60], start=1):
        sheet.column_dimensions[get_column_letter(ci)].width = w


def build_scores(
    path: Path, title: str, columns: list[Column], rows: list[Row], *, by_class: bool = True
) -> Path:
    """점수표를 쓴다 (임시 파일에 쓰고 바꿔 넣는다 — 엑셀에서 열어 둔 파일이면 실패, 부른 쪽이
    알림)."""
    wb = Workbook()
    first = wb.active
    assert first is not None
    taken: set[str] = set()
    if by_class:
        classes = sorted({r.klass for r in rows}, key=lambda k: (k == NO_CLASS, natural_key(k)))
        first.title = _sheet_title("전체 요약", taken)
        placed: list[tuple[str, _Placed]] = []
        for k in classes or [NO_CLASS]:
            sheet = wb.create_sheet(_sheet_title(k, taken))
            name = "" if k == NO_CLASS else f" — {k}"
            mine = [r for r in rows if r.klass == k]
            if (p := _write_class(sheet, f"{title} 성적{name}", columns, mine)) is not None:
                placed.append((k, p))
        _write_summary(first, title, columns, placed)
    else:
        first.title = _sheet_title("전체", taken)
        _write_class(first, f"{title} 성적", columns, rows)
    _write_reasons(wb.create_sheet(_sheet_title("채점 근거", taken)), columns, rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    wb.save(tmp)
    tmp.replace(path)
    return path
