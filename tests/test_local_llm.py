"""로컬 LLM (OpenAI 호환 서버) 연결 — 가짜 서버로 확인한다 (실제 모델은 부르지 않음)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from dawoni.agents import AgentClient, AgentError, AgentErrorCode
from dawoni.agents.base import AgentRequest
from dawoni.agents.detect import detect_local
from dawoni.agents.local import fix_latex_escapes
from dawoni.agents.selftest import SELFTEST_IMAGE
from dawoni.config import AgentSettings, AppConfig
from dawoni.paths import AppPaths

Handler = Callable[[BaseHTTPRequestHandler, dict[str, Any]], None]
Server = tuple[str, list[dict[str, Any]], list[Handler]]


def _sse(h: BaseHTTPRequestHandler, chunks: list[dict[str, Any]], delay: float = 0) -> None:
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.end_headers()
    for c in chunks:
        h.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
        h.wfile.flush()
        time.sleep(delay)
    h.wfile.write(b"data: [DONE]\n\n")


@pytest.fixture
def server() -> Generator[Server]:
    """(주소, 받은 요청들, 응답 처리기 — 비우면 기본 응답)."""
    seen: list[dict[str, Any]] = []
    handlers: list[Handler] = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            body = json.dumps({"data": [{"id": "gemma3:27b"}, {"id": "qwen3:8b"}]}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            n = int(self.headers["Content-Length"])
            req = json.loads(self.rfile.read(n))
            seen.append(req)
            if handlers:
                handlers.pop(0)(self, req)
                return
            text = '{"answer": 5}'
            _sse(
                self,
                [
                    {"model": req["model"], "choices": [{"delta": {"content": text[:5]}}]},
                    {"choices": [{"delta": {"content": text[5:]}}]},
                    {
                        "choices": [],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
                    },
                ],
            )

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1", seen, handlers
    srv.shutdown()


SCHEMA = {"type": "object", "properties": {"answer": {"type": "number"}}}


def _client(app_paths: AppPaths, url: str, model: str | None = "gemma3:27b") -> AgentClient:
    cfg = AppConfig()
    cfg.agents["local"] = AgentSettings(enabled=True, base_url=url, model=model)
    cfg.default_agent = "local"
    return AgentClient(cfg, app_paths)


def test_local_run_with_image_and_schema(app_paths: AppPaths, server: Server) -> None:
    url, seen, _ = server
    res = _client(app_paths, url).run(
        AgentRequest(prompt="풀어라", attachments=(SELFTEST_IMAGE,), json_schema=SCHEMA, task="t")
    )
    assert res.agent == "local" and res.model == "gemma3:27b"
    assert res.structured == {"answer": 5} and res.schema_enforced
    assert res.usage.total_tokens == 14
    content = seen[0]["messages"][0]["content"]
    assert content[0]["text"].startswith("풀어라")
    assert content[1]["image_url"]["url"].startswith("data:image/")
    assert seen[0]["response_format"]["json_schema"]["schema"] == SCHEMA
    assert not res.sandbox.exists()  # 성공하면 격리 폴더를 지운다


def test_local_retries_without_schema_on_400(app_paths: AppPaths, server: Server) -> None:
    url, seen, handlers = server

    def reject(h: BaseHTTPRequestHandler, req: dict[str, Any]) -> None:
        h.send_response(400)
        h.end_headers()
        h.wfile.write(b'{"error": "response_format not supported"}')

    handlers.append(reject)
    res = _client(app_paths, url).run(AgentRequest(prompt="p", json_schema=SCHEMA, task="t"))
    assert "response_format" not in seen[1]
    assert res.text == '{"answer": 5}' and not res.schema_enforced
    assert res.warnings


def test_local_without_model_stops(app_paths: AppPaths, server: Server) -> None:
    url, _, _ = server
    with pytest.raises(AgentError, match="모델 이름"):
        _client(app_paths, url, model=None).run(AgentRequest(prompt="p", task="t"))


def test_local_server_down_needs_switch(app_paths: AppPaths) -> None:
    with pytest.raises(AgentError) as e:
        _client(app_paths, "http://127.0.0.1:9/v1").run(AgentRequest(prompt="p", task="t"))
    assert e.value.code == AgentErrorCode.NOT_INSTALLED


def test_local_cancel_mid_stream(app_paths: AppPaths, server: Server) -> None:
    url, _, handlers = server
    chunk = {"choices": [{"delta": {"content": "x"}}]}
    handlers.append(lambda h, req: _sse(h, [chunk] * 50, delay=0.1))
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(AgentError) as e:
        _client(app_paths, url).run(AgentRequest(prompt="p", task="t"), cancel=cancel)
    assert e.value.code == AgentErrorCode.CANCELLED
    assert time.monotonic() - started < 3


def test_local_text_attachment_and_think_stripped(
    app_paths: AppPaths, server: Server, tmp_path: Path
) -> None:
    url, seen, handlers = server
    note = tmp_path / "note.md"
    note.write_text("자료 내용", encoding="utf-8")
    handlers.append(
        lambda h, req: _sse(h, [{"choices": [{"delta": {"content": "<think>음</think>답"}}]}])
    )
    res = _client(app_paths, url).run(AgentRequest(prompt="p", attachments=(note,), task="t"))
    assert res.text == "답"
    assert "자료 내용" in seen[0]["messages"][0]["content"][0]["text"]


def test_fix_latex_escapes() -> None:
    raw = r'{"a": "\frac{12}{5}", "b": "1.8\text{ A}", "c": "\beta+\rho", "d": "\alpha\neq\nu"}'
    fixed, n = fix_latex_escapes(raw)
    assert n == 7
    assert json.loads(fixed) == {
        "a": r"\frac{12}{5}",
        "b": r"1.8\text{ A}",
        "c": r"\beta+\rho",
        "d": r"\alpha\neq\nu",
    }
    # 바르게 쓴 것·진짜 이스케이프는 그대로
    ok = r'{"a": "\\frac{1}{2}", "b": "첫째\n둘째\nThe end\t끝", "c": "° \"q\""}'
    assert fix_latex_escapes(ok) == (ok, 0)
    assert json.loads(fix_latex_escapes(r'{"a": "\underline{x}"}')[0]) == {"a": r"\underline{x}"}


def test_local_fixes_single_backslash_latex(app_paths: AppPaths, server: Server) -> None:
    url, _, handlers = server
    text = r'{"answer": "\frac{12}{5}"}'
    handlers.append(lambda h, req: _sse(h, [{"choices": [{"delta": {"content": text}}]}]))
    res = _client(app_paths, url).run(AgentRequest(prompt="p", json_schema=SCHEMA, task="t"))
    assert res.structured == {"answer": r"\frac{12}{5}"}
    assert any("백슬래시" in w for w in res.warnings)


def test_detect_local(server: Server) -> None:
    url, _, _ = server
    d = detect_local(AgentSettings(base_url=url, model="llama3"))
    assert d.installed and d.models == ["gemma3:27b", "qwen3:8b"]
    assert any("llama3" in n for n in d.notes)
    assert not detect_local(AgentSettings(base_url="http://127.0.0.1:9/v1")).installed


def test_old_config_keeps_local_off() -> None:
    cfg = AppConfig.model_validate({"agents": {"agy": {"enabled": True}}})
    assert cfg.agent("local").enabled is False
    patched = AppConfig.model_validate(
        {**cfg.model_dump(mode="json"), "agents": {**cfg.model_dump(mode="json")["agents"]}}
    )
    assert patched.agents["local"].enabled is False
