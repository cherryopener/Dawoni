"""AI 호출 진입점: 어댑터 선택, 설정 적용, 다른 AI로 전환.

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
)
from dawoni.agents.claude import ClaudeAdapter
from dawoni.agents.codex import CodexAdapter
from dawoni.agents.detect import find_executable, subprocess_env
from dawoni.agents.runner import ProgressCallback, RunOptions, run_agent
from dawoni.config import AGENT_NAMES, AgentName, AppConfig
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
        fallback: bool = True,
        keep_sandbox: bool = False,
        on_progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> AgentResult:
        """요청을 실행한다.

        설치 안 됨·로그인 필요·한도 초과일 때만 다음 AI로 넘어간다. 그 밖의 오류(시간 초과,
        출력 오류 등)는 다른 AI로 바꿔도 같은 문제가 반복될 수 있으므로 그대로 올린다.
        `model`은 첫 번째 AI에만 적용하고, 전환된 AI는 각자 설정의 모델을 쓴다.
        """
        names: list[AgentName] = (
            self.order(agent) if fallback else [agent or self.config.default_agent]
        )
        last: AgentError | None = None
        for i, name in enumerate(names):
            settings = self.config.agent(name)
            exe = find_executable(name, settings.executable)
            if exe is None:
                last = AgentError(AgentErrorCode.NOT_INSTALLED, name, agent=name)
                log.info("%s 미설치 — 건너뜀", name)
                continue
            options = RunOptions(
                timeout_s=req.timeout_s or settings.timeout_s,
                idle_timeout_s=req.idle_timeout_s or settings.idle_timeout_s,
                keep_sandbox=keep_sandbox,
            )
            self.paths.sandboxes.mkdir(parents=True, exist_ok=True)
            try:
                return run_agent(
                    ADAPTERS[name],
                    exe,
                    req,
                    model=(model if i == 0 and model else settings.model),
                    sandbox_base=self.paths.sandboxes,
                    env=subprocess_env(),
                    options=options,
                    on_progress=on_progress,
                    cancel=cancel,
                )
            except AgentError as e:
                last = e
                if e.code in SWITCHABLE_ERRORS:
                    log.warning("%s 사용 불가(%s) — 다음 AI로 전환", name, e.code)
                    continue
                raise
        assert last is not None
        raise last
