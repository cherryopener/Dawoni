# pyright: reportPrivateUsage=false
"""한글 폰트 4중 고정 (tofu 방지, PLAN §7-1).

Word에서 한글이 네모(tofu)로 나오는 원인은 거의 항상 동아시아 폰트 슬롯(`w:eastAsia`)이 비었거나
테마 폰트(`w:asciiTheme` 등)가 명시 폰트를 덮어쓰는 것이다. 그래서

1. 문서 기본값(`docDefaults`)에 ascii·hAnsi·eastAsia·cs 4개 슬롯 + `w:lang eastAsia=ko-KR`
2. 모든 스타일에서 `*Theme` 속성을 지우고 4개 슬롯 지정
3. 글자 런은 **오직 `add_run()`** 으로만 만든다 (런마다 4개 슬롯을 다시 명시).
   `paragraph.add_run`을 직접 부르는 코드는 테스트가 막는다.
4. 수식(OMML) 안의 런에도 `w:rPr`로 수식 폰트 + 한글 폰트를 지정 (`style_math`)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from docx.document import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.oxml.text.font import CT_RPr
from docx.shared import Pt
from docx.styles.style import CharacterStyle
from docx.text.paragraph import Paragraph
from docx.text.run import Run
from lxml import etree

from dawoni.mathx.omml import M_NS, W_NS

LATEX_STYLE = "DawoniLatex"
"""수식으로 바꾸지 못한 학생 답안 LaTeX를 원문 그대로 보여 주는 글자 스타일 (검사에서 예외)."""
_CODE_FONT = "Consolas"

_SLOTS = ("ascii", "hAnsi", "eastAsia", "cs")
_THEME_ATTRS = ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme")


@dataclass(frozen=True)
class FontSpec:
    body: str = "맑은 고딕"
    math: str = "Cambria Math"
    size_pt: float = 10.0


def _child(parent: etree._Element, tag: str, *, first: bool = False) -> etree._Element:
    el = parent.find(qn(tag))
    if el is None:
        el = OxmlElement(tag)
        if first:
            parent.insert(0, el)
        else:
            parent.append(el)
    return el


def _rpr(el: etree._Element) -> CT_RPr:
    if not isinstance(el, CT_RPr):
        raise TypeError(f"w:rPr가 아님: {el.tag}")
    return el


def _style_rpr(style_el: etree._Element) -> CT_RPr:
    rpr = style_el.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        style_el.append(rpr)
    return _rpr(rpr)


def set_rfonts(
    rpr: etree._Element, *, latin: str, east_asia: str, complex_script: str | None = None
) -> None:
    """`w:rPr` 안의 `w:rFonts`를 4개 슬롯 모두 명시하고 테마 속성을 지운다."""
    rf = _rpr(rpr).get_or_add_rFonts()
    for a in _THEME_ATTRS:
        rf.attrib.pop(qn(f"w:{a}"), None)
    rf.set(qn("w:ascii"), latin)
    rf.set(qn("w:hAnsi"), latin)
    rf.set(qn("w:eastAsia"), east_asia)
    rf.set(qn("w:cs"), complex_script or east_asia)


def _set_lang(rpr: etree._Element) -> None:
    lang = _child(rpr, "w:lang")
    lang.set(qn("w:val"), "en-US")
    lang.set(qn("w:eastAsia"), "ko-KR")


def fix_document_fonts(doc: Document, spec: FontSpec) -> None:
    """문서 기본값과 모든 스타일의 폰트를 고정한다. 새 문서를 만든 직후 한 번 부른다."""
    styles = doc.styles.element
    defaults = _child(styles, "w:docDefaults", first=True)
    rpr_default = _child(defaults, "w:rPrDefault", first=True)
    rpr = _child(rpr_default, "w:rPr")
    set_rfonts(rpr, latin=spec.body, east_asia=spec.body)
    _set_lang(rpr)
    _rpr(rpr).get_or_add_sz().set(qn("w:val"), str(round(spec.size_pt * 2)))

    # 한글·숫자/영문 사이 자동 간격 끄기 ("[3점]"이 "[3 점]"으로 벌어지지 않게)
    ppr_default = _child(defaults, "w:pPrDefault")
    ppr = _child(ppr_default, "w:pPr")
    for tag in ("w:autoSpaceDN", "w:autoSpaceDE"):  # 앞에 넣으므로 역순 → DE, DN
        _child(ppr, tag, first=True).set(qn("w:val"), "0")

    # 스타일 본체뿐 아니라 표 스타일의 조건부 서식(tblStylePr) 안까지 모든 rFonts
    for rf in list(styles.iter(qn("w:rFonts"))):
        parent = rf.getparent()
        if parent is not None:
            set_rfonts(parent, latin=spec.body, east_asia=spec.body)

    if LATEX_STYLE not in [s.name for s in doc.styles]:
        st = cast(
            CharacterStyle,
            doc.styles.add_style(LATEX_STYLE, WD_STYLE_TYPE.CHARACTER),  # pyright: ignore[reportUnknownMemberType]
        )
        st.font.size = Pt(spec.size_pt * 0.9)
        set_rfonts(_style_rpr(st.element), latin=_CODE_FONT, east_asia=spec.body)

    # 테마 글꼴 언어도 한국어로 (Word가 테마를 고를 때 한글 글꼴 쪽을 쓰도록)
    settings = doc.settings.element
    tfl = _child(settings, "w:themeFontLang")
    tfl.set(qn("w:val"), "en-US")
    tfl.set(qn("w:eastAsia"), "ko-KR")


def add_run(
    paragraph: Paragraph,
    text: str,
    spec: FontSpec,
    *,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
    strike: bool = False,
    size_pt: float | None = None,
    latex_fallback: bool = False,
) -> Run:
    """글자 런을 만드는 **유일한** 함수."""
    run = paragraph.add_run(text)  # 이 파일에서만 허용 (test_docx가 검사)
    rpr = run._r.get_or_add_rPr()  # pyright: ignore[reportPrivateUsage]
    if latex_fallback:
        run.style = LATEX_STYLE
        set_rfonts(rpr, latin=_CODE_FONT, east_asia=spec.body)
    else:
        set_rfonts(rpr, latin=spec.body, east_asia=spec.body)
    if bold:
        run.bold = True
    if italic:
        run.italic = True
    if underline:
        run.underline = True
    if strike:
        run.font.strike = True
    if size_pt is not None:
        run.font.size = Pt(size_pt)
    return run


def style_math(omath: etree._Element, spec: FontSpec, *, size_pt: float | None = None) -> None:
    """OMML 안의 모든 `m:r`에 수식 폰트·한글 폰트 지정 (`\\text{속력}` tofu 방지)."""
    for r in omath.iter(f"{{{M_NS}}}r"):
        rpr = r.find(f"{{{W_NS}}}rPr")
        if rpr is None:
            rpr = OxmlElement("w:rPr")
            mrpr = r.find(f"{{{M_NS}}}rPr")
            # 순서: m:rPr → w:rPr → m:t
            if mrpr is not None:
                mrpr.addnext(rpr)
            else:
                r.insert(0, rpr)
        set_rfonts(rpr, latin=spec.math, east_asia=spec.body)
        if size_pt is not None:
            _rpr(rpr).get_or_add_sz().set(qn("w:val"), str(round(size_pt * 2)))
