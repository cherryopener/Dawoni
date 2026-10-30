"""AI 역할: 모든 작업이 역할에 묶이고, 역할 지시서·등급·수리 횟수가 호출에 반영된다 (가짜 CLI)."""

from __future__ import annotations

import gc
import importlib
import json
import pkgutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import MakeFake
from tests_support import fake_args, fake_prompts

import dawoni
from dawoni.agents import AgentClient
from dawoni.config import AppConfig
from dawoni.context import ContextEngine, TaskDef, TaskFailed, TaskInput
from dawoni.models.base import DawoniModel
from dawoni.paths import AppPaths
from dawoni.roles import ROLES, instructions, role_for_task, version


def _all_tasks() -> list[TaskDef[Any]]:
    for m in pkgutil.walk_packages(dawoni.__path__, "dawoni."):
        if not m.name.startswith(("dawoni.web", "dawoni.__main__")):
            importlib.import_module(m.name)
    return [o for o in gc.get_objects() if isinstance(o, TaskDef)]


def test_every_task_has_a_registered_role() -> None:
    tasks = {t.name: t for t in _all_tasks() if not t.name.startswith("simple")}
    assert len(tasks) >= 25
    for name, t in tasks.items():
        r = role_for_task(name)
        assert r is not None, f"{name}: 역할 등록부(ROLES.tasks)에 없음"
        assert t.role == r.name, f"{name}: TaskDef.role={t.role}, 등록부={r.name}"
    registered = {n for r in ROLES.values() for n in r.tasks}
    assert registered <= set(tasks), f"등록부에만 있는 작업: {registered - set(tasks)}"


def test_role_files_are_english_with_versions() -> None:
    for name in ROLES:
        text = instructions(name)
        assert version(name).startswith("2026-")
        assert text.startswith("Role:")
        assert "Korean" in text  # 강사에게 보이는 출력은 한국어
        assert len(text) < 900  # 호출마다 들어가므로 짧게


def test_role_tiers_follow_the_plan() -> None:
    tiers = {n: r.tier for n, r in ROLES.items()}
    assert {n for n, t in tiers.items() if t == "fast"} == {
        "cover_reader", "light_helper", "lecture_summarizer",
    }  # fmt: skip
    assert {n for n, t in tiers.items() if t == "strong"} == {"solver_verifier", "problem_maker"}


class _Out(DawoniModel):
    ok: bool


COVER_LIKE = TaskDef(
    name="read_cover", template="refine_rule.md.j2", output=_Out, rule_target=None,
    role="cover_reader",
)  # fmt: skip
VARS = TaskInput(variables={"request": "x", "existing": [], "targets": {}})


@pytest.fixture
def engine_for(
    app_paths: AppPaths, make_fake: MakeFake
) -> Callable[[list[dict[str, Any]], AppConfig | None], tuple[ContextEngine, Path]]:
    def make(
        script: list[dict[str, Any]], cfg: AppConfig | None = None
    ) -> tuple[ContextEngine, Path]:
        exe = make_fake("script", script=script)
        cfg = cfg or AppConfig()
        cfg.agents["agy"].executable = str(exe)
        cfg.agents["claude"].enabled = False
        cfg.agents["codex"].enabled = False
        return ContextEngine(AgentClient(cfg, app_paths)), exe

    return make


def _model(argv: list[str]) -> str:
    return argv[argv.index("--model") + 1]


def test_role_layer_and_tier_model_reach_the_cli(
    engine_for: Callable[..., tuple[ContextEngine, Path]],
) -> None:
    engine, exe = engine_for([{"response": json.dumps({"ok": True})}])
    res = engine.run(COVER_LIKE, VARS)
    prompt = fake_prompts(exe)[0]
    assert "## [역할]\nRole: cover reader." in prompt
    assert prompt.index("## [역할]") < prompt.index("## [과업]")
    assert res.template_versions["cover_reader.md"] == version("cover_reader")
    assert _model(fake_args(exe)[0]) == "gemini-3.8-flash-low"  # fast 등급


def test_teacher_can_raise_a_role_tier(
    engine_for: Callable[..., tuple[ContextEngine, Path]],
) -> None:
    cfg = AppConfig()
    cfg.role_tiers["cover_reader"] = "strong"
    engine, exe = engine_for([{"response": json.dumps({"ok": True})}], cfg)
    engine.run(COVER_LIKE, VARS)
    assert _model(fake_args(exe)[0]) == "gemini-3.8-flash-high"


def test_explicit_model_wins_over_tier(
    engine_for: Callable[..., tuple[ContextEngine, Path]],
) -> None:
    engine, exe = engine_for([{"response": json.dumps({"ok": True})}])
    engine.run(COVER_LIKE, VARS, model="my-model")
    assert _model(fake_args(exe)[0]) == "my-model"


def test_role_caps_repairs(engine_for: Callable[..., tuple[ContextEngine, Path]]) -> None:
    engine, exe = engine_for([{"response": "JSON 아님"}] * 5)
    with pytest.raises(TaskFailed):
        engine.run(COVER_LIKE, VARS)
    assert len(fake_prompts(exe)) == 1 + ROLES["cover_reader"].max_repairs
