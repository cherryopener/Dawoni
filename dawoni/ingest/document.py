"""DOCX·HWPX 시험지 → 문항 영역·해설 조각 (AI 없음, PLAN.md §3).

PDF처럼 쪽을 그림으로 그려 AI에게 읽히지 않는다. 문서에 든 글·식·그림을 직접 꺼낸다:

    문단(표 칸·글 상자 포함) → 식은 `\\( LaTeX \\)` → 문항 번호 줄 → 문항 영역
                              → 미주·각주와 "정답 및 해설" 뒤 문단 → 문항별 해설 조각

- 식: DOCX는 Word 수식(OMML), HWPX는 한글 수식 스크립트를 LaTeX로 바꾼다. PDF 글꼴 문자처럼
  깨지지 않으므로 해설지를 두 번 읽어 숫자를 비교할 필요가 없다 (토큰 ↓).
- 문항 번호: 문단 맨 앞의 "1." (DOCX 자동 번호도 글로 풀어 붙임) — PDF와 같은 번호 줄기 규칙.
- 학원 HWP 시험지는 문항마다 답·해설을 **미주**로 붙인다 (실측 2026-11-04: 주말반·심화반 3개).
  미주는 그것이 달린 문항의 해설로 모은다. 한글이 PDF로 저장하면 미주가 문서 끝에 몰려 어느 문항
  것인지 다시 맞춰야 하는데, 문서에서는 구조로 바로 안다.
- 그림: 문서에 든 이미지를 PNG로 꺼내 문항 그림(F1, F2 …)으로 쓴다. 바로 앞 글이 ①~⑤면 그 선지 그림.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from lxml import etree

from dawoni.ingest.layout import (
    _ANCHOR,  # pyright: ignore[reportPrivateUsage]
    _KEY_MARK,  # pyright: ignore[reportPrivateUsage]
    SET_MARK,
    DocumentLayout,
    Figure,
    Line,
    Rect,
    Region,
    _anchor_label,  # pyright: ignore[reportPrivateUsage]
    marked_points,
    number_chain,
)
from dawoni.mathx.omml import M as MATH
from dawoni.mathx.omml import omml_to_latex

log = logging.getLogger(__name__)

type _El = etree._Element  # pyright: ignore[reportPrivateUsage]

DOC_SUFFIXES = (".docx", ".hwpx")
QUESTIONS_PER_CALL = 4
"""문서 문항 구조화 한 번에 넘길 문항 수 (PDF의 한 쪽에 해당)."""
CHARS_PER_CALL = 4000
_NOWHERE = Rect(0, 0, 0, 0)
_CHOICE_BEFORE = re.compile(r"([①②③④⑤])\s*$")
_FENCE = re.compile(r"^\s*(?:답안지(?![에의를])|답안\s*작성란|OMR)")
"""문항 뒤에 붙은 답안지 표 — 마지막 문항 영역에 넣지 않는다 (실측: 주말반 테스트 끝 쪽).

"답안지에 쓰시오"처럼 문항 안의 문장은 경계가 아니다."""
_KEY_HEAD = re.compile(r"^\s*[\[［(<]?\s*(?:" + _KEY_MARK.pattern + r")")
"""해설 구간을 여는 문단 ("정답 및 해설", "[모범 답안]") — 문단 맨 앞에 있을 때만."""


@dataclass
class DocParagraph:
    text: str
    """글 + 식(`\\( … \\)`) + 그림 자리(`[그림]`)."""
    images: list[str] = field(default_factory=lambda: list[str]())
    """이 문단에 든 그림 (`ParsedDocument.images`의 키), 나온 순서대로."""
    choice_marks: list[int | None] = field(default_factory=lambda: list[int | None]())
    """그림마다 바로 앞의 선지 번호 (①→1), 없으면 None."""
    note: bool = False
    """미주·각주 안의 문단 (학원 시험지의 답·해설)."""


@dataclass
class ParsedDocument:
    path: Path
    paragraphs: list[DocParagraph]
    images: dict[str, bytes]
    """그림 ID(문서 안 경로) → 파일 바이트."""


IMAGE_MARK = "[그림]"


class _Para:
    """문단 하나를 모으는 중: 글 조각 + 그림."""

    def __init__(self, note: bool) -> None:
        self.parts: list[str] = []
        self.images: list[str] = []
        self.marks: list[int | None] = []
        self.note = note

    def text(self, s: str) -> None:
        self.parts.append(s)

    def math(self, latex: str) -> None:
        if latex.strip():
            self.parts.append(f"\\({latex.strip()}\\)")

    def image(self, ref: str) -> None:
        m = _CHOICE_BEFORE.search("".join(self.parts))
        self.marks.append("①②③④⑤".index(m.group(1)) + 1 if m else None)
        self.images.append(ref)
        self.parts.append(IMAGE_MARK)

    def done(self) -> DocParagraph | None:
        text = re.sub(r"[ \t ]+", " ", "".join(self.parts)).strip()
        if not text and not self.images:
            return None
        return DocParagraph(text, self.images, self.marks, self.note)


# ---------------------------------------------------------------- DOCX

WNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{" + WNS + "}"
R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
A_BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
V_IMAGEDATA = "{urn:schemas-microsoft-com:vml}imagedata"
_SKIP_W = {W + "del", W + "pPr", W + "rPr", W + "instrText", W + "delText", W + "sectPr"}


def _xml(z: zipfile.ZipFile, name: str) -> _El | None:
    try:
        return etree.fromstring(z.read(name))
    except KeyError:
        return None


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, str]:
    """관계 ID → zip 안 경로 (`word/media/image1.png`)."""
    p = PurePosixPath(part)
    root = _xml(z, str(p.parent / "_rels" / f"{p.name}.rels"))
    if root is None:
        return {}
    out: dict[str, str] = {}
    for rel in root:
        target = rel.get("Target") or ""
        if rel.get("TargetMode") == "External":
            continue
        path = PurePosixPath(target.lstrip("/")) if target.startswith("/") else p.parent / target
        parts: list[str] = []
        for seg in path.parts:
            if seg == "..":
                parts = parts[:-1]
            elif seg != ".":
                parts.append(seg)
        out[rel.get("Id") or ""] = "/".join(parts)
    return out


_ROMAN = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"),
          (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]  # fmt: skip
_GANADA = "가나다라마바사아자차카타파하"
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def _format_number(n: int, fmt: str) -> str:
    if fmt in ("upperLetter", "lowerLetter"):
        s = chr(ord("A") + (n - 1) % 26)
        return s if fmt == "upperLetter" else s.lower()
    if fmt in ("upperRoman", "lowerRoman"):
        out = ""
        for v, sym in _ROMAN:
            while n >= v:
                out, n = out + sym, n - v
        return out.upper() if fmt == "upperRoman" else out
    if fmt in ("ganada", "koreanDigital") and 0 < n <= len(_GANADA):
        return _GANADA[n - 1]
    if fmt == "decimalEnclosedCircle" and 0 < n <= len(_CIRCLED):
        return _CIRCLED[n - 1]
    if fmt in ("bullet", "none"):
        return ""
    return str(n)


class _Numbering:
    """DOCX 자동 번호 (`w:numPr`) → "1. " 같은 글.

    실측 월례 시험지는 문항 번호가 모두 자동 번호다."""

    def __init__(self, root: _El | None) -> None:
        self.levels: dict[str, dict[str, tuple[int, str, str]]] = {}
        self.counts: dict[tuple[bool, str], dict[int, int]] = {}
        if root is None:
            return
        abstract: dict[str, dict[str, tuple[int, str, str]]] = {}
        for a in root.iter(W + "abstractNum"):
            lv: dict[str, tuple[int, str, str]] = {}
            for lvl in a.iter(W + "lvl"):
                start = lvl.find(W + "start")
                fmt = lvl.find(W + "numFmt")
                text = lvl.find(W + "lvlText")
                lv[lvl.get(W + "ilvl") or "0"] = (
                    int(start.get(W + "val") or 1) if start is not None else 1,
                    (fmt.get(W + "val") or "decimal") if fmt is not None else "decimal",
                    (text.get(W + "val") or "") if text is not None else "",
                )
            abstract[a.get(W + "abstractNumId") or ""] = lv
        for num in root.iter(W + "num"):
            ref = num.find(W + "abstractNumId")
            if ref is not None:
                self.levels[num.get(W + "numId") or ""] = abstract.get(ref.get(W + "val") or "", {})

    def label(self, ppr: _El | None, *, note: bool) -> str:
        """`note`: 미주·각주의 번호는 본문과 따로 센다 (Word도 그렇다 — 실측: 미주의 "1) 2)"가
        본문 소문항 번호를 9)부터 시작하게 만듦)."""
        if ppr is None or (num_pr := ppr.find(W + "numPr")) is None:
            return ""
        num_el, ilvl_el = num_pr.find(W + "numId"), num_pr.find(W + "ilvl")
        num_id = num_el.get(W + "val") or "" if num_el is not None else ""
        ilvl = int(ilvl_el.get(W + "val") or 0) if ilvl_el is not None else 0
        levels = self.levels.get(num_id)
        if not levels:
            return ""
        counts = self.counts.setdefault((note, num_id), {})
        start = levels.get(str(ilvl), (1, "decimal", ""))[0]
        counts[ilvl] = counts.get(ilvl, start - 1) + 1
        for deeper in [k for k in counts if k > ilvl]:
            del counts[deeper]
        _, fmt, text = levels.get(str(ilvl), (1, "decimal", f"%{ilvl + 1}."))
        if fmt == "bullet":
            return ""

        def sub(m: re.Match[str]) -> str:
            k = int(m.group(1)) - 1
            lv = levels.get(str(k), (1, "decimal", ""))
            return _format_number(counts.get(k, lv[0]), lv[1])

        out = re.sub(r"%(\d)", sub, text)
        return out if out.endswith(" ") else out + " "


class _DocxReader:
    def __init__(self, z: zipfile.ZipFile) -> None:
        self.z = z
        self.rels = _rels(z, "word/document.xml")
        self.numbering = _Numbering(_xml(z, "word/numbering.xml"))
        self.notes: dict[tuple[str, str], _El] = {}
        for kind in ("endnote", "footnote"):
            root = _xml(z, f"word/{kind}s.xml")
            if root is None:
                continue
            for n in root.iter(W + kind):
                if (n.get(W + "type") or "normal") == "normal":
                    self.notes[(kind, n.get(W + "id") or "")] = n
        self.note_rels = {k: _rels(z, f"word/{k}s.xml") for k in ("endnote", "footnote")}
        self.images: dict[str, bytes] = {}
        self.out: list[DocParagraph] = []

    def read(self) -> list[DocParagraph]:
        root = _xml(self.z, "word/document.xml")
        body = root.find(W + "body") if root is not None else None
        if body is not None:
            self.block(body, note=False, rels=self.rels)
        return self.out

    def block(self, el: _El, *, note: bool, rels: dict[str, str]) -> None:
        for child in el:
            if child.tag == W + "p":
                self.paragraph(child, note=note, rels=rels)
            elif child.tag == W + "tbl":
                for tc in child.iter(W + "tc"):
                    self.block(tc, note=note, rels=rels)
            elif child.tag in (W + "sdt", W + "sdtContent", W + "customXml"):
                self.block(child, note=note, rels=rels)

    def paragraph(self, p: _El, *, note: bool, rels: dict[str, str]) -> None:
        para = _Para(note)
        para.text(self.numbering.label(p.find(W + "pPr"), note=note))
        after: list[_El] = []  # 글 상자 (이 문단 뒤에)
        refs: list[tuple[str, str]] = []  # 미주·각주 (이 문단 뒤에)
        self.inline(p, para, after, refs, rels)
        if (done := para.done()) is not None:
            self.out.append(done)
        for box in after:
            self.block(box, note=note, rels=rels)
        for kind, nid in refs:
            if (n := self.notes.get((kind, nid))) is not None:
                self.block(n, note=True, rels=self.note_rels[kind])

    def inline(
        self, el: _El, para: _Para, after: list[_El], refs: list[tuple[str, str]],
        rels: dict[str, str],
    ) -> None:  # fmt: skip
        for c in el:
            tag = c.tag
            if not isinstance(tag, str) or tag in _SKIP_W:
                continue
            if tag in (MATH + "oMath", MATH + "oMathPara"):
                para.math(omml_to_latex(c))
            elif tag == W + "t":
                para.text(c.text or "")
            elif tag in (W + "tab", W + "br", W + "cr"):
                para.text(" ")
            elif tag in (W + "endnoteReference", W + "footnoteReference"):
                refs.append((tag.removeprefix(W).removesuffix("Reference"), c.get(W + "id") or ""))
            elif tag in (W + "drawing", W + "pict", W + "object"):
                for blip in c.iter(A_BLIP, V_IMAGEDATA):
                    ref = blip.get(R_EMBED) or blip.get(R_ID)
                    if ref and (target := rels.get(ref)):
                        self._image(target, para)
                after += list(c.iter(W + "txbxContent"))
            else:
                self.inline(c, para, after, refs, rels)  # w:r, w:hyperlink, w:ins, w:smartTag …

    def _image(self, target: str, para: _Para) -> None:
        if target not in self.images:
            try:
                self.images[target] = self.z.read(target)
            except KeyError:
                return
        para.image(target)


def read_docx(path: Path) -> ParsedDocument:
    with zipfile.ZipFile(path) as z:
        r = _DocxReader(z)
        paragraphs = r.read()
        return ParsedDocument(path, paragraphs, r.images)


# ---------------------------------------------------------------- HWPX

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HC = "{http://www.hancom.co.kr/hwpml/2011/core}"
_OPF_ITEM = "{http://www.idpf.org/2007/opf/}item"
_HWPX_SKIP = {HP + "header", HP + "footer", HP + "linesegarray", HP + "secPr", HP + "pageNum",
              HP + "autoNum", HP + "newNum", HP + "colPr", HP + "shapeComment"}  # fmt: skip


class _HwpxReader:
    def __init__(self, z: zipfile.ZipFile) -> None:
        self.z = z
        self.bin: dict[str, str] = {}
        hpf = _xml(z, "Contents/content.hpf")
        if hpf is not None:
            for item in hpf.iter():
                if isinstance(item.tag, str) and item.tag.endswith("}item") and item.get("href"):
                    self.bin[item.get("id") or ""] = item.get("href") or ""
        self.images: dict[str, bytes] = {}
        self.out: list[DocParagraph] = []

    def sections(self) -> list[str]:
        names = [n for n in self.z.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n)]
        return sorted(names, key=lambda n: int(re.sub(r"\D", "", n)))

    def read(self) -> list[DocParagraph]:
        for name in self.sections():
            root = _xml(self.z, name)
            if root is not None:
                self.block(root, note=False)
        return self.out

    def block(self, el: _El, *, note: bool) -> None:
        for p in el:
            if p.tag == HP + "p":
                self.paragraph(p, note=note)

    def paragraph(self, p: _El, *, note: bool) -> None:
        para = _Para(note)
        after: list[tuple[_El, bool]] = []  # (subList, 미주·각주인가)
        self.inline(p, para, after)
        if (done := para.done()) is not None:
            self.out.append(done)
        for sub, is_note in after:
            self.block(sub, note=note or is_note)

    def inline(self, el: _El, para: _Para, after: list[tuple[_El, bool]]) -> None:
        for c in el:
            tag = c.tag
            if not isinstance(tag, str) or tag in _HWPX_SKIP:
                continue
            if tag == HP + "t":
                para.text(c.text or "")
                for sub in c:  # <hp:t>글<hp:tab/>글</hp:t>
                    para.text(" " if sub.tag in (HP + "tab", HP + "lineBreak") else "")
                    para.text(sub.tail or "")
            elif tag == HP + "equation":
                from dawoni.export.hwpx import from_eqedit

                script = c.find(HP + "script")
                src = (script.text or "") if script is not None else ""
                try:
                    para.math(from_eqedit(src))
                except Exception:  # 변환기가 모르는 식: 스크립트 그대로 (AI가 읽을 수 있음)
                    log.warning("한글 수식을 LaTeX로 바꾸지 못함: %r", src)
                    para.math(src)
            elif tag == HP + "pic":
                img = c.find(".//" + HC + "img")
                ref = img.get("binaryItemIDRef") if img is not None else None
                if ref and (target := self.bin.get(ref)):
                    self._image(target, para)
            elif tag in (HP + "endNote", HP + "footNote"):
                after += [(s, True) for s in c.iter(HP + "subList")]
            elif tag == HP + "tbl":
                after += [(s, False) for s in c.iter(HP + "subList")]
            elif tag == HP + "subList":  # 글 상자(drawText) 등
                after.append((c, False))
            else:
                self.inline(c, para, after)  # run, ctrl, rect, container, drawText …

    def _image(self, target: str, para: _Para) -> None:
        if target not in self.images:
            try:
                self.images[target] = self.z.read(target)
            except KeyError:
                return
        para.image(target)


def read_hwpx(path: Path) -> ParsedDocument:
    with zipfile.ZipFile(path) as z:
        r = _HwpxReader(z)
        paragraphs = r.read()
        return ParsedDocument(path, paragraphs, r.images)


def read_document(path: Path) -> ParsedDocument:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return read_docx(path)
    if suffix == ".hwpx":
        return read_hwpx(path)
    raise ValueError(f"DOCX·HWPX가 아님: {path.name}")


# ---------------------------------------------------------------- 문항으로 나누기


@dataclass
class KeyPiece:
    """한 문항의 해설 글 (미주·해설 구간) + 그 안의 그림 파일."""

    text: str
    images: list[Path] = field(default_factory=lambda: list[Path]())


@dataclass
class DocAnalysis:
    layout: DocumentLayout
    """문항 영역. 쪽 대신 `QUESTIONS_PER_CALL`개씩 묶은 번호(`Line.page`)를 쓴다 — 상자는 없다."""
    keys: dict[str, KeyPiece]
    """문항 번호 → 해설 조각 (미주·각주, "정답 및 해설" 뒤에서 그 번호로 시작하는 부분)."""
    key_rest: str = ""
    """해설 구간 중 어느 문항에도 붙이지 못한 글."""


def _png(data: bytes, out: Path) -> Path | None:
    """문서 그림 → PNG (BMP·JPG·GIF·WMF …). PIL이 못 여는 형식(EMF 일부)은 None."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            rgb = im.convert("RGBA") if im.mode in ("P", "LA", "RGBA") else im.convert("RGB")
            out.parent.mkdir(parents=True, exist_ok=True)
            rgb.save(out, "PNG")
    except Exception:
        return None
    return out


def _anchor(text: str) -> str | None:
    return _anchor_label(text) if _ANCHOR.match(text) else None


def analyze_document(path: Path, work_dir: Path) -> DocAnalysis:
    """`work_dir`: 꺼낸 그림 PNG를 둘 곳."""
    doc = read_document(path)
    layout = DocumentLayout(path=path, pages=[], regions=[], scanned=False)
    files: dict[str, Path | None] = {}

    def image_file(ref: str) -> Path | None:
        if ref not in files:
            name = re.sub(r"[^\w.-]", "_", ref)
            files[ref] = _png(doc.images[ref], work_dir / "doc_images" / f"{name}.png")
            if files[ref] is None:
                layout.warnings.append(
                    f"그림 {PurePosixPath(ref).name}을 읽지 못함 (형식) — 원본 확인"
                )
        return files[ref]

    # 해설 구간이 시작되는 곳 (그 뒤는 문항이 아니다)
    key_start = next(
        (i for i, p in enumerate(doc.paragraphs) if not p.note and _KEY_HEAD.match(p.text)),
        len(doc.paragraphs),
    )
    body = [(i, p) for i, p in enumerate(doc.paragraphs[:key_start]) if not p.note]
    cands = [(i, a) for i, p in body if (a := _anchor(p.text)) is not None]
    keep = number_chain([(k, int(a)) for k, (_, a) in enumerate(cands) if a.isdigit()])
    starts = {i: a for k, (i, a) in enumerate(cands) if not a.isdigit() or k in keep}
    if not starts:
        layout.warnings.append("문항 번호를 찾지 못했습니다.")

    regions: list[Region] = []
    notes: dict[str, list[DocParagraph]] = {}
    current: Region | None = None
    for i, p in enumerate(doc.paragraphs[:key_start]):
        if p.note:
            if current is not None:
                notes.setdefault(current.label, []).append(p)
            continue
        if i in starts:
            current = Region(label=starts[i])
            regions.append(current)
        elif SET_MARK.match(p.text) or _FENCE.match(p.text):
            current = None
        if current is None:
            continue
        current.lines.append(Line(0, _NOWHERE, p.text))
        for ref, mark in zip(p.images, p.choice_marks, strict=True):
            if (f := image_file(ref)) is not None:
                current.figures.append(Figure(0, _NOWHERE, "image", near_choice=mark, file=f))

    # 해설 구간: 번호 줄로 문항마다 나눈다 (번호 앞 글은 어디에도 붙이지 않음)
    rest: list[str] = []
    labels = {r.label for r in regions}
    owner: str | None = None
    for p in doc.paragraphs[key_start:]:
        a = _anchor(p.text)
        if a is not None and a in labels:
            owner = a
        if owner is None:
            rest.append(p.text)
        else:
            notes.setdefault(owner, []).append(p)

    for r in regions:
        r.points = marked_points(r.text)
        for k, f in enumerate(r.figures, start=1):
            f.id = f"F{k}"
    _chunk(regions)
    for r in regions:
        r.figures = [
            Figure(r.lines[0].page, _NOWHERE, f.kind, f.id, f.near_choice, f.file)
            for f in r.figures
        ]
    layout.regions = regions
    keys: dict[str, KeyPiece] = {}
    for label, ps in notes.items():
        imgs = [f for p in ps for ref in p.images if (f := image_file(ref)) is not None]
        text = "\n".join(p.text for p in ps).strip()
        if text or imgs:
            keys[label] = KeyPiece(text, imgs)
    return DocAnalysis(layout, keys, "\n".join(rest).strip())


def _chunk(regions: list[Region]) -> None:
    """문항을 AI 호출 묶음으로 (묶음 번호를 `Line.page`에) — 문항 수와 글자 수로 끊는다."""
    group, count, chars = 1, 0, 0
    for r in regions:
        size = len(r.text)
        if count and (count >= QUESTIONS_PER_CALL or chars + size > CHARS_PER_CALL):
            group, count, chars = group + 1, 0, 0
        count, chars = count + 1, chars + size
        r.lines = [Line(group, ln.rect, ln.text) for ln in r.lines]


def region_call(r: Region) -> int:
    """문항 구조화 호출 묶음: PDF는 첫 상자의 쪽, 문서는 `_chunk`가 정한 번호."""
    if r.boxes:
        return r.boxes[0][0]
    return r.lines[0].page if r.lines else 0


def is_document(path: Path) -> bool:
    return path.suffix.lower() in DOC_SUFFIXES
