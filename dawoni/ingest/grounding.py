"""AI가 만든 글이 원본(PDF 글자)에 근거하는지 보는 간단한 검사.

수식 표기는 바뀌어도(x² → x^2) 한글 문장은 원본에 있어야 한다. 원본에 없는 한글이 많으면
AI가 문장을 지어냈거나 고친 것이다.
"""

from __future__ import annotations

import re
from collections import Counter

_HANGUL = re.compile(r"[가-힣]")


def hangul_missing(produced: str, source: str) -> tuple[int, int]:
    """(만든 글의 한글 중 PDF 글자에 없는 글자 수, 만든 글의 한글 수)."""
    have = Counter(_HANGUL.findall(source))
    got = _HANGUL.findall(produced)
    missing = 0
    for ch in got:
        if have[ch] > 0:
            have[ch] -= 1
        else:
            missing += 1
    return missing, len(got)


def hangul_outside(produced: str, source: str) -> float:
    """만든 글의 한글 중 PDF 글자에 없는 것의 비율."""
    missing, total = hangul_missing(produced, source)
    return missing / total if total else 0.0
