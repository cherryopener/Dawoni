"""판독문 자동 검토 — AI를 부르지 않는다 (토큰 0).

AI 판독 뒤, 강사 확인 전에 돈다:
- 판독불가 표시를 `[판독불가]` 하나로 맞춘다. 남아 있으면 확정할 수 없다 (강사가 채운다).
- 앞뒤 문맥에 맞지 않는 곳에 `검토필요:` 표시를 단다 (그 줄의 `uncertain`에 붙어 강사 확인 화면에
  보인다).
  고치지는 않는다 — 학생이 실제로 그렇게 썼을 수 있다.

검토는 잘못 읽은 곳을 다 찾지 못한다("2+R" → "R+R"처럼 말이 되는 오독). 강사 확인을 대신하지
않고, 강사가 먼저 볼 곳을 짚는다. 규칙은 2026-11-04 실제 판독 6명분(claude·gemini)의 오독에서.
"""

from __future__ import annotations

import re
from collections import Counter

from dawoni.mathx.latex import latex_error
from dawoni.models.grading import UNREADABLE, Transcript, TranscriptLine

REVIEW = "검토필요: "
"""앱이 단 `uncertain` 항목의 머리. 다시 검토할 때 지우고 새로 단다."""

_UNREADABLE_ANY = re.compile(r"[\[(（]\s*(?:판독\s*불가|읽을\s*수\s*없음)\s*[\])）]")
_INLINE = re.compile(r"\\\((.+?)\\\)")
_LABELS = re.compile(r"[㉠-㉭①-⑳ⓐ-ⓩ]|\([가나다라마바사아자차카타파하]\)")
_CMD = re.compile(r"\\[A-Za-z]+")
_LETTER = re.compile(r"[a-z]")
"""소문자만 본다: 대문자는 점 이름(A, B, H, O)이 많아 한 번만 나와도 이상하지 않다 (2026-11-04)."""
_VARIABLE = re.compile(r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])")
_EXPRESSION = re.compile(r"[0-9=+\-^/<>]|\\(?:frac|le|ge|times|cdot|sqrt)")
"""식다운 식 — `\\(x\\)축`, `\\(A, B\\)`처럼 이름만 쓴 것은 빼고 본다."""
_UNITS = set("msgkh")
"""한 번만 나와도 이상하지 않은 단위 글자 (10 m/s, 2 kg, 3 h …)."""
_BRACES = re.compile(r"[{}]")
_GLUED = re.compile(
    r"[가-힣](?![A-Z][가-힣])[A-Za-z]+[가-힣]|(?<![0-9A-Za-z])\d+[A-Za-z]\d+(?![0-9A-Za-z])"
)
"""식 밖 글에서 한글 사이에 낀 영문, 숫자-영문-숫자 ("2i1") — 식을 글로 잘못 읽은 흔적.
대문자 한 글자는 점 이름이라 뺀다 ("원O의")."""
_DOUBLED = re.compile(r"([가-힣])\1")
_DOUBLED_OK = {"각각", "점점", "곳곳", "서서", "차차", "번번", "겹겹", "줄줄", "낱낱", "졸졸",
               "빙빙", "쑥쑥", "꼭꼭", "딱딱", "똑똑"}  # fmt: skip
"""같은 글자가 겹쳐도 흔한 낱말 ("동일하하지만"은 오독, "각각"은 아님)."""


def normalize_unreadable(s: str) -> str:
    return _UNREADABLE_ANY.sub(UNREADABLE, s)


def confirm_problems(t: Transcript) -> list[str]:
    """확정하기 전에 강사가 해결해야 할 것."""
    n = t.unreadable
    return [f"{UNREADABLE} {n}곳을 원본을 보고 채우거나 지워 주세요."] if n else []


def _maths(line: TranscriptLine) -> list[str]:
    return [*_INLINE.findall(line.text), *([line.math] if line.math else [])]


def _prose(line: TranscriptLine) -> str:
    return _INLINE.sub(" ", line.text)


def _letters(latex: str) -> list[str]:
    return _LETTER.findall(_CMD.sub(" ", latex))


def _rare_syllables(s: str) -> list[str]:
    """KS X 1001(완성형 2,350자) 밖의 한글 — 손글씨 답안에 거의 나오지 않는다."""
    out: list[str] = []
    for ch in s:
        if "가" <= ch <= "힣":
            try:
                ch.encode("euc-kr")
            except UnicodeEncodeError:
                out.append(ch)
    return out


def material_letters(material: str) -> set[str]:
    """문제 본문에 변수로 나오는 글자 (낱말·LaTeX 명령 속 글자는 빼고)."""
    return set(_VARIABLE.findall(_CMD.sub(" ", material)))


def line_flags(line: TranscriptLine, material: str, letter_count: Counter[str]) -> list[str]:
    """한 줄의 검토 표시. material: 문제 본문(기호·변수가 문제에 있는지),
    letter_count: 이 답안 전체의 식 글자 수."""
    flags: list[str] = []
    known = material_letters(material)
    text = line.text + " " + (line.math or "")
    if UNREADABLE in text:
        flags.append(f"{UNREADABLE} — 강사가 원본을 보고 채울 것")
    if odd := sorted({x for x in _LABELS.findall(text) if x not in material}):
        flags.append(f"문제에 없는 기호 {' '.join(odd)}")
    prose = _prose(line)
    if glued := _GLUED.findall(prose):
        flags.append(
            f"글 사이에 낀 영문·숫자 {', '.join(dict.fromkeys(glued))} (식을 글로 읽었는지)"
        )
    doubled = [m.group(0) for m in _DOUBLED.finditer(prose) if m.group(0) not in _DOUBLED_OK]
    if doubled:
        flags.append(f"같은 글자가 겹침 {', '.join(dict.fromkeys(doubled))}")
    if rare := _rare_syllables(prose):
        flags.append(f"흔하지 않은 글자 {' '.join(dict.fromkeys(rare))}")
    if _BRACES.search(prose):
        flags.append("식 밖에 중괄호 { } (식을 \\( \\)로 감싸지 않았는지)")
    for latex in _maths(line):
        if err := latex_error(latex):
            flags.append(f"식이 깨짐 `{latex}` ({err})")
        if not _EXPRESSION.search(latex):
            continue
        lone = sorted(
            {x for x in _letters(latex) if letter_count[x] == 1 and x not in _UNITS | known}
        )
        if lone:
            flags.append(f"이 답안에서 한 번만 나오는 문자 {', '.join(lone)} (문제에도 없음)")
    return [REVIEW + f for f in flags]


def review_lines(lines: list[TranscriptLine], material: str) -> list[TranscriptLine]:
    """판독불가 표시를 맞추고, 전에 단 검토 표시를 지운 뒤 새로 단다 (몇 번 돌려도 같다)."""
    lines = [
        x.model_copy(
            update={
                "text": normalize_unreadable(x.text),
                "math": normalize_unreadable(x.math) if x.math else x.math,
                "uncertain": [u for u in x.uncertain if not u.startswith(REVIEW)],
            }
        )
        for x in lines
    ]
    count = Counter(ch for x in lines if not x.struck for m in _maths(x) for ch in _letters(m))
    return [
        x.model_copy(update={"uncertain": [*x.uncertain, *line_flags(x, material, count)]})
        for x in lines
    ]


def review_transcript(t: Transcript, material: str) -> Transcript:
    return t.model_copy(update={"lines": review_lines(t.lines, material)})
