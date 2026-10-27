# pyright: reportPrivateUsage=false
"""문항 내용(블록·런) → Word. 시험지·해설지·리포트가 함께 쓰는 저수준 출력기.

- 글자 런은 `fonts.add_run()`으로만 만든다 (한글 폰트 4중 고정).
- 수식은 OMML(편집 가능한 Word 수식). 한 문서의 수식을 **먼저 한 번에 변환**해 두고
  (`prepare_math`) 쓰기 시작한다. `strict_math`이면 변환 실패가 하나라도 있을 때 아무것도
  쓰기 전에 `ExportError` — 시험지·해설지에 LaTeX 원문이 새어 나가지 않는다.
  학생 답안처럼 원문 그대로여야 하는 것은 `strict_math=False`로, 실패한 식만 원문 글자
  스타일(`DawoniLatex`)로 보여 준다.
- `<보기>`·조건·선지는 판별하지 않고 저장된 구조(`BogiBlock` 등)를 그대로 그린다.
"""

from __future__ import annotations

import math as pymath
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

import docx
from docx.blkcntnr import BlockItemContainer
from docx.document import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.section import Section
from docx.shared import Cm, Emu, Pt, RGBColor
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from lxml import etree

from dawoni.export.docx.fonts import FontSpec, add_run, fix_document_fonts, style_math
from dawoni.mathx.omml import M_NS, OmmlConverter, default_converter
from dawoni.models.content import (
    Block,
    BogiBlock,
    BoxBlock,
    ConditionsBlock,
    DisplayMathBlock,
    FigureBlock,
    LabeledItem,
    MathRun,
    ParagraphBlock,
    TableBlock,
    TextRun,
    iter_runs,
    runs_from_text,
)
from dawoni.models.figure import Figure
from dawoni.models.item import ChoiceLayout, ItemContent

CIRCLED = "①②③④⑤⑥⑦⑧⑨"
_BARE_LATEX = re.compile(r"\\[A-Za-z]+(?:\{[^{}]*\})*|[A-Za-z0-9)]\^[A-Za-z0-9{(]|_\{")
"""구분자 없이 쓴 식의 흔적: LaTeX 명령, `x^2`, `a_{n}`."""

Container = BlockItemContainer
Align = Literal["left", "center", "right"]
_ALIGN = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
}


class ExportError(Exception):
    pass


def circled(label: int) -> str:
    return CIRCLED[label - 1] if 1 <= label <= len(CIRCLED) else f"({label})"


@dataclass(frozen=True)
class PageSetup:
    width_cm: float = 21.0
    height_cm: float = 29.7
    margin_cm: float = 1.6
    column_gap_cm: float = 0.8

    @property
    def text_width_cm(self) -> float:
        return self.width_cm - 2 * self.margin_cm

    def column_width_cm(self, columns: int) -> float:
        return (self.text_width_cm - (columns - 1) * self.column_gap_cm) / columns


@dataclass
class FigureContext:
    """문항 그림을 찾는 곳. 그림 경로(`Figure.orig`)는 `base_dir` 기준."""

    figures: dict[str, Figure] = field(default_factory=lambda: dict[str, Figure]())
    base_dir: Path | None = None
    item: ItemContent | None = None
    """파라미터 그림을 그릴 때 쓰는 변수 값."""

    @classmethod
    def of(cls, item: ItemContent, base_dir: Path | None) -> FigureContext:
        return cls({f.id: f for f in item.figures}, base_dir, item)


def item_latex(item: ItemContent) -> list[str]:
    """문항(본문·선지·해설)의 모든 수식."""
    runs = iter_runs(item.stem) + iter_runs(item.solution)
    for c in item.choices:
        runs += c.runs
    return [r.v for r in runs if isinstance(r, MathRun)]


# ---------------------------------------------------------------- 저수준 OXML


def _insert_ordered(parent: etree._Element, el: etree._Element, successors: Sequence[str]) -> None:
    for tag in successors:
        nxt = parent.find(qn(tag))
        if nxt is not None:
            nxt.addprevious(el)
            return
    parent.append(el)


_TBLPR_AFTER_BORDERS = ("w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption")


def _el(tag: str) -> etree._Element:
    return cast(etree._Element, OxmlElement(tag))


def _x(el: object) -> etree._Element:
    """python-docx 요소를 lxml 요소로 (타입 검사용)."""
    return cast(etree._Element, el)


def set_table_borders(
    table: Table, style: Literal["none", "box", "grid"], *, size: int = 6
) -> None:
    """none: 선 없음(선지 배치) / box: 바깥 테두리(<보기>·조건 상자) / grid: 모든 칸(표)."""
    tblpr = _x(table._tbl.tblPr)
    old = tblpr.find(qn("w:tblBorders"))
    if old is not None:
        tblpr.remove(old)
    borders = _el("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        b = _el(f"w:{side}")
        if style == "none" or (style == "box" and side.startswith("inside")):
            b.set(qn("w:val"), "nil")
        else:
            b.set(qn("w:val"), "single")
            b.set(qn("w:sz"), str(size))
            b.set(qn("w:space"), "0")
            b.set(qn("w:color"), "000000")
        borders.append(b)
    _insert_ordered(tblpr, borders, _TBLPR_AFTER_BORDERS)


def shade_cell(cell: _Cell, fill: str = "EDEDED") -> None:
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    _insert_ordered(
        tcpr, shd, ("w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign")
    )


def set_columns(section: Section, n: int, *, gap_cm: float, separator: bool) -> None:
    sect = section._sectPr
    cols = sect.find(qn("w:cols"))
    if cols is None:
        cols = OxmlElement("w:cols")
        _insert_ordered(
            sect, cols, ("w:formProt", "w:vAlign", "w:noEndnote", "w:titlePg", "w:docGrid")
        )
    cols.set(qn("w:num"), str(n))
    cols.set(qn("w:space"), str(round(gap_cm / 2.54 * 1440)))
    if separator and n > 1:
        cols.set(qn("w:sep"), "1")
    else:
        cols.attrib.pop(qn("w:sep"), None)


def set_keep_next(p_el: etree._Element) -> None:
    ppr = p_el.find(qn("w:pPr"))
    if ppr is None:
        ppr = OxmlElement("w:pPr")
        p_el.insert(0, ppr)
    if ppr.find(qn("w:keepNext")) is None:
        _insert_ordered(
            ppr,
            OxmlElement("w:keepNext"),
            ("w:keepLines", "w:pageBreakBefore", "w:widowControl", "w:numPr", "w:spacing",
             "w:ind", "w:jc", "w:rPr"),
        )  # fmt: skip


def _is_empty_p(p_el: etree._Element) -> bool:
    return all(child.tag == qn("w:pPr") for child in p_el)


_NON_HANGUL = re.compile(r"[^가-힣ㄱ-ㅎㅏ-ㅣ]+")
_EDGE = " ,.;:·"


def loose_segments(s: str) -> list[tuple[Literal["text", "math"], str]]:
    """문장 → (글, 수식) 조각. `\\( \\)`·`$ $` 안은 수식, 그 밖에서는 한글 사이의 LaTeX 명령이
    든 부분(앞뒤 공백·문장부호 제외)을 수식으로 본다."""
    out: list[tuple[Literal["text", "math"], str]] = []

    def add_text(t: str) -> None:
        if not t:
            return
        if out and out[-1][0] == "text":
            out[-1] = ("text", out[-1][1] + t)
        else:
            out.append(("text", t))

    for r in runs_from_text(s):
        if isinstance(r, MathRun):
            out.append(("math", r.v))
            continue
        pos = 0
        for m in _NON_HANGUL.finditer(r.v):
            span = m.group(0)
            if not _BARE_LATEX.search(span):
                continue
            core = span.strip(_EDGE)
            while core.endswith(")") and core.count(")") > core.count("("):
                core = core[:-1].rstrip(_EDGE)  # "(단, \\frac{1}{2})"의 닫는 괄호는 글
            lead = span[: len(span) - len(span.lstrip(_EDGE))]
            trail = span[len(lead) + len(core) :]
            add_text(r.v[pos : m.start()] + lead)
            out.append(("math", core))
            add_text(trail)
            pos = m.end()
        add_text(r.v[pos:])
    return out


def loose_latex(s: str | None) -> list[str]:
    return [v for k, v in loose_segments(s or "") if k == "math"]


# ---------------------------------------------------------------- 선지 배치


def _visual_len(runs: Iterable[TextRun | MathRun]) -> float:
    """대략의 너비(em). 한글 1, 영문·숫자 0.55, 수식은 명령어를 뺀 글자 수 기준."""
    n = 0.0
    for r in runs:
        if isinstance(r, TextRun):
            for ch in r.v:
                n += 1.0 if ord(ch) > 0x2E7F else 0.55
        else:
            s = re.sub(r"\\[A-Za-z]+", "x", r.v)
            s = re.sub(r"[{}^_\s]", "", s)
            frac = 0.6 if "\\frac" in r.v or "\\dfrac" in r.v else 1.0
            n += len(s) * 0.6 * frac
    return n


def choose_layout(
    item: ItemContent, column_cm: float, size_pt: float, figure_widths_cm: list[float]
) -> tuple[Literal["row", "grid", "column"], int]:
    """(배치, 한 줄의 선지 수). `choice_layout`이 정해져 있으면 그대로 따른다."""
    n = len(item.choices)
    layout: ChoiceLayout = item.choice_layout
    if layout == "row":
        return "row", n
    if layout == "grid_3_2":
        return "grid", 3
    if layout == "grid_2":
        return "grid", 2
    if layout == "column":
        return "column", 1
    em_cm = size_pt / 72 * 2.54
    label_cm = 1.3 * em_cm
    if figure_widths_cm:
        widest = max(figure_widths_cm) + 0.5  # 번호는 그림 위 줄에
        for per_row in (n, 3, 2):
            if per_row <= n and widest * 0.8 <= column_cm / per_row:  # 20%까지 줄여 맞춤
                return ("row" if per_row == n else "grid"), per_row
        return "column", 1
    widest = max(_visual_len(c.runs) for c in item.choices) * em_cm + label_cm
    for per_row in (n, 3, 2):
        if per_row <= n and widest <= column_cm / per_row * 0.92:
            return ("row" if per_row == n else "grid"), per_row
    return "column", 1


# ---------------------------------------------------------------- 출력기


class DocWriter:
    def __init__(
        self,
        fonts: FontSpec | None = None,
        *,
        page: PageSetup | None = None,
        converter: OmmlConverter | None = None,
        strict_math: bool = True,
    ) -> None:
        self.doc: Document = docx.Document()
        self.fonts = fonts or FontSpec()
        self.page = page or PageSetup()
        self.conv = converter or default_converter()
        self.strict_math = strict_math
        self.warnings: list[str] = []
        self.math_fallbacks: list[str] = []
        fix_document_fonts(self.doc, self.fonts)
        self._setup_section(self.doc.sections[0])
        self.columns = 1

    # ------------------------------------------------ 문서·구역

    @property
    def body(self) -> Container:
        return self.doc._body

    @property
    def column_cm(self) -> float:
        return self.page.column_width_cm(self.columns)

    def _setup_section(self, s: Section) -> None:
        s.page_width = Cm(self.page.width_cm)
        s.page_height = Cm(self.page.height_cm)
        for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
            setattr(s, side, Cm(self.page.margin_cm))
        s.header_distance = Cm(0.8)
        s.footer_distance = Cm(0.8)

    def start_columns(self, n: int, *, separator: bool = True) -> None:
        """이어지는 구역(같은 쪽)에서 단 수를 바꾼다."""
        s = self.doc.add_section(WD_SECTION.CONTINUOUS)
        self._setup_section(s)
        set_columns(s, n, gap_cm=self.page.column_gap_cm, separator=separator)
        self.columns = n

    def page_numbers(self) -> None:
        """모든 구역 꼬리말 가운데에 쪽 번호."""
        for s in self.doc.sections:
            footer = s.footer
            footer.is_linked_to_previous = False
            p = footer.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = add_run(p, "1", self.fonts, size_pt=self.fonts.size_pt * 0.9)
            fld = _el("w:fldSimple")
            fld.set(qn("w:instr"), "PAGE")
            r = _x(run._r)
            r.addprevious(fld)
            fld.append(r)

    def page_break(self) -> None:
        p = self.body.add_paragraph()
        add_run(p, "", self.fonts).add_break(WD_BREAK.PAGE)

    def column_break(self) -> None:
        p = self.body.add_paragraph()
        add_run(p, "", self.fonts).add_break(WD_BREAK.COLUMN)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(str(path))
        return path

    # ------------------------------------------------ 수식

    def prepare_math(self, latexes: Iterable[str], loose: Iterable[str] = ()) -> None:
        """이 문서의 수식을 한 번에 변환. strict이면 `latexes`에 실패가 있을 때 쓰기 전에 중단.
        `loose`(AI가 쓴 설명 문장 속 식 등)는 실패해도 원문으로 보여 준다."""
        wanted = list(latexes)
        self.conv.convert_many([*wanted, *loose])
        if self.strict_math:
            problems = self.conv.problems(wanted)
            if problems:
                raise ExportError(
                    "Word 수식으로 바꿀 수 없는 식이 있어 내보내지 않았습니다:\n  "
                    + "\n  ".join(problems)
                )
        else:
            self.conv.convert_many(wanted)

    def math(
        self,
        p: Paragraph,
        latex: str,
        *,
        display: bool = False,
        size_pt: float | None = None,
        loose: bool = False,
    ) -> None:
        c = self.conv.convert(latex)
        if not c.ok:
            if self.strict_math and not loose:
                raise ExportError(f"수식 변환 실패: {latex} ({c.error})")
            self.math_fallbacks.append(latex)
            add_run(p, latex, self.fonts, latex_fallback=True, size_pt=size_pt)
            return
        om = c.element()
        style_math(om, self.fonts, size_pt=size_pt)
        if display:
            para = etree.SubElement(_x(p._p), f"{{{M_NS}}}oMathPara")
            ppr = etree.SubElement(para, f"{{{M_NS}}}oMathParaPr")
            jc = etree.SubElement(ppr, f"{{{M_NS}}}jc")
            jc.set(f"{{{M_NS}}}val", "center")
            para.append(om)
        else:
            _x(p._p).append(om)

    # ------------------------------------------------ 문단·런

    def par(
        self,
        container: Container | None = None,
        *,
        align: Align = "left",
        space_after_pt: float | None = 2,
        space_before_pt: float | None = None,
        left_indent_cm: float | None = None,
        first_line_cm: float | None = None,
        line_spacing: float | None = 1.15,
    ) -> Paragraph:
        """새 문단. 표 칸의 처음 빈 문단은 다시 쓴다 (칸 맨 위 빈 줄 방지)."""
        c = container if container is not None else self.body
        p: Paragraph | None = None
        if isinstance(c, _Cell):
            last = c._tc[-1] if len(c._tc) else None
            if last is not None and last.tag == qn("w:p") and _is_empty_p(last):
                p = Paragraph(last, c)  # pyright: ignore[reportArgumentType]
        if p is None:
            p = c.add_paragraph()
        p.alignment = _ALIGN[align]
        pf = p.paragraph_format
        if space_after_pt is not None:
            pf.space_after = Pt(space_after_pt)
        pf.space_before = Pt(space_before_pt or 0)
        if left_indent_cm is not None:
            pf.left_indent = Cm(left_indent_cm)
        if first_line_cm is not None:
            pf.first_line_indent = Cm(first_line_cm)
        if line_spacing is not None:
            pf.line_spacing = line_spacing
        return p

    def text(
        self,
        p: Paragraph,
        s: str,
        *,
        bold: bool = False,
        underline: bool = False,
        strike: bool = False,
        size_pt: float | None = None,
    ) -> None:
        add_run(p, s, self.fonts, bold=bold, underline=underline, strike=strike, size_pt=size_pt)

    def runs(
        self,
        p: Paragraph,
        runs: Sequence[TextRun | MathRun],
        *,
        bold: bool = False,
        strike: bool = False,
        size_pt: float | None = None,
    ) -> None:
        for r in runs:
            if isinstance(r, TextRun):
                add_run(
                    p,
                    r.v,
                    self.fonts,
                    bold=r.bold or bold,
                    underline=r.underline,
                    strike=strike,
                    size_pt=size_pt,
                )
            else:
                self.math(p, r.v, size_pt=size_pt)

    def heading(
        self, s: str, *, size_pt: float = 14, align: Align = "left", space_before_pt: float = 8
    ) -> Paragraph:
        p = self.par(align=align, space_before_pt=space_before_pt, space_after_pt=4)
        add_run(p, s, self.fonts, bold=True, size_pt=size_pt)
        set_keep_next(p._p)
        return p

    # ------------------------------------------------ 상자·표

    def table(self, container: Container, rows: int, cols: int, width_cm: float) -> Table:
        el = _x(container._element)
        children = [c for c in el if c.tag != qn("w:sectPr")]
        if children and children[-1].tag == qn("w:tbl"):
            # 표 바로 뒤에 표가 오면 Word가 하나로 붙여 버림 → 얇은 간격 문단
            gap = container.add_paragraph()
            gap.paragraph_format.space_after = Pt(0)
            gap.paragraph_format.line_spacing = Pt(5)
        t = BlockItemContainer.add_table(container, rows, cols, Cm(width_cm))
        t.autofit = False
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        if isinstance(container, _Cell):
            container.add_paragraph()  # Word: 칸의 마지막은 문단이어야 함 (다음 내용이 다시 씀)
        return t

    def boxed(self, container: Container, width_cm: float) -> _Cell:
        t = self.table(container, 1, 1, width_cm)
        set_table_borders(t, "box")
        return t.cell(0, 0)

    def _labeled(
        self, cell: Container, items: list[LabeledItem], *, sep: str, indent_cm: float
    ) -> None:
        for it in items:
            p = self.par(cell, left_indent_cm=indent_cm, first_line_cm=-indent_cm)
            self.text(p, f"{it.label}{sep}")
            self.runs(p, it.runs)

    def bogi(self, container: Container, b: BogiBlock, width_cm: float) -> None:
        cell = self.boxed(container, width_cm)
        title = " ".join(b.title) if len(b.title) <= 3 else b.title
        p = self.par(cell, align="center", space_after_pt=3)
        self.text(p, f"< {title} >", bold=True)
        self._labeled(cell, b.items, sep=". ", indent_cm=0.6)

    def conditions(self, container: Container, b: ConditionsBlock, width_cm: float) -> None:
        target: Container = self.boxed(container, width_cm) if b.boxed else container
        self._labeled(target, b.items, sep=" ", indent_cm=0.8)

    def box(self, container: Container, b: BoxBlock, width_cm: float) -> None:
        cell = self.boxed(container, width_cm)
        if b.title:
            p = self.par(cell, align="center", space_after_pt=3)
            self.text(p, b.title, bold=True)
        for runs in b.paragraphs:
            self.runs(self.par(cell), runs)

    def table_block(self, container: Container, b: TableBlock, width_cm: float) -> None:
        cols = max(len(r) for r in b.rows)
        t = self.table(container, len(b.rows), cols, width_cm)
        set_table_borders(t, "grid", size=4)
        for i, row in enumerate(b.rows):
            for j in range(cols):
                cell = t.cell(i, j)
                p = self.par(cell, align="center", space_after_pt=0)
                if j < len(row):
                    self.runs(p, row[j], bold=i < b.header_rows)
                if i < b.header_rows:
                    shade_cell(cell)

    # ------------------------------------------------ 그림

    def _figure_path(self, fig: Figure, ctx: FigureContext) -> Path | None:
        if fig.mode == "parametric" and fig.spec is not None and ctx.item is not None:
            # 스펙으로 다시 그린 그림 (문항 변수 값 반영, 같은 값이면 캐시)
            from dawoni.figures.common import FigureError
            from dawoni.figures.render import render_figure

            try:
                return render_figure(fig, ctx.item)
            except FigureError as e:
                self.warnings.append(f"그림 {fig.id}: 다시 그리지 못해 원본을 씀 ({e})")
        if fig.orig is None:
            return None
        p = Path(fig.orig)
        if not p.is_absolute() and ctx.base_dir is not None:
            p = ctx.base_dir / p
        return p if p.is_file() else None

    def figure_natural_cm(self, fig: Figure, ctx: FigureContext) -> float | None:
        """원본 크롭의 실제 너비(cm). 크롭은 PDF에서 dpi를 기록해 렌더하므로 원래 크기가 나온다."""
        path = self._figure_path(fig, ctx)
        if path is None:
            return None
        from PIL import Image

        with Image.open(path) as im:
            dpi_info = im.info.get("dpi")
            dpi = 300.0
            if isinstance(dpi_info, tuple) and dpi_info:
                first: object = dpi_info[0]  # pyright: ignore[reportUnknownVariableType]
                if isinstance(first, (int, float)) and first >= 50:
                    dpi = float(first)
            return im.width / dpi * 2.54

    def picture(
        self,
        p: Paragraph,
        fig: Figure,
        ctx: FigureContext,
        max_width_cm: float,
        *,
        scale: float | None = None,
    ) -> bool:
        path = self._figure_path(fig, ctx)
        if path is None:
            self.warnings.append(f"그림 {fig.id}: 원본 이미지가 없어 자리만 표시")
            self.text(p, f"[그림 {fig.id}]", bold=True)
            return False
        natural = self.figure_natural_cm(fig, ctx) or max_width_cm
        width = natural if scale is None else max_width_cm * scale
        width = min(width, max_width_cm)
        add_run(p, "", self.fonts).add_picture(str(path), width=Emu(int(Cm(width))))
        return True

    def figure(
        self, container: Container, b: FigureBlock, ctx: FigureContext, width_cm: float
    ) -> None:
        fig = ctx.figures.get(b.figure_id)
        p = self.par(container, align="center", space_after_pt=4)
        if fig is None:
            self.warnings.append(f"그림 {b.figure_id}: 문항에 정의되지 않음")
            self.text(p, f"[그림 {b.figure_id}]")
            return
        self.picture(p, fig, ctx, width_cm, scale=b.width)

    # ------------------------------------------------ 블록

    def blocks(
        self,
        container: Container,
        blocks: Sequence[Block],
        ctx: FigureContext,
        width_cm: float,
        *,
        first_prefix: Sequence[tuple[str, bool]] = (),
        last_suffix: str | None = None,
    ) -> None:
        """블록 목록. `first_prefix`는 첫 문단 앞(문항 번호), `last_suffix`는 끝 문단 뒤(배점)."""
        pending_prefix = list(first_prefix)
        # 배점은 마지막 '문장' 끝에 (뒤에 <보기>·표가 와도 발문 끝 "고른 것은? [3점]")
        last_par = max(
            (k for k, b in enumerate(blocks) if isinstance(b, ParagraphBlock)), default=-1
        )
        for k, b in enumerate(blocks):
            is_last = k == last_par
            if pending_prefix and not isinstance(b, ParagraphBlock):
                p = self.par(container)
                for s, bold in pending_prefix:
                    self.text(p, s, bold=bold)
                pending_prefix = []
            match b:
                case ParagraphBlock():
                    p = self.par(container, align=b.align)
                    for s, bold in pending_prefix:
                        self.text(p, s, bold=bold)
                    pending_prefix = []
                    self.runs(p, b.runs)
                    if is_last and last_suffix:
                        self.text(p, last_suffix)
                        last_suffix = None
                case DisplayMathBlock():
                    self.math(self.par(container, align="center"), b.latex, display=True)
                case FigureBlock():
                    self.figure(container, b, ctx, width_cm)
                case BogiBlock():
                    self.bogi(container, b, width_cm)
                case ConditionsBlock():
                    self.conditions(container, b, width_cm)
                case BoxBlock():
                    self.box(container, b, width_cm)
                case TableBlock():
                    self.table_block(container, b, width_cm)
        if last_suffix:
            p = self.par(container, align="right")
            self.text(p, last_suffix)

    # ------------------------------------------------ 선지

    def choices(
        self, container: Container, item: ItemContent, ctx: FigureContext, width_cm: float
    ) -> None:
        if not item.choices:
            return
        if item.choices_figure_id:
            # 한 장짜리 선지 그림: ①~⑤가 그림 안에 있다 → 그림만
            fig = ctx.figures.get(item.choices_figure_id)
            p = self.par(container, align="center", space_after_pt=4)
            if fig is None:
                self.text(p, f"[그림 {item.choices_figure_id}]")
            else:
                self.picture(p, fig, ctx, width_cm)
            return
        fig_widths: list[float] = []
        for c in item.choices:
            if c.figure_id and c.figure_id in ctx.figures:
                w = self.figure_natural_cm(ctx.figures[c.figure_id], ctx)
                fig_widths.append(w if w is not None else width_cm / 3)
        layout, per_row = choose_layout(item, width_cm, self.fonts.size_pt, fig_widths)
        if layout == "column":
            for c in item.choices:
                p = self.par(container, left_indent_cm=0.6, first_line_cm=-0.6)
                self._choice(container, p, c.label, c.runs, c.figure_id, ctx, width_cm - 0.8)
            return
        rows = pymath.ceil(len(item.choices) / per_row)
        t = self.table(container, rows, per_row, width_cm)
        set_table_borders(t, "none")
        cell_cm = width_cm / per_row
        for k, c in enumerate(item.choices):
            cell = t.cell(k // per_row, k % per_row)
            p = self.par(cell, space_after_pt=2)
            self._choice(cell, p, c.label, c.runs, c.figure_id, ctx, cell_cm - 0.5)

    def _choice(
        self,
        container: Container,
        p: Paragraph,
        label: int,
        runs: Sequence[TextRun | MathRun],
        figure_id: str | None,
        ctx: FigureContext,
        max_fig_cm: float,
    ) -> None:
        self.text(p, f"{circled(label)} ")
        if figure_id:
            # 그림 선지: 번호는 그림 왼쪽 위, 그림은 다음 줄
            p.paragraph_format.keep_with_next = True
            p.paragraph_format.space_after = Pt(0)
            fp = self.par(container, space_after_pt=4, left_indent_cm=0.3)
            fig = ctx.figures.get(figure_id)
            if fig is None:
                self.text(fp, f"[그림 {figure_id}]")
            else:
                self.picture(fp, fig, ctx, max_fig_cm)
            if runs:
                self.runs(self.par(container, left_indent_cm=0.3), runs)
            return
        self.runs(p, runs)

    # ------------------------------------------------ AI·학생 글 (구분자 섞인 문장)

    def loose_text(
        self,
        p: Paragraph,
        s: str,
        *,
        bold: bool = False,
        strike: bool = False,
        size_pt: float | None = None,
    ) -> None:
        """AI·학생이 쓴 문장. `\\( … \\)` 수식과 구분자 없이 쓴 LaTeX(`-\\frac{b}{2a}=2`)도
        수식으로. 변환되지 않는 식은 오류 대신 원문 글자 스타일로 (저장 전 검증 대상이 아님)."""
        for kind, v in loose_segments(s):
            if kind == "text":
                add_run(p, v, self.fonts, bold=bold, strike=strike, size_pt=size_pt)
            else:
                self.math(p, v, size_pt=size_pt, loose=True)

    # ------------------------------------------------ 문항 하나

    def _mark(self) -> int:
        return len(_x(self.body._element)) - 1  # 마지막은 sectPr

    def _keep_together(self, mark: int) -> None:
        """문항이 단·쪽 경계에서 갈라지지 않게 (마지막 문단 제외 모두 keepNext)."""
        body = _x(self.body._element)
        added = list(body)[mark:-1]
        paras = [p for el in added for p in el.iter(qn("w:p"))]
        for p in paras[:-1]:
            set_keep_next(p)

    def question(
        self,
        number: str,
        points: float | None,
        item: ItemContent,
        ctx: FigureContext,
        *,
        answer_lines: int = 0,
        keep_together: bool = True,
    ) -> None:
        mark = self._mark()
        width = self.column_cm
        label = f"{number}. " if number.isdigit() else f"[{number}] "
        self.blocks(
            self.body,
            item.stem,
            ctx,
            width,
            first_prefix=[(label, True)],
            last_suffix=f" [{points:g}점]" if points else None,
        )
        self.choices(self.body, item, ctx, width)
        if answer_lines > 0:
            cell = self.boxed(self.body, width)
            p = self.par(cell, space_after_pt=0)
            self.text(p, "(풀이)", size_pt=self.fonts.size_pt * 0.85)
            t_el = _x(cell._tc).getparent()
            if t_el is not None:
                trpr = t_el.find(qn("w:trPr"))
                if trpr is None:
                    trpr = _el("w:trPr")
                    t_el.insert(0, trpr)
                h = _el("w:trHeight")
                h.set(qn("w:val"), str(round(answer_lines * 0.75 / 2.54 * 1440)))
                h.set(qn("w:hRule"), "atLeast")
                trpr.append(h)
        if keep_together:
            self._keep_together(mark)
        gap = self.par(space_after_pt=0, line_spacing=1.0)
        gap.paragraph_format.space_before = Pt(10)

    # ------------------------------------------------ 링크

    def hyperlink(
        self, p: Paragraph, url: str, text: str, *, bold: bool = False, size_pt: float | None = None
    ) -> None:
        """바깥 주소로 가는 링크 (예: 영상의 그 시각). 글자 런은 add_run으로 만들어 옮긴다."""
        from docx.opc.constants import RELATIONSHIP_TYPE as RT

        rid = p.part.relate_to(url, RT.HYPERLINK, is_external=True)
        link = _el("w:hyperlink")
        link.set(qn("r:id"), rid)
        run = add_run(p, text, self.fonts, bold=bold, size_pt=size_pt, underline=True)
        run.font.color.rgb = RGBColor(0x1F, 0x4E, 0xB4)
        r = _x(run._r)
        r.addprevious(link)
        link.append(r)
