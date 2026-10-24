from __future__ import annotations

import json
import shlex
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dawoni.paths import ENV_HOME, AppPaths


@pytest.fixture
def app_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppPaths:
    """테스트마다 독립된 앱 데이터 폴더 (실제 사용자 설정을 건드리지 않음)."""
    home = tmp_path / "app"
    monkeypatch.setenv(ENV_HOME, str(home))
    paths = AppPaths.default()
    paths.ensure()
    return paths


@pytest.fixture
def incline_item() -> dict[str, Any]:
    """PLAN.md §5-2의 빗면 예시 문항 (AI가 출력할 JSON 형태)."""
    return {
        "item_type": "multiple_choice",
        "subject": "물리",
        "unit": "운동의 법칙",
        "variables": {
            "m": {"value": 2, "unit": "kg", "min": 1, "max": 5, "step": 1},
            "theta": {"value": 30, "unit": "deg", "choices": [30, 37, 45, 53, 60]},
            "mu": {"value": 0.2, "min": 0, "max": 0.5, "step": 0.1},
        },
        "constraints": ["mu < tan(theta*pi/180)"],
        "stem": [
            {
                "type": "paragraph",
                "runs": [
                    {"t": "text", "v": "그림과 같이 질량이 "},
                    {"t": "math", "v": "\\var{m}\\,\\mathrm{kg}"},
                    {"t": "text", "v": "인 물체가 경사각 "},
                    {"t": "math", "v": "\\var{theta}^\\circ"},
                    {"t": "text", "v": "인 빗면을 미끄러져 내려온다. 가속도의 크기는?"},
                ],
            },
            {"type": "figure", "figure_id": "F1"},
        ],
        "choices": [
            {"label": i, "runs": [{"t": "math", "v": f"{v}\\,\\mathrm{{m/s^2}}"}]}
            for i, v in enumerate(["1.5", "2.3", "3.3", "4.0", "5.0"], start=1)
        ],
        "answer": {"kind": "choice", "labels": [3]},
        "figures": [
            {
                "id": "F1",
                "mode": "parametric",
                "orig": "figures/F1.orig.png",
                "spec": {
                    "kind": "incline",
                    "angle_deg": "theta",
                    "angle_label": "θ",
                    "block_label": "\\var{m} kg",
                    "block_position": 0.55,
                    "friction_label": "μ = \\var{mu}",
                    "forces": [{"force": "acceleration", "label": "a"}],
                },
            }
        ],
    }


FAKE_AGY = Path(__file__).with_name("fake_agy.py")
MakeFake = Callable[..., Path]


@pytest.fixture
def make_fake(tmp_path: Path) -> MakeFake:
    """동작 모드가 고정된 가짜 agy 실행 파일을 만든다 (POSIX 전용).

    make_fake("ok") / make_fake("script", script=[{"response": "..."}, ...])
    script 모드에서는 실행 파일 옆 `<이름>.state/prompt_N.txt`에 받은 프롬프트가 남는다.
    """
    if sys.platform == "win32":
        pytest.skip("가짜 CLI는 POSIX 셸 스크립트")
    counter = {"n": 0}

    def make(mode: str, *, script: list[dict[str, Any]] | None = None) -> Path:
        counter["n"] += 1
        exe = tmp_path / f"fake-agy-{mode}-{counter['n']}"
        env = {"FAKE_AGENT_MODE": mode}
        if script is not None:
            script_path = exe.with_suffix(".script.json")
            script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
            env["FAKE_SCRIPT"] = str(script_path)
            env["FAKE_STATE_DIR"] = str(exe.with_suffix(".state"))
        assigns = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
        exe.write_text(
            f'#!/bin/sh\n{assigns} exec "{sys.executable}" "{FAKE_AGY}" "$@"\n', encoding="utf-8"
        )
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
        return exe

    return make
