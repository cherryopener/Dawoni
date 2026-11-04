"""학생 명부 (PLAN.md §7): 엑셀·CSV로 한 번에 넣고, 표지에서 읽은 이름·반과 맞춘다.

- 작업공간 `students/S0001.json` — 학생 1명 = 파일 1개 (함께 쓰기에서 두 강사가 한 파일을
  고치지 않게).
- 필드는 ID(자동 순번)·이름·반·학교뿐이다. **전화번호는 받지 않는다** (§15-5) — 표에 있어도
  읽지 않는다.
- 동명이인은 이름 뒤 알파벳으로 가른다 (김다원A·김다원B). 같은 반에 같은 이름이 두 줄이면 넣지
  않고 알린다.
- 표지 이름 맞추기: 띄어쓰기·알파벳 대소문자는 무시, 반은 "2반" = "2". 이름만 맞고 반이 다르거나,
  명부에 김다원A·김다원B가 있는데 표지에 "김다원"만 있으면 정하지 않고 강사에게 후보를 보여 준다.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import Field

from dawoai.fsio import read_json, write_model_atomic
from dawoai.models.base import DawoaiModel
from dawoai.paths import WorkspacePaths


class RosterStudent(DawoaiModel):
    id: str = Field(description="자동 순번 (S0001) — 이름이 같은 학생도 구분된다")
    name: str = Field(min_length=1)
    class_name: str | None = None
    school: str | None = None


_SPACE = re.compile(r"\s+")
_NUM = re.compile(r"(\d+)")
_SUFFIX = re.compile(r"[A-Za-z]$")


def _norm_name(s: str | None) -> str:
    return _SPACE.sub("", s or "").casefold()


def _norm_class(s: str | None) -> str:
    c = _SPACE.sub("", s or "").casefold()
    return c[:-1] if c.endswith("반") and len(c) > 1 else c


def same_name(a: str | None, b: str | None) -> bool:
    return bool(_norm_name(a)) and _norm_name(a) == _norm_name(b)


def same_class(a: str | None, b: str | None) -> bool:
    """`2반` = `2` = ` 2 반`, `TSS1` = `tss1`. 둘 다 비었으면 같은 반으로 본다."""
    return _norm_class(a) == _norm_class(b)


def natural_key(s: str | None) -> tuple[tuple[int, int | str], ...]:
    """자연 정렬: 1반 < 2반 < 10반, TSS1 < TSS2 < TSS10. 한글은 음절 코드 순(가나다순)."""
    return tuple((0, int(x)) if x.isdigit() else (1, x) for x in _NUM.split(s or "") if x)


MatchStatus = Literal["linked", "class_differs", "ambiguous", "new", "unknown"]


@dataclass
class CoverMatch:
    status: MatchStatus
    """linked=한 명으로 정해짐, class_differs=이름은 있는데 반이 다름, ambiguous=후보가 여럿,
    new=명부에 없음(새 학생 후보), unknown=표지 이름을 못 읽음."""
    student: RosterStudent | None = None
    candidates: list[RosterStudent] = field(default_factory=lambda: list[RosterStudent]())

    @property
    def note(self) -> str | None:
        """강사에게 보일 한 줄 (정해졌으면 None)."""
        names = ", ".join(_label(s) for s in self.candidates)
        return {
            "linked": None,
            "class_differs": f"명부에는 다른 반으로 있음 ({names}) — 같은 학생인지 확인",
            "ambiguous": f"명부의 후보가 여럿 ({names}) — 이름 뒤 알파벳을 확인",
            "new": "명부에 없음 — 새 학생이면 명부에 넣고, 아니면 이름을 고치세요",
            "unknown": "표지 이름을 읽지 못함 — 명부에서 고르세요",
        }[self.status]


def _label(s: RosterStudent) -> str:
    return " ".join(x for x in (s.class_name, s.name, s.school and f"({s.school})") if x)


@dataclass
class ImportResult:
    added: list[RosterStudent] = field(default_factory=lambda: list[RosterStudent]())
    updated: list[RosterStudent] = field(default_factory=lambda: list[RosterStudent]())
    unchanged: int = 0
    problems: list[str] = field(default_factory=lambda: list[str]())
    notes: list[str] = field(default_factory=lambda: list[str]())


class Roster:
    def __init__(self, ws: WorkspacePaths) -> None:
        self.dir = ws.students

    def all(self) -> list[RosterStudent]:
        if not self.dir.is_dir():
            return []
        out = [RosterStudent.model_validate(read_json(p)) for p in self.dir.glob("S*.json")]
        return sorted(out, key=lambda s: (natural_key(s.class_name), s.name, s.id))

    def get(self, student_id: str) -> RosterStudent | None:
        p = self.dir / f"{student_id}.json"
        return RosterStudent.model_validate(read_json(p)) if p.is_file() else None

    def save(self, s: RosterStudent) -> RosterStudent:
        write_model_atomic(self.dir / f"{s.id}.json", s)
        return s

    def _next_id(self, taken: list[RosterStudent]) -> str:
        nums = [int(s.id[1:]) for s in taken if s.id[1:].isdigit()]
        return f"S{max(nums, default=0) + 1:04d}"

    def add(
        self, name: str, class_name: str | None = None, school: str | None = None
    ) -> RosterStudent:
        name = _SPACE.sub("", name)
        everyone = self.all()
        if any(same_name(s.name, name) and same_class(s.class_name, class_name) for s in everyone):
            raise ValueError(
                f"{class_name or '반 없음'}에 {name}이(가) 이미 있습니다 — 동명이인이면 "
                f"{name}A, {name}B처럼 알파벳을 붙이세요"
            )
        s = RosterStudent(id=self._next_id(everyone), name=name, class_name=class_name or None,
                          school=school or None)  # fmt: skip
        return self.save(s)

    def classes(self) -> list[str]:
        """명부의 반 목록 (자연 정렬) — 표지를 읽을 때 이 중에서 고르게 한다."""
        names = {s.class_name for s in self.all() if s.class_name}
        return sorted(names, key=natural_key)

    def import_rows(self, rows: list[dict[str, str | None]]) -> ImportResult:
        """(이름, 반, 학교) 줄들 → 명부. 이름·반이 같은 학생은 학교만 고친다."""
        res = ImportResult()
        everyone = self.all()
        seen: set[tuple[str, str]] = set()
        for n, row in enumerate(rows, start=1):
            name = _SPACE.sub("", row.get("name") or "")
            klass = (row.get("class_name") or "").strip() or None
            school = (row.get("school") or "").strip() or None
            if not name:
                continue
            key = (_norm_name(name), _norm_class(klass))
            if key in seen:
                res.problems.append(
                    f"{n}번째 줄: {klass or '반 없음'}에 {name}이(가) 두 번 — 동명이인이면 "
                    f"{name}A, {name}B처럼 알파벳을 붙여 다시 넣으세요"
                )
                continue
            seen.add(key)
            old = next(
                (
                    s
                    for s in everyone
                    if same_name(s.name, name) and same_class(s.class_name, klass)
                ),
                None,
            )
            if old is None:
                s = RosterStudent(id=self._next_id(everyone), name=name, class_name=klass,
                                  school=school)  # fmt: skip
                everyone.append(self.save(s))
                res.added.append(s)
            elif school and old.school != school:
                res.updated.append(self.save(old.model_copy(update={"school": school})))
            else:
                res.unchanged += 1
        return res

    def match(self, name: str | None, class_name: str | None) -> CoverMatch:
        """표지에서 읽은 이름·반 → 명부의 학생 (PLAN.md §5-1 3단계)."""
        if not _norm_name(name):
            return CoverMatch("unknown")
        everyone = self.all()
        named = [s for s in everyone if same_name(s.name, name)]
        if class_name:
            in_class = [s for s in named if same_class(s.class_name, class_name)]
        else:
            in_class = named
        if len(in_class) == 1:
            return CoverMatch("linked", in_class[0], in_class)
        if in_class:
            return CoverMatch("ambiguous", candidates=in_class)
        if named:
            return CoverMatch("class_differs", candidates=named)
        # 명부에는 김다원A·김다원B, 표지에는 "김다원"만 (알파벳을 안 적음)
        base = _norm_name(name)
        lettered = [
            s
            for s in everyone
            if _SUFFIX.search(s.name)
            and _norm_name(s.name[:-1]) == base
            and (not class_name or same_class(s.class_name, class_name))
        ]
        if lettered:
            return CoverMatch("ambiguous", candidates=lettered)
        return CoverMatch("new")


# ---------------------------------------------------------------- 표 읽기


_HEADERS: dict[str, tuple[str, ...]] = {
    "name": ("이름", "성명", "학생", "학생명", "학생이름", "name"),
    "class_name": ("반", "반명", "학급", "반이름", "class"),
    "school": ("학교", "학교명", "school"),
}
_PHONE = ("전화", "연락처", "휴대폰", "핸드폰", "phone", "tel")


def _header_of(cell: str) -> str | None:
    c = _SPACE.sub("", cell).casefold()
    if not c:
        return None
    if any(p in c for p in _PHONE):
        return "phone"
    return next((k for k, names in _HEADERS.items() if c in names), None)


def _cells(path: Path) -> list[list[str]]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.worksheets[0]
            return [
                ["" if v is None else str(v).strip() for v in row]
                for row in ws.iter_rows(values_only=True)
            ]
        finally:
            wb.close()
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):  # 엑셀이 저장한 한국어 CSV는 cp949인 경우가 많다
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"{path.name}: 글자 인코딩을 알 수 없습니다 (UTF-8로 저장해 주세요)")
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text))]


def read_table(path: Path) -> tuple[list[dict[str, str | None]], list[str]]:
    """엑셀(xlsx)·CSV → (이름, 반, 학교) 줄과 알림. 머리 줄(이름/성명, 반, 학교)로 열을 찾고,
    머리 줄이 없으면 점수표와 같은 순서(반, 이름, 학교)로 읽는다. 전화번호 열은 읽지 않는다."""
    rows = [r for r in _cells(path) if any(r)]
    notes: list[str] = []
    if not rows:
        return [], ["표가 비어 있습니다"]
    cols = {i: h for i, c in enumerate(rows[0]) if (h := _header_of(c))}
    if "name" in cols.values():
        body = rows[1:]
    else:
        cols = dict(enumerate(("class_name", "name", "school")))
        body = rows
        notes.append("머리 줄(이름·반·학교)이 없어 반, 이름, 학교 순서로 읽었습니다")
    if "phone" in cols.values():
        notes.append("전화번호 열은 읽지 않았습니다 (명부에 전화번호는 두지 않음)")
    out: list[dict[str, str | None]] = []
    for r in body:
        row: dict[str, str | None] = {"name": None, "class_name": None, "school": None}
        for i, key in cols.items():
            if key != "phone" and i < len(r) and r[i]:
                row[key] = r[i]
        out.append(row)
    return out, notes
