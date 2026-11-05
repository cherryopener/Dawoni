"""로컬 LLM 연결 — Ollama·LM Studio·llama.cpp 서버처럼 OpenAI 호환 주소(`/v1`)를 여는 프로그램.

CLI 어댑터와 달리 프로세스를 띄우지 않고 HTTP로 부른다. 나머지는 runner와 같게 맞춘다:
- 격리 폴더에 요청·응답을 남기고, 성공하면 지운다 (실패 시 보존).
- 응답은 스트리밍으로 받아 멈춤(`idle_timeout_s`)·전체 제한·취소를 0.25초마다 확인한다.
- 첨부 그림은 파일로 읽게 할 수 없으므로 메시지에 직접 담는다 (PDF는 쪽 그림으로).
- JSON 스키마는 `response_format`으로 요청하고, 서버가 거절(400)하면 스키마 없이 한 번 더 —
  컨텍스트 엔진이 프롬프트의 스키마로 사후 검증·수리한다.

모델 이름은 짐작하지 않는다: 설정에 정한 이름(예: `gemma3:27b`)이 없으면 멈추고 알린다.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from dawoni.agents.base import (
    AgentError,
    AgentErrorCode,
    AgentRequest,
    AgentResult,
    AgentUsage,
    as_dict,
    as_int,
    as_list,
    classify_failure,
)
from dawoni.agents.runner import ProgressCallback, RunOptions
from dawoni.agents.sandbox import Sandbox
from dawoni.config import AgentSettings

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:11434/v1"
"""Ollama 기본 주소. LM Studio는 http://localhost:1234/v1."""
DEFAULT_IMAGE_PX = 1536
"""역할이 정하지 않았을 때 그림 긴 변 상한 — 로컬 모델은 문맥 길이·메모리가 작다."""

_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_TEXT_SUFFIXES = {".txt", ".md", ".json", ".tex", ".csv", ".yaml", ".yml"}
_THINK = re.compile(r"<think>.*?</think>", re.S)
_EOF = object()

# 로컬 모델은 JSON 문자열 안 LaTeX의 `\`를 한 번만 쓰는 일이 잦다 → `\frac`가 폼피드+"rac",
# `\beta`가 백스페이스+"eta"로 읽히거나(조용히 깨짐) `\alpha`처럼 잘못된 이스케이프로 JSON 전체가
# 읽히지 않는다. JSON 이스케이프 글자(b f n r t)로 시작하는 명령은 아래 목록과 통째로 맞을 때만
# 고친다 — 진짜 줄바꿈 `\n다음`·`\nThe`는 그대로 둔다.
_LATEX_ON_JSON_ESCAPE = frozenset(
    """
    backslash bar because beta bf big bigcap bigcup bigg biggl biggr bigl bigr binom bm bmod
    boldsymbol bot boxed breve bullet
    flat forall frac frown
    nabla ne nearrow neg neq newline nexists ngeq ngtr ni nleq nless nmid nolimits not notin nu
    nparallel
    rangle rbrace rbrack rceil rfloor rho right rightarrow rightharpoonup rightleftharpoons rm
    rvert
    tan tanh tau text textbf textcircled textdegree textit textrm textsf texttt tfrac therefore
    theta tilde times tiny to top triangle triangleq
    """.split()  # noqa: SIM905
)
_BACKSLASH = re.compile(r"\\(?:\\|u[0-9a-fA-F]{4}|([a-zA-Z]+)|.)", re.S)


def fix_latex_escapes(text: str) -> tuple[str, int]:
    """JSON 문자열 안의 한 번만 쓴 LaTeX 백슬래시를 두 번으로. (고친 글, 고친 곳 수)."""
    n = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal n
        word = m.group(1)
        if word is None or (word[0] in "bfnrt" and word not in _LATEX_ON_JSON_ESCAPE):
            return m.group(0)
        n += 1
        return "\\" + m.group(0)

    return _BACKSLASH.sub(repl, text), n


def base_url(settings: AgentSettings) -> str:
    return (settings.base_url or DEFAULT_BASE_URL).rstrip("/")


def _headers(settings: AgentSettings) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if settings.api_key:
        h["Authorization"] = f"Bearer {settings.api_key}"
    return h


def list_models(settings: AgentSettings, timeout: float = 3) -> list[str]:
    """서버에 올라 있는 모델 이름. 연결이 안 되면 OSError(URLError 포함)."""
    req = urllib.request.Request(f"{base_url(settings)}/models", headers=_headers(settings))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = as_dict(json.loads(resp.read().decode("utf-8")))
    return [str(as_dict(m).get("id")) for m in as_list(data.get("data")) if as_dict(m).get("id")]


# ---------------------------------------------------------------- 메시지 만들기


def _content(prompt: str, files: list[Path]) -> tuple[list[dict[str, Any]], list[str]]:
    """사용자 메시지 내용과 (기록용) 첨부 요약."""
    texts: list[str] = [prompt]
    images: list[dict[str, Any]] = []
    summary: list[str] = []
    for f in files:
        suffix = f.suffix.lower()
        if suffix in _IMAGE_MIME:
            b64 = base64.b64encode(f.read_bytes()).decode("ascii")
            url = f"data:{_IMAGE_MIME[suffix]};base64,{b64}"
            images.append({"type": "image_url", "image_url": {"url": url}})
            summary.append(f"그림 {f.name} ({f.stat().st_size:,} bytes)")
        elif suffix in _TEXT_SUFFIXES:
            body = f.read_text(encoding="utf-8", errors="replace")
            texts.append(f"\n\n[첨부 파일 {f.name}]\n{body}")
            summary.append(f"글 {f.name} ({len(body):,}자)")
        else:
            raise AgentError(
                AgentErrorCode.PROCESS_FAILED, f"로컬 LLM에 보낼 수 없는 첨부 형식: {f.name}"
            )
    if images:
        texts.append(
            f"\n\n[첨부 그림 {len(images)}장 — 이 메시지에 순서대로 들어 있다. 파일을 열 필요 없음]"
        )
    return [{"type": "text", "text": "".join(texts)}, *images], summary


# ---------------------------------------------------------------- 실행


def _http_error(e: urllib.error.HTTPError) -> AgentError:
    body = ""
    with contextlib.suppress(Exception):
        body = e.read().decode("utf-8", errors="replace")[:2000]
    detail = f"HTTP {e.code}: {body}".strip()
    if e.code in (401, 403):
        return AgentError(AgentErrorCode.NOT_LOGGED_IN, detail)
    if e.code == 429:
        return AgentError(AgentErrorCode.QUOTA, detail)
    if e.code == 404:
        return AgentError(AgentErrorCode.PROCESS_FAILED, f"모델이나 주소를 찾지 못함 — {detail}")
    return AgentError(classify_failure(detail), detail)


def _stream(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    sb: Sandbox,
    options: RunOptions,
    progress: ProgressCallback,
    cancel: threading.Event | None,
) -> tuple[str, AgentUsage, str | None]:
    """요청 하나를 보내고 (응답 글, 사용량, 서버가 알린 모델)."""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    out: queue.Queue[object] = queue.Queue()
    holder: dict[str, Any] = {}

    def pump() -> None:
        try:
            # 소켓 제한을 멈춤 기준과 같게 — 서버가 아무것도 안 보내면 여기서 끝난다
            resp = urllib.request.urlopen(req, timeout=options.idle_timeout_s)
            holder["resp"] = resp
            for raw in resp:
                out.put(raw)
        except Exception as e:  # 오류는 주 스레드에서 분류
            out.put(e)
        finally:
            out.put(_EOF)

    threading.Thread(target=pump, daemon=True).start()
    started = time.monotonic()
    last_activity = started
    last_note = 0.0
    parts: list[str] = []
    usage = AgentUsage()
    model: str | None = None
    log_file = sb.log_path("stdout.jsonl").open("w", encoding="utf-8")

    def close() -> None:
        with contextlib.suppress(Exception):
            holder["resp"].close()

    try:
        while True:
            now = time.monotonic()
            if cancel is not None and cancel.is_set():
                close()
                raise AgentError(AgentErrorCode.CANCELLED)
            if now - started > options.timeout_s:
                close()
                raise AgentError(AgentErrorCode.TIMEOUT, f"{options.timeout_s:.0f}초 초과")
            if now - last_activity > options.idle_timeout_s:
                close()
                raise AgentError(
                    AgentErrorCode.STALLED, f"{options.idle_timeout_s:.0f}초 동안 출력 없음"
                )
            try:
                item = out.get(timeout=0.25)
            except queue.Empty:
                continue
            if item is _EOF:
                break
            if isinstance(item, urllib.error.HTTPError):
                raise _http_error(item)
            if isinstance(item, TimeoutError):
                raise AgentError(AgentErrorCode.STALLED, "응답 대기 시간 초과")
            if isinstance(item, urllib.error.URLError | ConnectionError):
                raise AgentError(
                    AgentErrorCode.NOT_INSTALLED,
                    f"로컬 LLM 서버({url}) 연결 안 됨 — Ollama·LM Studio 실행 확인: {item}",
                )
            if isinstance(item, Exception):
                raise AgentError(classify_failure(str(item)), str(item))
            assert isinstance(item, bytes)
            last_activity = time.monotonic()
            line = item.decode("utf-8", errors="replace").strip()
            log_file.write(line + "\n")
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = as_dict(json.loads(payload))
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                detail = json.dumps(chunk["error"], ensure_ascii=False)
                raise AgentError(classify_failure(detail), detail)
            if isinstance(chunk.get("model"), str) and model is None:
                model = str(chunk["model"])
                progress(f"모델 {model} 준비")
            u = as_dict(chunk.get("usage"))
            if u:
                inp, outp = as_int(u.get("prompt_tokens")), as_int(u.get("completion_tokens"))
                usage = AgentUsage(inp, outp, as_int(u.get("total_tokens")))
            for choice in map(as_dict, as_list(chunk.get("choices"))):
                delta = as_dict(choice.get("delta"))
                if isinstance(delta.get("content"), str):
                    parts.append(str(delta["content"]))
                thinking = bool(delta.get("reasoning_content") or delta.get("reasoning"))
                if time.monotonic() - last_note > 2:
                    last_note = time.monotonic()
                    n = sum(map(len, parts))
                    progress("생각 중" if thinking and not n else f"응답 작성 중 ({n:,}자)")
    finally:
        log_file.close()
    return _THINK.sub("", "".join(parts)).strip(), usage, model


def run_local(
    settings: AgentSettings,
    req: AgentRequest,
    *,
    model: str | None,
    sandbox_base: Path,
    options: RunOptions,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> AgentResult:
    if cancel is not None and cancel.is_set():
        raise AgentError(AgentErrorCode.CANCELLED, agent="local")
    if not model:
        raise AgentError(
            AgentErrorCode.PROCESS_FAILED,
            "로컬 LLM 모델 이름이 없습니다. 연결·설정 화면에서 모델(예: gemma3:27b)을 정하세요.",
            agent="local",
        )

    def progress(msg: str) -> None:
        if on_progress is not None:
            try:
                on_progress(msg)
            except Exception:
                log.exception("진행 콜백 오류")

    sb = Sandbox.create(sandbox_base, req.task)
    ok = False
    try:
        files: list[Path] = []
        for p in sb.add_attachments(
            req.attachments, max_image_px=req.max_image_px or DEFAULT_IMAGE_PX
        ):
            full = sb.root / p
            if full.suffix.lower() == ".pdf":
                from dawoni.ocr.pages import render_pdf

                (sb.inputs / full.stem).mkdir()
                files += render_pdf(full, sb.inputs / full.stem, 1)
            else:
                files.append(full)
        content, summary = _content(req.prompt, files)
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if req.json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": req.json_schema},
            }
        sb.log_path("stdin.txt").write_text(
            content[0]["text"] + "\n\n" + "\n".join(summary), encoding="utf-8"
        )
        url = f"{base_url(settings)}/chat/completions"
        headers = _headers(settings)
        warnings: list[str] = []
        started = time.monotonic()
        progress(f"로컬 LLM ({model}) 시작")
        try:
            text, usage, reported = _stream(url, headers, body, sb, options, progress, cancel)
        except AgentError as e:
            if "response_format" not in body or "HTTP 400" not in e.detail:
                raise
            # JSON 스키마를 지원하지 않는 서버 — 프롬프트의 스키마로 검증한다
            body.pop("response_format")
            warnings.append("서버가 JSON 스키마 강제를 지원하지 않아 프롬프트로만 요청함")
            text, usage, reported = _stream(url, headers, body, sb, options, progress, cancel)
        if not text:
            raise AgentError(AgentErrorCode.BAD_OUTPUT, "빈 응답")
        if req.json_schema is not None:
            text, fixed = fix_latex_escapes(text)
            if fixed:
                warnings.append(f"JSON 안 LaTeX 백슬래시 {fixed}곳을 두 번으로 고침")
        structured: Any | None = None
        if "response_format" in body:
            with contextlib.suppress(json.JSONDecodeError):
                structured = json.loads(text)
        duration = time.monotonic() - started
        progress(f"로컬 LLM 완료 ({duration:.1f}초)")
        ok = True
        return AgentResult(
            agent="local",
            model=reported or model,
            text=text,
            structured=structured,
            schema_enforced=structured is not None,
            usage=usage,
            duration_s=duration,
            sandbox=sb.root,
            warnings=warnings,
        )
    except AgentError as e:
        e.agent = "local"
        e.sandbox = sb.root
        raise
    finally:
        if ok and not options.keep_sandbox:
            sb.remove()
        elif not ok:
            log.warning("로컬 LLM 호출 실패 — 기록 보존: %s", sb.root)
