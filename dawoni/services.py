"""앱 구성요소 조립 (명령줄·서버 공용)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dawoni.agents import AgentClient
from dawoni.config import AppConfig, load_config
from dawoni.context import ContextEngine, MemoryStore, RuleStore, SkillStore
from dawoni.context.calllog import CallLog
from dawoni.logs import setup_logging
from dawoni.paths import AppPaths, WorkspacePaths
from dawoni.workspace import WorkspaceError, open_workspace


@dataclass
class Services:
    paths: AppPaths
    config: AppConfig
    workspace: WorkspacePaths
    rules: RuleStore
    engine: ContextEngine
    skills: SkillStore
    memory: MemoryStore


def open_services(workspace: Path | None = None, *, paths: AppPaths | None = None) -> Services:
    paths = paths or AppPaths.default()
    paths.ensure()
    setup_logging(paths.logs)
    config = load_config(paths)
    root = workspace or config.workspace
    if root is None:
        raise WorkspaceError(
            "작업공간이 정해지지 않았습니다. "
            "`dawoni workspace init <경로>` 후 --workspace로 지정하세요."
        )
    ws, _ = open_workspace(root.expanduser())
    rules, skills, memory = RuleStore(ws.rules), SkillStore(ws.skills), MemoryStore(ws.memory)
    engine = ContextEngine(
        AgentClient(config, paths),
        rules=rules,
        skills=skills,
        memory=memory,
        calllog=CallLog(paths.logs / "calls.jsonl"),
    )
    return Services(
        paths=paths,
        config=config,
        workspace=ws,
        rules=rules,
        engine=engine,
        skills=skills,
        memory=memory,
    )
