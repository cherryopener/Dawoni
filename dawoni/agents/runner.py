"""AI CLI 프로세스 실행.

AIHelper에서 겪은 문제와 대책:
- 긴 프롬프트를 명령행 인자로 넘김 → 항상 stdin으로 넘기고 바로 닫는다(EOF).
- 권한 승인 대기로 5분 멈춤 → 출력 이벤트 감시(heartbeat). `idle_timeout_s` 동안 아무 줄도
  없으면 멈춘 것으로 보고 프로세스 트리째 종료한다.
- 원인 모를 실패 → 격리 폴더에 stdin·stdout·stderr를 그대로 남긴다.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from dawoni.agents.base import (
    AgentAdapter,
    AgentError,
    AgentErrorCode,
    AgentRequest,
    AgentResult,
    ParsedLine,
    attachment_note,
)
from dawoni.agents.sandbox import Sandbox

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]

_EOF = object()


@dataclass(frozen=True)
class RunOptions:
    timeout_s: float
    idle_timeout_s: float
    keep_sandbox: bool = False
    """True면 성공해도 격리 폴더를 남긴다 (디버깅용). 실패 시에는 항상 남긴다."""


def _spawn(argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen[bytes]:
    """새 프로세스 그룹으로 실행 (종료 시 하위 프로세스까지 한 번에 정리하기 위해)."""
    if sys.platform == "win32":
        return subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    return subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """CLI가 띄운 하위 프로세스(node 등)까지 함께 종료."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError, PermissionError, OSError:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        log.error("프로세스 종료 실패: pid=%s", proc.pid)


def _pump(stream: IO[bytes] | None, out: queue.Queue[object]) -> None:
    try:
        if stream is not None:
            for raw in iter(stream.readline, b""):
                out.put(raw)
    finally:
        out.put(_EOF)


def run_agent(
    adapter: AgentAdapter,
    exe: Path,
    req: AgentRequest,
    *,
    model: str | None,
    sandbox_base: Path,
    env: dict[str, str],
    options: RunOptions,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> AgentResult:
    sb = Sandbox.create(sandbox_base, req.task)
    ok = False
    try:
        result = _run_in_sandbox(adapter, exe, req, sb, model, env, options, on_progress, cancel)
        ok = True
        return result
    except AgentError as e:
        e.agent = adapter.name
        e.sandbox = sb.root
        raise
    finally:
        if ok and not options.keep_sandbox:
            sb.remove()
        elif not ok:
            log.warning("AI 호출 실패 — 기록 보존: %s", sb.root)


def _run_in_sandbox(
    adapter: AgentAdapter,
    exe: Path,
    req: AgentRequest,
    sb: Sandbox,
    model: str | None,
    env: dict[str, str],
    options: RunOptions,
    on_progress: ProgressCallback | None,
    cancel: threading.Event | None,
) -> AgentResult:
    rels = sb.add_attachments(req.attachments)
    prompt = req.prompt + attachment_note([p.as_posix() for p in rels])
    schema_path = sb.write_schema(req.json_schema) if req.json_schema is not None else None
    inv = adapter.build(
        exe,
        prompt,
        model=model,
        schema_path=schema_path,
        schema=req.json_schema,
        attachments=rels,
        timeout_s=options.timeout_s,
        workdir=sb.root,
    )
    sb.log_path("stdin.txt").write_bytes(inv.stdin)
    sb.log_path("argv.txt").write_text("\n".join(inv.argv), encoding="utf-8")

    def progress(msg: str) -> None:
        if on_progress is not None:
            try:
                on_progress(msg)
            except Exception:  # UI 콜백 오류가 AI 호출을 망치지 않게
                log.exception("진행 콜백 오류")

    started = time.monotonic()
    try:
        proc = _spawn(inv.argv, sb.root, env)
    except OSError as e:
        raise AgentError(AgentErrorCode.NOT_INSTALLED, f"{exe}: {e}") from e

    out_q: queue.Queue[object] = queue.Queue()
    err_q: queue.Queue[object] = queue.Queue()
    threading.Thread(target=_pump, args=(proc.stdout, out_q), daemon=True).start()
    threading.Thread(target=_pump, args=(proc.stderr, err_q), daemon=True).start()

    def feed() -> None:
        assert proc.stdin is not None
        with contextlib.suppress(OSError):  # BrokenPipe: CLI가 먼저 종료한 경우
            proc.stdin.write(inv.stdin)
        with contextlib.suppress(OSError):
            proc.stdin.close()  # EOF → 대화형 입력 대기 원천 차단

    threading.Thread(target=feed, daemon=True).start()
    progress(f"{adapter.name} 시작")

    parsed: list[ParsedLine] = []
    stderr_tail: deque[str] = deque(maxlen=200)
    stdout_log = sb.log_path("stdout.jsonl").open("w", encoding="utf-8")
    last_activity = time.monotonic()
    stdout_done = False
    try:
        while not stdout_done:
            now = time.monotonic()
            if cancel is not None and cancel.is_set():
                _kill_tree(proc)
                raise AgentError(AgentErrorCode.CANCELLED)
            if now - started > options.timeout_s:
                _kill_tree(proc)
                raise AgentError(AgentErrorCode.TIMEOUT, f"{options.timeout_s:.0f}초 초과")
            if now - last_activity > options.idle_timeout_s:
                _kill_tree(proc)
                raise AgentError(
                    AgentErrorCode.STALLED,
                    f"{options.idle_timeout_s:.0f}초 동안 출력 없음 (권한 승인 대기 등)",
                )
            while True:
                try:
                    raw = err_q.get_nowait()
                except queue.Empty:
                    break
                if isinstance(raw, bytes):
                    stderr_tail.append(raw.decode("utf-8", errors="replace").rstrip())
                    last_activity = time.monotonic()
            try:
                raw = out_q.get(timeout=0.25)
            except queue.Empty:
                continue
            if raw is _EOF:
                stdout_done = True
                break
            assert isinstance(raw, bytes)
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            last_activity = time.monotonic()
            stdout_log.write(line + "\n")
            if not line.strip():
                continue
            p = adapter.parse_line(line)
            parsed.append(p)
            if p.progress:
                progress(p.progress)
    finally:
        stdout_log.close()

    try:
        returncode = proc.wait(timeout=max(5.0, options.timeout_s - (time.monotonic() - started)))
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        raise AgentError(AgentErrorCode.TIMEOUT, "출력 종료 후 프로세스가 끝나지 않음") from None
    # 남은 stderr 수거
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            raw = err_q.get(timeout=0.1)
        except queue.Empty:
            continue
        if raw is _EOF:
            break
        if isinstance(raw, bytes):
            stderr_tail.append(raw.decode("utf-8", errors="replace").rstrip())
    stderr_text = "\n".join(stderr_tail)
    sb.log_path("stderr.txt").write_text(stderr_text, encoding="utf-8")

    final = adapter.finish(
        parsed, returncode, stderr_text, schema_requested=req.json_schema is not None
    )
    duration = time.monotonic() - started
    progress(f"{adapter.name} 완료 ({duration:.1f}초)")
    return AgentResult(
        agent=adapter.name,
        model=final.model or model,
        text=final.text,
        structured=final.structured,
        schema_enforced=inv.schema_enforced and final.structured is not None,
        usage=final.usage,
        duration_s=duration,
        sandbox=sb.root,
        warnings=final.warnings,
    )
