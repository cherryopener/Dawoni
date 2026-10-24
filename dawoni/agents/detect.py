"""AI CLI 설치·버전·로그인 상태 감지.

macOS에서 Finder로 실행한 앱은 PATH가 `/usr/bin:/bin` 정도로 짧아서 Homebrew·npm·
`~/.local/bin`에 설치된 CLI를 못 찾는다. 알려진 설치 위치를 PATH에 덧붙여 찾는다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dawoni.agents.base import as_dict
from dawoni.config import AgentName

EXECUTABLE_NAMES: dict[AgentName, str] = {"agy": "agy", "claude": "claude", "codex": "codex"}


def extra_bin_dirs() -> list[Path]:
    home = Path.home()
    dirs = [home / ".local" / "bin", home / ".npm-global" / "bin", home / ".bun" / "bin"]
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        roaming = os.environ.get("APPDATA")
        if local:
            dirs += [Path(local) / "agy" / "bin", Path(local) / "Programs" / "claude"]
        if roaming:
            dirs.append(Path(roaming) / "npm")
    else:
        dirs += [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    return dirs


def augmented_path() -> str:
    current = os.environ.get("PATH", "").split(os.pathsep)
    extra = [str(d) for d in extra_bin_dirs() if str(d) not in current]
    return os.pathsep.join([p for p in current if p] + extra)


def subprocess_env() -> dict[str, str]:
    """CLI 실행용 환경 변수. node 기반 CLI가 node를 찾을 수 있게 PATH를 보강한다."""
    env = dict(os.environ)
    env["PATH"] = augmented_path()
    env.setdefault("NO_COLOR", "1")
    return env


def find_executable(name: AgentName, override: str | None = None) -> Path | None:
    if override:
        p = Path(override).expanduser()
        return p if p.is_file() else None
    found = shutil.which(EXECUTABLE_NAMES[name], path=augmented_path())
    return Path(found) if found else None


def _run_quick(argv: list[str], timeout: float = 15) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=subprocess_env(),
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return None


@dataclass
class DetectedAgent:
    name: AgentName
    path: Path | None
    version: str | None = None
    logged_in: bool | None = None
    """None = 이 CLI는 상태 확인 명령이 없어 실제 호출로만 알 수 있음."""
    notes: list[str] = field(default_factory=lambda: list[str]())

    @property
    def installed(self) -> bool:
        return self.path is not None


def _claude_logged_in(exe: Path) -> bool | None:
    r = _run_quick([str(exe), "auth", "status"])
    if r is None or not r.stdout.strip().startswith("{"):
        return None
    try:
        data = as_dict(json.loads(r.stdout))
    except json.JSONDecodeError:
        return None
    v = data.get("loggedIn")
    return v if isinstance(v, bool) else None


def _context_file_notes(name: AgentName) -> list[str]:
    """CLI가 자동으로 읽는 사용자 전역 지침 파일이 비어 있지 않으면 경고."""
    home = Path.home()
    candidates = {
        "agy": [home / ".gemini" / "GEMINI.md"],
        "claude": [home / ".claude" / "CLAUDE.md"],
        "codex": [home / ".codex" / "AGENTS.md"],
    }[name]
    notes: list[str] = []
    for p in candidates:
        try:
            if p.is_file() and p.stat().st_size > 0:
                notes.append(f"전역 지침 파일이 있어 AI 응답에 섞일 수 있음: {p}")
        except OSError:
            pass
    return notes


def detect(name: AgentName, override: str | None = None) -> DetectedAgent:
    exe = find_executable(name, override)
    if exe is None:
        return DetectedAgent(name=name, path=None)
    det = DetectedAgent(name=name, path=exe)
    r = _run_quick([str(exe), "--version"])
    if r is not None and r.returncode == 0:
        out = r.stdout.strip() or r.stderr.strip()
        det.version = out.splitlines()[0] if out else None
    if name == "claude":
        det.logged_in = _claude_logged_in(exe)
    det.notes += _context_file_notes(name)
    return det
