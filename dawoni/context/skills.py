"""스킬: 특정 상황에서만 꺼내 쓰는 작업 방식 (절차서).

전역 규칙은 해당 작업의 모든 요청에 들어간다. 스킬은 길어도 되는 대신 **상황이 맞을 때만** 들어간다.

- 스킬 1개 = 폴더 1개 (`<작업공간>/skills/<id>/SKILL.md`). 앞머리(`---` 사이)에 이름·설명·
  적용 작업·상황 단어, 그 아래가 본문. 사람이 직접 열어 고쳐도 되고, 다른 곳의 SKILL.md를
  넣어도 된다
- 상황 단어(`triggers`) 가운데 하나라도 이번 작업의 상황(자료 파일 이름, 강의 제목·채널,
  시험 이름 등)에 들어 있으면 적용. 상황 단어가 없으면 그 작업마다 적용
- 다워니가 배워서 만든 스킬은 `proposed`로 저장되고, 강사가 승인해야 적용된다
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from dawoni.context.rules import RuleScope
from dawoni.context.tasks import RuleTarget
from dawoni.fsio import write_text_atomic
from dawoni.models.base import DawoniModel, new_id, utcnow

log = logging.getLogger(__name__)

SKILL_FILE = "SKILL.md"
BODY_MAX = 4000
_FRONT = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.S)


class Skill(DawoniModel):
    id: str = Field(default_factory=lambda: new_id("skill"))
    name: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=200, description="무엇을, 언제 쓰는 스킬인지")
    applies_to: list[RuleScope] = Field(min_length=1)
    triggers: list[str] = Field(
        default_factory=lambda: list[str](),
        description="상황 단어. 하나라도 이번 작업 상황에 들어 있으면 적용. 비면 그 작업마다 적용",
    )
    body: str = Field(min_length=1, max_length=BODY_MAX, description="AI에게 그대로 전달되는 본문")
    enabled: bool = True
    proposed: bool = False
    """다워니가 배워서 제안한 것. 강사가 승인하기 전에는 적용하지 않는다."""
    revises: str | None = None
    """제안이 기존 스킬을 고치는 것이면 그 스킬 ID (승인하면 그 스킬에 덮어쓴다)."""
    request: str | None = Field(default=None, description="이 스킬이 나온 강사의 말·수정 내용")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    created_by: str | None = None

    def applies(self, target: RuleTarget, situation: str) -> bool:
        if not self.enabled or self.proposed:
            return False
        if "all" not in self.applies_to and target not in self.applies_to:
            return False
        if not self.triggers:
            return True
        s = situation.casefold()
        return any(t.strip() and t.strip().casefold() in s for t in self.triggers)


def dump_skill(skill: Skill) -> str:
    """SKILL.md 형식. 앞머리 값은 JSON으로 쓴다 (YAML로도 그대로 읽히는 형태)."""
    data = skill.model_dump(mode="json", exclude={"body"})
    head = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in data.items())
    return f"---\n{head}\n---\n\n{skill.body.strip()}\n"


def parse_skill(text: str, *, fallback_id: str) -> Skill:
    m = _FRONT.match(text.replace("\r\n", "\n"))
    if not m:
        raise ValueError("앞머리(--- … ---)가 없음")
    meta: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, raw = line.split(":", 1)
        raw = raw.strip()
        try:
            meta[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            meta[key.strip()] = raw.strip("'\"")  # 손으로 쓴 YAML 문자열
    meta.setdefault("id", fallback_id)
    meta.setdefault("applies_to", ["all"])  # 다른 곳에서 가져온 SKILL.md
    return Skill.model_validate({**meta, "body": m.group(2).strip()})


class SkillStore:
    def __init__(self, directory: Path) -> None:
        self.dir = directory

    def _path(self, skill_id: str) -> Path:
        return self.dir / skill_id / SKILL_FILE

    def all(self) -> list[Skill]:
        if not self.dir.is_dir():
            return []
        skills: list[Skill] = []
        for p in sorted(self.dir.glob(f"*/{SKILL_FILE}")):
            try:
                skills.append(parse_skill(p.read_text(encoding="utf-8"), fallback_id=p.parent.name))
            except (OSError, ValueError, ValidationError) as e:
                log.warning("스킬 파일을 읽지 못함: %s (%s)", p, e)
        return sorted(skills, key=lambda s: s.created_at)

    def get(self, skill_id: str) -> Skill | None:
        return next((s for s in self.all() if s.id == skill_id), None)

    def require(self, skill_id: str) -> Skill:
        skill = self.get(skill_id)
        if skill is None:
            raise KeyError(f"스킬 {skill_id} 없음")
        return skill

    def save(self, skill: Skill) -> Skill:
        skill.updated_at = utcnow()
        path = self._path(skill.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, dump_skill(skill))
        return skill

    def delete(self, skill_id: str) -> bool:
        d = self.dir / skill_id
        if not (d / SKILL_FILE).exists():
            return False
        shutil.rmtree(d)
        return True

    def approve(self, skill_id: str) -> Skill:
        """제안 승인. 기존 스킬을 고치는 제안이면 그 스킬에 덮어쓰고 제안은 지운다."""
        prop = self.require(skill_id)
        if prop.revises and (orig := self.get(prop.revises)) is not None:
            fields = ("name", "description", "applies_to", "triggers", "body", "request")
            updated = orig.model_copy(update={f: getattr(prop, f) for f in fields})
            self.delete(prop.id)
            return self.save(updated)
        return self.save(prop.model_copy(update={"proposed": False, "revises": None}))

    def active_for(self, targets: Iterable[RuleTarget], situation: str) -> list[Skill]:
        ts = tuple(targets)
        return [s for s in self.all() if any(s.applies(t, situation) for t in ts)]
