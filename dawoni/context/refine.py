"""강사의 자연어 요구 → 전역 규칙 초안 (메타프롬프트의 입구).

정제 결과는 바로 저장하지 않는다. 강사가 보고 고친 뒤 저장한다(`GlobalRule`).
"""

from __future__ import annotations

import dataclasses

from pydantic import Field

from dawoni.context.engine import ContextEngine, TaskInput, TaskResult
from dawoni.context.rules import GlobalRule, RuleScope, RuleStore
from dawoni.context.tasks import RULE_TARGET_LABELS, TaskDef
from dawoni.models.base import DawoniModel


class RuleConflict(DawoniModel):
    rule_id: str
    reason: str


class RefinedRule(DawoniModel):
    title: str = Field(min_length=1, max_length=30)
    text: str = Field(min_length=1, max_length=600)
    applies_to: list[RuleScope] = Field(min_length=1)
    conflicts: list[RuleConflict] = Field(default_factory=lambda: list[RuleConflict]())
    clarification_needed: str | None = Field(
        default=None, description="요구가 모호해 강사에게 물어야 할 것. 없으면 null"
    )

    def to_rule(self, request: str, created_by: str | None = None) -> GlobalRule:
        return GlobalRule(
            title=self.title,
            text=self.text,
            applies_to=self.applies_to,
            request=request,
            created_by=created_by,
        )


REFINE_RULE: TaskDef[RefinedRule] = TaskDef(
    name="refine_rule",
    template="refine_rule.md.j2",
    output=RefinedRule,
    rule_target=None,  # 규칙을 만드는 작업에 기존 규칙을 '지시'로 넣으면 안 됨 (자료로만 넣음)
    timeout_s=180,
)


def refine_rule(engine: ContextEngine, request: str, store: RuleStore) -> TaskResult[RefinedRule]:
    existing = store.all()
    known = {r.id for r in existing}

    def check(v: RefinedRule) -> list[str]:
        bad = [c.rule_id for c in v.conflicts if c.rule_id not in known]
        problems = (
            [f"conflicts에 없는 규칙 ID: {bad} (위 '이미 있는 규칙'의 ID만 사용)"] if bad else []
        )
        if "all" in v.applies_to and len(v.applies_to) > 1:
            problems.append('applies_to에 "all"이 있으면 다른 값을 함께 쓰지 않는다')
        return problems

    task = dataclasses.replace(REFINE_RULE, validator=check)
    return engine.run(
        task,
        TaskInput(
            variables={"request": request, "existing": existing, "targets": RULE_TARGET_LABELS}
        ),
    )
