"""컨텍스트 엔진: 작업 하나를 끝까지 책임진다.

    조립 → AI 호출(새 세션) → JSON 추출 → 스키마 검증 → 작업별 검증
         └─ 실패하면 문제 목록을 붙여 다시 조립해 수리 요청 (최대 max_repairs회)

기능 코드는 `engine.run(TASK, TaskInput(...))`만 부르고, 타입이 보장된 결과를 받는다.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from dawoni.agents import AgentClient, AgentError, AgentErrorCode, AgentRequest, AgentResult
from dawoni.agents.base import as_dict
from dawoni.agents.runner import ProgressCallback
from dawoni.config import AgentName, TierName
from dawoni.context.assembler import (
    AssembledPrompt,
    LayerInput,
    TemplateStore,
    assemble,
)
from dawoni.context.calllog import CallLog, CallRecord
from dawoni.context.extract import describe_validation_error, extract_json_object
from dawoni.context.rules import GlobalRule, RuleStore
from dawoni.context.schema import agent_schema
from dawoni.context.tasks import TaskDef
from dawoni.roles import ROLES, Role, instructions
from dawoni.roles import version as role_version

log = logging.getLogger(__name__)

DEFAULT_BUDGET_TOKENS = 60_000
TRANSIENT_ERRORS = frozenset({AgentErrorCode.STALLED, AgentErrorCode.PERMISSION_DENIED})
"""새 세션으로 한 번 더 해 볼 오류. 권한 거부: 가끔 쓸데없는 명령(`echo test` 등)을 시도했다가
막혀 빈 응답을 낸다 — 막는 것은 그대로(샌드박스), 다시 물을 뿐."""
_PREVIOUS_CLIP = 4_000

_CANCEL: ContextVar[threading.Event | None] = ContextVar("dawoni_cancel", default=None)
_PROGRESS: ContextVar[ProgressCallback | None] = ContextVar("dawoni_progress", default=None)
_MADE_BY: ContextVar[list[tuple[str, str | None]] | None] = ContextVar(
    "dawoni_made_by", default=None
)


@contextmanager
def made_by() -> Generator[list[tuple[str, str | None]]]:
    """이 안에서 성공한 `engine.run`의 (AI, 모델)을 차례로 모은다.

    결과에 누가 만들었는지 남길 때 쓴다."""
    calls: list[tuple[str, str | None]] = []
    token = _MADE_BY.set(calls)
    try:
        yield calls
    finally:
        _MADE_BY.reset(token)


@contextmanager
def call_context(
    *, cancel: threading.Event | None = None, on_progress: ProgressCallback | None = None
) -> Generator[None]:
    """이 안에서 일어나는 모든 `engine.run`에 취소 신호·진행 알림을 건다.

    파이프라인 함수마다 cancel·on_progress 인자를 끌고 다니지 않게 한다 (서버 작업 실행기용).
    """
    t1 = _CANCEL.set(cancel)
    t2 = _PROGRESS.set(on_progress)
    try:
        yield
    finally:
        _CANCEL.reset(t1)
        _PROGRESS.reset(t2)


@dataclass
class TaskInput:
    variables: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())
    """과업 템플릿 변수."""
    scope: LayerInput = None
    material: LayerInput = None
    examples: LayerInput = None
    target: str | None = None
    attachments: tuple[Path, ...] = ()


@dataclass
class TaskResult[T: BaseModel]:
    value: T
    attempts: int
    agent: AgentName
    model: str | None
    template_versions: dict[str, str]
    rule_ids: list[str]
    total_tokens: int | None
    duration_s: float
    warnings: list[str]


class TaskFailed(Exception):
    """수리 요청까지 했지만 검증을 통과한 결과를 얻지 못함."""

    def __init__(self, task: str, errors: list[str], attempts: int, last_response: str) -> None:
        self.task = task
        self.errors = errors
        self.attempts = attempts
        self.last_response = last_response
        summary = "; ".join(errors[:3])
        super().__init__(f"{task}: {attempts}회 시도 후에도 검증 실패 — {summary}")


@dataclass(frozen=True)
class _CallOptions:
    fallback: bool | None
    tier: TierName
    keep_sandbox: bool
    on_progress: ProgressCallback | None
    cancel: threading.Event | None


def _clip(s: str, n: int = _PREVIOUS_CLIP) -> str:
    return s if len(s) <= n else s[:n] + f"\n… (이하 {len(s) - n:,}자 생략)"


class ContextEngine:
    def __init__(
        self,
        client: AgentClient,
        *,
        templates: TemplateStore | None = None,
        rules: RuleStore | None = None,
        calllog: CallLog | None = None,
        budget_tokens: int = DEFAULT_BUDGET_TOKENS,
        max_repairs: int = 2,
        transient_retries: int = 1,
    ) -> None:
        self.client = client
        self.templates = templates or TemplateStore()
        self.rules = rules
        self.calllog = calllog
        self.budget_tokens = budget_tokens
        self.max_repairs = max_repairs
        self.transient_retries = transient_retries

    def active_rules(self, task: TaskDef[Any]) -> list[GlobalRule]:
        if self.rules is None or task.rule_target is None:
            return []
        targets = (task.rule_target,) if isinstance(task.rule_target, str) else task.rule_target
        picked: dict[str, GlobalRule] = {}
        for target in targets:
            for r in self.rules.active_for(target):
                picked.setdefault(r.id, r)
        return list(picked.values())

    def role(self, task: TaskDef[Any]) -> Role:
        return ROLES[task.role]

    def tier(self, task: TaskDef[Any]) -> TierName:
        """역할의 등급 — 강사가 설정(`role_tiers`)에서 바꿨으면 그것."""
        role = self.role(task)
        return self.client.config.role_tiers.get(role.name, role.tier)

    def budget(self, task: TaskDef[Any]) -> int:
        return min(self.budget_tokens, self.role(task).budget_tokens)

    def preview(
        self, task: TaskDef[Any], inp: TaskInput, *, repair: str | None = None
    ) -> AssembledPrompt:
        """AI에게 보낼 프롬프트를 그대로 만들어 본다 (호출하지 않음)."""
        role = self.role(task)
        return assemble(
            self.templates,
            task_template=task.template,
            variables=inp.variables,
            output_schema=agent_schema(task.output),
            rules=self.active_rules(task),
            scope=inp.scope,
            material=inp.material,
            examples=inp.examples,
            target=inp.target,
            repair=repair,
            budget_tokens=self.budget(task),
            role=(role.file, role_version(role.name), instructions(role.name)),
        )

    def _log(self, rec: CallRecord) -> None:
        if self.calllog is not None:
            try:
                self.calllog.write(rec)
            except OSError:
                log.exception("호출 기록 실패")

    def _call(
        self, req: AgentRequest, agent: AgentName | None, model: str | None, o: _CallOptions
    ) -> AgentResult:
        return self.client.run(
            req,
            agent=agent,
            model=model,
            tier=o.tier,
            fallback=o.fallback,
            keep_sandbox=o.keep_sandbox,
            on_progress=o.on_progress,
            cancel=o.cancel,
        )

    def run[T: BaseModel](
        self,
        task: TaskDef[T],
        inp: TaskInput,
        *,
        agent: AgentName | None = None,
        model: str | None = None,
        fallback: bool | None = None,
        keep_sandbox: bool = False,
        on_progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> TaskResult[T]:
        """`fallback`: None이면 설정(`auto_fallback`) — 기본은 다른 AI로 넘어가지 않고 멈춤."""
        schema = agent_schema(task.output)
        cancel = cancel or _CANCEL.get()
        on_progress = on_progress or _PROGRESS.get()
        opts = _CallOptions(fallback, self.tier(task), keep_sandbox, on_progress, cancel)
        role = self.role(task)
        own = task.max_repairs if task.max_repairs is not None else role.max_repairs
        max_repairs = min(self.max_repairs, own)
        errors: list[str] = []
        previous = ""
        total_tokens: int | None = None
        duration = 0.0
        warnings: list[str] = []
        pinned: AgentName | None = agent
        attempts = 0
        transient_left = self.transient_retries

        for attempt in range(1, max_repairs + 2):
            attempts = attempt
            repair = None
            if errors:
                repair = self.templates.render(
                    "repair.md.j2", attempt=attempt - 1, errors=errors, previous=_clip(previous)
                )
                if on_progress:
                    on_progress(f"응답 수리 요청 ({attempt - 1}/{max_repairs})")
            prompt = self.preview(task, inp, repair=repair)
            rec = CallRecord(
                task=task.name,
                attempt=attempt,
                ok=False,
                role=role.name,
                tier=opts.tier,
                template_versions=prompt.template_versions,
                rule_ids=prompt.rule_ids,
                est_prompt_tokens=prompt.est_tokens,
            )

            req = AgentRequest(
                prompt=prompt.text,
                attachments=inp.attachments,
                json_schema=schema,
                task=task.name,
                timeout_s=task.timeout_s or role.timeout_s,
                max_image_px=role.image_max_px,
            )
            res: AgentResult
            try:
                try:
                    res = self._call(req, pinned, model if pinned == agent else None, opts)
                except AgentError as e:
                    if e.code not in TRANSIENT_ERRORS or transient_left <= 0:
                        raise
                    # 일시적인 경우가 많다: 새 세션으로 한 번 더 (수리 횟수와 별개)
                    transient_left -= 1
                    log.warning("%s 시도 %d 일시 오류(%s) — 재시도", task.name, attempt, e.code)
                    if on_progress:
                        why = (
                            "AI가 허용되지 않은 작업을 시도해 막았습니다"
                            if e.code == AgentErrorCode.PERMISSION_DENIED
                            else "응답이 멈췄습니다"
                        )
                        on_progress(f"{why} — 새 세션으로 다시 시도합니다 ({e.code})")
                    res = self._call(req, pinned, model if pinned == agent else None, opts)
            except AgentError as e:
                rec.agent = e.agent
                rec.error_code = str(e.code)
                rec.errors = [e.detail] if e.detail else []
                self._log(rec)
                raise
            # 수리 요청은 같은 AI에게 (다른 AI는 같은 실수를 하지 않을 수도 있지만 비교가 흐려짐)
            pinned = res.agent
            rec.agent, rec.model = res.agent, res.model
            rec.total_tokens, rec.duration_s = res.usage.total_tokens, res.duration_s
            rec.schema_enforced = res.schema_enforced
            duration += res.duration_s
            if res.usage.total_tokens is not None:
                total_tokens = (total_tokens or 0) + res.usage.total_tokens
            warnings += res.warnings

            raw = as_dict(res.structured) or None
            data = raw if raw is not None else extract_json_object(res.text)
            previous = res.text or (json.dumps(data, ensure_ascii=False) if data else "")
            if data is None:
                errors = ["응답에서 JSON 객체를 찾지 못했다. 설명 없이 JSON 객체 하나만 출력할 것."]
            else:
                try:
                    value = task.output.model_validate(data)
                except ValidationError as e:
                    errors = describe_validation_error(e)
                else:
                    errors = task.validator(value) if task.validator else []
                    if not errors:
                        rec.ok = True
                        self._log(rec)
                        if (made := _MADE_BY.get()) is not None:
                            made.append((res.agent, res.model))
                        return TaskResult(
                            value=value,
                            attempts=attempt,
                            agent=res.agent,
                            model=res.model,
                            template_versions=prompt.template_versions,
                            rule_ids=prompt.rule_ids,
                            total_tokens=total_tokens,
                            duration_s=duration,
                            warnings=warnings,
                        )
            rec.errors = errors
            self._log(rec)
            log.info("%s 시도 %d 검증 실패: %s", task.name, attempt, errors[:3])

        raise TaskFailed(task.name, errors, attempts, previous)
