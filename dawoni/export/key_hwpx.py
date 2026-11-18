"""다듬은 해설지 HWPX 쓰기 (0단계 해설 다듬기, `dawoni.keyprep`).

강사가 넣은 HWPX를 그대로 두고 문항마다 **해설 조각의 문단만** 고친다:

- 원래 부분 점수 기준 문단(`KeyBlock.remove`)을 지우고,
- 그 문항 해설 조각의 마지막 문단 바로 뒤에 새 문단(`KeyBlock.lines`)을 넣는다. 미주 안이면 그
  미주 안에, 다음 미주의 번호 앞(한글에서 앞 미주의 끝으로 보이는 곳 — `ingest.document`의
  `note_lead`)이면 거기에 넣어 다시 읽어도 같은 문항 해설이 된다.
- 해설 조각이 없는 문항은 문서 끝 "정답 및 해설" 아래 "N." 문단으로 (해설 구간이 있으면 그 뒤에).

문항·그림·표·글꼴·쪽 모양은 그대로 — 고친 구역 파일(`Contents/section*.xml`)만 다시 쓰고 나머지는
바이트까지 옮긴다. 새 문단의 글자·문단 모양은 그 문항 해설 문단에서 빌린다(자동 번호가 붙은 문단
모양은 빼고). 식(`\\( \\)`)은 한글 수식으로 (`ingest.eqedit.to_eqedit`). 다 쓴 뒤 다시 읽어 새
기준이
그 문항 해설로 읽히는지 확인한다.
"""

from __future__ import annotations

import re
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from dawoni.ingest.document import (
    HH,
    HP,
    _HwpxNumbering,  # pyright: ignore[reportPrivateUsage]
    analyze_document,
    is_question_anchor,
    key_section_start,
    read_hwpx,
)
from dawoni.ingest.eqedit import equation_baseline, equation_size, to_eqedit

type _El = etree._Element  # pyright: ignore[reportPrivateUsage]

_MATH = re.compile(r"\\\((.+?)\\\)", re.S)
_XML_HEAD = b'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
_KEEP = (HP + "autoNum", HP + "endNote", HP + "footNote", HP + "secPr", HP + "tbl", HP + "pic")
"""지우면 안 되는 것이 든 문단 — 미주 번호·미주·구역 설정·표·그림."""
KEY_HEADING = "정답 및 해설"


@dataclass
class KeyBlock:
    number: str
    """문항 번호."""
    remove: list[int] = field(default_factory=lambda: list[int]())
    """지울 원래 해설 문단 — 그 문항 해설 조각 안의 자리 (0부터)."""
    lines: list[str] = field(default_factory=lambda: list[str]())
    """새 문단 글 (식은 `\\( \\)`)."""
    marker: str | None = None
    """다시 읽었을 때 그 문항 해설에 있어야 할 글 (확인용, 보통 첫 줄)."""


@dataclass
class WriteResult:
    path: Path
    warnings: list[str] = field(default_factory=lambda: list[str]())
    failed_math: list[str] = field(default_factory=lambda: list[str]())


@dataclass
class _Style:
    para_pr: str = "0"
    style: str = "0"
    char_pr: str = "0"
    base_unit: int = 1000


def _char_heights(header: _El | None) -> dict[str, int]:
    if header is None:
        return {}
    return {cp.get("id") or "": int(cp.get("height") or 1000) for cp in header.iter(HH + "charPr")}


def _text_char(p: _El) -> str | None:
    """문단의 글자 모양: 글이 든 첫 `hp:run`의 것."""
    for run in p.iter(HP + "run"):
        if any((t.text or "").strip() for t in run.iter(HP + "t")):
            return run.get("charPrIDRef")
    run = next(p.iter(HP + "run"), None)
    return run.get("charPrIDRef") if run is not None else None


def _style_of(paras: list[_El], heads: set[str], heights: dict[str, int]) -> _Style:
    """새 문단이 빌릴 모양 — 자동 번호(문단 번호·개요)가 붙지 않은 문단에서."""
    for p in paras:
        pr = p.get("paraPrIDRef") or "0"
        if pr in heads:
            continue
        char = _text_char(p) or "0"
        size = heights.get(char, 1000)
        return _Style(pr, p.get("styleIDRef") or "0", char, size)
    return _Style()


def _sub(parent: _El, tag: str, attrs: dict[str, str] | None = None) -> _El:
    return etree.SubElement(parent, tag, attrs or {})


def _equation(run: _El, script: str, base_unit: int) -> None:
    width, height = equation_size(script, base_unit)
    eq = _sub(run, HP + "equation", {
        "id": str(uuid.uuid4().int & 0x7FFFFFFF),
        "zOrder": "0",
        "numberingType": "EQUATION",
        "textWrap": "TOP_AND_BOTTOM",
        "textFlow": "BOTH_SIDES",
        "lock": "0",
        "dropcapstyle": "None",
        "version": "Equation Version 60",
        "baseLine": str(equation_baseline(script, base_unit)),
        "textColor": "#000000",
        "baseUnit": str(base_unit),
        "lineMode": "CHAR",
        "font": "HYhwpEQ",
    })  # fmt: skip
    _sub(eq, HP + "sz", {"width": str(width), "widthRelTo": "ABSOLUTE", "height": str(height),
                         "heightRelTo": "ABSOLUTE", "protect": "0"})  # fmt: skip
    _sub(eq, HP + "pos", {
        "treatAsChar": "1", "affectLSpacing": "0", "flowWithText": "1", "allowOverlap": "0",
        "holdAnchorAndSO": "0", "vertRelTo": "PARA", "horzRelTo": "PARA", "vertAlign": "TOP",
        "horzAlign": "LEFT", "vertOffset": "0", "horzOffset": "0",
    })  # fmt: skip
    _sub(eq, HP + "outMargin", {"left": "56", "right": "56", "top": "0", "bottom": "0"})
    _sub(eq, HP + "shapeComment").text = "수식입니다."
    _sub(eq, HP + "script").text = script


def _paragraph(maker: _El, text: str, st: _Style, failed: list[str]) -> _El:
    """새 문단: 글 + 한글 수식. 한글은 줄 나눔(linesegarray)이 없으면 열 때 다시 짠다."""
    p = maker.makeelement(HP + "p", {
        "id": "0", "paraPrIDRef": st.para_pr, "styleIDRef": st.style,
        "pageBreak": "0", "columnBreak": "0", "merged": "0",
    })  # fmt: skip
    run = _sub(p, HP + "run", {"charPrIDRef": st.char_pr})
    pos = 0
    for m in _MATH.finditer(text):
        if m.start() > pos:
            _sub(run, HP + "t").text = text[pos : m.start()]
        latex = m.group(1).strip()
        try:
            script = to_eqedit(latex)
            _equation(run, script, st.base_unit)
        except Exception:  # 변환기가 모르는 식: LaTeX 글 그대로 (강사가 한글에서 고친다)
            failed.append(latex)
            _sub(run, HP + "t").text = latex
        pos = m.end()
    if pos < len(text) or len(run) == 0:
        _sub(run, HP + "t").text = text[pos:]
    return p


def _keepable(p: _El) -> bool:
    return any(next(p.iter(tag), None) is not None for tag in _KEEP)


def write_key_hwpx(src: Path, dest: Path, blocks: list[KeyBlock]) -> WriteResult:
    """`src` HWPX의 해설을 `blocks`대로 고쳐 `dest`에 쓴다 (`src`는 그대로)."""
    out = WriteResult(dest)
    doc = read_hwpx(src)
    with tempfile.TemporaryDirectory() as tmp:
        analysis = analyze_document(src, Path(tmp), doc=doc, images=False)
    with zipfile.ZipFile(src) as z:
        header = etree.fromstring(z.read("Contents/header.xml"))
    heads = set(_HwpxNumbering(header).heads)
    heights = _char_heights(header)
    touched: set[str] = set()
    tail: list[KeyBlock] = []

    for b in blocks:
        index = analysis.key_index.get(b.number, [])
        if not index:
            if b.lines:
                tail.append(b)
            continue
        paras = [doc.elements[i] for i in index]
        doomed = [paras[k] for k in sorted(set(b.remove)) if 0 <= k < len(paras)]
        kept_back = [p for p in doomed if _keepable(p)]
        if kept_back:
            out.warnings.append(
                f"{b.number}번: 원래 기준 문단 {len(kept_back)}개는 미주 번호·표·그림이 있어 "
                "지우지 않았습니다 — 한글에서 확인하세요"
            )
        doomed = [p for p in doomed if not _keepable(p)]
        st = _style_of([*doomed, *reversed(paras)], heads, heights)
        last = paras[-1]
        parent = last.getparent()
        if parent is None:
            continue
        at = parent.index(last) + 1
        for k, text in enumerate(b.lines):
            parent.insert(at + k, _paragraph(last, text, st, out.failed_math))
        for p in doomed:
            if (up := p.getparent()) is not None:
                up.remove(p)
        top = last.getroottree().getroot()
        touched.add(next((name for name, r in doc.roots.items() if r is top), ""))

    if tail:
        names = list(doc.roots)
        root = doc.roots[names[-1]]
        st = _Style(
            base_unit=heights.get("0", 1000)
        )  # 바탕글 — 본문 끝 문단은 표·답안지일 때가 많다
        lines: list[str] = []
        if key_section_start(doc.paragraphs) >= len(doc.paragraphs):
            lines.append(KEY_HEADING)
        for b in tail:
            lines.append(f"{b.number}. {b.lines[0]}")
            lines += [_unanchored(t) for t in b.lines[1:]]
        for text in lines:
            root.append(_paragraph(root, text, st, out.failed_math))
        touched.add(names[-1])

    touched.discard("")
    _rezip(src, dest, {name: doc.roots[name] for name in touched})
    out.warnings += _verify(dest, blocks)
    if out.failed_math:
        out.warnings.append(
            f"한글 수식으로 바꾸지 못한 식 {len(out.failed_math)}개는 글로 넣었습니다: "
            + ", ".join(out.failed_math[:3])
        )
    return out


def _unanchored(text: str) -> str:
    """해설 구간에서 "2."로 시작하는 줄은 2번 해설로 넘어간다 — 앞에 표시를 붙인다."""
    return f"· {text}" if is_question_anchor(text) else text


def _rezip(src: Path, dest: Path, changed: dict[str, _El]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for info in zin.infolist():
            if info.filename in changed:
                data = _XML_HEAD + etree.tostring(changed[info.filename], encoding="UTF-8")
            else:
                data = zin.read(info.filename)
            zout.writestr(info, data, compress_type=info.compress_type)
    tmp.replace(dest)  # 강사가 한글에서 열어 둔 파일이면 여기서 실패한다 — 부른 쪽이 알린다


def _verify(path: Path, blocks: list[KeyBlock]) -> list[str]:
    """다시 읽어 새 기준이 그 문항 해설로 읽히는지."""
    with tempfile.TemporaryDirectory() as tmp:
        a = analyze_document(path, Path(tmp), images=False)

    def squash(s: str) -> str:  # 읽을 때 문단 앞뒤 빈칸(낮은 단계의 "　└")을 떼고 빈칸을 줄인다
        return re.sub(r"\s+", "", s)

    problems: list[str] = []
    for b in blocks:
        if not b.marker:
            continue
        got = a.keys.get(b.number)
        if got is None or squash(b.marker) not in squash(got.text):
            problems.append(
                f"{b.number}번: 다시 읽었더니 새 부분 점수 기준이 이 문항 해설로 읽히지 않습니다 "
                "— 한글에서 확인하세요"
            )
    return problems
