"""AI CLI에 넘길 JSON Schema 만들기.

pydantic 스키마를 그대로 넘기면 CLI마다 다르게 깨진다.
- agy 1.4.7: `$ref: "#/$defs/…"`를 스키마 파일 기준 상대 경로로 잘못 해석해 실패 (2026-10-25 실측)
- `discriminator`는 OpenAPI 키워드라 표준 검증기가 무시하거나 `mapping`의 `$ref`를 따라가다 실패

그래서 `$ref`를 모두 펼치고(`$defs` 제거), `discriminator`와 `title`을 뺀다.
판별 유니온은 각 분기의 `const` 태그만으로도 표준 `oneOf`/`anyOf`로 검증된다.
"""

from __future__ import annotations

import copy
from typing import Any, cast

from pydantic import BaseModel


class SchemaError(Exception):
    pass


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    defs = cast(dict[str, Any], schema.get("$defs", {}))

    def resolve(node: Any, stack: tuple[str, ...]) -> Any:
        if isinstance(node, list):
            return [resolve(v, stack) for v in cast(list[Any], node)]
        if not isinstance(node, dict):
            return node
        d = cast(dict[str, Any], node)
        ref = d.get("$ref")
        if isinstance(ref, str):
            if not ref.startswith("#/$defs/"):
                raise SchemaError(f"지원하지 않는 $ref: {ref}")
            name = ref.removeprefix("#/$defs/")
            if name in stack:
                path = " → ".join((*stack, name))
                raise SchemaError(f"순환 참조 스키마는 펼칠 수 없음: {path}")
            if name not in defs:
                raise SchemaError(f"정의되지 않은 $ref: {ref}")
            # $ref 옆에 붙은 설명(description 등)은 살린다
            siblings = {k: v for k, v in d.items() if k != "$ref"}
            merged: dict[str, Any] = {**copy.deepcopy(defs[name]), **siblings}
            return resolve(merged, (*stack, name))
        out: dict[str, Any] = {}
        for k, v in d.items():
            if k in ("$defs", "discriminator") or (k == "title" and isinstance(v, str)):
                continue
            out[k] = resolve(v, stack)
        return out

    return cast(dict[str, Any], resolve(schema, ()))


def agent_schema(model: type[BaseModel]) -> dict[str, Any]:
    """AI에게 보낼(프롬프트·CLI 옵션 공용) 스키마."""
    return inline_refs(model.model_json_schema())
