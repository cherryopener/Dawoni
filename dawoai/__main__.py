"""`python -m dawoai` — 인자가 없으면 화면을 연다 (배포본 Dawoai.exe를 두 번 누른 것과 같음)."""

from __future__ import annotations

import sys


def _stdio_to_log() -> None:
    """창 없는 실행(pythonw)에는 stdout·stderr가 없다(None).

    uvicorn이 `sys.stdout.isatty()`에서 죽지 않게 로그 파일로 돌린다."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from dawoai.paths import AppPaths

    paths = AppPaths.default()
    paths.ensure()
    log = (paths.logs / "app.log").open("a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


def run() -> int:
    _stdio_to_log()
    from dawoai.cli import main

    return main(sys.argv[1:] or ["ui"])


if __name__ == "__main__":
    raise SystemExit(run())
