"""실제 AI CLI 연결 테스트. 기본 실행에서 제외되며 `uv run pytest -m agent`로 돌린다.

구독 사용량을 쓰므로 agy만 대상으로 한다 (claude는 개발 중 토큰 절약을 위해 제외).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dawoni.agents import AgentClient
from dawoni.agents.detect import find_executable
from dawoni.agents.selftest import evaluate, selftest_request
from dawoni.config import AppConfig
from dawoni.paths import AppPaths

pytestmark = [
    pytest.mark.agent,
    pytest.mark.skipif(find_executable("agy") is None, reason="agy 미설치"),
]


def test_agy_reads_image_without_permission_prompt(app_paths: AppPaths) -> None:
    client = AgentClient(AppConfig(), app_paths)
    res = client.run(selftest_request(), agent="agy", fallback=False)
    report = evaluate(res)
    assert report.passed, report.problems
    assert res.schema_enforced
    assert not res.warnings, res.warnings


def test_agy_refines_rule_and_flags_conflict(app_paths: AppPaths, tmp_path: Path) -> None:
    from dawoni.context import ContextEngine, GlobalRule, RuleStore
    from dawoni.context.refine import refine_rule

    store = RuleStore(tmp_path / "rules")
    short = store.save(
        GlobalRule(title="짧은 첨삭", text="첨삭은 두 문장 이내로 쓸 것.", applies_to=["feedback"])
    )
    engine = ContextEngine(AgentClient(AppConfig(), app_paths), rules=store)
    res = refine_rule(engine, "첨삭은 풀이 단계마다 아주 자세하게 길게 써 줘", store)
    assert "feedback" in res.value.applies_to or "all" in res.value.applies_to
    assert [c.rule_id for c in res.value.conflicts] == [short.id]
