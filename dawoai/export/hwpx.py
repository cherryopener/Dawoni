"""채점 HWPX 만들기 (PLAN.md §5-2, §9-1) — 코드만, 토큰 0.

학생 × 문항 = 파일 1개(`반_이름_문항.hwpx`): 머리 → 원본 스캔 그림 → 학생이 쓴 글 그대로(소문항 표식
`■ 1-(1)` 아래, 글 사이 식은 한글 수식) → 새 쪽에서 채점(점수표, 맞은 근거 연두 형광펜·틀린 곳 빨간
밑줄, 소문항 점수와 ○ 파란·✕ 빨간 이유 — 글에 섞인 식은 한글 수식). 반별 묶음
(`(반별)_반_문항.hwpx`)은 그 반 학생 파일을 이름순으로 이어 붙인 인쇄용 — 입력을 기다리는
학생은 "입력 대기" 쪽으로 넣어 먼저 만들고, 채워질 때마다 다시 만든다.

앱은 판독 부분만 다시 읽는다(`read_back`, 채점 머리 스타일에서 멈춤). 실험
`experiments/hwpx_probe.py`에서 한글 2024로 확인한 것을 옮겼다 (2026-11-04: 실제 학생 6명 21개
파일, 한글이 다시 저장해도 같음).
"""

from __future__ import annotations

import contextlib
import io
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from hwpx.document import HwpxDocument
from hwpx.equation.authoring import latex_to_eqedit
from hwpx.equation.eqedit import eqedit_to_latex
from hwpx.equation.measure import measure_equation
from hwpx.oxml.paragraph import HwpxOxmlParagraph
from lxml import etree

from dawoai.grading.checks import normalize_latex
from dawoai.models.grading import UNREADABLE, GradeResult, Rubric, TranscriptLine

warnings.filterwarnings("ignore", category=DeprecationWarning, module="hwpx")

FONT = "맑은 고딕"
NORMAL = "0"  # 바탕글
STYLE_HEAD = "다워이 머리"
STYLE_PART = "다워이 소문항"
STYLE_GRADED = "다워이 채점"  # 채점 부분의 첫 문단 (새 쪽). 다시 읽기는 여기서 멈춘다
MARK = "■"
PART_MARK = re.compile(rf"^{MARK}\s*(.+?)\s*$")
UNASSIGNED = "문항을 알 수 없는 줄"

YELLOW = "#FFF200"  # 불확실한 글자
PINK = "#FFC7CE"  # [판독불가]
GREEN = "#C6EFCE"  # 맞은 근거 (연두 형광펜)
BLUE = "#1F4E9E"  # 맞은 근거 코멘트
RED = "#C00000"  # 감점 코멘트, 틀린 곳 밑줄
GRAY = "#7F7F7F"

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
SCAN_WIDTH_PX = 1400
"""원본 그림을 이 폭으로 줄여 넣는다 (반별 묶음이 수십 MB가 되지 않게)."""


# ---------------------------------------------------------------- 넣을 것


@dataclass
class PartGrade:
    awarded: float
    points: float
    good: list[tuple[str, str]] = field(default_factory=lambda: list[tuple[str, str]]())
    """(판독문 인용, 맞은 이유). 인용 조각이 여럿이면 이유는 첫 조각에만."""
    bad: list[tuple[str, str]] = field(default_factory=lambda: list[tuple[str, str]]())
    """(판독문 인용, 감점 이유)."""


@dataclass
class Sheet:
    """학생 한 명 × 문항 하나."""

    head: str
    """`시험 · 반 · 이름 · 학교` — 앱이 다시 읽을 때 누구 파일인지 확인한다."""
    lines: list[TranscriptLine]
    """소문항 표시는 `1-(1)`처럼 문항 번호를 붙여 둔다 (`sheet_lines`)."""
    scans: list[bytes] = field(default_factory=lambda: list[bytes]())
    """원본 쪽 그림 (PNG)."""
    grades: dict[str, PartGrade] | None = None
    """소문항(`1-(1)`) → 채점. None이면 채점 전 (판독본만)."""
    comment: str = ""
    notes: list[str] = field(default_factory=lambda: list[str]())
    """채점 머리 아래 회색 글씨 (예: 판독문 확인 전 채점)."""
    reading_notes: list[str] = field(default_factory=lambda: list[str]())
    """판독본 머리 아래 빨간 글씨 (예: 입력 대기, 확인 전). 다시 읽기는 소문항 표식 전 문단을 보지
    않으므로 판독문에 섞이지 않는다."""
    waiting: str | None = None
    """묶음에서 이 학생 대신 넣을 안내 ("입력 대기 …"). 있으면 판독·채점을 쓰지 않는다."""


def sheet_lines(question: str, lines: list[TranscriptLine]) -> list[TranscriptLine]:
    """판독문의 소문항 표시 `(1)`에 문항 번호를 붙인다 → `1-(1)` (소문항이 없으면 `1`)."""
    return [
        x.model_copy(update={"part": f"{question}-{x.part}" if x.part else question}) for x in lines
    ]


def _fragments(quote: str) -> list[str]:
    """채점 근거 인용 → 판독문에서 찾을 글 조각: `…`로 나뉜 조각의 식 밖 부분 (6자 이상)."""
    out: list[str] = []
    for chunk in re.split(r"…|\.\.\.|;;", quote):
        pieces = _INLINE.split(chunk)
        out += [s for i, x in enumerate(pieces) if i % 2 == 0 and len(s := x.strip()) >= 6]
    return out


def part_grades(question: str, g: GradeResult, rubric: Rubric) -> dict[str, PartGrade]:
    """채점 결과(채점 항목별) → 소문항별 점수·근거. 강사가 고친 점수가 있으면 그것을 쓴다."""
    part_of = {i.id: i.part for i in rubric.items}
    out: dict[str, PartGrade] = {}
    for j in g.judgements:
        part = part_of.get(j.rubric_item_id)
        pg = out.setdefault(f"{question}-{part}" if part else question, PartGrade(0, 0))
        pg.awarded += j.awarded
        pg.points += g.item_points.get(j.rubric_item_id, 0)
        quotes = _fragments(j.evidence_quote or "") or [""]
        if j.awarded > 0:
            pg.good += [(x, j.reason if not i else "") for i, x in enumerate(quotes)]
        if j.deduction_reason:  # 일부만 받은 항목은 근거를 맞은 곳으로 칠하고 이유만 단다
            bad = quotes if j.awarded == 0 else [""]
            pg.bad += [(x, j.deduction_reason if not i else "") for i, x in enumerate(bad)]
    for part, score in g.teacher_part_scores.items():
        key = f"{question}-{part}" if part else question
        if key in out:
            out[key].awarded = score
    return out


def png_scan(path: Path) -> bytes:
    """스캔 그림 → 줄인 PNG (python-hwpx는 PNG에서만 그림 높이를 잰다 — JPEG는 정사각형이 됨)."""
    from PIL import Image

    with Image.open(path) as img:
        im = img.convert("RGB")
        if im.width > SCAN_WIDTH_PX:
            im = im.resize((SCAN_WIDTH_PX, round(im.height * SCAN_WIDTH_PX / im.width)))
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        return buf.getvalue()


# ---------------------------------------------------------------- 수식


_SPACING = re.compile(r"\\(qquad|quad|[ ;:!])")
_SPACES = {"qquad": "~" * 8, "quad": "~" * 4, " ": "~", ";": "~", ":": "~", "!": ""}
"""변환기가 모르는 LaTeX 띄우기 → `~`(한글 수식 띄어쓰기, 약 1/4 em). `\\,`은 변환기가 `` ` ``로
바꾼다. 공백으로만 바꾸면 한글 수식이 공백을 무시해 `R=6\\Omega \\quad R=…`가 붙어 버린다."""
_EQ_WORDS = {r"\uparrow": "uparrow", r"\downarrow": "downarrow", r"\Leftrightarrow": "LRARROW"}
"""python-hwpx 변환기가 모르는 LaTeX 명령 → 한글 수식 낱말 (실제 판독문에서 나옴)."""
_NON_ASCII = re.compile(r"\\text\{([^{}]*[^\x00-\x7f][^{}]*)\}|[^\x00-\x7f]+")
_INLINE = re.compile(r"\\\((.+?)\\\)")
"""판독문 `text` 안의 식 (글과 식이 섞인 줄은 식만 `\\( \\)`로 감싼다)."""


def _preclean(latex: str) -> str:
    return _SPACING.sub(lambda m: _SPACES[m.group(1)], latex)


def _token(i: int) -> str:
    """글자만 된 자리표 — 숫자를 넣으면 변환기가 `ZZQ 0 ZZ`처럼 띄워 버린다."""
    return "ZZ" + "".join(chr(65 + int(d)) for d in str(i)) + "ZZ"


def to_eqedit(latex: str) -> str:
    """LaTeX → 한글 수식 스크립트. 변환기는 ASCII 밖 글자(㉠, 한글)와 `_EQ_WORDS`를 받지 않으므로
    자리표로 바꿔 넘기고 되돌린다. ㉠은 `\\text{}` 안에 들어가 한글 수식 `rm "㉠"`이 된다."""
    held: list[str] = []

    def hold(script: str) -> str:
        held.append(script)
        return _token(len(held) - 1)

    s = _preclean(latex)
    for cmd, word in _EQ_WORDS.items():
        s = re.sub(re.escape(cmd) + r"(?![A-Za-z])", lambda _, w=word: f" {hold(w)} ", s)
    s = _NON_ASCII.sub(lambda m: rf"\text{{{hold(m.group(1) or m.group(0))}}}", s)
    script = latex_to_eqedit(s)
    for i, original in reversed(list(enumerate(held))):  # ZZBAZZ(10)를 ZZBZZ(1)보다 먼저
        script = script.replace(_token(i), original)
    return script


_GLUE_WORDS = sorted(
    {"over", "overline", "underline", "atop", "sqrt", "times", "cdot", "DEG", "LEFT", "RIGHT",
     "sin", "cos", "tan", "log", "ln", "THEREFORE", "alpha", "beta", "gamma", "delta", "epsilon",
     "theta", "lambda", "mu",
     "pi", "rho", "sigma", "tau", "phi", "omega", "Delta", "Omega", "Sigma", "Phi", "Gamma",
     "OMEGA", "DELTA"},
    key=len, reverse=True,
)  # fmt: skip
_GLUE_RUN = re.compile(r'"[^"]*"|[A-Za-z]+')
_OVER_SCRIPT = re.compile(
    r"\b(over|atop)\s+((?:[A-Za-z]+|[0-9.]+)(?:\s*[_^]\s*(?:\{[^{}]*\}|[A-Za-z0-9.]+))+)"
)
"""`L over v_0`: 한글은 분모에 첨자까지 넣는데 변환기는 `\\frac{L}{v}_0`으로 바꾼다
— 묶어 넘긴다."""


def _unglue(script: str) -> str:
    """한글 수식 편집기는 붙여 쓴 명령을 앞에서부터 알아본다: `v^2 overg`, `2 pim overBq`,
    `Deltax_1` (실측 학원 시험지 2026-11-04). 변환기는 글자 덩어리 전체를 한 낱말로 보므로
    띄워 준다."""

    def split(m: re.Match[str]) -> str:
        run = m.group(0)
        if run.startswith('"') or run in _GLUE_WORDS:
            return run.upper() if run in ("Delta", "Omega", "Sigma", "Phi", "Gamma") else run
        out: list[str] = []
        while run:
            word = next((w for w in _GLUE_WORDS if run.startswith(w) and len(run) > len(w)), None)
            if word is None:
                break
            out.append(word)
            run = run[len(word) :]
        return " ".join([*out, run]).strip() if out else m.group(0)

    script = _GLUE_RUN.sub(split, script)
    script = re.sub(r"(?<![A-Za-z])(Delta|Omega|Sigma|Phi|Gamma)(?![A-Za-z])",
                    lambda m: m.group(1).upper(), script)  # fmt: skip
    return _OVER_SCRIPT.sub(lambda m: f"{m.group(1)} {{{m.group(2)}}}", script)


def from_eqedit(script: str) -> str:
    latex = eqedit_to_latex(_unglue(script))
    latex = re.sub(r"(?<![\\A-Za-z])DEG(?![A-Za-z])", r"^{\\circ}", latex)
    for cmd, word in _EQ_WORDS.items():
        latex = re.sub(rf"(?<![\\A-Za-z]){word}(?![A-Za-z])", lambda _, c=cmd: c, latex)
    return latex


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
        self.failed_equations: list[str] = []

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
            # inherit_style=False: 안 주면 앞 문단 스타일을 물려받아 다음 문단도 '다워이 머리'가 됨
            p = self.doc.add_paragraph(text, char_pr_id_ref=char or self.plain, inherit_style=False)
        if style is not None:
            p.style_id_ref = style
        return p

    def runs(self, p: HwpxOxmlParagraph, pieces: list[tuple[str, str]]) -> None:
        for text, char in pieces:
            if text:
                p.add_run(text, char_pr_id_ref=char)

    def equation(
        self, p: HwpxOxmlParagraph, latex: str, *, lead: str = " ", color: str | None = None
    ) -> bool:
        """한글 수식으로 넣는다. 못 바꾸면 글자로 넣고 False (PLAN.md §14).
        lead: 앞에 둘 글자 — 줄 끝에 붙는 식은 " ", 글 사이에 낀 식(`\\( \\)`)은 "".
        color: 식 글자색 (○ 파란·✕ 빨간 이유 속 식이 글과 같은 색이 되게)."""
        try:
            script = to_eqedit(latex)
        except Exception:
            p.add_run(f"{lead}[식: {latex}]", char_pr_id_ref=self.red)
            self.failed_equations.append(latex)
            return False
        if lead:
            p.add_run(lead, char_pr_id_ref=self.plain)
        # python-hwpx는 식 상자 폭을 스스로 재고 한글은 그 폭으로 쪽을 짠다. 틀리게 재는 두 가지를
        # 비슷한 폭의 글자로 바꿔 잰다 (2026-11-04 한글 2024): `:` — 한글은 `=`처럼 양옆을 띄우는데
        # 문장부호 폭으로 재어 뒤 글자가 겹침, `uparrow` 등 — 화살표 하나인데 글자 7개 폭으로 잼.
        sized = script.replace(" : ", " = ")
        for word in _EQ_WORDS.values():
            sized = re.sub(rf"(?<![A-Za-z]){word}(?![A-Za-z])", "x", sized)
        size = None
        if sized != script:
            m = measure_equation(sized)
            size = (m.width, m.height)
        eq = self.doc.shapes.add_equation(script, paragraph=p, size=size)
        if color:
            eq.set_attribute("textColor", color)
        return True

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        self.doc.save_to_path(tmp)
        tmp.replace(path)  # 강사가 열어 둔 파일이면 여기서 실패한다 — 부른 쪽이 알린다
        return path


def _by_part(lines: list[TranscriptLine]) -> dict[str, list[TranscriptLine]]:
    out: dict[str, list[TranscriptLine]] = {}
    for line in lines:
        out.setdefault(line.part or UNASSIGNED, []).append(line)
    return out


def _split_quote(text: str, quote: str) -> tuple[str, str, str] | None:
    i = text.find(quote)
    return (text[:i], quote, text[i + len(quote) :]) if i >= 0 else None


def _marked(text: str, quotes: list[str], base: str, mark: str) -> list[tuple[str, str]]:
    """quotes가 나오는 곳만 mark 글자 모양으로 나눈다."""
    for q in quotes:
        if q and (s := _split_quote(text, q)):
            return [
                *_marked(s[0], quotes, base, mark),
                (s[1], mark),
                *_marked(s[2], quotes, base, mark),
            ]
    return [(text, base)]


def write_line(
    b: Builder,
    line: TranscriptLine,
    *,
    highlights: list[tuple[str, str]] | None = None,
    underline: list[str] | None = None,
) -> HwpxOxmlParagraph:
    """판독문 한 줄 = 문단 하나. 글 사이의 `\\( \\)` 식은 그 자리에 한글 수식으로 넣는다.
    highlights: (글, 색) 형광펜, underline: 빨간 밑줄 — 둘 다 글 부분에서만 찾는다."""
    p = b.para()
    if line.struck:
        b.runs(p, [("[지움] ", b.small_gray)])
    base = b.struck if line.struck else b.plain
    for i, piece in enumerate(_INLINE.split(line.text)):
        if i % 2:
            b.equation(p, piece, lead="")
        else:
            b.runs(p, _marked(piece, [] if line.struck else underline or [], base, b.red_underline))
    if not line.struck:
        for match, color in highlights or []:
            with contextlib.suppress(ValueError):  # 판독문에 없거나 식에 걸치면 칠하지 않는다
                p.add_highlight(color=color, match=match)
    if line.math:
        b.equation(p, line.math)
    return p


_PROSE_TEXT = re.compile(r"[\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f]+")
"""AI가 쓴 이유 글에서 식을 가르는 한글 (음절·자모). ㉠·Ω·→는 식 쪽에 남는다."""
_LATEX_SIGN = re.compile(r"\\[A-Za-z]+|[\^_{}]")
_DOLLAR = re.compile(r"\$\$?(.+?)\$\$?")
_EDGE = " \t,.;:!?'\"“”‘’·"


def _trim_math(run: str) -> tuple[str, str, str]:
    """식 덩어리 → (앞 글, 식, 뒤 글): 양끝의 띄어쓰기·문장부호와 짝 없는 괄호는 글로 둔다."""
    lead = run[: len(run) - len(run.lstrip(_EDGE))]
    core = run[len(lead) :]
    tail = core[len(core.rstrip(_EDGE)) :]
    core = core[: len(core) - len(tail)]
    while core.startswith("(") and core.count("(") > core.count(")"):
        lead, core = lead + "(", core[1:].lstrip()
    while core.endswith(")") and core.count(")") > core.count("("):
        core, tail = core[:-1].rstrip(), ")" + tail
    if core.startswith("(") and core.endswith(")") and _wrapped(core):  # (P=I^2R) → 괄호는 글
        lead, core, tail = lead + "(", core[1:-1].strip(), ")" + tail
    return lead, core, tail


def _wrapped(s: str) -> bool:
    """맨 앞 `(`의 짝이 맨 뒤 `)`인가 (`(a)(b)`는 아님)."""
    depth = 0
    for i, c in enumerate(s):
        depth += (c == "(") - (c == ")")
        if depth == 0:
            return i == len(s) - 1
    return False


def prose_pieces(text: str) -> list[tuple[str, bool]]:
    """AI가 쓴 이유·총평 → (조각, 식인가). 지시서가 `$`로 감싸지 말라고 해서(contract 6번)
    식이 `2\\Omega이어야`, `T_1 = 2\\sqrt{\\frac{r}{g}}를`처럼 글에 그냥 섞여 온다 — 한글 사이의
    덩어리 중 LaTeX 흔적(`\\명령`, `^`, `_`, `{}`)이 있는 것만 식으로 본다. `\\( \\)`·`$ $`로
    감싼 것은 그대로 식."""
    text = _DOLLAR.sub(lambda m: f"\\({m.group(1)}\\)", text)
    out: list[tuple[str, bool]] = []
    for i, piece in enumerate(_INLINE.split(text)):
        if i % 2:
            out.append((piece, True))
            continue
        pos = 0
        for m in [*_PROSE_TEXT.finditer(piece), None]:
            end = m.start() if m else len(piece)
            run = piece[pos:end]
            if _LATEX_SIGN.search(run):
                lead, core, tail = _trim_math(run)
                out += [(lead, False), (core, True), (tail, False)]
            else:
                out.append((run, False))
            if m:
                out.append((m.group(0), False))
                pos = m.end()
    merged: list[tuple[str, bool]] = []
    for s, is_math in out:
        if not s:
            continue
        if merged and not is_math and not merged[-1][1]:
            merged[-1] = (merged[-1][0] + s, False)
        else:
            merged.append((s, is_math))
    return merged


def write_prose(b: Builder, text: str, char: str, color: str) -> HwpxOxmlParagraph:
    """AI가 쓴 이유 한 문단: 글은 char 글씨, 글에 섞인 식은 그 자리에 같은 색 한글 수식."""
    p = b.para()
    for piece, is_math in prose_pieces(text):
        if is_math:
            b.equation(p, piece, lead="", color=color)
        else:
            b.runs(p, [(piece, char)])
    return p


def write_reading(b: Builder, sheet: Sheet) -> HwpxOxmlParagraph:
    """원본 그림 → 학생이 쓴 글 그대로(글자·수식)를 쓰고 머리 문단을 돌려준다."""
    head = b.para(sheet.head, char=b.bold, style=b.style_head)
    for note in sheet.reading_notes:
        b.para(note, char=b.red)
    for i, png in enumerate(sheet.scans, start=1):
        b.para(f"[원본] 답안 {i}쪽", char=b.small_gray)
        b.doc.add_picture(png, "png", width_mm=150, style_id_ref=NORMAL)
    for part, part_lines in _by_part(sheet.lines).items():
        b.para(f"{MARK} {part}", char=b.part, style=b.style_part)
        for line in part_lines:
            marks = [(u, YELLOW) for u in line.uncertain] + [(UNREADABLE, PINK)]
            write_line(b, line, highlights=marks)
    return head


def write_graded(b: Builder, sheet: Sheet, *, style: str) -> HwpxOxmlParagraph:
    """채점 부분을 쓰고 그 머리 문단을 돌려준다."""
    grades = sheet.grades or {}
    total = sum(g.awarded for g in grades.values())
    full = sum(g.points for g in grades.values())
    head = b.para(f"{sheet.head} · 총점 {total:g} / {full:g}", char=b.bold, style=style)
    for note in sheet.notes:
        b.para(note, char=b.small_gray)
    table = b.doc.add_table(len(grades) + 1, 3)
    for c, h in enumerate(("소문항", "점수", "배점")):
        table.set_cell_text(0, c, h)
    for r, (part, g) in enumerate(grades.items(), start=1):
        for c, v in enumerate((part, f"{g.awarded:g}", f"{g.points:g}")):
            table.set_cell_text(r, c, v)
    for part, part_lines in _by_part(sheet.lines).items():
        g = grades.get(part)
        b.para(f"{MARK} {part}", char=b.part, style=b.style_part)
        for line in part_lines:  # 맞은 곳: 연두 형광펜, 틀린 곳: 빨간 밑줄
            write_line(
                b, line,
                highlights=[(q, GREEN) for q, _ in g.good] if g else [],
                underline=[q for q, _ in g.bad] if g else [],
            )  # fmt: skip
        if g is None:
            continue
        b.para(f"점수 {g.awarded:g} / {g.points:g}", char=b.bold)
        # 이유는 색 글씨로만 쓴다 — 한글 메모(오른쪽 여백 댓글)는 인쇄되지 않고 ✕ 줄과 겹친다
        for _, why in g.good:
            if why:
                write_prose(b, f"○ {why}", b.blue, BLUE)
        for _, why in g.bad:
            if why:
                write_prose(b, f"✕ {why}", b.red, RED)
    if sheet.comment:
        write_prose(b, f"총평: {sheet.comment}", b.blue, BLUE)
    return head


def write_sheet(b: Builder, sheet: Sheet) -> HwpxOxmlParagraph:
    """한 학생 × 한 문항: 판독본 → (채점이 있으면) 새 쪽에서 채점. 머리 문단을 돌려준다."""
    if sheet.waiting is not None:
        head = b.para(sheet.head, char=b.bold, style=b.style_head)
        b.para(sheet.waiting, char=b.red)
        return head
    head = write_reading(b, sheet)
    if sheet.grades is not None:
        write_graded(b, sheet, style=b.style_graded).element.set("pageBreak", "1")
    return head


def build_student(path: Path, sheet: Sheet) -> Path:
    b = Builder()
    write_sheet(b, sheet)
    return b.save(path)


def build_bundle(path: Path, sheets: list[Sheet]) -> Path:
    """반별 묶음: 학생마다 새 쪽에서 시작한다 (받은 순서대로 — 부르는 쪽이 이름순으로 넘긴다)."""
    b = Builder()
    for i, sheet in enumerate(sheets):
        head = write_sheet(b, sheet)
        if i:
            head.element.set("pageBreak", "1")
    return b.save(path)


# ---------------------------------------------------------------- 다시 읽기


@dataclass
class ReadBack:
    head: str
    parts: dict[str, list[str]]
    """소문항 → 줄 (글자 + 수식은 `\\( … \\)`)."""
    pictures: int


_FAILED_EQ = re.compile(r"\[식: (.+?)\]")
"""한글 수식으로 못 바꿔 글자로 넣은 식 (`Builder.equation`) — 다시 읽을 때 식으로 되돌린다."""


def _paragraph_text(p: etree._Element) -> str:  # pyright: ignore[reportPrivateUsage]
    out: list[str] = []
    for el in p.iter(f"{HP}t", f"{HP}script"):
        if el.tag == f"{HP}t":
            out.append("".join(el.itertext()))
        else:
            out.append(f"\\({from_eqedit(el.text or '')}\\)")
    return _FAILED_EQ.sub(lambda m: f"\\({m.group(1)}\\)", "".join(out)).strip()


def read_back(path: Path) -> ReadBack:
    """강사가 한글에서 고친 판독본을 다시 읽는다 — 채점 머리에서 멈춘다 (채점은 앱이 만든다)."""
    doc = HwpxDocument.open(path)
    styles = {s: doc.styles.get(s) for s in (STYLE_HEAD, STYLE_PART, STYLE_GRADED)}
    ids = {k: (str(v.id) if v else None) for k, v in styles.items()}
    head = ""
    parts: dict[str, list[str]] = {}
    current: str | None = None
    pictures = 0
    for p in doc.paragraphs:
        el = cast("etree._Element", p.element)  # pyright: ignore[reportPrivateUsage]
        pictures += len(list(el.iter(f"{HP}pic")))
        parent = el.getparent()
        if parent is not None and str(parent.tag).endswith("subList"):
            continue  # 표 칸 안의 문단
        text = _paragraph_text(el)
        style = str(p.style_id_ref) if p.style_id_ref is not None else None
        if style is not None and style == ids[STYLE_GRADED]:
            break
        m = PART_MARK.match(text)
        if style == ids[STYLE_HEAD] or (not head and " · " in text and not parts):
            head = head or text
        elif m and (style == ids[STYLE_PART] or text.startswith(MARK)):
            current = str(m.group(1))
            parts.setdefault(current, [])
        elif current is not None and text:
            parts[current].append(text)
    return ReadBack(head, parts, pictures)


def count_heads(path: Path) -> int:
    """묶음에 든 학생 수 (머리 문단 수)."""
    doc = HwpxDocument.open(path)
    style = doc.styles.get(STYLE_HEAD)
    return sum(1 for p in doc.paragraphs if style and str(p.style_id_ref) == str(style.id))


def expected_text(lines: list[TranscriptLine]) -> dict[str, list[str]]:
    """`read_back`이 돌려줄 꼴로 쓴 판독문 (만든 파일을 검사할 때)."""
    out: dict[str, list[str]] = {}
    for part, part_lines in _by_part(lines).items():
        out[part] = []
        for line in part_lines:
            text = f"[지움] {line.text}" if line.struck else line.text
            if line.math:
                text += f" \\({line.math}\\)"
            out[part].append(text)
    return out


_TEXT_WRAP = re.compile(r"\\(?:text|mathrm)\s*\{([^{}]*)\}")
"""식 안의 한글·㉠은 한글 수식으로 넣을 때 `\\text{}`에 싸여 돌아온다 (`i_ㄱ` → `i_{\\text{ㄱ}}`).
"""


def _canon_math(s: str) -> str:
    s = re.sub(r"\\neq(?![A-Za-z])", r"\\ne", _preclean(s))  # 띄어쓰기를 지우기 전에
    s = _TEXT_WRAP.sub(lambda m: m.group(1), normalize_latex(s))
    s = re.sub(r"\{([^{}\\]|\\[A-Za-z]+)\}", lambda m: m.group(1), s)
    s = s.replace("\\geq", "\\ge").replace("\\leq", "\\le")
    return re.sub(r"\s+", "", s)  # 식 안의 띄어쓰기는 한글 수식이 다시 정한다


def canon(s: str) -> str:
    """다시 읽은 줄과 쓴 줄을 견줄 꼴: 식 안의 띄어쓰기·중괄호만 무시하고 글은 그대로."""
    pieces = _INLINE.split(s.strip())
    return "".join(f"\\({_canon_math(x)}\\)" if i % 2 else x for i, x in enumerate(pieces))


# ---------------------------------------------------------------- 다시 읽은 판독본 → 판독문


@dataclass
class Reconciled:
    """강사가 한글에서 고친 판독본을 앱 판독문으로 되돌린 것."""

    lines: list[TranscriptLine]
    changed: int
    """강사가 고치거나 넣거나 지운 줄 수 (0이면 고친 것 없음)."""
    problems: list[str] = field(default_factory=lambda: list[str]())
    """있으면 확정하지 않는다 (소문항 표식이 없어짐 등 — 그 문항만 강사에게 묻는다)."""


_STRUCK = "[지움]"
_WHOLE_MATH = re.compile(r"\\((.+)\\)")


def _part_of(question: str, label: str) -> tuple[bool, str | None]:
    """표식 글 → (이 문항의 표식인가, 판독문의 소문항): `2-(1)` → `(1)`, `2` → None."""
    if label in (question, UNASSIGNED):
        return True, None
    if label.startswith(f"{question}-") and len(label) > len(question) + 1:
        return True, label[len(question) + 1 :]
    return False, None


def _parse_line(text: str, part: str | None) -> TranscriptLine:
    struck = text.startswith(_STRUCK)
    if struck:
        text = text[len(_STRUCK) :].strip()
    m = _WHOLE_MATH.fullmatch(text)
    if m and r"\(" not in m.group(1):
        return TranscriptLine(part=part, math=m.group(1).strip(), struck=struck)
    return TranscriptLine(part=part, text=text, struck=struck)


def reconcile(question: str, parts: dict[str, list[str]], old: list[TranscriptLine]) -> Reconciled:
    """다시 읽은 소문항별 줄(`read_back`) → 판독문 줄. 강사가 고치지 않은 줄은 앱에 둔 원래 줄을
    그대로 쓴다 — 한글 수식을 거치면 LaTeX 표기가 조금 바뀌므로(`^2` → `^{2}`). 고친 줄만 새로
    만든다 (확신도 1, 불확실 표시 없음). 소문항 표식이 없어진 소문항이 있으면 고치지 않고 문제로
    돌려준다 (PLAN.md §14 — 표식을 못 찾으면 그 소문항만 확인을 요청)."""
    import difflib

    prefixed = sheet_lines(question, old)
    want = expected_text(prefixed)
    index: dict[str, list[int]] = {}
    for i, line in enumerate(prefixed):
        index.setdefault(line.part or UNASSIGNED, []).append(i)
    problems = [
        f"'{MARK} {label}' 표식이 없어 그 소문항을 읽을 수 없습니다 — 표식 줄을 되살려 저장하세요"
        for label in want
        if label not in parts
    ]
    problems += [
        f"'{MARK} {label}'은(는) 이 문항({question})의 표식이 아닙니다 — '{MARK} {question}-(1)'"
        " 꼴로 고치세요"
        for label in parts
        if not _part_of(question, label)[0]
    ]
    if problems:
        return Reconciled(lines=list(old), changed=0, problems=problems)
    out: list[TranscriptLine] = []
    changed = 0
    for label, got in parts.items():
        _, part = _part_of(question, label)
        before = [canon(x) for x in want.get(label, [])]
        after = [canon(x) for x in got]
        olds = index.get(label, [])
        sm = difflib.SequenceMatcher(None, before, after, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                out += [old[olds[k]] for k in range(i1, i2)]
                continue
            changed += max(i2 - i1, j2 - j1)
            out += [_parse_line(got[j], part) for j in range(j1, j2)]
    return Reconciled(lines=out, changed=changed)
