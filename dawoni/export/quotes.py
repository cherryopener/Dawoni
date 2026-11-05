"""채점 근거 인용 → 판독문의 어느 글자·식인가 (PLAN.md §9-1) — 코드만, 토큰 0.

채점은 인용이 판독문에 정말 있는지 `grading.checks.quote_in_transcript`로 이미 본다. 여기서는 같은
잣대(공백·식 구분자·LaTeX 간격 명령·중괄호를 무시, 줄을 넘어 이어도 됨, 인용에 붙은 소문항 표시는
지움)로 찾되, 찾은 글자가 판독문의 몇째 줄·몇째 조각·몇째 글자인지까지 돌려준다. 그래야 여러 줄에
걸친 인용도 그 자리만 칠하고, 식(`\\( \\)` 조각이나 줄 끝 식)에 걸친 인용은 그 식에 색을 입힌다.

M6 실측 전에는 인용을 `…`로 나눈 조각의 식 밖 6자 이상만 한 줄 안에서 찾았다 — 맞은 근거 조각
202개 중 84개만 칠해졌다 (여러 줄 인용, `(1) ` 표시, `\\( \\)` 없이 쓴 식, 식만 있는 줄).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from dawoni.grading.checks import normalize_for_compare
from dawoni.models.grading import TranscriptLine

Kind = Literal["good", "bad"]

INLINE = re.compile(r"\\\((.+?)\\\)")
_TOKEN = re.compile(r"\\[A-Za-z]+|\\.|.", re.DOTALL)
_CHUNKS = re.compile(r"…|\.\.\.|;;")
_BARE = re.compile(r"[0-9A-Za-z.]+")
"""문항 번호뿐인 표시 (`1`, `3a`) — 인용에서 지우면 숫자가 지워진다."""
MATH = -1
"""식 조각은 글자 자리 없이 통째로 칠한다."""


def segments(line: TranscriptLine) -> list[tuple[str, bool]]:
    """한 줄 → (조각, 식인가): 글 사이의 `\\( \\)` 식, 그리고 줄 끝 식(`math`)은 맨 뒤 조각."""
    out = [(x, bool(i % 2)) for i, x in enumerate(INLINE.split(line.text))]
    if line.math:
        out.append((line.math, True))
    return out


@dataclass
class LineSpots:
    """한 줄에서 칠할 곳: 글 조각 번호 → 글자 자리 → 종류, 식 조각 번호 → 종류."""

    chars: dict[int, dict[int, set[Kind]]] = field(
        default_factory=lambda: dict[int, dict[int, set[Kind]]]()
    )
    maths: dict[int, set[Kind]] = field(default_factory=lambda: dict[int, set[Kind]]())


@dataclass
class Spots:
    lines: dict[int, LineSpots] = field(default_factory=lambda: dict[int, LineSpots]())
    found: int = 0
    """판독문에서 찾은 인용 조각 수 (`…`로 나뉜 조각마다)."""
    missed: list[str] = field(default_factory=lambda: list[str]())
    """찾지 못한 인용 조각."""


def _stream(lines: list[TranscriptLine]) -> tuple[str, list[tuple[int, int, int]]]:
    """판독문 전체를 견줄 꼴로 이은 글과, 그 글자마다 (줄, 조각, 글자 자리 또는 MATH)."""
    chars: list[str] = []
    where: list[tuple[int, int, int]] = []
    for li, line in enumerate(lines):
        for si, (piece, is_math) in enumerate(segments(line)):
            if is_math:
                norm = normalize_for_compare(piece)
                chars.append(norm)
                where += [(li, si, MATH)] * len(norm)
                continue
            for m in _TOKEN.finditer(piece):
                norm = normalize_for_compare(m.group(0))
                chars.append(norm)
                where += [(li, si, m.start())] * len(norm)
    return "".join(chars), where


def _chunks(quote: str) -> list[str]:
    return [c for c in _CHUNKS.split(quote) if normalize_for_compare(c)]


def part_labels(lines: list[TranscriptLine]) -> list[str]:
    """채점에 보여 준 판독문의 소문항 표시(`(1)①`) — 긴 것부터. 내보내기의 줄은 `1-(1)①`처럼 문항
    번호가 붙어 있어 떼어 낸다 (소문항이 없는 문항 `1`은 표시가 없다 — 숫자를 지우면 안 됨)."""
    labels = {p.split("-", 1)[1] if "-" in p else p for x in lines if (p := x.part)}
    return sorted((x for x in labels if not _BARE.fullmatch(x)), key=len, reverse=True)


Quote = tuple[str, Kind, str | None]
"""(채점 인용, 종류, 그 채점 항목의 소문항 — 내보내기 줄의 `part`와 같은 꼴)."""


def locate(
    lines: list[TranscriptLine], quotes: list[Quote], labels: list[str] | None = None
) -> Spots:
    """인용들을 판독문에서 찾는다. 같은 글이 여러 번 나오면 그 소문항 안의 것 → `…`로 이은 앞 조각
    뒤의 것 → 처음 것 순으로 고른다 (짧은 식 `\\theta = \\alpha`가 앞 소문항에도 있는 일 — 실측).
    지운 줄(`struck`)은 찾기에는 넣지만 칠하지 않는다. labels: 인용에 붙어 올 수 있는 소문항
    표시."""
    hay, where = _stream(lines)
    labels = part_labels(lines) if labels is None else labels
    spots = Spots()
    for quote, kind, part in quotes:
        pos = 0

        def in_part(start: int, end: int, part: str | None = part) -> bool:
            return part is not None and all(lines[w[0]].part == part for w in where[start:end])

        for chunk in _chunks(quote):
            hit = _find(hay, chunk, labels, pos, in_part)
            if hit is None:
                spots.missed.append(chunk.strip())
                continue
            start, end = hit
            pos = end
            spots.found += 1
            for li, si, off in where[start:end]:
                if lines[li].struck:
                    continue
                ls = spots.lines.setdefault(li, LineSpots())
                if off == MATH:
                    ls.maths.setdefault(si, set()).add(kind)
                else:
                    ls.chars.setdefault(si, {}).setdefault(off, set()).add(kind)
    return spots


def _find(
    hay: str, chunk: str, labels: list[str], pos: int, in_part: Callable[[int, int], bool]
) -> tuple[int, int] | None:
    """그대로 → 소문항 표시(`(1)`)를 지우고. 나온 곳 가운데 소문항 안 → pos 뒤 → 처음 것."""
    tries = [chunk]
    stripped = chunk
    for label in labels:
        stripped = stripped.replace(label, " ")
    if stripped != chunk:
        tries.append(stripped)
    for text in tries:
        needle = normalize_for_compare(text)
        if not needle:
            continue
        hits: list[tuple[int, int]] = []
        i = hay.find(needle)
        while i >= 0:
            hits.append((i, i + len(needle)))
            i = hay.find(needle, i + 1)
        if hits:
            return min(hits, key=lambda h: (not in_part(*h), h[0] < pos, h[0]))
    return None
