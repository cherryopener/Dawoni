"""CLI 브리지 시나리오 테스트 (가짜 agy 사용, 실제 AI 호출 없음)."""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from dawoni.agents import client as client_mod
from dawoni.agents import runner as runner_mod
from dawoni.agents.agy import AgyAdapter
from dawoni.agents.base import (
    AgentError,
    AgentErrorCode,
    AgentRequest,
    classify_failure,
)
from dawoni.agents.claude import MAX_INLINE_SCHEMA_CHARS, ClaudeAdapter
from dawoni.agents.codex import CodexAdapter
from dawoni.agents.detect import extra_bin_dirs, find_executable, subprocess_env
from dawoni.agents.runner import RunOptions, run_agent
from dawoni.config import AgentName, AppConfig
from dawoni.paths import AppPaths

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="가짜 CLI는 POSIX 셸 스크립트")

MakeFake = Callable[..., Path]
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


def _run(
    exe: Path,
    req: AgentRequest,
    sandbox_base: Path,
    *,
    idle: float = 5,
    timeout: float = 20,
    cancel: threading.Event | None = None,
    progress: list[str] | None = None,
):
    return run_agent(
        AgyAdapter(),
        exe,
        req,
        model="fake-model",
        sandbox_base=sandbox_base,
        env=subprocess_env(),
        options=RunOptions(timeout_s=timeout, idle_timeout_s=idle),
        on_progress=progress.append if progress is not None else None,
        cancel=cancel,
    )


def _err(
    exe: Path,
    sandbox_base: Path,
    code: AgentErrorCode,
    *,
    idle: float = 5,
    timeout: float = 20,
) -> AgentError:
    with pytest.raises(AgentError) as ei:
        _run(exe, AgentRequest(prompt="hi"), sandbox_base, idle=idle, timeout=timeout)
    assert ei.value.code == code, ei.value
    return ei.value


# ---------------------------------------------------------------- 정상 동작


def test_ok_with_schema(make_fake: MakeFake, tmp_path: Path) -> None:
    progress: list[str] = []
    res = _run(
        make_fake("ok"),
        AgentRequest(prompt="안녕하세요", json_schema=SCHEMA),
        tmp_path / "sb",
        progress=progress,
    )
    assert res.structured == {"ok": True, "echo": "안녕하세요"}
    assert res.schema_enforced
    assert res.model == "fake-model"
    assert res.usage.total_tokens == 15
    assert not res.sandbox.exists(), "성공하면 격리 폴더 삭제"
    assert any("read_file" in p for p in progress)


def test_ok_without_schema(make_fake: MakeFake, tmp_path: Path) -> None:
    res = _run(make_fake("ok"), AgentRequest(prompt="x"), tmp_path / "sb")
    assert res.structured is None
    assert not res.schema_enforced
    assert json.loads(res.text)["ok"] is True


def test_large_korean_prompt_via_stdin_with_attachments(
    make_fake: MakeFake, tmp_path: Path
) -> None:
    """명령행 길이 한계(Windows 32K)를 훨씬 넘는 프롬프트도 stdin으로 온전히 전달."""
    prompt = "가" * 200_000
    a = tmp_path / "문제 사진.PNG"
    a.write_bytes(b"\x89PNG fake")
    b = tmp_path / "시험지.pdf"
    b.write_bytes(b"%PDF fake")
    res = _run(
        make_fake("echo_len"), AgentRequest(prompt=prompt, attachments=(a, b)), tmp_path / "sb"
    )
    body = json.loads(res.text)
    assert body["first"] == "가"
    assert body["length"] > 200_000  # 첨부 안내가 덧붙음
    assert body["inputs"] == ["input_01.png", "input_02.pdf"]


def test_slow_but_active_is_not_stalled(make_fake: MakeFake, tmp_path: Path) -> None:
    res = _run(make_fake("slow"), AgentRequest(prompt="x"), tmp_path / "sb", idle=1)
    assert json.loads(res.text) == {"done": True}


def test_lingering_after_result_is_success(
    make_fake: MakeFake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """agy가 result를 낸 뒤 종료하지 않아 '멈춤'으로 정상 답을 버리던 문제."""
    monkeypatch.setattr(runner_mod, "FINAL_GRACE_S", 0.5)
    t0 = time.monotonic()
    res = _run(make_fake("linger"), AgentRequest(prompt="x"), tmp_path / "sb", idle=5)
    assert json.loads(res.text) == {"done": True}
    assert time.monotonic() - t0 < 4


# ---------------------------------------------------------------- 실패 시나리오


def test_stall_detected_quickly(make_fake: MakeFake, tmp_path: Path) -> None:
    t0 = time.monotonic()
    err = _err(make_fake("stall"), tmp_path / "sb", AgentErrorCode.STALLED, idle=1)
    assert time.monotonic() - t0 < 8
    assert err.sandbox is not None and (err.sandbox / ".dawoni" / "stdin.txt").exists(), (
        "실패하면 기록 보존"
    )


def test_total_timeout(make_fake: MakeFake, tmp_path: Path) -> None:
    _err(make_fake("slow"), tmp_path / "sb", AgentErrorCode.TIMEOUT, timeout=0.8)


def test_cancel(make_fake: MakeFake, tmp_path: Path) -> None:
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    with pytest.raises(AgentError) as ei:
        _run(make_fake("stall"), AgentRequest(prompt="x"), tmp_path / "sb", cancel=cancel)
    assert ei.value.code == AgentErrorCode.CANCELLED


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("quota", AgentErrorCode.QUOTA),
        ("auth", AgentErrorCode.NOT_LOGGED_IN),
        ("denied", AgentErrorCode.PERMISSION_DENIED),
        ("garbage", AgentErrorCode.PROCESS_FAILED),
        ("crash", AgentErrorCode.PROCESS_FAILED),
    ],
)
def test_failures_classified(
    make_fake: MakeFake, tmp_path: Path, mode: str, code: AgentErrorCode
) -> None:
    err = _err(make_fake(mode), tmp_path / "sb", code)
    assert err.agent == "agy"
    assert err.user_message  # 강사에게 보여 줄 한국어 안내


def test_crash_detail_includes_stderr(make_fake: MakeFake, tmp_path: Path) -> None:
    err = _err(make_fake("crash"), tmp_path / "sb", AgentErrorCode.PROCESS_FAILED)
    assert "something broke" in err.detail


def test_missing_executable(tmp_path: Path) -> None:
    with pytest.raises(AgentError) as ei:
        _run(tmp_path / "nope", AgentRequest(prompt="x"), tmp_path / "sb")
    assert ei.value.code == AgentErrorCode.NOT_INSTALLED


def test_missing_attachment(make_fake: MakeFake, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _run(
            make_fake("ok"),
            AgentRequest(prompt="x", attachments=(tmp_path / "없음.png",)),
            tmp_path / "sb",
        )


# ---------------------------------------------------------------- 전환 (AgentClient)


class _ClaudeNamedAgy(AgyAdapter):
    name: AgentName = "claude"


@pytest.fixture
def client(
    app_paths: AppPaths, make_fake: MakeFake, monkeypatch: pytest.MonkeyPatch
) -> Callable[[str, str], client_mod.AgentClient]:
    monkeypatch.setitem(client_mod.ADAPTERS, "claude", _ClaudeNamedAgy())

    def make(agy_mode: str, claude_mode: str) -> client_mod.AgentClient:
        cfg = AppConfig()
        cfg.agents["agy"].executable = str(make_fake(agy_mode))
        cfg.agents["claude"].executable = str(make_fake(claude_mode))
        cfg.agents["agy"].idle_timeout_s = 5
        return client_mod.AgentClient(cfg, app_paths)

    return make


def test_fallback_on_quota(client: Callable[[str, str], client_mod.AgentClient]) -> None:
    res = client("quota", "ok").run(AgentRequest(prompt="x"))
    assert res.agent == "claude"


def test_no_fallback_on_stall(client: Callable[[str, str], client_mod.AgentClient]) -> None:
    c = client("stall", "ok")
    with pytest.raises(AgentError) as ei:
        c.run(AgentRequest(prompt="x", idle_timeout_s=1))
    assert ei.value.code == AgentErrorCode.STALLED


def test_fallback_disabled(client: Callable[[str, str], client_mod.AgentClient]) -> None:
    with pytest.raises(AgentError) as ei:
        client("quota", "ok").run(AgentRequest(prompt="x"), fallback=False)
    assert ei.value.code == AgentErrorCode.QUOTA


def test_all_unavailable(client: Callable[[str, str], client_mod.AgentClient]) -> None:
    with pytest.raises(AgentError) as ei:
        client("quota", "auth").run(AgentRequest(prompt="x"))
    assert ei.value.code in (AgentErrorCode.NOT_LOGGED_IN, AgentErrorCode.NOT_INSTALLED)


# ---------------------------------------------------------------- 어댑터 단위


def test_agy_command_never_skips_permissions(tmp_path: Path) -> None:
    inv = AgyAdapter().build(
        Path("agy"),
        "프롬프트",
        model="m",
        schema_path=tmp_path / "s.json",
        schema=SCHEMA,
        attachments=[],
        timeout_s=300,
        workdir=tmp_path,
    )
    assert "--dangerously-skip-permissions" not in inv.argv
    assert "--sandbox" in inv.argv
    assert "-p" not in inv.argv and "--print" not in inv.argv
    assert "프롬프트" not in " ".join(inv.argv), "프롬프트는 argv가 아니라 stdin으로"
    msg = json.loads(inv.stdin)
    assert msg == {"event": "user", "message": {"content": "프롬프트"}}


def test_claude_command_isolation_and_schema_size(tmp_path: Path) -> None:
    kw = dict(model=None, schema_path=None, attachments=[], timeout_s=60, workdir=tmp_path)
    small = ClaudeAdapter().build(Path("claude"), "p", schema=SCHEMA, **kw)  # type: ignore[arg-type]
    for flag in ("--restricted", "--safe-mode", "--no-session-persistence", "--strict-mcp-config"):
        assert flag in small.argv
    assert small.argv[small.argv.index("--tools") + 1] == "Read"
    assert "--json-schema" in small.argv and small.schema_enforced
    assert "bypassPermissions" not in small.argv
    big_schema = {"type": "object", "description": "x" * (MAX_INLINE_SCHEMA_CHARS + 10)}
    big = ClaudeAdapter().build(Path("claude"), "p", schema=big_schema, **kw)  # type: ignore[arg-type]
    assert "--json-schema" not in big.argv and not big.schema_enforced
    assert small.stdin == b"p"


CLAUDE_OK = [
    '{"type":"system","subtype":"init","model":"claude-sonnet-5-5","tools":["Read"]}',
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{}}]}}',
    '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed"}}',
    '{"type":"result","subtype":"success","is_error":false,"result":"{\\"ok\\":true}",'
    '"structured_output":{"ok":true},"usage":{"input_tokens":5,"output_tokens":3,'
    '"cache_read_input_tokens":100},"permission_denials":[]}',
]


def test_claude_parse_success() -> None:
    a = ClaudeAdapter()
    parsed = [a.parse_line(x) for x in CLAUDE_OK]
    assert parsed[1].progress == "도구 사용: Read"
    out = a.finish(parsed, 0, "", schema_requested=True)
    assert out.structured == {"ok": True}
    assert out.model == "claude-sonnet-5-5"
    assert out.usage.total_tokens == 108


@pytest.mark.parametrize(
    ("result_line", "code"),
    [
        (
            '{"type":"result","subtype":"success","is_error":true,'
            '"result":"Not logged in · Please run /login"}',
            AgentErrorCode.NOT_LOGGED_IN,
        ),
        (
            '{"type":"result","subtype":"success","is_error":true,'
            '"result":"Claude AI usage limit reached|1792883400"}',
            AgentErrorCode.QUOTA,
        ),
        (
            '{"type":"result","subtype":"success","is_error":false,"result":"",'
            '"permission_denials":[{"tool_name":"Write"}]}',
            AgentErrorCode.PERMISSION_DENIED,
        ),
        (
            '{"type":"result","subtype":"error_max_turns","is_error":false,"result":""}',
            AgentErrorCode.PROCESS_FAILED,
        ),
    ],
)
def test_claude_parse_errors(result_line: str, code: AgentErrorCode) -> None:
    a = ClaudeAdapter()
    with pytest.raises(AgentError) as ei:
        a.finish([a.parse_line(result_line)], 0, "", schema_requested=False)
    assert ei.value.code == code


def test_codex_parse_and_command(tmp_path: Path) -> None:
    a = CodexAdapter()
    inv = a.build(
        Path("codex"),
        "p",
        model=None,
        schema_path=tmp_path / "s.json",
        schema=SCHEMA,
        attachments=[Path("inputs/input_01.png"), Path("inputs/input_02.pdf")],
        timeout_s=60,
        workdir=tmp_path,
    )
    assert inv.argv[-1] == "-"
    assert inv.argv.count("-i") == 1, "이미지만 -i로 첨부"
    assert inv.argv[inv.argv.index("--sandbox") + 1] == "read-only"
    lines = [
        '{"type":"thread.started"}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"{\\"ok\\": true}"}}',
        '{"type":"turn.completed","usage":{"input_tokens":7,"output_tokens":2}}',
    ]
    out = a.finish([a.parse_line(x) for x in lines], 0, "", schema_requested=True)
    assert out.structured == {"ok": True}
    assert out.usage.total_tokens == 9
    with pytest.raises(AgentError) as ei:
        a.finish(
            [a.parse_line('{"type":"turn.failed","error":{"message":"429 Too Many Requests"}}')],
            1,
            "",
            schema_requested=False,
        )
    assert ei.value.code == AgentErrorCode.QUOTA


@pytest.mark.parametrize(
    ("msg", "code"),
    [
        ("RESOURCE_EXHAUSTED", AgentErrorCode.QUOTA),
        ("You've hit your usage limit", AgentErrorCode.QUOTA),
        ("Print mode: not authenticated, trying silent auth", AgentErrorCode.NOT_LOGGED_IN),
        ("Print mode: timed out waiting for cascade", AgentErrorCode.TIMEOUT),
        ("segfault", AgentErrorCode.PROCESS_FAILED),
        ("The stream was interrupted. Please continue the task", AgentErrorCode.STALLED),
    ],
)
def test_classify_failure(msg: str, code: AgentErrorCode) -> None:
    assert classify_failure(msg) == code


def test_find_executable_override(tmp_path: Path) -> None:
    exe = tmp_path / "my-agy"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    assert find_executable("agy", str(exe)) == exe
    assert find_executable("agy", str(tmp_path / "missing")) is None
    assert Path.home() / ".local" / "bin" in extra_bin_dirs()
