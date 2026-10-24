"""실제 AI CLI 연결 테스트. 기본 실행에서 제외되며 `uv run pytest -m agent`로 돌린다.

구독 사용량을 쓰므로 agy만 대상으로 한다 (claude는 개발 중 토큰 절약을 위해 제외).
"""

from __future__ import annotations

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
