"""AI가 쓴 채점 이유·감점 이유 → (조각, 식인가) — 채점 답안지 PDF에 식을 그릴 때 (`red_pen`).

화면(`frontend/src/lib/prose.ts`)과 같은 규칙. 2026-11-18까지는 채점 HWPX(`export/hwpx.py`)에 있었다
(실측 채점 이유 317개의 식 319개 모두 바뀜).
"""

from __future__ import annotations

import re

_INLINE = re.compile(r"\\\((.+?)\\\)")
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
