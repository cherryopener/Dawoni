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
    r"^\s*(?:\[\s*(서술형|단답형)\s*(\d+)\s*\]|(서술형|단답형)\s*(\d+)\s*[.)]|(\d{1,2})\s*[.．])"
)
_POINTS = re.compile(r"\[\s*(\d+(?:\.\d+)?)\s*점\s*\]")


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
    if len(body) < 4:
        return [(0.0, width)]
    best_x, best_cross = width / 2, len(body) + 1
    x = width * 0.35
    while x <= width * 0.65:
        cross = sum(1 for ln in body if ln.rect.x0 < x < ln.rect.x1)
        if cross < best_cross:
            best_x, best_cross = x, cross
        x += 2
    left = sum(1 for ln in body if ln.rect.x1 <= best_x)
    right = sum(1 for ln in body if ln.rect.x0 >= best_x)
    if (
        best_cross <= max(1, len(body) * 0.05)
        and left >= len(body) * 0.2
        and right >= len(body) * 0.2
    ):
        return [(0.0, best_x), (best_x, width)]
    return [(0.0, width)]


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
    for d in page.get_drawings():
        r = Rect.of(d["rect"])
        if r.y1 <= header_y or r.y0 >= footer_y:
            continue
        if r.w < 3 and r.h > (footer_y - header_y) * 0.6:  # 단 구분선
            continue
        if r.h < 3 and r.w > col_w * 0.85:  # 가로 구분선
            continue
        kinds = [str(item[0]) for item in d["items"]]
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
    for ln in lines:
        if ln.rect.cy < h * HEADER_BAND:
            in_banner = any(r.contains(ln.rect, tol=2) for r in banners)
            spans_middle = ln.rect.x0 < w / 2 < ln.rect.x1
            if in_banner or spans_middle or ln.text in repeated:
                header_y = max(header_y, ln.rect.y1 + 2)
        elif ln.rect.cy > h * FOOTER_BAND and (len(ln.text) <= 8 or ln.text in repeated):
            footer_y = min(footer_y, ln.rect.y0 - 2)
    return header_y, footer_y


def _page_info(page: pymupdf.Page, number: int, repeated: set[str]) -> PageInfo:
    w, h = float(page.rect.width), float(page.rect.height)
    lines = _lines(page, number)
    header_y, footer_y = _header_footer(page, lines, repeated)
    columns = _columns(lines, w, header_y, footer_y)
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


def _anchors(pages: list[PageInfo]) -> list[Anchor]:
    found: list[Anchor] = []
    last_number = 0
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
                if label.isdigit():
                    n = int(label)
                    if n <= last_number or n > last_number + 5:
                        continue  # 번호 흐름에 맞지 않음 (본문 속 "2." 등)
                    last_number = n
                found.append(Anchor(p.number, col, label, ln))
    return found


def analyze(path: Path) -> DocumentLayout:
    doc = pymupdf.open(path)
    with doc:
        # 여러 쪽 위·아래 띠에 똑같이 나오는 줄 (쪽마다 반복되는 머리말·꼬리말)
        bands: dict[str, int] = {}
        for i in range(doc.page_count):
            pg = doc.load_page(i)
            h = float(pg.rect.height)
            seen = {
                ln.text
                for ln in _lines(pg, i + 1)
                if ln.rect.cy < h * HEADER_BAND or ln.rect.cy > h * FOOTER_BAND
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

    anchors = _anchors(pages)
    if not anchors:
        layout.warnings.append("문항 번호를 찾지 못했습니다.")
        return layout

    regions: list[Region] = []
    current: Region | None = None
    for p in pages:
        for col, (a, b) in enumerate(p.columns):
            col_anchors = [x for x in anchors if x.page == p.number and x.column == col]
            starts = [x.line.rect.y0 - 2 for x in col_anchors]
            cuts = [p.header_y, *starts, p.footer_y]
            # 첫 앵커 위쪽 = 앞 문항의 이어짐
            segments: list[tuple[Region | None, float, float]] = []
            segments.append((current, cuts[0], cuts[1]))
            for k, anc in enumerate(col_anchors):
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
        m = _POINTS.search(r.text)
        r.points = float(m.group(1)) if m else None
        r.figures.sort(key=lambda f: (f.page, round(f.rect.y0 / 20), f.rect.x0))
        for i, f in enumerate(r.figures, start=1):
            f.id = f"F{i}"
            f.near_choice = _near_choice(f, r, pages)
    layout.regions = regions
    return layout


def _near_choice(f: Figure, r: Region, pages: list[PageInfo]) -> int | None:
    """그림 왼쪽 위에 붙어 있는 ①~⑤ 기호 (그림 선지)."""
    page = pages[f.page - 1]
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
