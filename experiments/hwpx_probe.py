"""실험 (M0): 학생 판독본·채점본 HWPX를 만들고, 다시 읽고, (선택) 한글에서 열어 본다.

PLAN.md §5-2(판독본 구조), §5-3(만드는 법), §9-1(채점본 꾸밈)의 요소를 python-hwpx로 하나씩 넣는다:
머리 표식, 원본 스캔 그림, 소문항 표식(`■ 1-(1)`, 고정 문단 스타일), 판독문(불확실한 글자는 노란
형광펜), 수식(LaTeX → 한글 수식), 지운 줄(취소선), 점수표, 파란·빨간 코멘트, 근거 인용(맞은 곳
연두 형광펜, 틀린 곳 빨간 밑줄), 메모.

    uv run python experiments/hwpx_probe.py                      # 만들고 다시 읽기
    uv run python experiments/hwpx_probe.py --scan 답안.pdf --page 2  # 원본 그림을 실제 스캔으로
    uv run python experiments/hwpx_probe.py --hancom             # + 한글(COM)로 열어 PDF·HWPX 다시 저장

`--hancom`은 Windows + 한글 + `uv sync --extra windows`에서만 된다. 한글이 다시 저장한 HWPX를
앱이 다시 읽어도 같은지(강사가 한글에서 저장하는 경우) 확인하고, PDF 첫 쪽을 PNG로 남긴다.
결과물은 `experiments/out/`(git 제외).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import sys
import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hwpx.document import HwpxDocument
from hwpx.equation.authoring import latex_to_eqedit
from hwpx.equation.eqedit import eqedit_to_latex
from hwpx.oxml.paragraph import HwpxOxmlParagraph
from lxml import etree

from dawoai.grading.checks import normalize_latex
from dawoai.models.grading import TranscriptLine

warnings.filterwarnings("ignore", category=DeprecationWarning, module="hwpx")

OUT = Path(__file__).resolve().parent / "out"
FONT = "맑은 고딕"
NORMAL = "0"  # 바탕글
STYLE_HEAD = "다워이 머리"
STYLE_PART = "다워이 소문항"
STYLE_GRADED = "다워이 채점"  # 채점 부분의 첫 문단 (새 쪽). 다시 읽기는 여기서 멈춘다
MARK = "■"
PART_MARK = re.compile(rf"^{MARK}\s*(.+?)\s*$")
UNASSIGNED = "문항을 알 수 없는 줄"

YELLOW = "#FFF200"  # 불확실한 글자
GREEN = "#C6EFCE"  # 맞은 근거 (연두 형광펜)
BLUE = "#1F4E9E"  # 맞은 근거 코멘트
RED = "#C00000"  # 감점 코멘트, 틀린 곳 밑줄
GRAY = "#7F7F7F"

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


# ---------------------------------------------------------------- 표본 자료


@dataclass
class Student:
    exam: str
    klass: str
    name: str
    school: str
    """name: 같은 반 동명이인은 학원 방식대로 이름 뒤 알파벳으로 가른다 (김다원A, 김다원B)"""

    def head(self) -> str:
        return f"{self.exam} · {self.klass} · {self.name} · {self.school}"

    def filename(self, question: str) -> str:
        """강사 1명이 문항 1개를 맡으므로 학생 × 문항 = 파일 1개: `반_이름_문항.hwpx`."""
        return f"{self.klass}_{self.name}_{question}.hwpx"


def bundle_filename(klass: str, question: str) -> str:
    """반별 묶음 `(반별)_반_문항.hwpx`. `(`는 탐색기 이름순(StrCmpLogicalW)에서도, 글자 코드 순에서도
    숫자·한글·영문 앞이라 묶음 파일이 학생 파일보다 위에 온다 (2026-11-04 확인)."""
    return f"(반별)_{klass}_{question}.hwpx"


@dataclass
class PartGrade:
    awarded: float
    points: float
    good: list[tuple[str, str]] = field(default_factory=lambda: list[tuple[str, str]]())
    """(판독문 인용, 맞은 이유)"""
    bad: list[tuple[str, str]] = field(default_factory=lambda: list[tuple[str, str]]())
    """(판독문 인용, 감점 이유)"""


STUDENT = Student("2026-11 중간고사 물리", "2반", "김다원", "○○고")
CLASSMATE = Student("2026-11 중간고사 물리", "2반", "강민수", "○○고")  # 묶음 정렬 확인용
LINES: list[TranscriptLine] = [
    TranscriptLine(part="1-(1)", text="옴의 법칙으로 전류를 구하면", math=r"I = \frac{V}{R} = \frac{6}{3} = 2"),
    TranscriptLine(part="1-(1)", text="따라서 전류는 2 A이다."),
    TranscriptLine(part="1-(2)", text="저항이 반이 되면 전류가 2배라 2배 밝다", uncertain=["2배라"]),
    TranscriptLine(part="1-(2)", text="P = VI", struck=True),
    TranscriptLine(part="2-①", text="㉢이 가장 밝다", math=r"P = I^2 R,\ \sqrt{2}\,v_0 \ge 3"),
    TranscriptLine(part=None, text="(여백에 쓴 낙서)"),
]  # fmt: skip
GRADES: dict[str, PartGrade] = {
    "1-(1)": PartGrade(4, 4, good=[("옴의 법칙으로 전류를 구하면", "옴의 법칙으로 전류를 구함 → 2점"),
                                   ("전류는 2 A", "답이 맞음 → 2점")]),
    "1-(2)": PartGrade(0, 3, bad=[("2배 밝다", "전류가 2배면 밝기(전력)는 4배여야 함 → 0점")]),
    "2-①": PartGrade(2, 2, good=[("㉢이 가장 밝다", "결론이 해설지와 같음")]),
}  # fmt: skip


def placeholder_scan() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((40, 40, 1200, 1714), outline="black", width=4)
    d.text((80, 80), "scan page 1 (placeholder)", fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def scan_page(pdf: Path, page: int) -> bytes:
    import pymupdf

    with pymupdf.open(pdf) as doc:
        pix = doc[page - 1].get_pixmap(dpi=110)
        return pix.tobytes("png")


# ---------------------------------------------------------------- 만들기


class Builder:
    def __init__(self) -> None:
        self.doc = HwpxDocument.new()
        st = self.doc.styles
        st.ensure_font(
            FONT, lang=["HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER"]
        )
        fonts = dict.fromkeys(
            ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user"), FONT
        )

        def run(**kw: object) -> str:
            return st.ensure_run(font_by_lang=fonts, **kw)  # pyright: ignore[reportArgumentType]

        self.plain = run()
        self.small_gray = run(color=GRAY, size=9)
        self.bold = run(bold=True, size=12)
        self.part = run(bold=True, color="#000000")
        self.struck = run(strike=True, color=GRAY)
        self.blue = run(color=BLUE)
        self.red = run(color=RED)
        self.red_underline = run(underline=True, underline_color=RED, underline_shape="SOLID")
        self.style_head = st.ensure_style(STYLE_HEAD, char_pr_id_ref=self.bold)
        self.style_part = st.ensure_style(STYLE_PART, char_pr_id_ref=self.part)
        self.style_graded = st.ensure_style(STYLE_GRADED, char_pr_id_ref=self.bold)
        self.first = True

    def para(
        self, text: str = "", *, char: str | None = None, style: str | None = None
    ) -> HwpxOxmlParagraph:
        if self.first:  # 빈 새 문서의 첫 문단을 쓴다 (빈 줄이 남지 않게)
            self.first = False
            p = self.doc.paragraphs[0]
            p.text = text
            if char:
                for r in p.runs:
                    r.char_pr_id_ref = char
        else:
            # inherit_style=False: 기본값은 앞 문단 스타일을 물려받아 머리 다음 문단까지 '다워이 머리'가 됨
            p = self.doc.add_paragraph(text, char_pr_id_ref=char or self.plain, inherit_style=False)
        if style is not None:
            p.style_id_ref = style
        return p

    def runs(self, p: HwpxOxmlParagraph, pieces: list[tuple[str, str]]) -> None:
        for text, char in pieces:
            if text:
                p.add_run(text, char_pr_id_ref=char)

    def equation(self, p: HwpxOxmlParagraph, latex: str) -> bool:
        """한글 수식으로 넣는다. 못 바꾸면 글자로 넣고 False (PLAN.md §14)."""
        try:
            script = latex_to_eqedit(_preclean(latex))
        except Exception:
            p.add_run(f" [식: {latex}]", char_pr_id_ref=self.red)
            return False
        p.add_run(" ", char_pr_id_ref=self.plain)
        self.doc.shapes.add_equation(script, paragraph=p)
        return True

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save_to_path(path)
        return path


_SPACING = re.compile(re.escape("\\") + r"(?:quad|qquad|[ ,;:!])")


def _preclean(latex: str) -> str:
    return _SPACING.sub(" ", latex)


def _by_part(lines: list[TranscriptLine]) -> dict[str, list[TranscriptLine]]:
    out: dict[str, list[TranscriptLine]] = {}
    for line in lines:
        out.setdefault(line.part or UNASSIGNED, []).append(line)
    return out


def build_reading(
    path: Path, student: Student, lines: list[TranscriptLine], scans: list[bytes]
) -> Path:
    """판독본: 머리 → 원본 그림 → 소문항별 판독문."""
    b = Builder()
    write_reading(b, student, lines, scans)
    return b.save(path)


def write_reading(
    b: Builder, student: Student, lines: list[TranscriptLine], scans: list[bytes]
) -> HwpxOxmlParagraph:
    """원본 그림 → 학생이 쓴 글 그대로(글자·수식)를 쓰고 머리 문단을 돌려준다."""
    head = b.para(student.head(), char=b.bold, style=b.style_head)
    for i, png in enumerate(scans, start=1):
        b.para(f"[원본] 답안 {i}쪽", char=b.small_gray)
        b.doc.add_picture(png, "png", width_mm=150, style_id_ref=NORMAL)
    for part, part_lines in _by_part(lines).items():
        b.para(f"{MARK} {part}", char=b.part, style=b.style_part)
        for line in part_lines:
            p = b.para()
            if line.struck:
                b.runs(p, [("[지움] ", b.small_gray), (line.text, b.struck)])
            else:
                b.runs(p, [(line.text, b.plain)])
                for u in line.uncertain:
                    p.add_highlight(color=YELLOW, match=u)
            if line.math:
                b.equation(p, line.math)
    return head


def _split_quote(text: str, quote: str) -> tuple[str, str, str] | None:
    i = text.find(quote)
    return (text[:i], quote, text[i + len(quote) :]) if i >= 0 else None


def build_graded(
    path: Path, student: Student, lines: list[TranscriptLine], grades: dict[str, PartGrade]
) -> Path:
    """채점본: 머리 → 점수표 → 소문항별 판독문(근거 강조) + 점수 + 코멘트 + 메모."""
    b = Builder()
    write_graded(b, student, lines, grades, style=b.style_head)
    return b.save(path)


def build_combined(
    path: Path,
    student: Student,
    lines: list[TranscriptLine],
    grades: dict[str, PartGrade],
    scans: list[bytes],
) -> Path:
    """학생 × 문항 파일 1개: 판독본이 쭉 나오고, 채점본은 새 쪽에서 시작한다 (§15-4 답, 2026-11-04)."""
    b = Builder()
    write_student(b, student, lines, grades, scans)
    return b.save(path)


def write_student(
    b: Builder,
    student: Student,
    lines: list[TranscriptLine],
    grades: dict[str, PartGrade],
    scans: list[bytes],
) -> HwpxOxmlParagraph:
    head = write_reading(b, student, lines, scans)
    write_graded(b, student, lines, grades, style=b.style_graded).element.set("pageBreak", "1")
    return head


Entry = tuple[Student, list[TranscriptLine], dict[str, PartGrade], list[bytes]]


def build_class_bundle(path: Path, entries: list[Entry]) -> Path:
    """반별 묶음: 학생 파일을 이름순으로 이어 붙인 인쇄용 파일. 학생마다 새 쪽에서 시작한다.
    앱은 묶음을 다시 읽지 않는다 — 강사가 고치는 곳은 학생 파일이고, 묶음은 그때마다 다시 만든다."""
    b = Builder()
    for i, entry in enumerate(sorted(entries, key=lambda e: e[0].name)):
        head = write_student(b, *entry)
        if i:
            head.element.set("pageBreak", "1")
    return b.save(path)


def count_heads(path: Path) -> int:
    doc = HwpxDocument.open(path)
    style = doc.styles.get(STYLE_HEAD)
    return sum(1 for p in doc.paragraphs if style and str(p.style_id_ref) == str(style.id))


def write_graded(
    b: Builder,
    student: Student,
    lines: list[TranscriptLine],
    grades: dict[str, PartGrade],
    *,
    style: str,
) -> HwpxOxmlParagraph:
    """채점 부분을 쓰고 그 머리 문단을 돌려준다."""
    total = sum(g.awarded for g in grades.values())
    full = sum(g.points for g in grades.values())
    head = b.para(f"{student.head()} · 총점 {total:g} / {full:g}", char=b.bold, style=style)
    table = b.doc.add_table(len(grades) + 1, 3)
    for c, h in enumerate(("소문항", "점수", "배점")):
        table.set_cell_text(0, c, h)
    for r, (part, g) in enumerate(grades.items(), start=1):
        for c, v in enumerate((part, f"{g.awarded:g}", f"{g.points:g}")):
            table.set_cell_text(r, c, v)
    for part, part_lines in _by_part(lines).items():
        g = grades.get(part)
        b.para(f"{MARK} {part}", char=b.part, style=b.style_part)
        for line in part_lines:
            p = b.para()
            if line.struck:
                b.runs(p, [("[지움] ", b.small_gray), (line.text, b.struck)])
            else:
                bad = next(
                    (s for q, _ in (g.bad if g else []) if (s := _split_quote(line.text, q))), None
                )
                if bad:  # 틀린 곳: 빨간 밑줄
                    b.runs(p, [(bad[0], b.plain), (bad[1], b.red_underline), (bad[2], b.plain)])
                else:
                    b.runs(p, [(line.text, b.plain)])
                for q, _ in g.good if g else []:
                    if q in line.text:  # 맞은 곳: 연두 형광펜
                        p.add_highlight(color=GREEN, match=q)
            if line.math:
                b.equation(p, line.math)
        if g is None:
            continue
        score = b.para(f"점수 {g.awarded:g} / {g.points:g}", char=b.bold)
        for _, why in g.good:
            b.para(f"○ {why}", char=b.blue)
        for _, why in g.bad:
            b.para(f"✕ {why}", char=b.red)
        if g.bad:
            b.doc.notes.add_memo(g.bad[0][1], anchor=score, author="다워이")
    return head


# ---------------------------------------------------------------- 다시 읽기


@dataclass
class ReadBack:
    head: str
    parts: dict[str, list[str]]
    """소문항 → 줄 (글자 + 수식은 `\\( … \\)`)."""
    pictures: int


def _paragraph_text(p: etree._Element) -> str:  # pyright: ignore[reportPrivateUsage]
    out: list[str] = []
    for el in p.iter(f"{HP}t", f"{HP}script"):
        if el.tag == f"{HP}t":
            out.append("".join(el.itertext()))
        else:
            out.append(f"\\({eqedit_to_latex(el.text or '')}\\)")
    return "".join(out).strip()


def read_back(path: Path) -> ReadBack:
    doc = HwpxDocument.open(path)
    styles = {s: doc.styles.get(s) for s in (STYLE_HEAD, STYLE_PART, STYLE_GRADED)}
    ids = {k: (str(v.id) if v else None) for k, v in styles.items()}
    head = ""
    parts: dict[str, list[str]] = {}
    current: str | None = None
    pictures = 0
    for p in doc.paragraphs:
        el = p.element
        pictures += len(list(el.iter(f"{HP}pic")))
        if el.getparent() is not None and el.getparent().tag.endswith("subList"):
            continue  # 표 칸 안의 문단
        text = _paragraph_text(el)
        style = str(p.style_id_ref) if p.style_id_ref is not None else None
        if style is not None and style == ids[STYLE_GRADED]:
            break  # 채점 부분은 앱이 다시 만든다 — 판독문만 읽는다
        m = PART_MARK.match(text)
        if style == ids[STYLE_HEAD] or (not head and " · " in text and not parts):
            head = head or text
        elif m and (style == ids[STYLE_PART] or text.startswith(MARK)):
            current = m.group(1)
            parts.setdefault(current, [])
        elif current is not None and text:
            parts[current].append(text)
    return ReadBack(head, parts, pictures)


def expected(lines: list[TranscriptLine]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for part, part_lines in _by_part(lines).items():
        out[part] = []
        for line in part_lines:
            text = f"[지움] {line.text}" if line.struck else line.text
            if line.math:
                text += f" \\({line.math}\\)"
            out[part].append(text)
    return out


def _canon(s: str) -> str:
    s = _preclean(s)
    s = re.sub(r"\{([^{}\\])\}", lambda m: m.group(1), normalize_latex(s))
    return s.replace("\\geq", "\\ge").replace("\\leq", "\\le")


def compare(got: ReadBack, want: dict[str, list[str]], student: Student) -> list[str]:
    problems: list[str] = []
    if not got.head.startswith(student.head()):
        problems.append(f"머리 다름: {got.head!r}")
    if list(got.parts) != list(want):
        problems.append(f"소문항 표식 다름: {list(got.parts)} != {list(want)}")
    for part, lines in want.items():
        g = got.parts.get(part, [])
        if [_canon(x) for x in g] != [_canon(x) for x in lines]:
            problems.append(f"{part} 판독문 다름:\n    읽음 {g}\n    기대 {lines}")
    return problems


def teacher_edit(src: Path, dest: Path) -> Path:
    """강사가 한글에서 판독문 한 줄을 고친 것처럼: 1-(2) 첫 줄의 '2배 밝다' → '4배 밝다'."""
    doc = HwpxDocument.open(src)
    for p in doc.paragraphs:
        if "2배 밝다" in p.text:
            p.text = p.text.replace("2배 밝다", "4배 밝다")
            break
    doc.save_to_path(dest)
    return dest


# ---------------------------------------------------------------- 한글(COM) 확인


@contextlib.contextmanager
def hancom() -> Iterator[Any]:
    """한글 하나를 띄워 여러 파일에 쓴다. 파일마다 띄우고 끄면 두 번째 Dispatch가 아직 꺼지는 중인
    한글에 붙어 'The server threw an exception' → 'RPC server is unavailable'로 죽는다 (2026-11-04)."""
    import win32com.client  # pyright: ignore[reportMissingModuleSource]

    hwp = win32com.client.DispatchEx("HWPFrame.HwpObject")
    try:
        # 보안 모듈(FilePathCheckerModule)이 등록된 PC면 접근 확인 창이 뜨지 않는다. 없으면 한글이
        # 창을 띄워 묻는다 — 창을 숨기면 아무도 답할 수 없어 멈추므로 보이게 둔다 (2026-11-04 실측).
        with contextlib.suppress(Exception):
            hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
        hwp.XHwpWindows.Item(0).Visible = True
        yield hwp
    finally:
        with contextlib.suppress(Exception):
            hwp.Clear(1)
        hwp.Quit()


def hancom_roundtrip(hwp: Any, src: Path) -> tuple[Path, Path, list[Path]]:
    """한글로 열어 PDF·HWPX로 다시 저장한다. (PDF, 다시 저장한 HWPX, PDF 쪽마다 PNG)."""
    import pymupdf

    pdf = src.with_suffix(".hancom.pdf")
    resaved = src.with_name(src.stem + ".hancom.hwpx")
    print(
        f"  한글로 여는 중: {src.name} — '접근 허용' 창이 뜨면 [모두 허용]을 누르세요", flush=True
    )
    if not hwp.Open(str(src), "HWPX", "forceopen:true"):
        raise RuntimeError(f"한글이 열지 못함: {src}")
    hwp.SaveAs(str(pdf), "PDF", "")
    hwp.SaveAs(str(resaved), "HWPX", "")
    hwp.Clear(1)
    pngs: list[Path] = []
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc, start=1):
            pngs.append(src.with_suffix(f".hancom.p{i}.png"))
            page.get_pixmap(dpi=80).save(pngs[-1])
    return pdf, resaved, pngs


# ---------------------------------------------------------------- 실행


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--scan", type=Path, help="원본 그림으로 쓸 스캔 PDF")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--hancom", action="store_true", help="한글(COM)로 열어 PDF·HWPX 다시 저장")
    args = ap.parse_args(argv)

    scans = [scan_page(args.scan, args.page)] if args.scan else [placeholder_scan()]
    want = expected(LINES)
    reading = build_reading(OUT / "판독본_김다원.hwpx", STUDENT, LINES, scans)
    graded = build_graded(OUT / "채점본_김다원.hwpx", STUDENT, LINES, GRADES)
    print(
        f"만듦: {reading.name} ({reading.stat().st_size:,} B), {graded.name} ({graded.stat().st_size:,} B)"
    )

    failed = False
    for label, path in (("판독본", reading), ("채점본", graded)):
        issues = HwpxDocument.open(path).validate().issues
        print(f"{label} 스키마 검사: {issues if issues else '통과'}")
        failed |= bool(issues)
    got = read_back(reading)
    problems = compare(got, want, STUDENT)
    print(f"판독본 다시 읽기: {'같음' if not problems else '다름'} (그림 {got.pictures}개)")
    for p in problems:
        print("  - " + p)
    failed |= bool(problems)

    edited = teacher_edit(reading, OUT / "판독본_김다원.강사수정.hwpx")
    fixed = read_back(edited).parts.get("1-(2)", [])
    ok = fixed[:1] == ["저항이 반이 되면 전류가 2배라 4배 밝다"]
    print(f"강사 수정 흉내 → 다시 읽기: {'반영됨' if ok else f'반영 안 됨 {fixed}'}")
    failed |= not ok

    # 학생 × 문항 파일: 원본 그림 → 학생이 쓴 글 → 새 쪽 → 채점 (§15-4 답), 반별 묶음
    combined: dict[str, tuple[Path, Student, dict[str, list[str]]]] = {}
    bundles: list[Path] = []
    for q in dict.fromkeys(line.part.split("-")[0] for line in LINES if line.part):
        q_lines = [x for x in LINES if x.part and x.part.split("-")[0] == q]
        q_grades = {k: v for k, v in GRADES.items() if k.split("-")[0] == q}
        entries: list[Entry] = [(st, q_lines, q_grades, scans) for st in (STUDENT, CLASSMATE)]
        for st, *rest in entries:
            path = build_combined(OUT / st.filename(q), st, *rest)
            issues = HwpxDocument.open(path).validate().issues
            q_problems = compare(read_back(path), expected(q_lines), st)
            print(f"문항 파일 {path.name}: 스키마 {issues or '통과'}, "
                  f"다시 읽기(판독 부분만) {q_problems or '같음'}")  # fmt: skip
            failed |= bool(issues) or bool(q_problems)
            combined[path.name] = (path, st, expected(q_lines))
        bundle = build_class_bundle(OUT / bundle_filename(STUDENT.klass, q), entries)
        issues = HwpxDocument.open(bundle).validate().issues
        heads = count_heads(bundle)
        print(f"반별 묶음 {bundle.name}: 스키마 {issues or '통과'}, 학생 {heads}명")
        failed |= bool(issues) or heads != len(entries)
        bundles.append(bundle)

    if args.hancom:
        saved: dict[str, Path] = {}
        with hancom() as hwp:
            targets = [("판독본", reading), ("채점본", graded)]
            targets += [(name, path) for name, (path, _, _) in combined.items()]
            targets += [(p.name, p) for p in bundles]
            for label, path in targets:
                pdf, saved[label], pngs = hancom_roundtrip(hwp, path)
                print(
                    f"한글 {label}: PDF {pdf.name} ({len(pngs)}쪽), 다시 저장 {saved[label].name}"
                )
        for name, (_, st, q_want) in combined.items():
            q_problems = compare(read_back(saved[name]), q_want, st)
            print(f"한글이 다시 저장한 {name} 다시 읽기: {q_problems or '같음'}")
            failed |= bool(q_problems)
        got2 = read_back(saved["판독본"])
        problems2 = compare(got2, want, STUDENT)
        print(
            f"한글이 다시 저장한 판독본 다시 읽기: {'같음' if not problems2 else '다름'} (그림 {got2.pictures}개)"
        )
        for p in problems2:
            print("  - " + p)
        failed |= bool(problems2)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
