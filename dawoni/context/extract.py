"""AI 응답에서 JSON 객체 꺼내기와 검증 오류 정리.

CLI가 스키마 강제를 지원하면 구조화 결과를 바로 쓰지만, 지원하지 않거나 못 쓴 경우
(긴 스키마, 다른 CLI)에는 응답 텍스트에서 꺼내야 한다. AI는 흔히 다음을 섞는다:
코드 울타리(```json), 앞뒤 설명 문장, 여러 개의 JSON 조각.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

_FENCE = re.compile(r"```(?:json|JSON)?\s*\n(.*?)\n\s*```", re.DOTALL)


def _decode_objects(s: str) -> list[tuple[int, dict[str, Any]]]:
    """문자열 안의 모든 최상위 JSON 객체 (길이, 객체)."""
    decoder = json.JSONDecoder()
    found: list[tuple[int, dict[str, Any]]] = []
    i = s.find("{")
    while i != -1:
        try:
            obj, end = decoder.raw_decode(s, i)
        except json.JSONDecodeError:
            i = s.find("{", i + 1)
            continue
        if isinstance(obj, dict):
            found.append((end - i, obj))  # pyright: ignore[reportUnknownArgumentType]
            i = s.find("{", end)
        else:
            i = s.find("{", i + 1)
    return found


def extract_json_object(text: str) -> dict[str, Any] | None:
    """가장 그럴듯한 JSON 객체 하나. 없으면 None.

    우선순위: 전체가 JSON → 코드 울타리 안 → 본문에서 찾은 객체 중 가장 긴 것.
    """
    s = text.strip()
    if not s:
        return None
    try:
        v = json.loads(s)
        if isinstance(v, dict):
            return v  # pyright: ignore[reportUnknownVariableType]
    except json.JSONDecodeError:
        pass
    for block in _FENCE.findall(s):
        found = _decode_objects(block)
        if found:
            return max(found, key=lambda t: t[0])[1]
    found = _decode_objects(s)
    return max(found, key=lambda t: t[0])[1] if found else None


def describe_validation_error(e: ValidationError, limit: int = 20) -> list[str]:
    """pydantic 오류를 AI가 고칠 수 있는 문장 목록으로."""
    out: list[str] = []
    for err in e.errors(include_url=False)[:limit]:
        loc = ".".join(str(x) for x in err["loc"]) or "(최상위)"
        msg = err["msg"]
        if err["type"] == "missing":
            out.append(f"필드 `{loc}` 가 빠졌다 (필수).")
        elif err["type"] == "extra_forbidden":
            out.append(f"필드 `{loc}` 는 스키마에 없다 — 지울 것.")
        else:
            out.append(f"`{loc}`: {msg}")
    extra = len(e.errors()) - limit
    if extra > 0:
        out.append(f"(그 밖에 {extra}개 오류)")
    return out
