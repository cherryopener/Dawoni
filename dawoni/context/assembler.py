"""프롬프트 조립: 정해진 레이어를 정해진 순서로.

    [계약] → [역할] → [과업] → [전역 규칙] → [메모리] → [스킬] → [풀이 범위] → [자료]
    → [기준 예시] → [대상] → [출력 형식]

- 출력 형식(스키마)은 맨 끝에 둔다. 마지막에 읽은 지시를 가장 잘 따르기 때문.
- 예산을 넘으면 줄일 수 있는 레이어(기준 예시 → 풀이 범위 → 자료)를 정해진 순서로 줄이고,
  그래도 넘치면 `ContextBudgetError` — 조용히 잘라 내지 않는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from dawoni.context.memory import MemoryNote
from dawoni.context.rules import GlobalRule
from dawoni.context.skills import Skill

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_VERSION = re.compile(r"\{#-?\s*version:\s*([\w.\-]+)")


class ContextBudgetError(Exception):
    pass


class TemplateStore:
    def __init__(self, directory: Path = TEMPLATE_DIR) -> None:
        self.dir = directory
        self.env = Environment(
            loader=FileSystemLoader(str(directory)),
            undefined=StrictUndefined,  # 빠진 변수는 조용히 빈칸이 되지 않고 오류
            keep_trailing_newline=True,
            autoescape=False,
        )

    def version(self, name: str) -> str:
        head = (self.dir / name).read_text(encoding="utf-8").split("\n", 1)[0]
        m = _VERSION.search(head)
        if not m:
            raise ValueError(f"템플릿 첫 줄에 version이 없음: {name}")
        return m.group(1)

    def render(self, template: str, /, **variables: Any) -> str:
        # 위치 전용 인자: 템플릿 변수 이름(name, template 등)과 충돌하지 않게
        return self.env.get_template(template).render(**variables).strip()


def estimate_tokens(text: str) -> int:
    """보수적 토큰 추정. 한글 등 비ASCII는 글자당 1토큰, ASCII는 4글자당 1토큰."""
    non_ascii = sum(1 for c in text if ord(c) > 127)
    return non_ascii + (len(text) - non_ascii + 3) // 4


def compact_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """프롬프트용 스키마: 토큰을 먹는 `title`만 뺀다 (description은 AI에게 필요한 설명이라 유지)."""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():  # pyright: ignore[reportUnknownVariableType]
                if k == "title" and isinstance(v, str):
                    continue
                out[str(k)] = walk(v)  # pyright: ignore[reportUnknownArgumentType]
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]  # pyright: ignore[reportUnknownVariableType]
        return node

    result: dict[str, Any] = walk(schema)
    return result


@dataclass
class Shrinkable:
    """줄일 수 있는 레이어: 가장 자세한 것부터 가장 짧은 것까지 여러 판본."""

    variants: list[str]

    def __post_init__(self) -> None:
        if not self.variants:
            raise ValueError("판본이 하나 이상 필요")


LayerInput = str | Shrinkable | None


@dataclass
class AssembledPrompt:
    text: str
    template_versions: dict[str, str]
    rule_ids: list[str]
    est_tokens: int
    skill_ids: list[str] = field(default_factory=lambda: list[str]())
    memory_ids: list[str] = field(default_factory=lambda: list[str]())
    shrunk: list[str] = field(default_factory=lambda: list[str]())
    """예산 때문에 줄인 레이어 이름."""


# 줄이는 순서 (먼저 줄일 것부터)
_SHRINK_ORDER = ("examples", "scope", "material")

_HEADINGS = {
    "role": "역할",
    "task": "과업",
    "rules": "반드시 지킬 전역 규칙 (강사 설정)",
    "memory": "메모리 (이전 작업에서 강사에게 배운 것)",
    "skills": "스킬 (이번 상황에 맞는 작업 방식 — 따를 것)",
    "scope": "풀이 범위",
    "material": "자료",
    "examples": "기준 예시",
    "target": "대상",
    "output": "출력 형식",
}


def render_rules(rules: list[GlobalRule]) -> str:
    return "\n".join(f"- [{r.title}] {r.text}" for r in rules)


def render_memory(notes: list[MemoryNote]) -> str:
    return "\n".join(f"- {n.text}" for n in notes)


def render_skills(skills: list[Skill]) -> str:
    return "\n\n".join(f"### {s.name}\n{s.body.strip()}" for s in skills)


def render_output_section(schema: dict[str, Any]) -> str:
    body = json.dumps(compact_schema(schema), ensure_ascii=False, separators=(",", ":"))
    return (
        "아래 JSON Schema를 만족하는 JSON 객체 하나만 출력한다. "
        "스키마에 없는 필드를 만들지 않는다. 필수 필드를 빠뜨리지 않는다.\n" + body
    )


def assemble(
    templates: TemplateStore,
    *,
    task_template: str,
    variables: dict[str, Any],
    output_schema: dict[str, Any],
    rules: list[GlobalRule],
    scope: LayerInput = None,
    material: LayerInput = None,
    examples: LayerInput = None,
    target: str | None = None,
    repair: str | None = None,
    budget_tokens: int,
    role: tuple[str, str, str] | None = None,
    memory: list[MemoryNote] | None = None,
    skills: list[Skill] | None = None,
) -> AssembledPrompt:
    """`role`: (역할 지시서 파일 이름, 판, 본문) — 계약 다음, 과업 앞에 넣는다."""
    versions = {
        "contract.md.j2": templates.version("contract.md.j2"),
        task_template: templates.version(task_template),
    }
    if role is not None:
        versions[role[0]] = role[1]
    contract = templates.render("contract.md.j2")
    task = templates.render(task_template, **variables)

    picks: dict[str, int] = {"scope": 0, "material": 0, "examples": 0}
    inputs: dict[str, LayerInput] = {"scope": scope, "material": material, "examples": examples}

    def layer_text(name: str) -> str | None:
        v = inputs[name]
        if v is None:
            return None
        if isinstance(v, str):
            return v
        return v.variants[min(picks[name], len(v.variants) - 1)]

    def build() -> str:
        sections: list[tuple[str, str | None]] = [
            ("role", role[2] if role else None),
            ("task", task),
            ("rules", render_rules(rules) if rules else None),
            ("memory", render_memory(memory) if memory else None),
            ("skills", render_skills(skills) if skills else None),
            ("scope", layer_text("scope")),
            ("material", layer_text("material")),
            ("examples", layer_text("examples")),
            ("target", target),
            ("output", render_output_section(output_schema)),
        ]
        parts = [contract]
        for name, body in sections:
            if body and body.strip():
                parts.append(f"## [{_HEADINGS[name]}]\n{body.strip()}")
        if repair:
            parts.append(repair.strip())
        return "\n\n".join(parts) + "\n"

    text = build()
    shrunk: list[str] = []
    for name in _SHRINK_ORDER:
        v = inputs[name]
        if not isinstance(v, Shrinkable):
            continue
        while estimate_tokens(text) > budget_tokens and picks[name] < len(v.variants) - 1:
            picks[name] += 1
            text = build()
            if name not in shrunk:
                shrunk.append(name)
    est = estimate_tokens(text)
    if est > budget_tokens:
        raise ContextBudgetError(
            f"프롬프트가 예산을 넘음: 약 {est:,} 토큰 > {budget_tokens:,}. "
            "대상을 나눠서 호출해야 합니다."
        )
    return AssembledPrompt(
        text=text,
        template_versions=versions,
        rule_ids=[r.id for r in rules],
        est_tokens=est,
        skill_ids=[s.id for s in skills or []],
        memory_ids=[n.id for n in memory or []],
        shrunk=shrunk,
    )
