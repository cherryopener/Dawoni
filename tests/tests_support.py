"""테스트 공용 도우미 (conftest의 픽스처와 함께 쓴다)."""

from __future__ import annotations

import json
from pathlib import Path


def _calls(state: Path) -> int:
    """가짜 CLI가 받은 호출 수 — 호출마다 prompt_N.txt를 1부터 차례로 만든다."""
    n = 0
    while (state / f"prompt_{n + 1}.txt").exists():
        n += 1
    return n


def fake_args(exe: Path) -> list[list[str]]:
    """script 모드 가짜 CLI가 받은 명령줄 인자들 (호출 순서)."""
    state = exe.with_suffix(".state")
    return [json.loads((state / f"args_{i}.json").read_text()) for i in range(1, _calls(state) + 1)]


def fake_prompts(exe: Path) -> list[str]:
    """script 모드 가짜 CLI가 받은 프롬프트들 (호출 순서)."""
    state = exe.with_suffix(".state")
    n = _calls(state)
    return [(state / f"prompt_{i}.txt").read_text(encoding="utf-8") for i in range(1, n + 1)]
