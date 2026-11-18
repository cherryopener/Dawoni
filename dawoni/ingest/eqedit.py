"""한글 수식 스크립트 → LaTeX (HWPX 시험지·해설지를 읽을 때, `ingest.document`).

2026-11-18까지는 채점 HWPX를 쓰는 `export/hwpx.py`에 함께 있었다. 앱이 HWPX를 쓰지 않게 되어
읽는 쪽만 여기로 옮겼다 (실측은 그대로: 학원 HWP 15개의 수식 1023개, 2026-11-11).
"""

from __future__ import annotations

import re

from hwpx.equation.eqedit import eqedit_to_latex

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
