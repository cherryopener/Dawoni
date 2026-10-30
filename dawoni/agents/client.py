"""AI 호출 진입점: 어댑터 선택, 설정·등급 적용, (허락했을 때만) 다른 AI로 전환.

기능 코드는 이 모듈의 `AgentClient.run()`만 쓴다.
"""

from __future__ import annotations

import logging
import threading

from dawoni.agents.agy import AgyAdapter
from dawoni.agents.base import (
    SWITCHABLE_ERRORS,
    AgentAdapter,
    AgentError,
    AgentErrorCode,
    AgentRequest,
    AgentResult,
    AgentSwitchNeeded,
)
from dawoni.agents.claude import ClaudeAdapter
from dawoni.agents.codex import CodexAdapter
from dawoni.agents.detect import find_executable, subprocess_env
from dawoni.agents.runner import ProgressCallback, RunOptions, run_agent
from dawoni.config import AGENT_NAMES, AgentName, AppConfig, TierName
from dawoni.paths import AppPaths

log = logging.getLogger(__name__)

ADAPTERS: dict[AgentName, AgentAdapter] = {
    "agy": AgyAdapter(),
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
}


class AgentClient:
    def __init__(self, config: AppConfig, paths: AppPaths) -> None:
        self.config = config
        self.paths = paths

    def order(self, preferred: AgentName | None) -> list[AgentName]:
        first: AgentName = preferred or self.config.default_agent
        rest: list[AgentName] = [
            n for n in AGENT_NAMES if n is not first and self.config.agent(n).enabled
        ]
        return [first, *rest]

    def run(
        self,
        req: AgentRequest,
        *,
        agent: AgentName | None = None,
        model: str | None = None,
        tier: TierName | None = None,
        fallback: bool | None = None,
        keep_sandbox: bool = False,
        on_progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> AgentResult:
        """요청을 실행한다.

        모델: `model`(첫 번째 AI에만) → 등급(`tier`)의 AI별 모델 → AI 설정의 모델 순.
        설치 안 됨·로그인 필요·한도 초과일 때 `fallback`(None이면 설정 `auto_fallback`)이
        켜져 있으면 다음 AI로 넘어가고, 꺼져 있으면 `AgentSwitchNeeded`로 멈춘다 — 조용히
        다른 AI의 한도를 쓰지 않는다. `fallback=False`(연결 시험 등 특정 AI만)는 원래 오류.
        그 밖의 오류(시간 초과, 출력 오류 등)는 그대로 올린다.
        """
        first: AgentName = agent or self.config.default_agent
        switch = self.config.auto_fallback if fallback is None else fallback
        names: list[AgentName] = self.order(first) if switch else [first]
        last: AgentError | None = None
        for i, name in enumerate(names):
            settings = self.config.agent(name)
            exe = find_executable(name, settings.executable)
            if exe is None:
                last = AgentError(AgentErrorCode.NOT_INSTALLED, name, agent=name)
                log.info("%s 미설치", name)
                continue
            picked = self.config.tier_model(name, tier)
            options = RunOptions(
                timeout_s=req.timeout_s or settings.timeout_s,
                idle_timeout_s=req.idle_timeout_s or settings.idle_timeout_s,
                keep_sandbox=keep_sandbox,
                effort=None if (i == 0 and model) else picked.effort,
            )
            self.paths.sandboxes.mkdir(parents=True, exist_ok=True)
            try:
                return run_agent(
                    ADAPTERS[name],
                    exe,
                    req,
                    model=(model if i == 0 and model else picked.model),
                    sandbox_base=self.paths.sandboxes,
                    env=subprocess_env(),
                    options=options,
                    on_progress=on_progress,
                    cancel=cancel,
                )
            except AgentError as e:
                last = e
                if e.code in SWITCHABLE_ERRORS:
                    log.warning("%s 사용 불가(%s)", name, e.code)
                    continue
                raise
        assert last is not None
        if fallback is None and not switch and last.code in SWITCHABLE_ERRORS:
            others: list[AgentName] = [
                n for n in AGENT_NAMES if n != first and self.config.agent(n).enabled
            ]
            raise AgentSwitchNeeded(last, others) from last
        raise last
