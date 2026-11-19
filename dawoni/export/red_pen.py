# pyright: basic, reportMissingTypeStubs=false
"""채점 답안지 PDF — 학생 답안지 스캔 위에 빨간 글씨로 점수·총점·감점 이유 (코드만, 토큰 0).

2026-11-18 사용자 요청: Claude 대화에서 만든 채점 답안지
(`real_data/docs/MSS1-J_물리_채점답안지.pdf`)처럼 표지 오른쪽 위에 총점, 표지 점수 칸(1번·2번·3번)에
문항 점수, 답안 쪽 점수 칸에 소문항 점수, 답안 칸 빈자리에 짧은 이유("정답 → 4점", "1점 (−2: …)")를
적는다. 글씨는 컴퓨터 글꼴, 식은 제대로 그린다.

- 원본: 판독할 때 세워 둔 쪽 그림(`scans/<학생>/page_NN.png`, 200dpi) — 원본 PDF는 작업공간에
  남지 않는다. JPEG로 줄여 넣는다.
- 자리 (AI 없이): 답안지는 인쇄된 표(번호 | 답안 | 배점 | 점수)다. 긴 줄을 찾아(기울기 바로잡기
  포함) 맨 오른쪽 칸을 점수 칸으로, 맨 왼쪽(번호) 칸에서 시작하는 가로줄을 문항 경계로, 번호 칸
  오른쪽에서 시작하는 줄을 소문항 경계로 본다. 문항은 시험 순서대로, 소문항은 수가 맞을 때 차례로
  잇는다(안 맞으면 문항 전체를 한 덩어리로 — 여러 점수 칸에 걸치면 괄호로 묶는다).
  표지 점수 칸은 문항 수 + 1개의 같은 폭·같은 간격 가로줄. 못 찾으면 총점 아래에 "1번 9 · …"로.
- 이유 자리: 그 칸 안에서 잉크가 가장 적은 곳 (같으면 오른쪽 아래). 이유는 채점의 `pen` 메모
  (없으면 감점 이유를 줄여서).
- 수식: ziamath(LaTeX → SVG, STIX Two Math) → PyMuPDF가 PDF 조각으로 (벡터). 글은 이 PC의
  한글 글꼴 (Windows 맑은 고딕, 맥 Apple SD 산돌고딕 Neo).
"""

from __future__ import annotations

import io
import itertools
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

from dawoni.export.prose import prose_pieces
from dawoni.export.sheet import part_grades
from dawoni.grading.store import ExamStore, safe_name
from dawoni.models.grading import Exam, ExamQuestion, GradeResult, Rubric

log = logging.getLogger(__name__)

RED = (0.84, 0.0, 0.0)
RED_HEX = "#d60000"
DPI = 200
"""판독 쪽 그림의 해상도 (`ocr.pages.PDF_DPI`) — 그림 픽셀 → PDF 포인트."""
JPEG_QUALITY = 70

FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/malgun.ttf"),
    Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
    Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
)


# ---------------------------------------------------------------- 칸 찾기


@dataclass(frozen=True)
class Box:
    """세운 쪽 그림의 픽셀 자리."""

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

    def inset(self, d: float) -> Box:
        return Box(self.x0 + d, self.y0 + d, self.x1 - d, self.y1 - d)


Seg = tuple[int, int, int]
"""(자리, 시작, 끝): 가로줄이면 (y, x0, x1), 세로줄이면 (x, y0, y1)."""


def _skew(gray: Image.Image) -> float:
    """가로줄이 가장 곧게 서는 각도 (−2°…2°, 0.05° 간격)."""
    small = gray.resize((max(1, gray.width // 4), max(1, gray.height // 4)))
    best, angle = -1.0, 0.0
    for a in np.arange(-2.0, 2.01, 0.05):
        turned = small.rotate(float(a), resample=Image.Resampling.BILINEAR, fillcolor=255)
        d = np.asarray(turned) < 200
        rows = d.sum(axis=1).astype(np.float64)
        score = float((rows**2).sum())
        if score > best:
            best, angle = score, float(a)
    return angle


def _segments(mask: np.ndarray, min_len: int, gap: int) -> list[Seg]:
    """행마다 빈틈 `gap` 이하를 이은 어두운 구간 중 `min_len` 이상 (위아래 1픽셀까지 한 줄로)."""
    band = mask.copy()
    band[1:] |= mask[:-1]
    band[:-1] |= mask[1:]
    found: list[Seg] = []
    for y in range(band.shape[0]):
        xs = np.flatnonzero(band[y])
        if xs.size < min_len:
            continue
        br = np.flatnonzero(np.diff(xs) > gap)
        for a, b in zip(np.r_[xs[0], xs[br + 1]], np.r_[xs[br], xs[-1]], strict=True):
            if b - a >= min_len:
                found.append((y, int(a), int(b)))
    found.sort()
    out: list[list[int]] = []  # [첫 행, x0, x1, 끝 행]
    for p, a, b in found:
        last = out[-1] if out else None
        if last and p - last[3] <= 3 and min(b, last[2]) > max(a, last[1]):
            last[1], last[2], last[3] = min(a, last[1]), max(b, last[2]), p
        else:
            out.append([p, a, b, p])
    return [((p + q) // 2, a, b) for p, a, b, q in out]


def _cluster(segs: list[Seg], tol: int) -> list[Seg]:
    """같은 자리(±tol)의 줄을 하나로: 자리는 가운데, 구간은 합친 것."""
    out: list[list[int]] = []
    for p, a, b in sorted(segs):
        if out and p - out[-1][0] <= tol:
            o = out[-1]
            o[0], o[1], o[2] = (o[0] + p) // 2, min(a, o[1]), max(b, o[2])
        else:
            out.append([p, a, b])
    return [(p, a, b) for p, a, b in out]


@dataclass
class Scan:
    """세운 쪽 그림 하나: 기울기를 바로잡은 회색 그림과, 그 좌표를 원래 그림 좌표로 되돌리는
    각도."""

    image: Image.Image
    gray: np.ndarray
    angle: float

    @classmethod
    def open(cls, path: Path) -> Scan:
        im = Image.open(path).convert("RGB")
        g = im.convert("L")
        angle = _skew(g)
        flat = g.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=255) if angle else g
        return cls(im, np.asarray(flat), angle)

    def back(self, b: Box) -> Box:
        """바로잡은 좌표 → 원래 그림 좌표 (네 꼭짓점을 돌려 감싸는 상자)."""
        if not self.angle:
            return b
        cx, cy = self.image.width / 2, self.image.height / 2
        t = np.deg2rad(self.angle)  # PIL rotate(a)는 반시계 a도 → 되돌리기는 시계 a도
        c, s = np.cos(t), np.sin(t)
        xs, ys = [], []
        for x, y in ((b.x0, b.y0), (b.x1, b.y0), (b.x0, b.y1), (b.x1, b.y1)):
            dx, dy = x - cx, y - cy
            xs.append(cx + c * dx - s * dy)
            ys.append(cy + s * dx + c * dy)
        return Box(min(xs), min(ys), max(xs), max(ys))

    def ink(self) -> np.ndarray:
        """원래 그림 좌표의 잉크(어두운 곳) 지도."""
        return np.asarray(self.image.convert("L")) < 170


@dataclass
class Table:
    """답안지 표: 세로 칸 경계와, 점수 칸까지 닿는 가로줄 (높이, 수준 0=문항 1=소문항 2=그 안)."""

    cols: list[int]
    rules: list[tuple[int, int]]

    @property
    def score(self) -> tuple[int, int]:
        return self.cols[-2], self.cols[-1]

    @property
    def answer(self) -> tuple[int, int]:
        """번호 칸 오른쪽 ~ 배점 칸 왼쪽."""
        return self.cols[1], self.cols[-3]


def find_table(scan: Scan) -> Table | None:
    """번호 | 답안 | 배점 | 점수 표. 세로줄 5개 이상(표 높이의 60% 넘게), 점수 칸을 지나는
    가로줄 3개 이상."""
    dark = scan.gray < 200
    h, w = dark.shape
    vs = _cluster(_segments(dark.T, h // 10, 8), 8)
    if not vs:
        return None
    tall = max(b - a for _, a, b in vs)
    cols = sorted(x for x, a, b in vs if b - a >= 0.6 * tall)
    if len(cols) < 5:
        return None
    left, right = cols[0], cols[-1]
    hs = _cluster(_segments(dark, w // 8, 8), 8)
    spans = [(y, a) for y, a, b in hs if b >= right - 20 and a <= cols[-2] - 10]
    if len(spans) < 3:
        return None
    rules: list[tuple[int, int]] = []
    for y, a in spans:
        level = 0 if a <= left + 25 else 1 if a <= cols[1] + 25 else 2
        rules.append((y, level))
    return Table(cols, rules)


@dataclass
class Slot:
    """점수를 적을 한 덩어리 — 소문항, 또는 소문항을 나누지 못한 문항 전체."""

    question: str
    parts: list[str]
    """이 자리의 소문항 (보통 하나). 비었으면 문항 전체, 여럿이면 그 소문항들의 합 — 칸이 소문항마다
    나뉘지 않은 덩어리 (예: 3-(2) 칸 하나에 실험결과 1·2·3 소문항)."""
    cells: list[Box]
    """점수 칸 (여러 줄에 걸치면 여럿)."""
    answer: Box
    """답안 칸 — 이유를 쓸 빈자리를 여기서 찾는다."""


_TOP = re.compile(r"\s*(\(?[0-9A-Za-z가-힣ㄱ-ㅎ㉠-㉯①-⑳]{1,3}\)?)")


def _top(label: str) -> str:
    """소문항 표시의 맨 앞 칸 — "(2)[실험결과 1]" → "(2)", "1)" → "1)", "A" → "A"."""
    m = _TOP.match(label)
    return m.group(1) if m else label.strip()


Rows = tuple[int, int]
"""표 줄 번호 (문항 안에서): 이 줄부터 저 줄까지가 한 칸."""


def _plan(labels: list[str], groups: list[Rows]) -> list[tuple[list[str], Rows]] | None:
    """소문항 표시 → 표의 소문항 칸. 수가 같으면 하나씩. 아니면 표시의 맨 앞으로 묶어 칸 수와
    맞추고(3-(2)[실험결과 1]·[실험결과 2]… → (2) 칸), 그 칸 안이 점수 칸까지 소문항 수만큼
    나뉘었으면 하나씩, 아니면 그 소문항들의 합으로. 맞출 수 없으면 None (문항 전체 한 자리)."""
    if not labels or not groups:
        return None
    if len(labels) == len(groups):
        return [([label], g) for label, g in zip(labels, groups, strict=True)]
    tops = list(dict.fromkeys(_top(x) for x in labels))
    if len(tops) != len(groups):
        return None
    plan: list[tuple[list[str], Rows]] = []
    for top, (i, j) in zip(tops, groups, strict=True):
        mine = [x for x in labels if _top(x) == top]
        if len(mine) > 1 and j - i == len(mine):
            plan += [([x], (i + k, i + k + 1)) for k, x in enumerate(mine)]
        else:
            plan.append((mine, (i, j)))
    return plan


def rubric_parts(rubric: Rubric | None) -> list[str]:
    """루브릭 항목의 소문항 표시 (차례대로) — 시험에 소문항이 없는데 루브릭이 A·B로 나눈 문항."""
    return list(dict.fromkeys(i.part for i in (rubric.items if rubric else []) if i.part))


def table_slots(
    table: Table, questions: list[ExamQuestion], fallback: dict[str, list[str]] | None = None
) -> tuple[list[Slot], int]:
    """표의 문항 칸 → 점수 자리. 앞에서부터 문항을 차례로 채우고, 쓴 문항 수를 돌려준다.
    `fallback`: 시험에 소문항이 없는 문항의 소문항 표시 (루브릭에서, `rubric_parts`) — 2026-11-19
    사용자 요청: 소문항이 여럿인 문항도 소문항마다 점수를."""
    rules = table.rules
    heights = [b[0] - a[0] for a, b in itertools.pairwise(rules)]
    start = 0
    if len(heights) >= 2 and heights[0] < 0.6 * float(np.median(heights[1:])):
        start = 1  # 머리 줄 (번호 | 답안 | 배점 | 점수)
    body = rules[start:]
    qtops = [i for i, (_, lv) in enumerate(body) if lv == 0]
    slots: list[Slot] = []
    used = 0
    s0, s1 = table.score
    a0, a1 = table.answer
    for top, bottom in itertools.pairwise(qtops):
        if used >= len(questions):
            break
        q = questions[used]
        used += 1
        inside = body[top : bottom + 1]
        subs = [i for i, (_, lv) in enumerate(inside) if lv <= 1]
        labels = [p.label for p in q.parts] or (fallback or {}).get(q.number, [])
        plan = _plan(labels, list(itertools.pairwise(subs))) or [([], (0, len(inside) - 1))]
        for parts, (i, j) in plan:
            ys = [y for y, _ in inside[i : j + 1]]
            cells = [Box(s0, y, s1, z) for y, z in itertools.pairwise(ys)]
            slots.append(Slot(q.number, parts, cells, Box(a0, ys[0], a1, ys[-1])))
    return slots, used


def find_cover_cells(scan: Scan, n: int) -> list[Box] | None:
    """표지의 문항 점수 칸: 폭·자리가 같은 가로줄 n+1개가 고르게 (n = 문항 수). 칸은 표의
    오른쪽 64%."""
    dark = scan.gray < 200
    w = dark.shape[1]
    hs = _cluster(_segments(dark, w // 14, 6), 6)
    hs = [s for s in hs if s[2] - s[1] < w * 0.45]
    best: list[Seg] | None = None
    for i, (y, a, b) in enumerate(hs):
        group = [(y, a, b)]
        for s in hs[i + 1 :]:
            if abs(s[1] - a) <= 20 and abs(s[2] - b) <= 20:
                group.append(s)
        if len(group) < n + 1:
            continue
        group = group[: n + 1]
        gaps = [q[0] - p[0] for p, q in itertools.pairwise(group)]
        if min(gaps) < 20 or max(gaps) > 1.25 * min(gaps):
            continue
        if best is None or group[0][0] < best[0][0]:
            best = group
    if best is None:
        return None
    x0 = float(np.median([s[1] for s in best]))
    x1 = float(np.median([s[2] for s in best]))
    split = x0 + (x1 - x0) * 0.36
    return [scan.back(Box(split, p[0], x1, q[0])) for p, q in itertools.pairwise(best)]


def free_spot(ink: np.ndarray, area: Box, size: tuple[float, float], step: int = 6) -> Box | None:
    """`area` 안에서 `size`(폭, 높이) 상자를 둘 자리 — 잉크가 가장 적고, 같으면 오른쪽 아래."""
    w, h = int(size[0]) + 1, int(size[1]) + 1
    x0, y0 = int(max(area.x0, 0)), int(max(area.y0, 0))
    x1, y1 = int(min(area.x1, ink.shape[1])), int(min(area.y1, ink.shape[0]))
    if x1 - x0 < w or y1 - y0 < h:
        return None
    sub = ink[y0:y1, x0:x1].astype(np.int32)
    integ = np.zeros((sub.shape[0] + 1, sub.shape[1] + 1), dtype=np.int64)
    integ[1:, 1:] = sub.cumsum(0).cumsum(1)
    ys = np.arange(0, sub.shape[0] - h + 1, step)
    xs = np.arange(0, sub.shape[1] - w + 1, step)
    yy, xx = np.meshgrid(ys, xs, indexing="ij")
    sums = integ[yy + h, xx + w] - integ[yy, xx + w] - integ[yy + h, xx] + integ[yy, xx]
    tol = max(3, int(0.002 * w * h))
    ok = sums <= sums.min() + tol
    # 오른쪽 아래를 먼저: 아래쪽 행부터, 그 안에서 오른쪽
    iy, ix = np.nonzero(ok)
    k = int(np.lexsort((ix, iy))[-1])
    bx, by = int(xs[ix[k]]) + x0, int(ys[iy[k]]) + y0
    return Box(bx, by, bx + w, by + h)


# ---------------------------------------------------------------- 글·식 쓰기


@lru_cache(maxsize=1)
def korean_font() -> pymupdf.Font:
    for p in FONT_CANDIDATES:
        if p.is_file():
            try:
                return pymupdf.Font(fontfile=str(p))
            except RuntimeError:
                continue
    log.warning("한글 글꼴을 찾지 못해 PDF 기본 한글 글꼴을 씁니다 (%s)", sys.platform)
    return pymupdf.Font("korea")


def _width(text: str, size: float) -> float:
    """글 폭 (포인트). PyMuPDF 형식 정보는 글자 크기를 int로 적었지만 소수도 받는다."""
    return float(korean_font().text_length(text, size))  # pyright: ignore[reportArgumentType]


def _put(tw: pymupdf.TextWriter, at: tuple[float, float], text: str, size: float) -> None:
    tw.append(at, text, font=korean_font(), fontsize=size)  # pyright: ignore[reportArgumentType]


@lru_cache(maxsize=512)
def _math(latex: str, size: float) -> tuple[bytes, float, float, float] | None:
    """LaTeX → (PDF 조각, 폭, 높이, 기준선 위 높이). 못 그리면 None (글로 쓴다)."""
    import ziamath as zm

    zm.config.svg2 = False  # pyright: ignore[reportPrivateImportUsage]  # MuPDF는 SVG2 `href`를 읽지 못한다 (xlink:href로)
    try:
        m = zm.Math.fromlatex(latex, size=size, color=RED_HEX)  # pyright: ignore[reportPrivateImportUsage]
        w, h = m.getsize()
        svg = m.svg()
        pdf = pymupdf.open(stream=svg.encode("utf-8"), filetype="svg").convert_to_pdf()
    except Exception:  # 변환기가 모르는 명령 등
        log.warning("식을 그리지 못해 글로 씁니다: %r", latex)
        return None
    return pdf, float(w), float(h), float(-m.getyofst())


@dataclass
class _Run:
    text: str
    math: bool
    w: float
    up: float
    down: float


def _plain(s: str) -> str:
    """글꼴에 없는 글자를 비슷한 글자로 (맥 산돌고딕에는 U+2212 빼기가 없다)."""
    f = korean_font()
    return "".join(c if f.has_glyph(ord(c)) else {"−": "-", "×": "x"}.get(c, c) for c in s)


def _runs(text: str, size: float) -> list[_Run]:
    out: list[_Run] = []
    for piece, is_math in prose_pieces(text):
        if is_math and (m := _math(piece, size)) is not None:
            out.append(_Run(piece, True, m[1], m[3], m[2] - m[3]))
            continue
        for word in _words(_plain(piece)):
            out.append(_Run(word, False, _width(word, size), size * 0.88, size * 0.24))
    return out


def _words(s: str) -> list[str]:
    """띄어쓰기 뒤에서 끊을 수 있게: '정답은 ' '4점' …"""
    out: list[str] = []
    cur = ""
    for c in s:
        cur += c
        if c == " ":
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


@dataclass
class Layout:
    lines: list[list[tuple[float, _Run]]] = field(default_factory=lambda: [])
    widths: list[float] = field(default_factory=lambda: [])
    ups: list[float] = field(default_factory=lambda: [])
    downs: list[float] = field(default_factory=lambda: [])
    size: float = 10.0
    cut: bool = False

    @property
    def w(self) -> float:
        return max(self.widths, default=0.0)

    @property
    def h(self) -> float:
        return sum(self.ups) + sum(self.downs) + 0.2 * self.size * max(0, len(self.lines) - 1)


def layout(text: str, size: float, max_w: float, max_lines: int) -> Layout:
    """글·식을 줄로 나눈다 (포인트). 넘치면 마지막 줄 끝을 '…'로."""
    out = Layout(size=size)
    line: list[tuple[float, _Run]] = []
    x = 0.0
    for run in _runs(text, size):
        if line and x + run.w > max_w and not run.text.isspace():
            out.lines.append(line)
            line, x = [], 0.0
        if not line and not run.math:
            run = _Run(run.text.lstrip(), False, _width(run.text.lstrip(), size),
                       run.up, run.down)  # fmt: skip
        line.append((x, run))
        x += run.w
    if line:
        out.lines.append(line)
    if len(out.lines) > max_lines:
        out.lines = out.lines[:max_lines]
        out.cut = True
    for ln in out.lines:
        out.widths.append(max((x + r.w for x, r in ln), default=0.0))
        out.ups.append(max((r.up for _, r in ln), default=size * 0.88))
        out.downs.append(max((r.down for _, r in ln), default=size * 0.24))
    return out


def draw(page: pymupdf.Page, lay: Layout, at: tuple[float, float]) -> None:
    """`at` = 왼쪽 위 (포인트)."""
    tw = pymupdf.TextWriter(page.rect)
    y = at[1]
    for i, ln in enumerate(lay.lines):
        base = y + lay.ups[i]
        for j, (x, r) in enumerate(ln):
            last = lay.cut and i == len(lay.lines) - 1 and j == len(ln) - 1
            text = r.text + ("…" if last else "")
            if r.math and (m := _math(r.text, lay.size)) is not None:
                src = pymupdf.open("pdf", m[0])
                top = base - m[3]
                page.show_pdf_page(pymupdf.Rect(at[0] + x, top, at[0] + x + m[1], top + m[2]),
                                   src, 0)  # fmt: skip
            else:
                _put(tw, (at[0] + x, base), text, lay.size)
        y = base + lay.downs[i] + 0.2 * lay.size
    tw.write_text(page, color=RED)


def draw_centered(page: pymupdf.Page, text: str, rect: pymupdf.Rect, size: float) -> None:
    w = _width(text, size)
    tw = pymupdf.TextWriter(page.rect)
    base = (rect.y0 + rect.y1) / 2 + size * 0.34
    _put(tw, ((rect.x0 + rect.x1 - w) / 2, base), text, size)
    tw.write_text(page, color=RED)


# ---------------------------------------------------------------- 무엇을 쓰나


def pts(v: float) -> str:
    return f"{v:g}"


def slot_note(slot: Slot, g: GradeResult, rubric: Rubric) -> tuple[float, float, str]:
    """(받은 점수, 배점, 이유 글). 만점이면 '정답 → 4점', 아니면 '1점 (−2: 감점 이유) …'."""
    part_of = {i.id: i.part for i in rubric.items}
    want = set(slot.parts)
    js = [j for j in g.judgements if not want or part_of.get(j.rubric_item_id) in want]
    pg = part_grades(slot.question, g, rubric)
    keys = [f"{slot.question}-{p}" for p in slot.parts]
    if not slot.parts:
        awarded, full = g.total, g.max_points
    elif all(k in pg for k in keys):
        awarded, full = sum(pg[k].awarded for k in keys), sum(pg[k].points for k in keys)
    else:
        awarded = sum(j.awarded for j in js)
        full = sum(g.item_points.get(j.rubric_item_id, 0) for j in js)
    if awarded >= full:
        return awarded, full, f"정답 → {pts(full)}점"
    notes = [g.teacher_part_notes.get(p, "").strip() for p in slot.parts or [""]]
    teacher = " · ".join(n for n in notes if n)
    if teacher:
        return awarded, full, f"{pts(awarded)}점 ({teacher})"
    bits: list[str] = []
    for j in js:
        lost = g.item_points.get(j.rubric_item_id, 0) - j.awarded
        why = (
            g.teacher_item_notes.get(j.rubric_item_id) or j.pen or short(j.deduction_reason or "")
        ).strip()
        if lost > 0 and why:
            bits.append(f"(−{pts(lost)}: {why.rstrip('.')})")
    return awarded, full, " ".join([f"{pts(awarded)}점", *bits])


PEN_CHARS = 28
"""`pen` 메모가 없는 예전 채점: 감점 이유를 이만큼(식은 1/3로 셈)에서 자른다."""


def short(text: str, limit: int = PEN_CHARS) -> str:
    """감점 이유 → 빨간 펜 메모: 첫 문장(또는 ' — ' 앞)만, 길면 낱말 경계에서 '…'. 식은
    자르지 않는다."""
    text = text.strip()
    for stop in (" — ", ". ", "; ", "다. ", ": "):
        if stop in text and text.index(stop) >= 8:
            text = text[: text.index(stop)]
    out = ""
    used = 0.0
    for piece, is_math in prose_pieces(text):
        cost = len(piece) / 3 if is_math else len(piece)
        if used + cost <= limit:
            out += f"\\({piece}\\)" if is_math else piece
            used += cost
            continue
        if not is_math:
            room = piece[: max(0, int(limit - used))]
            cut = room.rsplit(" ", 1)[0] if " " in room else room
            out += cut
        return out.rstrip(" ,") + "…"
    return out


# ---------------------------------------------------------------- 한 학생


@dataclass
class StudentPdf:
    pages: int = 0
    placed: int = 0
    warnings: list[str] = field(default_factory=lambda: list[str]())


def _image_page(doc: pymupdf.Document, im: Image.Image) -> pymupdf.Page:
    buf = io.BytesIO()
    rgb = np.asarray(im)
    gray_like = float(np.abs(rgb[..., 0].astype(int) - rgb[..., 2].astype(int)).mean()) < 4
    (im.convert("L") if gray_like else im).save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)
    page = doc.new_page(width=im.width * 72 / DPI, height=im.height * 72 / DPI)
    page.insert_image(page.rect, stream=buf.getvalue())
    return page


def _rect(b: Box) -> pymupdf.Rect:
    k = 72 / DPI
    return pymupdf.Rect(b.x0 * k, b.y0 * k, b.x1 * k, b.y1 * k)


def write_student(doc: pymupdf.Document, store: ExamStore, student: str, exam: Exam) -> StudentPdf:
    """학생 한 명의 쪽을 `doc`에 붙인다 (표지 + 답안 쪽)."""
    res = StudentPdf()
    scans = sorted(store.scan_dir(student).glob("page_*.png"))
    if not scans:
        res.warnings.append(f"{student}: 원본 쪽 그림이 없음 (입력한 답안)")
        return res
    grades = {q.number: store.grade(student, q.number) for q in exam.questions}
    rubrics = {q.number: store.rubric(q.number) for q in exam.questions}
    missing = [n for n, g in grades.items() if g is None]
    if missing:
        res.warnings.append(f"{student}: {', '.join(missing)}번 채점 전 — 빈칸으로 둠")
    review = [n for n, g in grades.items() if g is not None and g.status == "needs_review"]
    if review:
        res.warnings.append(f"{student}: {', '.join(review)}번 검토 필요인 채 씀")
    todo = list(exam.questions)
    for path in scans:
        scan = Scan.open(path)
        page = _image_page(doc, scan.image)
        res.pages += 1
        if path.name == "page_01.png":
            _write_cover(page, scan, exam, grades, res)
            continue
        table = find_table(scan)
        if table is None:
            if todo:
                res.warnings.append(f"{student}: {path.name}에서 답안 표를 찾지 못함")
            continue
        fallback = {n: rubric_parts(r) for n, r in rubrics.items()}
        slots, used = table_slots(table, todo, fallback)
        todo = todo[used:]
        ink = scan.ink()
        for slot in slots:
            g, r = grades.get(slot.question), rubrics.get(slot.question)
            if g is None or r is None:
                continue
            awarded, _, note = slot_note(slot, g, r)
            _score(page, [_rect(scan.back(c)) for c in slot.cells], pts(awarded))
            area = scan.back(slot.answer).inset(10)
            _note(page, ink, area, note)
            res.placed += 1
    if todo:
        res.warnings.append(f"{student}: {', '.join(q.number for q in todo)}번 칸을 찾지 못함")
    return res


def _score(page: pymupdf.Page, cells: list[pymupdf.Rect], text: str) -> None:
    """점수 칸에 점수. 한 소문항이 여러 칸(실험결과 1·2·3 등)에 걸치면 칸들을 묶는 괄호를 긋고
    가운데에 쓴다 — 그 칸들 전체의 점수라는 표시."""
    first, last = cells[0], cells[-1]
    size = min(26.0, max(12.0, min(first.width * 0.5, first.height * 0.55)))
    union = pymupdf.Rect(first.x0, first.y0, last.x1, last.y1)
    if len(cells) > 1:
        x = union.x0 + 5
        top, bottom = union.y0 + 6, union.y1 - 6
        for a, b in ((pymupdf.Point(x + 4, top), pymupdf.Point(x, top)),
                     (pymupdf.Point(x, top), pymupdf.Point(x, bottom)),
                     (pymupdf.Point(x, bottom), pymupdf.Point(x + 4, bottom))):  # fmt: skip
            page.draw_line(a, b, color=RED, width=1.0)
        # 칸 경계선 위에 숫자가 걸리지 않게: 가운데에 가장 가까운 칸 가운데로
        mid = (union.y0 + union.y1) / 2
        best = min(cells, key=lambda c: abs((c.y0 + c.y1) / 2 - mid))
        union = pymupdf.Rect(union.x0 + 8, best.y0, union.x1, best.y1)
    draw_centered(page, text, union, size)


def _note(page: pymupdf.Page, ink: np.ndarray, area: Box, text: str) -> None:
    k = 72 / DPI
    max_w = area.w * k * 0.62
    for size, lines in ((10.0, 2), (9.0, 3), (8.0, 4)):
        lay = layout(text, size, max_w, lines)
        if not lay.cut:
            break
    spot = free_spot(ink, area, ((lay.w + 4) / k, (lay.h + 4) / k))
    if spot is None:
        spot = Box(area.x1 - (lay.w + 4) / k, area.y1 - (lay.h + 4) / k, area.x1, area.y1)
    # 답안 칸이 글씨로 꽉 찼으면 겹친다 — 옅은 흰 바탕으로 이유를 읽히게 (아래 글씨는 비친다)
    page.draw_rect(_rect(spot), color=None, fill=(1, 1, 1), fill_opacity=0.78)
    draw(page, lay, (spot.x0 * k + 2, spot.y0 * k + 2))


def _write_cover(
    page: pymupdf.Page,
    scan: Scan,
    exam: Exam,
    grades: dict[str, GradeResult | None],
    res: StudentPdf,
) -> None:
    done = [g for g in grades.values() if g is not None]
    full = sum(q.points for q in exam.questions)
    if len(done) == len(grades):
        total = sum(g.total for g in done)
        text = f"{pts(total)} / {pts(full)}"
    else:
        text = f"채점 중 / {pts(full)}"
    size = 40.0
    w = _width(text, size)
    ink = scan.ink()
    k = 72 / DPI
    top = Box(scan.image.width * 0.52, scan.image.height * 0.015, scan.image.width * 0.98,
              scan.image.height * 0.13)  # fmt: skip
    spot = free_spot(ink, top, ((w + 10) / k, (size * 1.3) / k))
    box = spot or top
    r = _rect(box)
    draw_centered(page, text, r, size)
    under = (pymupdf.Point(r.x0, r.y1 - 2), pymupdf.Point(r.x1, r.y1 - 2))
    page.draw_line(*under, color=RED, width=1.2)
    cells = find_cover_cells(scan, len(exam.questions))
    if cells is None:
        line = " · ".join(
            f"{q.number}번 {pts(g.total) if (g := grades[q.number]) else '-'}"
            for q in exam.questions
        )
        lay = layout(line, 14.0, page.rect.width * 0.45, 2)
        draw(page, lay, (r.x0, r.y1 + 6))
        res.warnings.append("표지 점수 칸을 찾지 못해 총점 아래에 적음")
        return
    for q, cell in zip(exam.questions, cells, strict=True):
        g = grades[q.number]
        if g is not None:
            c = _rect(cell)
            draw_centered(page, pts(g.total), c, min(24.0, c.height * 0.62))


# ---------------------------------------------------------------- 시험 전체


@dataclass
class MarkedResult:
    written: list[Path] = field(default_factory=lambda: list[Path]())
    warnings: list[str] = field(default_factory=lambda: list[str]())
    students: int = 0
    placed: int = 0


def write_marked(
    store: ExamStore,
    out_dir: Path,
    *,
    classes: list[str] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> MarkedResult:
    """반마다 `<반>_채점답안지.pdf` (이름순, 학생마다 표지 + 답안 쪽). 이름 확인 전인 학생도
    넣는다 (표지에 학생이 쓴 이름이 그대로 보인다)."""
    from dawoni.students import natural_key

    exam = store.exam()
    res = MarkedResult()
    by_class: dict[str, list[tuple[str, str]]] = {}
    for r in store.student_records():
        if r.name_pending:
            res.warnings.append(f"{r.name or r.student_id}: 이름 확인 전 (그대로 넣음)")
        klass = r.class_name or "반없음"
        if classes and klass not in classes:
            continue
        by_class.setdefault(klass, []).append((r.name or r.student_id, r.student_id))
    total = sum(len(v) for v in by_class.values())
    done = 0
    for klass in sorted(by_class, key=natural_key):
        doc = pymupdf.open()
        for name, sid in sorted(by_class[klass]):
            if progress:
                progress(done, total, f"{klass} {name}")
            one = write_student(doc, store, sid, exam)
            res.warnings += one.warnings
            res.placed += one.placed
            res.students += 1
            done += 1
        if doc.page_count == 0:
            continue
        path = out_dir / safe_name(f"{klass}_채점답안지.pdf")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            doc.save(path, garbage=3, deflate=True)
            res.written.append(path)
        except (OSError, RuntimeError) as e:
            res.warnings.append(f"{path.name}을 쓰지 못함 ({e}) — PDF 보기에서 닫고 다시 만드세요")
    return res
