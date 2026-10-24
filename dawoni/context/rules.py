"""강사 전역 규칙 (ON/OFF).

- 규칙 1개 = 파일 1개 (`<작업공간>/rules/<id>.json`) → 팀 공유 시 충돌 범위 최소화
- 규칙마다 켜기/끄기와 적용 대상 작업(`applies_to`)을 둔다
- 강사가 자연어로 적은 원문(`request`)을 함께 보관한다 (나중에 다시 정제할 수 있게)
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from dawoni.context.tasks import RuleTarget
from dawoni.fsio import read_json, write_model_atomic
from dawoni.models.base import DawoniModel, new_id, utcnow

log = logging.getLogger(__name__)

RuleScope = RuleTarget | Literal["all"]


class GlobalRule(DawoniModel):
    id: str = Field(default_factory=lambda: new_id("rule"))
    title: str = Field(min_length=1, max_length=60)
    text: str = Field(min_length=1, max_length=1000, description="AI에게 그대로 전달되는 규칙 문장")
    enabled: bool = True
    applies_to: list[RuleScope] = Field(min_length=1)
    request: str | None = Field(default=None, description="강사가 처음 적은 자연어 요청")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    created_by: str | None = None

    def applies(self, target: RuleTarget) -> bool:
        return self.enabled and ("all" in self.applies_to or target in self.applies_to)


class RuleStore:
    def __init__(self, directory: Path) -> None:
        self.dir = directory

    def _path(self, rule_id: str) -> Path:
        return self.dir / f"{rule_id}.json"

    def all(self) -> list[GlobalRule]:
        if not self.dir.is_dir():
            return []
        rules: list[GlobalRule] = []
        for p in sorted(self.dir.glob("rule-*.json")):
            try:
                rules.append(GlobalRule.model_validate(read_json(p)))
            except (ValueError, ValidationError) as e:
                # 깨진 파일 하나 때문에 전체가 멈추지 않게. 로그로 알린다.
                log.warning("규칙 파일을 읽지 못함: %s (%s)", p, e)
        return sorted(rules, key=lambda r: r.created_at)

    def get(self, rule_id: str) -> GlobalRule | None:
        p = self._path(rule_id)
        return GlobalRule.model_validate(read_json(p)) if p.exists() else None

    def save(self, rule: GlobalRule) -> GlobalRule:
        rule.updated_at = utcnow()
        write_model_atomic(self._path(rule.id), rule)
        return rule

    def set_enabled(self, rule_id: str, enabled: bool) -> GlobalRule:
        rule = self.get(rule_id)
        if rule is None:
            raise KeyError(rule_id)
        rule.enabled = enabled
        return self.save(rule)

    def delete(self, rule_id: str) -> bool:
        p = self._path(rule_id)
        if not p.exists():
            return False
        p.unlink()
        return True

    def active_for(self, target: RuleTarget) -> list[GlobalRule]:
        return [r for r in self.all() if r.applies(target)]

    def resolve(self, prefix: str) -> GlobalRule:
        """ID 앞부분으로 규칙 찾기 (명령줄 편의)."""
        matches = [r for r in self.all() if r.id.startswith(prefix) or r.id.endswith(prefix)]
        if len(matches) != 1:
            raise KeyError(f"규칙 '{prefix}'에 해당하는 것이 {len(matches)}개")
        return matches[0]
