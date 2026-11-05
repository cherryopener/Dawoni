"""배우기: 강사의 수정·지적 → 메모리 또는 스킬 제안 (배움의 고리).

강사가 AI 결과를 고치거나 "앞으로는 이렇게"라고 말하면, AI가 그것을 다음 작업에 쓸 수 있는
메모리(짧은 한 문장) 또는 스킬(상황별 작업 방식)로 정리한다. 결과는 **제안**으로 저장되고
강사가 승인해야 적용된다.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import Field

from dawoni.context.engine import ContextEngine, TaskInput, TaskResult
from dawoni.context.memory import MemoryNote, MemoryStore
from dawoni.context.rules import RuleScope, RuleStore
from dawoni.context.skills import BODY_MAX, Skill, SkillStore
from dawoni.context.tasks import RULE_TARGET_LABELS, RuleTarget, TaskDef
from dawoni.models.base import DawoniModel

_CLIP = 3_000


class LessonMemory(DawoniModel):
    text: str = Field(min_length=1, max_length=200)
    applies_to: list[RuleScope] = Field(min_length=1)
    replaces: list[str] = Field(
        default_factory=lambda: list[str](), description="이 메모로 합쳐 대신할 기존 메모 ID"
    )


class LessonSkill(DawoniModel):
    revises: str | None = Field(default=None, description="고칠 기존 스킬 ID. 새 스킬이면 null")
    name: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=200)
    applies_to: list[RuleScope] = Field(min_length=1)
    triggers: list[str] = Field(default_factory=lambda: list[str](), max_length=8)
    body: str = Field(min_length=1, max_length=BODY_MAX)


class Lesson(DawoniModel):
    kind: Literal["none", "memory", "skill"]
    reason: str = Field(min_length=1, max_length=300)
    memory: LessonMemory | None = Field(default=None, description="kind가 memory일 때만")
    skill: LessonSkill | None = Field(default=None, description="kind가 skill일 때만")


LEARN: TaskDef[Lesson] = TaskDef(
    role="light_helper",
    name="learn_lesson",
    template="learn_lesson.md.j2",
    output=Lesson,
    rule_target=None,  # 배우는 작업에 기존 규칙·메모리·스킬을 '지시'로 넣지 않음 (자료로만)
    timeout_s=240,
)


@dataclasses.dataclass(frozen=True)
class Correction:
    note: str = ""
    """강사의 말 ("앞으로는 이렇게")."""
    before: str = ""
    """AI가 낸 결과."""
    after: str = ""
    """강사가 고친 결과."""
    target: RuleTarget | None = None
    situation: str = ""
    """자료 파일 이름, 강의 제목·채널, 시험 이름 등 — 스킬의 상황 단어 후보."""


def _leaves(node: Any, path: str, ignore: frozenset[str], out: dict[str, str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():  # pyright: ignore[reportUnknownVariableType]
            if str(k) not in ignore:  # pyright: ignore[reportUnknownArgumentType]
                _leaves(v, f"{path}.{k}" if path else str(k), ignore, out)  # pyright: ignore[reportUnknownArgumentType]
    elif isinstance(node, list):
        for i, v in enumerate(node):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            _leaves(v, f"{path}[{i}]", ignore, out)
    elif node not in (None, "", [], {}):
        out[path] = str(node)


def diff_text(before: Any, after: Any, *, ignore: Iterable[str] = ()) -> tuple[str, str]:
    """두 JSON 값에서 **바뀐 부분만** "경로: 값" 줄로 (배우기 요청을 짧게)."""
    skip = frozenset(ignore)
    b: dict[str, str] = {}
    a: dict[str, str] = {}
    _leaves(before, "", skip, b)
    _leaves(after, "", skip, a)
    changed = [k for k in dict.fromkeys([*b, *a]) if b.get(k) != a.get(k)]

    def show(d: dict[str, str]) -> str:
        return "\n".join(f"{k}: {d[k]}" if k in d else f"{k}: (없음)" for k in changed)

    return show(b), show(a)


def _clip(s: str) -> str:
    s = s.strip()
    return s if len(s) <= _CLIP else s[:_CLIP] + f"\n… (이하 {len(s) - _CLIP:,}자 생략)"


def _known(rules: RuleStore, memory: MemoryStore, skills: SkillStore) -> str:
    lines = ["[이미 있는 규칙]"]
    lines += [f"- {r.title}: {r.text}" for r in rules.all() if r.enabled] or ["(없음)"]
    used = memory.usage()
    lines += [
        "",
        f"[이미 있는 메모리] (작업마다 상한 {memory.limit}자, 지금 사용: "
        + ", ".join(f"{t} {n}자" for t, n in used.items() if n)
        + ")",
    ]
    lines += [f"- {n.id} [{', '.join(n.applies_to)}] {n.text}" for n in memory.all() if n.live] or [
        "(없음)"
    ]
    lines += ["", "[이미 있는 스킬]"]
    for s in skills.all():
        if s.proposed:
            continue
        head = f"- {s.id} [{', '.join(s.applies_to)}] {s.name} — {s.description}"
        lines.append(head + (f" (상황 단어: {', '.join(s.triggers)})" if s.triggers else ""))
        lines.append("  본문: " + _clip(s.body).replace("\n", "\n  "))
    if lines[-1] == "[이미 있는 스킬]":
        lines.append("(없음)")
    return "\n".join(lines)


def learn(
    engine: ContextEngine,
    c: Correction,
    *,
    rules: RuleStore,
    memory: MemoryStore,
    skills: SkillStore,
) -> TaskResult[Lesson]:
    if not (c.note.strip() or c.after.strip()):
        raise ValueError("강사의 말이나 고친 결과 중 하나는 있어야 합니다")
    mem_ids = {n.id for n in memory.all() if n.live}
    skill_ids = {s.id for s in skills.all() if not s.proposed}

    def check(v: Lesson) -> list[str]:
        problems: list[str] = []
        if v.kind == "memory" and v.memory is None:
            problems.append("kind가 memory이면 memory를 채울 것")
        if v.kind == "skill" and v.skill is None:
            problems.append("kind가 skill이면 skill을 채울 것")
        if v.memory and (bad := [i for i in v.memory.replaces if i not in mem_ids]):
            problems.append(f"replaces에 없는 메모 ID: {bad} (위 '이미 있는 메모리'의 ID만)")
        if v.skill and v.skill.revises and v.skill.revises not in skill_ids:
            problems.append(f"revises에 없는 스킬 ID: {v.skill.revises}")
        for scopes in (v.memory and v.memory.applies_to, v.skill and v.skill.applies_to):
            if scopes and "all" in scopes and len(scopes) > 1:
                problems.append('applies_to에 "all"이 있으면 다른 값을 함께 쓰지 않는다')
        return problems

    shown: list[str] = []
    if c.before.strip():
        shown.append("[AI가 낸 결과]\n" + _clip(c.before))
    if c.after.strip():
        shown.append("[강사가 고친 결과]\n" + _clip(c.after))
    return engine.run(
        dataclasses.replace(LEARN, validator=check),
        TaskInput(
            variables={
                "targets": RULE_TARGET_LABELS,
                "target_label": RULE_TARGET_LABELS[c.target] if c.target else "(정하지 않음)",
                "situation": c.situation.strip(),
                "note": c.note.strip(),
            },
            material=_known(rules, memory, skills),
            target="\n\n".join(shown) or None,
        ),
    )


def propose(
    lesson: Lesson,
    c: Correction,
    *,
    memory: MemoryStore,
    skills: SkillStore,
    by: str | None = None,
) -> MemoryNote | Skill | None:
    """배운 것을 제안으로 저장한다 (승인 전에는 적용되지 않음)."""
    request = c.note.strip() or "(결과 수정에서 배움)"
    if lesson.kind == "memory" and lesson.memory:
        m = lesson.memory
        return memory.save(
            MemoryNote(
                text=m.text,
                applies_to=m.applies_to,
                replaces=m.replaces,
                proposed=True,
                request=request,
                created_by=by,
            )
        )
    if lesson.kind == "skill" and lesson.skill:
        k = lesson.skill
        return skills.save(
            Skill(
                name=k.name,
                description=k.description,
                applies_to=k.applies_to,
                triggers=[t.strip() for t in k.triggers if t.strip()],
                body=k.body,
                proposed=True,
                revises=k.revises,
                request=request,
                created_by=by,
            )
        )
    return None
