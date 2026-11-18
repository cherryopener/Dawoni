"""한글 수식 스크립트 ↔ LaTeX.

- 읽기 (`from_eqedit`): HWPX 시험지·해설지를 읽을 때 (`ingest.document`).
- 쓰기 (`to_eqedit`, `equation_size`): 다듬은 해설지 HWPX에 식을 넣을 때 (`export.key_hwpx`).

2026-11-18까지는 채점 HWPX를 쓰는 `export/hwpx.py`에 함께 있었다. 앱이 채점 HWPX를 쓰지 않게 되어
읽는 쪽만 여기로 옮겼다가, 0단계(해설 다듬기)가 해설지 HWPX를 쓰게 되어 쓰는 쪽도 되살렸다
(실측은 그대로: 학원 HWP 15개의 수식 1023개 2026-11-11, Windows 한글 2024 식 상자 폭 2026-11-07).
"""

from __future__ import annotations

import re

from hwpx.equation.authoring import latex_to_eqedit
from hwpx.equation.eqedit import eqedit_to_latex
from hwpx.equation.measure import measure_equation

_EQ_WORDS = {r"\uparrow": "uparrow", r"\downarrow": "downarrow", r"\Leftrightarrow": "LRARROW"}
"""변환기가 모르는 한글 수식 낱말 → LaTeX 명령."""

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


_UPPER_WORDS = {
    "OVER": "over", "ATOP": "atop", "SQRT": "sqrt", "CDOT": "cdot", "TIMES": "times",
    "ANGLE": "angle", "SIM": "sim", "BULLET": "cdot",
}  # fmt: skip
"""한글 수식은 명령을 대문자로 써도 같다 — 변환기는 소문자만 안다. 실측(2026-11-11, 학원 HWP 15개의
수식 1023개): `OVER` 51(분수가 깨짐), `BULLET`(가운뎃점) 12, `ANGLE` 12, `SIM` 4가 그대로 남았다."""
_CENTIGRADE = re.compile(r"\\mathrm\{CENTIGRADE\}|(?<![\\A-Za-z])CENTIGRADE(?![A-Za-z])")
"""`CENTIGRADE` = ℃ (단위 글꼴 `rm` 안에서는 `\\mathrm{CENTIGRADE}`로 나온다)."""


_ROOT_OF = re.compile(r"(?<![A-Za-z])root\s*(\{[^{}]*\}|[A-Za-z0-9.]+)\s*of\b")
_ROOT = re.compile(r"(?<![A-Za-z])root(?=\s*[{A-Za-z0-9.(])")
"""`root 3 of x`는 세제곱근, `of` 없는 `root6`은 제곱근 (한글 수식에서 sqrt와 같음) — 변환기는
`of` 없는 `root6 over6`을 `\\sqrt[6]{over}`로 바꿨다 (2026-11-18 `_revision.hwpx`의 정답 식)."""


def _plain_roots(script: str) -> str:
    held: list[str] = []

    def hold(m: re.Match[str]) -> str:
        held.append(m.group(0))
        return f"\x00{len(held) - 1}\x00"

    s = _ROOT.sub("sqrt ", _ROOT_OF.sub(hold, script))
    return re.sub(r"\x00(\d+)\x00", lambda m: held[int(m.group(1))], s)


def from_eqedit(script: str) -> str:
    script = _plain_roots(script)
    script = _GLUE_RUN.sub(lambda m: _UPPER_WORDS.get(m.group(0), m.group(0)), script)
    latex = eqedit_to_latex(_unglue(script))
    latex = _CENTIGRADE.sub(lambda _: r"{}^{\circ}\mathrm{C}", latex)
    latex = re.sub(r"(?<![\\A-Za-z])DEG(?![A-Za-z])", r"^{\\circ}", latex)
    for cmd, word in _EQ_WORDS.items():
        latex = re.sub(rf"(?<![\\A-Za-z]){word}(?![A-Za-z])", lambda _, c=cmd: c, latex)
    return latex


# ---------------------------------------------------------------- 쓰기 (LaTeX → 한글 수식)

_SPACING = re.compile(r"\\(qquad|quad|[ ;:!])")
_SPACES = {"qquad": "~" * 8, "quad": "~" * 4, " ": "~", ";": "~", ":": "~", "!": ""}
"""변환기가 모르는 LaTeX 띄우기 → `~`(한글 수식 띄어쓰기, 약 1/4 em). `\\,`은 변환기가 `` ` ``로
바꾼다. 공백으로만 바꾸면 한글 수식이 공백을 무시해 `R=6\\Omega \\quad R=…`가 붙어 버린다."""
_NON_ASCII = re.compile(r"\\text\{([^{}]*[^\x00-\x7f][^{}]*)\}|[^\x00-\x7f]+")
EQ_BASE_UNIT = 1100
"""식 글자 크기 (1/100 pt) — python-hwpx `add_equation`의 기본값."""
EQ_WIDTH_SCALE = 1.1
EQ_WIDTH_PAD_EM = 0.1
"""python-hwpx가 잰 식 상자 폭을 이만큼 넓힌다 (폭 × 1.1 + 글자 크기 × 0.1). python-hwpx의 글자
폭은 맥 한글에 맞춘 값이라 Windows 한글 2024보다 좁다 — 2026-11-07 Windows 한글 2024가 저장한 학원
시험지 식 129개(HYhwpEQ)와 비교: 그대로면 61개가 0.18mm 넘게 좁고(최대 2.7mm), 넓히면 4개(최대
0.9mm). 한글은 저장된 폭으로 줄을 짜서, 좁으면 뒤 글자가 식에 겹친다. 빈칸이 겹침보다 낫다."""


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

    s = _SPACING.sub(lambda m: _SPACES[m.group(1)], latex)
    for cmd, word in _EQ_WORDS.items():
        s = re.sub(re.escape(cmd) + r"(?![A-Za-z])", lambda _, w=word: f" {hold(w)} ", s)
    s = _NON_ASCII.sub(lambda m: rf"\text{{{hold(m.group(1) or m.group(0))}}}", s)
    script = latex_to_eqedit(s)
    for i, original in reversed(list(enumerate(held))):  # ZZBAZZ(10)를 ZZBZZ(1)보다 먼저
        script = script.replace(_token(i), original)
    return script


def equation_size(script: str, base_unit: int = EQ_BASE_UNIT) -> tuple[int, int]:
    """한글 수식 스크립트의 상자 (폭, 높이) HWPUNIT — Windows 한글이 뒤 글자를 겹치지 않게 잰다.
    python-hwpx가 틀리게 재는 두 가지를 비슷한 폭의 글자로 바꿔 잰다 (2026-11-04 한글 2024):
    `:`은 한글이 `=`처럼 양옆을 띄우는데 문장부호 폭으로 재고, `uparrow` 등은 화살표 하나인데
    글자 7개 폭으로 잰다."""
    sized = script.replace(" : ", " = ")
    for word in _EQ_WORDS.values():
        sized = re.sub(rf"(?<![A-Za-z]){word}(?![A-Za-z])", "x", sized)
    m = measure_equation(sized, base_unit=base_unit)
    return round(m.width * EQ_WIDTH_SCALE + base_unit * EQ_WIDTH_PAD_EM), m.height


def equation_baseline(script: str, base_unit: int = EQ_BASE_UNIT) -> int:
    return measure_equation(script, base_unit=base_unit).base_line
