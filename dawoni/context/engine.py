"""컨텍스트 엔진: 작업 하나를 끝까지 책임진다.

    조립 → AI 호출(새 세션) → JSON 추출 → 스키마 검증 → 작업별 검증
         └─ 실패하면 문제 목록을 붙여 다시 조립해 수리 요청 (최대 max_repairs회)

기능 코드는 `engine.run(TASK, TaskInput(...))`만 부르고, 타입이 보장된 결과를 받는다.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from dawoni.agents import AgentClient, AgentError, AgentErrorCode, AgentRequest, AgentResult
from dawoni.agents.base import as_dict
from dawoni.agents.runner import ProgressCallback
from dawoni.config import AgentName
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

log = logging.getLogger(__name__)

DEFAULT_BUDGET_TOKENS = 60_000
TRANSIENT_ERRORS = frozenset({AgentErrorCode.STALLED})
_PREVIOUS_CLIP = 4_000


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
    fallback: bool
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

    def preview(
        self, task: TaskDef[Any], inp: TaskInput, *, repair: str | None = None
    ) -> AssembledPrompt:
        """AI에게 보낼 프롬프트를 그대로 만들어 본다 (호출하지 않음)."""
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
            budget_tokens=self.budget_tokens,
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
        fallback: bool = True,
        keep_sandbox: bool = False,
        on_progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> TaskResult[T]:
        schema = agent_schema(task.output)
        opts = _CallOptions(fallback, keep_sandbox, on_progress, cancel)
        errors: list[str] = []
        previous = ""
        total_tokens: int | None = None
        duration = 0.0
        warnings: list[str] = []
        pinned: AgentName | None = agent
        attempts = 0
        transient_left = self.transient_retries

        for attempt in range(1, self.max_repairs + 2):
            attempts = attempt
            repair = None
            if errors:
                repair = self.templates.render(
                    "repair.md.j2", attempt=attempt - 1, errors=errors, previous=_clip(previous)
                )
                if on_progress:
                    on_progress(f"응답 수리 요청 ({attempt - 1}/{self.max_repairs})")
            prompt = self.preview(task, inp, repair=repair)
            rec = CallRecord(
                task=task.name,
                attempt=attempt,
                ok=False,
                template_versions=prompt.template_versions,
                rule_ids=prompt.rule_ids,
                est_prompt_tokens=prompt.est_tokens,
            )

            req = AgentRequest(
                prompt=prompt.text,
                attachments=inp.attachments,
                json_schema=schema,
                task=task.name,
                timeout_s=task.timeout_s,
            )
            res: AgentResult
            try:
                try:
                    res = self._call(req, pinned, model if pinned == agent else None, opts)
                except AgentError as e:
                    if e.code not in TRANSIENT_ERRORS or transient_left <= 0:
                        raise
                    # 멈춤은 일시적인 경우가 많다: 새 세션으로 한 번 더 (수리 횟수와 별개)
                    transient_left -= 1
                    log.warning("%s 시도 %d 일시 오류(%s) — 재시도", task.name, attempt, e.code)
                    if on_progress:
                        on_progress(f"응답이 멈춰 새 세션으로 다시 시도합니다 ({e.code})")
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
