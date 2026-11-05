"""로그 설정.

- `dawoni.log`: 전체 기록 (회전)
- `error.log`: 경고 이상만 — 사용자가 문제를 보고할 때 이 파일만 보내면 되도록
AI 호출 기록(calls.jsonl)은 별도로 M2 컨텍스트 엔진에서 남긴다.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_configured = False


def setup_logging(log_dir: Path, *, console_level: int = logging.WARNING) -> None:
    global _configured
    if _configured:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("dawoni")
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter(_FORMAT)

    main = RotatingFileHandler(
        log_dir / "dawoni.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    main.setLevel(logging.DEBUG)
    main.setFormatter(fmt)

    err = RotatingFileHandler(
        log_dir / "error.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8"
    )
    err.setLevel(logging.WARNING)
    err.setFormatter(fmt)

    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    for h in (main, err, console):
        root.addHandler(h)
    _configured = True
