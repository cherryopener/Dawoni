"""교차 판독: 두 번째 판독(다른 모델·AI)과 줄 단위로 맞춰 보고, 다른 곳을 표시한다.

한 AI의 판독이 그럴듯해 보여도 틀린 곳은 두 판독이 갈리는 곳에 몰려 있다.
갈린 줄은 확신도를 낮추고 다른 판독을 `uncertain`에 적어 강사가 먼저 보게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from dawoni.grading.checks import normalize_for_compare
from dawoni.models.grading import TranscribedLine

DISAGREE_CONFIDENCE = 0.5


@dataclass
class CrossCheck:
    lines: list[TranscribedLine]
    """첫 판독에 교차 판독 결과를 표시한 것."""
    agreement: float
    only_in_second: list[str] = field(default_factory=lambda: list[str]())


def _mark(line: TranscribedLine, note: str) -> TranscribedLine:
    return line.model_copy(
        update={
            "uncertain": [*line.uncertain, note],
            "confidence": min(line.confidence, DISAGREE_CONFIDENCE),
        }
    )


def cross_check(first: list[TranscribedLine], second: list[TranscribedLine]) -> CrossCheck:
    na = [normalize_for_compare(x.joined) for x in first]
    nb = [normalize_for_compare(x.joined) for x in second]
    out = list(first)
    extra: list[str] = []
    matcher = SequenceMatcher(None, na, nb, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag in ("replace", "delete"):
            for k, i in enumerate(range(i1, i2)):
                j = j1 + k
                if tag == "replace" and j < j2:
                    out[i] = _mark(first[i], f"교차 판독은 '{second[j].joined}'")
                else:
                    out[i] = _mark(first[i], "교차 판독에는 이 줄이 없음")
        if tag in ("replace", "insert"):
            used = (i2 - i1) if tag == "replace" else 0
            extra += [second[j].joined for j in range(j1 + used, j2)]
    agreement = SequenceMatcher(None, "".join(na), "".join(nb), autojunk=False).ratio()
    if not na and not nb:
        agreement = 1.0
    return CrossCheck(lines=out, agreement=round(agreement, 3), only_in_second=extra)
