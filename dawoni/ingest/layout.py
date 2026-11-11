# pyright: basic, reportMissingTypeStubs=false
"""디지털 PDF 시험지의 결정적 레이아웃 분석 (AI 없음).

    컬럼 감지 → 머리말·꼬리말 띠 → 문항 번호 앵커 → 문항 영역(단·페이지 이어짐 포함)
    → 그림 후보(벡터 군집·이미지) → 영역 안의 그림만 그 문항에 귀속

AIHelper에서 터졌던 문제를 구조로 막는다 (PLAN §10):
- 머리말 배너는 문항 영역 밖이라 그림이 될 수 없다.
- 2단 시험지는 단별 읽기 순서로 영역을 정하므로 그림 순서가 지그재그로 밀리지 않는다.
- 그림은 **포함 관계**로 귀속한다. 순서대로 나눠 주지 않는다.
- 테두리 상자 안에 글이 있으면(<보기>, 조건) 그림이 아니라 글 상자다.
- 그림 선지는 가장 가까운 ①~⑤ 기호로 짝짓는다.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

HEADER_BAND = 0.11
FOOTER_BAND = 0.94
MIN_FIGURE = 15.0
CLUSTER_GAP = 8.0
CHOICE_MARKS = "①②③④⑤"

_ANCHOR = re.compile(
    r"^\s*(?:\[\s*(서술형|단답형)\s*(\d+)\s*\]|(서술형|단답형)\s*(\d+)\s*[.)]|(\d{1,3})\s*[.．](?!\d))"
)
"""문항 번호 줄. 숫자는 세 자리까지 (문제집·교재는 수백 번까지), "1.5 m" 같은 소수는 아님."""
SET_MARK = re.compile(
    r"^\s*(기본\s*문제|학습\s*과제|연습\s*문제|확인\s*문제|실전\s*문제|심화\s*문제|도전\s*문제|"
    r"종합\s*문제|발전\s*문제|대표\s*문제|기출\s*문제|서술형\s*문제|단원\s*(?:평가|마무리)|유제)"
    r"\s*[\dⅠⅡⅢ]*\s*$"
)
"""문제 묶음 제목 ("기본 문제", "학습 과제" …).

한 강·챕터에 묶음이 여럿이면 번호가 1번부터 다시 시작한다."""
_BARE_NUMBER = re.compile(r"^\s*\d{1,3}\s*[.．]\s*(?:[A-Za-z]{1,2})?\s*$")
"""번호뿐인 줄 ("2.", "7. A") — 문항 번호이지 머리말이 아니다.

"7. 원운동" 같은 챕터 머리말은 해당하지 않는다."""
MAX_STEP = 5
"""번호 흐름: 다음 문항 번호는 앞 번호보다 크고 이만큼 이내."""
_KINDS = r"(?:서술형|단답형|논술형|약술형|선택형|객관식|주관식)"
_POINTS = re.compile(
    r"[\[［~]\s*(?:" + _KINDS + r"\s*:?\s*)?(?:각\s*\d+(?:\.\d+)?\s*점\s*[/,]\s*)?"
    r"(?:총\s*)?(\d+(?:\.\d+)?)\s*점\s*[\]］₩]"
)
"""배점 "[3점]", "[단답형 4점]", "[서술형 각 3점/총 9점]"(→ 9) — 수능 PDF는 괄호가 글꼴 탓에
"~3점₩"로 나온다 (실측)."""
_EACH_ONLY = re.compile(r"[\[［]\s*(?:" + _KINDS + r"\s*:?\s*)?각\s*\d+(?:\.\d+)?\s*점\s*[\]］]")
""""[단답형 각 2점]" — 소문항 수를 알아야 합을 안다."""
_TOTAL = re.compile(r"\[\s*총\s*(\d+(?:\.\d+)?)\s*점\s*\]")
_SPLIT_TOTAL = re.compile(r"총\s*점\s*\[?\s*(\d+(?:\.\d+)?)\s*\]")
"""조각난 "[총 8점]": PDF가 "총 점"을 한 조각으로 두고 숫자·괄호를 따로 둬 "총 점 [ 8 ]",
"[ 총 점 9 ]"로 읽힌다 (실측 공통형3회 — 배점을 못 읽어 소문항 배점 합을 확인하지 못함)."""


def marked_points(text: str) -> float | None:
    """문항 글에 적힌 배점.

    - 총점 표시("[총 8점]", 조각난 "총 점 [ 8 ]")가 있으면 그것.
    - 아니면 배점 표시들: 하나면 그것, 여럿이면 소문항마다 적은 것이라 그 합. 단 셋 이상인데
      첫 표시가 나머지의 합이면 첫 표시가 문항 배점 (문항 머리에 "[8점]", 소문항에 "[3점]"·"[5점]").
    - "[단답형 각 2점]"처럼 소문항 수를 알아야 하는 표시가 있으면 None.

    실측(2026-11-10, 11월 월례평가 HWP): "1) … [단답형 4점]", "2) … [단답형 5점]"처럼 소문항에만
    유형과 함께 적혀 하나도 읽지 못해, 9·8·13점 문항이 모두 자리값 4점이 됐다."""
    return _marked(text)[0]


def summed_marks(text: str) -> bool:
    """배점이 소문항 표시 여럿의 합인가 (총점 표시·표시 하나가 아님). 그렇다면 표시가 없는 소문항이
    있을 때 합이 모자란다 — 실측(2026-11-11): "(1)·(2)"에는 표시가 없고 (3)~(8)에만 "[1점]"이 있어
    6점이 되고, AI가 맞춰 (1)·(2)를 뺐다. 다른 시험은 (1) 5점에 표시가 없어 AI의 15점이 세 번 거절돼
    시험 읽기가 실패했다."""
    return _marked(text)[1]


_PART_LABEL = re.compile(r"(?:^|\s)(?:\(\s*\d{1,2}\s*\)|\d{1,2}\s*\)|[①-⑳])")
"""소문항 표시 ("(1)", "1)", "①")."""


def _marked(text: str) -> tuple[float | None, bool]:
    # 문항 첫 줄에서 소문항 표시보다 앞에 있는 배점이 문항 배점 — 소문항 안의 "[총 10점]"을 문항
    # 총점으로 읽으면 안 된다 (실측 2026-11-11 서울형 4회 2번: 머리 "[서술형 15점]",
    # (2)에 "[총 10점]")
    head = text.lstrip().split("\n", 1)[0]
    m = _TOTAL.search(head) or _SPLIT_TOTAL.search(head) or _POINTS.search(head)
    if m and not _EACH_ONLY.search(head) and not _PART_LABEL.search(head[: m.start()]):
        return float(m.group(1)), False
    m = _TOTAL.search(text) or _SPLIT_TOTAL.search(text)
    if m:
        return float(m.group(1)), False
    if _EACH_ONLY.search(text):
        return None, False
    marks = [float(m.group(1)) for m in _POINTS.finditer(text)]
    if not marks:
        return None, False
    if len(marks) >= 3 and abs(marks[0] - sum(marks[1:])) < 1e-9:
        return marks[0], False
    return sum(marks), len(marks) > 1


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    def expand(self, d: float) -> Rect:
        return Rect(self.x0 - d, self.y0 - d, self.x1 + d, self.y1 + d)

    def intersects(self, o: Rect) -> bool:
        return self.x0 < o.x1 and o.x0 < self.x1 and self.y0 < o.y1 and o.y0 < self.y1

    def contains_point(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def contains(self, o: Rect, tol: float = 1.0) -> bool:
        return (
            o.x0 >= self.x0 - tol
            and o.y0 >= self.y0 - tol
            and o.x1 <= self.x1 + tol
            and o.y1 <= self.y1 + tol
        )

    def union(self, o: Rect) -> Rect:
        return Rect(min(self.x0, o.x0), min(self.y0, o.y0), max(self.x1, o.x1), max(self.y1, o.y1))

    @classmethod
    def of(cls, r: object) -> Rect:
        x0, y0, x1, y1 = r  # type: ignore[misc]
        return cls(float(x0), float(y0), float(x1), float(y1))


@dataclass
class Line:
    page: int
    rect: Rect
    text: str


@dataclass
class Anchor:
    page: int
    column: int
    label: str
    """문항 번호 (예: "3", "서술형 1")."""
    line: Line


@dataclass
class Figure:
    page: int
    rect: Rect
    kind: str
    """vector | image"""
    id: str = ""
    near_choice: int | None = None
    file: Path | None = None
    """문서(DOCX·HWPX)에서 꺼낸 그림 파일 — PDF 그림은 None (쪽에서 잘라 낸다)."""


@dataclass
class TextBox:
    """테두리 상자 안의 글 (<보기>, 조건 등) — 그림이 아니다."""

    page: int
    rect: Rect


@dataclass
class Region:
    """문항 하나의 영역. 단·페이지를 넘어가면 상자가 여러 개."""

    label: str
    boxes: list[tuple[int, Rect]] = field(default_factory=lambda: list[tuple[int, Rect]]())
    lines: list[Line] = field(default_factory=lambda: list[Line]())
    figures: list[Figure] = field(default_factory=lambda: list[Figure]())
    textboxes: list[TextBox] = field(default_factory=lambda: list[TextBox]())
    points: float | None = None

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def pages(self) -> list[int]:
        return sorted({p for p, _ in self.boxes})


@dataclass
class PageInfo:
    number: int
    width: float
    height: float
    columns: list[tuple[float, float]]
    header_y: float
    footer_y: float
    lines: list[Line]
    figures: list[Figure]
    textboxes: list[TextBox]
    choice_marks: list[tuple[int, Rect]]


@dataclass
class DocumentLayout:
    path: Path
    pages: list[PageInfo]
    regions: list[Region]
    scanned: bool
    warnings: list[str] = field(default_factory=lambda: list[str]())

    def region(self, label: str) -> Region:
        for r in self.regions:
            if r.label == label:
                return r
        raise KeyError(label)


# ---------------------------------------------------------------- 페이지 단위 추출


def _lines(page: pymupdf.Page, number: int) -> list[Line]:
    out: list[Line] = []
    info: dict[str, Any] = page.get_text("dict")  # type: ignore[assignment]
    for block in info["blocks"]:
        for ln in block.get("lines", []):
            text = "".join(s["text"] for s in ln["spans"]).strip()
            if text:
                out.append(Line(number, Rect.of(ln["bbox"]), text))
    return out


def _columns(
    lines: list[Line], width: float, top: float, bottom: float
) -> list[tuple[float, float]]:
    """2단이면 [(왼쪽), (오른쪽)], 아니면 [(전체)]."""
    body = [ln for ln in lines if top <= ln.rect.cy <= bottom]
    # 첫 문항 번호 줄 위(쪽 제목·이름 칸)는 단을 가르는 데 쓰지 않는다 — 실측(2026-11-11, DT3 2쪽):
    # 가운데를 가로지르는 제목 "DAY 3. 회로와 정전기 DT 이름:"이 머리말 아래에 있어 1단으로 잡혔고,
    # 같은 높이의 오른쪽 단 2번이 1번에 붙어 사라졌다.
    first = _first_anchor_y(lines, top, bottom)
    if first is not None:
        below = [ln for ln in body if ln.rect.y0 >= first - 1]
        if len(below) >= 4:
            body = below
    if len(body) < 4:
        return [(0.0, width)]
    xs: list[tuple[float, int]] = []
    x = width * 0.35
    while x <= width * 0.65:
        xs.append((x, sum(1 for ln in body if ln.rect.x0 < x < ln.rect.x1)))
        x += 2
    best_cross = min(c for _, c in xs)
    # 걸치는 줄이 가장 적은 x들의 이어진 띠 중 쪽 가운데에 가장 가까운 띠의 가운데.
    # 띠의 왼쪽 끝을 쓰면 왼쪽 단이 짧은 쪽에서 오른쪽 단 문항 번호가 들여쓴 줄로 빠진다
    # (실측: 띠 236~300, 번호 306)
    bands: list[list[float]] = []
    for xv, c in xs:
        if c == best_cross:
            if bands and xv - bands[-1][-1] <= 2.5:
                bands[-1].append(xv)
            else:
                bands.append([xv])
    band = min(bands, key=lambda b: abs((b[0] + b[-1]) / 2 - width / 2))
    best_x = (band[0] + band[-1]) / 2
    left = sum(1 for ln in body if ln.rect.x1 <= best_x)
    right = sum(1 for ln in body if ln.rect.x0 >= best_x)
    # 한쪽 단이 짧아도(그림이 큰 문항 하나) 가운데 빈 띠가 있으면 2단 — 실측: 왼쪽 4줄·오른쪽 20줄
    side = min(len(body) * 0.2, max(3, len(body) * 0.15))
    if best_cross <= max(1, len(body) * 0.05) and left >= side and right >= side:
        return [(0.0, best_x), (best_x, width)]
    return [(0.0, width)]


def _first_anchor_y(lines: list[Line], top: float, bottom: float) -> float | None:
    """머리말·꼬리말 사이에서 처음 나오는 문항 번호 줄의 위쪽 y."""
    ys = [ln.rect.y0 for ln in lines if top <= ln.rect.cy <= bottom and _ANCHOR.match(ln.text)]
    return min(ys, default=None)


def _title_above_columns(
    lines: list[Line], boundary: float, header_y: float, footer_y: float
) -> float:
    """2단 쪽에서 첫 문항 번호 위에 있고 단 경계를 가로지르는 줄(쪽 제목·이름 칸)까지를 머리말로.
    오른쪽 단에 넣으면 단의 왼쪽 끝이 그 줄로 잡혀 오른쪽 단 문항 번호가 들여쓴 줄로 빠진다
    (실측 2026-11-11, DT3 2쪽: 2번이 1번에 붙어 사라짐)."""
    first = _first_anchor_y(lines, header_y, footer_y)
    if first is None:
        return header_y
    crossing = [
        ln.rect.y1
        for ln in lines
        if header_y < ln.rect.cy < first and ln.rect.x0 < boundary < ln.rect.x1
    ]
    return max([header_y, *crossing])


def _column_of(rect: Rect, columns: list[tuple[float, float]]) -> int:
    for i, (a, b) in enumerate(columns):
        if a <= rect.cx < b:
            return i
    return len(columns) - 1


def _drawings(
    page: pymupdf.Page,
    header_y: float,
    footer_y: float,
    columns: list[tuple[float, float]],
    height: float,
) -> list[tuple[Rect, list[str], bool]]:
    """(영역, 그리기 명령 종류, 채움 여부). 머리말·꼬리말·구분선 제외."""
    out: list[tuple[Rect, list[str], bool]] = []
    col_w = min(b - a for a, b in columns)
    body_h = footer_y - header_y

    def separator(r: Rect) -> bool:
        return (r.w < 3 and r.h > body_h * 0.6) or (r.h < 3 and r.w > col_w * 0.85)

    for d in page.get_drawings():
        r = Rect.of(d["rect"])
        if r.y1 <= header_y or r.y0 >= footer_y or separator(r):
            continue
        items = d["items"]
        kinds = [str(item[0]) for item in items]
        if all(k == "l" for k in kinds) and len(items) > 1 and (r.w > col_w or r.h > body_h * 0.5):
            # 여러 선이 한 명령으로 묶인 큰 것 (실측: 쪽 위 가로선 + 단 구분 세로선 → 쪽 전체 크기가
            # 되어 쪽의 그림을 모두 하나로 이었다) → 선마다 따로, 구분선은 뺀다
            for item in items:
                a, b = item[1], item[2]
                seg = Rect(min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y))
                if not separator(seg) and header_y < seg.cy < footer_y:
                    out.append((seg, ["l"], False))
            continue
        out.append((r, kinds, d.get("fill") is not None))
    return out


def _cluster(rects: list[tuple[Rect, list[str], bool]]) -> list[tuple[Rect, list[str], int]]:
    """가까운 그리기 명령을 하나로 묶는다. (영역, 명령 종류 모음, 명령 수)."""
    groups: list[tuple[Rect, list[str], int]] = []
    for r, kinds, _ in rects:
        merged = (r, list(kinds), 1)
        changed = True
        while changed:
            changed = False
            for g in list(groups):
                if g[0].expand(CLUSTER_GAP).intersects(merged[0]):
                    groups.remove(g)
                    merged = (g[0].union(merged[0]), g[1] + merged[1], g[2] + merged[2])
                    changed = True
        groups.append(merged)
    return groups


def _is_text_box(rect: Rect, kinds: list[str], count: int, lines: list[Line]) -> bool:
    """테두리 사각형 하나 + 안에 글 2줄 이상 → 글 상자."""
    inside = [ln for ln in lines if rect.contains(ln.rect, tol=2)]
    return count <= 2 and all(k in ("re", "l") for k in kinds) and len(inside) >= 2


def _header_footer(
    page: pymupdf.Page, lines: list[Line], repeated: set[str]
) -> tuple[float, float]:
    """머리말 아래 경계, 꼬리말 위 경계.

    위치만으로 자르지 않는다 (다음 쪽 맨 위의 이어진 문항이 잘려 나감). 근거가 있을 때만:
    - 머리말: 위쪽 띠 안에서 ① 넓은 배너(채운 사각형) 안의 줄 ② 쪽 가운데를 가로지르는 줄
      ③ 여러 쪽에 똑같이 반복되는 줄
    - 꼬리말: 아래쪽 띠 안의 짧은 줄(쪽 번호) 또는 반복되는 줄
    """
    w, h = float(page.rect.width), float(page.rect.height)
    header_y, footer_y = 0.0, h
    banners = [
        Rect.of(d["rect"])
        for d in page.get_drawings()
        if d.get("fill") is not None
        and Rect.of(d["rect"]).w > w * 0.6
        and Rect.of(d["rect"]).y0 < h * HEADER_BAND
    ]
    for r in banners:
        header_y = max(header_y, r.y1 + 1)
    others: list[Line] = []
    for ln in lines:
        if ln.rect.cy < h * HEADER_BAND:
            in_banner = any(r.contains(ln.rect, tol=2) for r in banners)
            spans_middle = ln.rect.x0 < w / 2 < ln.rect.x1
            if in_banner or spans_middle or ln.text in repeated:
                header_y = max(header_y, ln.rect.y1 + 2)
            else:
                others.append(ln)
        elif ln.rect.cy > h * FOOTER_BAND and (len(ln.text) <= 8 or ln.text in repeated):
            footer_y = min(footer_y, ln.rect.y0 - 2)
    # 문항 번호 줄은 머리말이 아니다: 쪽마다 같은 자리의 "[총 10점]" 조각("[", "10", "점")이
    # '반복 줄'로 잡혀 그 위의 "2. …" 줄까지 머리말로 삼키던 문제
    # 배너(채운 사각형) 안이어도 마찬가지
    tops = [ln.rect.y0 for ln in lines
            if ln.rect.cy < h * HEADER_BAND and ln.rect.y0 < header_y
            and ln.text not in repeated and _anchor_label(ln.text)]  # fmt: skip
    if tops:
        header_y = min(tops) - 1
    return header_y, footer_y


def _page_info(page: pymupdf.Page, number: int, repeated: set[str]) -> PageInfo:
    w, h = float(page.rect.width), float(page.rect.height)
    lines = _lines(page, number)
    header_y, footer_y = _header_footer(page, lines, repeated)
    columns = _columns(lines, w, header_y, footer_y)
    if len(columns) == 2:
        header_y = _title_above_columns(lines, columns[0][1], header_y, footer_y)
    body_lines = [ln for ln in lines if header_y < ln.rect.cy < footer_y]

    figures: list[Figure] = []
    textboxes: list[TextBox] = []
    for rect, kinds, count in _cluster(_drawings(page, header_y, footer_y, columns, h)):
        if _is_text_box(rect, kinds, count, body_lines):
            textboxes.append(TextBox(number, rect))
            continue
        if rect.w < MIN_FIGURE and rect.h < MIN_FIGURE:
            continue
        # 그림 바로 곁의 짧은 글(꼭짓점 이름 A, B …)까지 포함
        for ln in body_lines:
            if any(c in CHOICE_MARKS for c in ln.text):
                continue  # 선지 기호는 그림이 아니라 선지 짝짓기에 쓴다
            if len(ln.text) <= 3 and rect.expand(12).intersects(ln.rect):
                rect = rect.union(ln.rect)
        figures.append(Figure(number, rect, "vector"))
    for info in page.get_image_info():
        r = Rect.of(info["bbox"])
        if header_y < r.cy < footer_y and (r.w >= MIN_FIGURE or r.h >= MIN_FIGURE):
            figures.append(Figure(number, r, "image"))

    marks: list[tuple[int, Rect]] = []
    for k, ch in enumerate(CHOICE_MARKS, start=1):
        for r in page.search_for(ch):
            rr = Rect.of(r)
            if header_y < rr.cy < footer_y:
                marks.append((k, rr))
    return PageInfo(
        number, w, h, columns, header_y, footer_y, body_lines, figures, textboxes, marks
    )


# ---------------------------------------------------------------- 문항 영역


def _anchor_label(text: str) -> str | None:
    m = _ANCHOR.match(text)
    if not m:
        return None
    if m.group(1):
        return f"{m.group(1)} {m.group(2)}"
    if m.group(3):
        return f"{m.group(3)} {m.group(4)}"
    return m.group(5)


def _anchors(pages: list[PageInfo]) -> tuple[list[Anchor], set[int]]:
    """(문항 번호 앵커, 문항 안 번호 목록 줄의 `id(line)`)."""
    found: list[tuple[Anchor, float]] = []
    for p in pages:
        for col, (a, b) in enumerate(p.columns):
            col_lines = sorted(
                (ln for ln in p.lines if _column_of(ln.rect, p.columns) == col),
                key=lambda ln: ln.rect.y0,
            )
            left = min((ln.rect.x0 for ln in col_lines), default=a)
            for ln in col_lines:
                label = _anchor_label(ln.text)
                if label is None or ln.rect.x0 > left + (b - a) * 0.12:
                    continue
                found.append((Anchor(p.number, col, label, ln), ln.rect.x0 - left))
    numbered = [(i, x) for i, (x, _) in enumerate(found) if x.label.isdigit()]
    inner = {
        numbered[k][0] for k in nested_lists([(int(x.label), found[i][1]) for i, x in numbered])
    }
    keep = number_chain([(i, int(x.label)) for i, x in numbered if i not in inner])
    anchors = [x for i, (x, _) in enumerate(found) if not x.label.isdigit() or i in keep]
    return anchors, {id(found[i][0].line) for i in inner}


def _fences(
    pages: list[PageInfo], anchors: list[Anchor], inner: set[int]
) -> list[tuple[int, int, float]]:
    """문항 영역을 끊는 곳 (쪽, 단, y): 문제 묶음 제목, 그리고 번호 줄기 밖에서 다시 시작하는 "1."
    (실측: 한 쪽에 "9. … 학습 과제 1. 2." — 9번 영역이 다음 묶음 문항까지 먹음).

    `inner`(문항 안 번호 목록의 "1.")는 새 묶음이 아니다."""
    chosen = {id(a.line) for a in anchors}
    first = min(((a.page, a.line.rect.y0) for a in anchors), default=None)
    out: list[tuple[int, int, float]] = []
    for p in pages:
        for ln in p.lines:
            col = _column_of(ln.rect, p.columns)
            if SET_MARK.match(ln.text) or (
                first is not None
                and id(ln) not in chosen
                and id(ln) not in inner
                and _anchor_label(ln.text) == "1"
                and (p.number, ln.rect.y0) > first
            ):
                out.append((p.number, col, ln.rect.y0))
    return out


NEST_INDENT = 2.0
"""문항 번호보다 이만큼(pt) 넘게 들여 쓴 번호 줄은 문항 안의 번호 목록일 수 있다.
실측한 시험지·교재의 문항 번호는 모두 단 왼쪽 끝(들여쓰기 0)."""


def nested_lists(cands: list[tuple[int, float]]) -> set[int]:
    """읽는 순서의 (번호, 들여쓰기) 중 문항 안의 번호 목록인 것의 위치.

    바깥 번호(가장 덜 들여 쓴 번호들)의 줄기가 둘 이상일 때, 그 줄기의 번호 뒤에 더 들여 쓴
    번호 줄이 이어지며 그 번호 이하로 다시 시작하면 ("3. … [실험 단서] 1. 2. 3. 4.") 그 줄들은
    문항이 아니다. 실측(공통형3회): 3번 안의 단서 1.~4.가 3번까지의 줄기보다 길어 문항 1~4번이
    되고, 1·2번 쪽은 어느 문항에도 들지 않았다.

    다시 시작하지 않는 들여 쓴 번호(왼쪽에 걸친 글 때문에 들여 쓴 것처럼 보이는 진짜 문항,
    오른쪽 정렬된 한 자리 번호)는 그대로 둔다."""
    if not cands:
        return set()
    margin = min(indent for _, indent in cands)
    outer = [i for i, (_, indent) in enumerate(cands) if indent <= margin + NEST_INDENT]
    if len(outer) == len(cands):
        return set()
    chain = number_chain([(i, cands[i][0]) for i in outer])
    if len(chain) < 2:
        return set()
    out: set[int] = set()
    last: int | None = None  # 가장 최근 바깥 줄기 번호
    run: list[int] = []
    for i, (n, _) in enumerate([*cands, (0, margin)]):  # 끝의 가짜 바깥 줄: 마지막 묶음 닫기
        if i < len(cands) and i not in outer:
            run.append(i)
            continue
        if run and last is not None and cands[run[0]][0] <= last:
            out.update(run)
        run = []
        if i in chain:
            last = n
    return out


def number_chain(cands: list[tuple[int, int]]) -> set[int]:
    """읽는 순서의 번호 후보 중 가장 길게 이어지는 줄기 (앞 번호 < 다음 ≤ 앞 + MAX_STEP).

    본문 속 "2." 같은 번호는 줄기에서 빠진다. 1번부터 시작하지 않아도 된다 (문제집 31번부터 등 —
    실측: 개념 설명의 "1.~4."를 문항으로 잡고 31~48번을 놓쳤다).
    """
    if not cands:
        return set()
    best = [1] * len(cands)
    prev = [-1] * len(cands)
    for i, (_, n) in enumerate(cands):
        for j in range(i):
            m = cands[j][1]
            # 길이가 같으면 읽는 순서상 가까운 앞 번호 (본문 속 "2." 뒤의 진짜 2번을 고르게)
            if m < n <= m + MAX_STEP and best[j] + 1 >= best[i]:
                best[i], prev[i] = best[j] + 1, j
    end = max(range(len(cands)), key=lambda i: (best[i], -i))
    out: set[int] = set()
    while end >= 0:
        out.add(cands[end][0])
        end = prev[end]
    return out


_KEY_MARK = re.compile(r"부분\s*점수|정답\s*[및과]\s*해설|빠른\s*정답|모범\s*답안|채점\s*기준")
"""조각난 PDF 글("부분 점수" / "[" / ": 1~5" / "]")에서도 잡히게 괄호·쌍점은 보지 않는다."""


def find_key_pages(path: Path) -> list[int]:
    """해설지 쪽 (1부터): "[부분 점수: …]", "정답 및 해설" 같은 표시가 있는 쪽.

    시험지 PDF 뒤에 해설을 붙여 두는 경우가 흔하다 — 문항 영역에서 빼고 해설지로 읽는다.
    """
    with pymupdf.open(path) as doc:
        return [
            i + 1
            for i in range(doc.page_count)
            if _KEY_MARK.search(str(doc.load_page(i).get_text()))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        ]


def page_count(path: Path) -> int:
    with pymupdf.open(path) as doc:
        return int(doc.page_count)


def analyze(path: Path, *, exclude: Collection[int] = ()) -> DocumentLayout:
    """`exclude`: 문항 영역에서 뺄 쪽 (해설지 쪽 등)."""
    doc = pymupdf.open(path)
    with doc:
        # 여러 쪽 위·아래 띠에 똑같이 나오는 줄 (쪽마다 반복되는 머리말·꼬리말)
        bands: dict[str, int] = {}
        for i in range(doc.page_count):
            pg = doc.load_page(i)
            h = float(pg.rect.height)
            # 번호만 있는 줄("2.")은 여러 쪽 같은 자리에 나와도 머리말이 아니다
            # (실측: 쪽 맨 위 문항이 잘림). "1. 속도와 가속도" 같은 챕터 머리말은 그대로 반복 줄
            seen = {
                ln.text
                for ln in _lines(pg, i + 1)
                if (ln.rect.cy < h * HEADER_BAND or ln.rect.cy > h * FOOTER_BAND)
                and not _BARE_NUMBER.match(ln.text)
            }
            for s in seen:
                bands[s] = bands.get(s, 0) + 1
        repeated = {s for s, n in bands.items() if n >= 2 and doc.page_count >= 2}
        pages = [_page_info(doc.load_page(i), i + 1, repeated) for i in range(doc.page_count)]
    total_chars = sum(len(ln.text) for p in pages for ln in p.lines)
    scanned = total_chars < 15 * max(1, len(pages))  # 스캔본은 글자 정보가 거의 0
    layout = DocumentLayout(path=path, pages=pages, regions=[], scanned=scanned)
    if scanned:
        layout.warnings.append("글자 정보가 거의 없는 PDF (스캔본) — 이미지로만 판독해야 합니다.")
        return layout

    question_pages = [p for p in pages if p.number not in exclude]
    anchors, inner = _anchors(question_pages)
    if not anchors:
        layout.warnings.append("문항 번호를 찾지 못했습니다.")
        return layout
    fences = _fences(question_pages, anchors, inner)

    regions: list[Region] = []
    current: Region | None = None
    for p in question_pages:
        for col, (a, b) in enumerate(p.columns):
            col_anchors = [x for x in anchors if x.page == p.number and x.column == col]
            # 앵커(새 문항)와 울타리(묶음 제목 등 — 그 뒤는 앞 문항이 아님)를 위에서부터
            marks: list[tuple[float, Anchor | None]] = []
            marks += [(x.line.rect.y0 - 2, x) for x in col_anchors]
            marks += [(y - 2, None) for pg, c, y in fences if pg == p.number and c == col]
            marks.sort(key=lambda m: m[0])
            cuts = [p.header_y, *[y for y, _ in marks], p.footer_y]
            # 첫 앵커 위쪽 = 앞 문항의 이어짐
            segments: list[tuple[Region | None, float, float]] = []
            segments.append((current, cuts[0], cuts[1]))
            for k, (_, anc) in enumerate(marks):
                if anc is None:
                    current = None
                else:
                    current = Region(label=anc.label)
                    regions.append(current)
                segments.append((current, cuts[k + 1], cuts[k + 2]))
            for region, y0, y1 in segments:
                box = Rect(a, y0, b, y1)
                content = [ln for ln in p.lines if box.contains_point(ln.rect.cx, ln.rect.cy)]
                figs = [f for f in p.figures if box.contains_point(f.rect.cx, f.rect.cy)]
                if not content and not figs:
                    continue
                if region is None:
                    if y0 <= cuts[0] + 1:
                        layout.warnings.append(
                            f"{p.number}쪽 첫 문항 앞의 내용은 어느 문항에도 속하지 않음"
                        )
                    continue
                bottom = max([ln.rect.y1 for ln in content] + [f.rect.y1 for f in figs]) + 4
                top = min([ln.rect.y0 for ln in content] + [f.rect.y0 for f in figs]) - 4
                region.boxes.append((p.number, Rect(a, max(y0, top), b, min(y1, bottom))))
                region.lines += content
                region.figures += figs
                region.textboxes += [
                    t for t in p.textboxes if box.contains_point(t.rect.cx, t.rect.cy)
                ]

    for r in regions:
        r.points = marked_points(_reading_text(r.lines))
        r.figures.sort(key=lambda f: (f.page, round(f.rect.y0 / 20), f.rect.x0))
        for i, f in enumerate(r.figures, start=1):
            f.id = f"F{i}"
            f.near_choice = _near_choice(f, r, pages[f.page - 1])
    layout.regions = regions
    return layout


def _reading_text(lines: list[Line]) -> str:
    """줄 조각을 읽는 순서로: 같은 높이의 조각은 한 줄로 묶어 왼쪽부터.

    PDF가 "[총 10점]"을 "[", "10", "점", "]" 조각으로 따로 두면 추출 순서가 뒤섞인다.
    """
    rows: list[list[Line]] = []
    for ln in sorted(lines, key=lambda x: (x.page, x.rect.y0)):
        row = rows[-1] if rows else None
        if (
            row
            and row[0].page == ln.page
            and min(row[0].rect.y1, ln.rect.y1) - max(row[0].rect.y0, ln.rect.y0)
            > 0.5 * min(row[0].rect.h, ln.rect.h)
        ):
            row.append(ln)
        else:
            rows.append([ln])
    return "\n".join(" ".join(x.text for x in sorted(r, key=lambda x: x.rect.x0)) for r in rows)


def _near_choice(f: Figure, r: Region, page: PageInfo) -> int | None:
    """그림 왼쪽 위에 붙어 있는 ①~⑤ 기호 (그림 선지)."""
    best: tuple[float, int] | None = None
    for k, m in page.choice_marks:
        if not any(p == f.page and box.contains_point(m.cx, m.cy) for p, box in r.boxes):
            continue
        dx = f.rect.x0 - m.x1
        dy = abs(m.cy - f.rect.y0) if m.cy < f.rect.y0 else 0.0
        if -5 <= dx <= 40 and m.cy <= f.rect.cy and dy <= 25:
            d = dx + dy
            if best is None or d < best[0]:
                best = (d, k)
    return best[1] if best else None


def crop_region(path: Path, label: str, boxes: list[tuple[int, Rect]]) -> DocumentLayout:
    """강사가 원본 쪽에서 끌어 지정한 상자들(쪽, PDF 좌표) → 그 상자만으로 된 문항 영역 하나.

    레이아웃이 문항을 잘못 나눴을 때 (실측 공통형3회: 1·2번 쪽이 어느 문항에도 들지 않음)
    강사가 정한 영역을 그대로 쓴다. 글·그림·글 상자·선지 기호는 `analyze`와 같은 쪽 분석에서
    상자 안의 것만."""
    doc = pymupdf.open(path)
    with doc:
        infos = {
            p: _page_info(doc.load_page(p - 1), p, set()) for p in sorted({p for p, _ in boxes})
        }
    region = Region(label=label)
    for p, box in boxes:
        info = infos[p]
        region.boxes.append((p, box))
        region.lines += [ln for ln in info.lines if box.contains_point(ln.rect.cx, ln.rect.cy)]
        region.figures += [f for f in info.figures if box.contains_point(f.rect.cx, f.rect.cy)]
        region.textboxes += [t for t in info.textboxes if box.contains_point(t.rect.cx, t.rect.cy)]
    region.points = marked_points(_reading_text(region.lines))
    order = {p: k for k, (p, _) in enumerate(boxes)}  # 강사가 끈 순서의 상자, 그 안에서는 읽는 순서
    region.figures.sort(key=lambda f: (order[f.page], round(f.rect.y0 / 20), f.rect.x0))
    for i, f in enumerate(region.figures, start=1):
        f.id = f"F{i}"
        f.near_choice = _near_choice(f, region, infos[f.page])
    return DocumentLayout(path=path, pages=list(infos.values()), regions=[region], scanned=False)


def clip_text(path: Path, boxes: list[tuple[int, Rect]]) -> str:
    """상자 안의 PDF 글자 (해설 영역 — 한글 문장 근거)."""
    doc = pymupdf.open(path)
    with doc:
        return "\n".join(
            str(doc.load_page(p - 1).get_text(clip=pymupdf.Rect(r.x0, r.y0, r.x1, r.y1)))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            for p, r in boxes
        )


def page_sizes(path: Path) -> list[tuple[float, float]]:
    """쪽마다 (너비, 높이) PDF 좌표 — 화면의 상자(쪽 비율)를 PDF 좌표로 바꿀 때."""
    with pymupdf.open(path) as doc:
        return [(float(pg.rect.width), float(pg.rect.height)) for pg in doc]


def render_clip(
    path: Path, page: int, rect: Rect, out: Path, *, dpi: int = 200, pad: float = 4
) -> Path:
    """페이지의 한 부분을 PNG로 (그림 크롭·문항 영역 이미지)."""
    doc = pymupdf.open(path)
    with doc:
        pg = doc.load_page(page - 1)
        clip = pymupdf.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad) & pg.rect
        pg.get_pixmap(dpi=dpi, clip=clip).save(str(out))
    return out


def render_page(path: Path, page: int, out: Path, *, dpi: int = 150) -> Path:
    doc = pymupdf.open(path)
    with doc:
        doc.load_page(page - 1).get_pixmap(dpi=dpi).save(str(out))
    return out
