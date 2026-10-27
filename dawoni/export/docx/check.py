# pyright: reportPrivateUsage=false
"""내보낸 DOCX 자동 검사 (PLAN §7-1). Word를 열지 않고 XML로 확인한다.

- 문서 기본값에 ascii·hAnsi·eastAsia·cs 4개 슬롯이 모두 있고 테마 폰트가 없을 것
- 어떤 곳에도 테마 폰트 속성(`w:asciiTheme` 등)이 없을 것
- 한글이 든 모든 글자 런·수식 런에 `eastAsia` 폰트가 명시돼 있을 것
- LaTeX 원문(`$`, `\\frac` …)이 글자로 남지 않았을 것 (원문 스타일 `DawoniLatex` 런은 예외)
- 모든 그림이 문서 안에 실제로 들어 있을 것
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from dawoni.export.docx.fonts import LATEX_STYLE
from dawoni.mathx.omml import M_NS, W_NS

_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_HANGUL = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
_LATEX_LEFT = re.compile(r"\$|\\[A-Za-z]+|\\\(|\\\[")
_THEME_ATTRS = ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme")
_SLOTS = ("ascii", "hAnsi", "eastAsia", "cs")


def _w(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


@dataclass
class CheckReport:
    errors: list[str] = field(default_factory=lambda: list[str]())
    warnings: list[str] = field(default_factory=lambda: list[str]())
    text_runs: int = 0
    hangul_runs: int = 0
    equations: int = 0
    images: int = 0
    latex_fallbacks: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        return (
            f"글자 런 {self.text_runs}개(한글 {self.hangul_runs}), 수식 {self.equations}개, "
            f"그림 {self.images}개"
            + (f", 수식 원문 표시 {self.latex_fallbacks}개" if self.latex_fallbacks else "")
        )


def _run_text(r: etree._Element, tag: str) -> str:
    return "".join(t.text or "" for t in r.iter(tag))


def _east_asia(rpr: etree._Element | None) -> str | None:
    if rpr is None:
        return None
    rf = rpr.find(_w("rFonts"))
    return rf.get(_w("eastAsia")) if rf is not None else None


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, str]:
    d, name = posixpath.split(part)
    path = posixpath.join(d, "_rels", f"{name}.rels")
    if path not in z.namelist():
        return {}
    root = etree.fromstring(z.read(path))
    out: dict[str, str] = {}
    for rel in root.iter(f"{{{_PKG_REL_NS}}}Relationship"):
        if rel.get("TargetMode") == "External":
            continue
        out[rel.get("Id", "")] = posixpath.normpath(posixpath.join(d, rel.get("Target", "")))
    return out


def check_docx(path: Path) -> CheckReport:
    rep = CheckReport()
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        styles = etree.fromstring(z.read("word/styles.xml"))
        rf = styles.find(f"{_w('docDefaults')}/{_w('rPrDefault')}/{_w('rPr')}/{_w('rFonts')}")
        if rf is None:
            rep.errors.append("문서 기본값에 폰트(rFonts)가 없음")
        else:
            missing = [s for s in _SLOTS if not rf.get(_w(s))]
            if missing:
                rep.errors.append(f"문서 기본값 폰트 슬롯 누락: {missing}")
        stories = sorted(
            n for n in names if re.fullmatch(r"word/(document|header\d*|footer\d*)\.xml", n)
        )
        parts = ["word/styles.xml", *stories]
        for part in parts:
            root = etree.fromstring(z.read(part))
            themed = [
                el for el in root.iter(_w("rFonts")) if any(el.get(_w(a)) for a in _THEME_ATTRS)
            ]
            if themed:
                rep.errors.append(f"{part}: 테마 폰트 속성 {len(themed)}곳 (한글 폰트를 덮어씀)")
            if part == "word/styles.xml":
                continue
            _check_runs(root, part, rep)
            rels = _rels(z, part)
            for blip in root.iter(f"{{{_A_NS}}}blip"):
                rep.images += 1
                rid = blip.get(f"{{{_R_NS}}}embed")
                target = rels.get(rid or "")
                if target is None or target not in names:
                    rep.errors.append(f"{part}: 깨진 그림 참조 {rid}")
    return rep


def _check_runs(root: etree._Element, part: str, rep: CheckReport) -> None:
    no_font = 0
    latex_left: list[str] = []
    for r in root.iter(_w("r")):
        txt = _run_text(r, _w("t"))
        if not txt:
            continue
        rep.text_runs += 1
        rpr = r.find(_w("rPr"))
        style = rpr.find(_w("rStyle")) if rpr is not None else None
        is_fallback = style is not None and style.get(_w("val")) == LATEX_STYLE
        if _HANGUL.search(txt):
            rep.hangul_runs += 1
            if not _east_asia(rpr):
                no_font += 1
        if is_fallback:
            rep.latex_fallbacks += 1
        elif _LATEX_LEFT.search(txt):
            latex_left.append(txt[:40])
    no_math_font = 0
    for om in root.iter(f"{{{M_NS}}}oMath"):
        rep.equations += 1
        for mr in om.iter(f"{{{M_NS}}}r"):
            txt = _run_text(mr, f"{{{M_NS}}}t")
            if _HANGUL.search(txt) and not _east_asia(mr.find(_w("rPr"))):
                no_math_font += 1
    if no_font:
        rep.errors.append(f"{part}: 한글 런 {no_font}개에 eastAsia 폰트 없음 (tofu 위험)")
    if no_math_font:
        rep.errors.append(f"{part}: 수식 안 한글 {no_math_font}곳에 한글 폰트 없음 (tofu 위험)")
    if latex_left:
        rep.errors.append(f"{part}: LaTeX 원문이 글자로 남음 {len(latex_left)}곳: {latex_left[:3]}")
