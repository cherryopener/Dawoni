"""AI CLI에 넘길 JSON Schema가 문제없이 만들어지는지.

CLI의 `--json-schema` 구현마다 지원 범위가 달라서, 호환성이 떨어지는 구문(튜플용
prefixItems 등)은 아예 쓰지 않는다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel

from dawoni.cli import SCHEMAS


def _walk(node: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(node, dict):
        out.append(node)  # pyright: ignore[reportUnknownArgumentType]
        for v in node.values():  # pyright: ignore[reportUnknownVariableType]
            out.extend(_walk(v))
    elif isinstance(node, list):
        for v in node:  # pyright: ignore[reportUnknownVariableType]
            out.extend(_walk(v))
    return out


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schema_generates(name: str) -> None:
    model: type[BaseModel] = SCHEMAS[name]
    schema = model.model_json_schema()
    json.dumps(schema, ensure_ascii=False)
    nodes = _walk(schema)
    assert not any("prefixItems" in n for n in nodes), "튜플 스키마 사용 금지"
    objects = [n for n in nodes if n.get("type") == "object" and "properties" in n]
    assert objects
    for n in objects:
        assert n.get("additionalProperties") is False, n.get("title")


def test_item_content_schema_has_discriminators() -> None:
    schema = SCHEMAS["item-content"].model_json_schema()
    discriminated = [n for n in _walk(schema) if "discriminator" in n]
    props = {n["discriminator"]["propertyName"] for n in discriminated}
    assert {"t", "type", "kind"} <= props
