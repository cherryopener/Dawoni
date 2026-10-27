"""`dawoni ui` — 서버를 켜고 앱 창을 연다.

- macOS: pywebview 창 (설치돼 있으면), 없으면 기본 브라우저
- Windows: Edge `--app` 모드 창 (pywebview의 pythonnet 문제 회피 — AIHelper에서 겪음)
- 서버는 127.0.0.1에만 열고, 실행할 때마다 새 토큰을 만든다.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from dawoni.paths import AppPaths
from dawoni.server.app import create_app
from dawoni.server.security import new_token

DEFAULT_PORT = 8765


def _free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return int(s.getsockname()[1])
    raise RuntimeError("빈 포트를 찾지 못했습니다")


def _edge() -> Path | None:
    for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES")):
        if base:
            p = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if p.is_file():
                return p
    return None


def run_ui(
    paths: AppPaths | None = None,
    *,
    port: int = DEFAULT_PORT,
    window: bool = True,
) -> int:
    paths = paths or AppPaths.default()
    token = new_token()
    port = _free_port(port)
    app = create_app(paths, token)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    url = f"http://127.0.0.1:{port}/login?t={token}"
    sys.stdout.write(f"다워니 화면 주소 (이 PC에서만): {url}\n")
    sys.stdout.flush()

    if not window:
        server.run()
        return 0

    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)

    if sys.platform == "darwin":
        try:
            import webview  # pyright: ignore[reportMissingTypeStubs]

            webview.create_window(  # pyright: ignore[reportUnknownMemberType]
                "다워니", url, width=1320, height=880, min_size=(960, 640)
            )
            webview.start()  # pyright: ignore[reportUnknownMemberType]
            server.should_exit = True
            t.join(timeout=5)
            return 0
        except ImportError:
            pass
    edge = _edge() if sys.platform == "win32" else None
    if edge is not None:
        subprocess.Popen([str(edge), f"--app={url}", "--window-size=1320,880"])
    else:
        webbrowser.open(url)
    try:
        while t.is_alive():
            t.join(timeout=0.5)
    except KeyboardInterrupt:
        server.should_exit = True
    return 0
