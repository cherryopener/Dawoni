"""AI가 만든 글이 원본(PDF 글자)에 근거하는지 보는 간단한 검사.

수식 표기는 바뀌어도(x² → x^2) 한글 문장은 원본에 있어야 한다. 원본에 없는 한글이 많으면
AI가 문장을 지어냈거나 고친 것이다 — 또는 글이 그림 이미지 속에 있거나 PDF 글자가 깨진 쪽이다.
그런 곳에서 수리 요청을 해도 이 검사만 남으면 받아들이고 강사 확인으로 넘긴다
(`accept_ungrounded`). PDF 글자가 멀쩡한 곳에서 지어낸 글은 여전히 거부한다.
"""

from __future__ import annotations

import re
from collections import Counter

from pydantic import BaseModel, ValidationError

from dawoni.context import TaskFailed
from dawoni.context.extract import extract_json_object

_HANGUL = re.compile(r"[가-힣]")

UNGROUNDED = "PDF 글자에 없는 한글이"
"""근거 검사 오류 문장에 들어가는 말. 이 말이 든 오류만 남았는지로 받아들일지 정한다."""


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


_PRIVATE_USE = re.compile(r"[-]")


def broken_glyphs(text: str) -> bool:
    """PDF 글자에 글꼴 전용 문자(개인 영역)가 있음 — 그 자리의 글은 PDF 글자로 확인할 수 없다."""
    return _PRIVATE_USE.search(text) is not None


def only_ungrounded(errors: list[str]) -> bool:
    return bool(errors) and all(UNGROUNDED in e for e in errors)


def accept_ungrounded[T: BaseModel](e: TaskFailed, output: type[T]) -> T | None:
    """수리한 뒤에도 근거 검사만 남은 마지막 응답. 다른 오류가 있으면 None.

    받을지는 부르는 쪽이 정한다 — 글이 PDF 글자 밖에 있을 수 있는 곳(그림 이미지·깨진 글자)만.
    실측(2026-10-30): 파동 62쪽 '실험 A, B' 상자는 이미지라 PDF 글자에 없고, 기초물리학 97쪽
    해설은 글자가 깨진 기호로 들어 있다 — Gemini·Claude 모두 같은 검사에 3번씩 걸려 쪽을 잃음."""
    if not only_ungrounded(e.errors):
        return None
    data = extract_json_object(e.last_response)
    if data is None:
        return None
    try:
        return output.model_validate(data)
    except ValidationError:
        return None


def review_note(share: float) -> str:
    return (
        f"PDF 글자에 없는 한글 {share:.0%} — 그림 속 글이거나 PDF 글자가 깨진 쪽일 수 있고, "
        "AI가 고쳐 쓴 것일 수도 있음. 원본과 비교하세요"
    )
