"""복합기 스캔본 가져오기: 복합기 공유 폴더(SMB)와 작업공간의 '스캔본' 폴더를 나란히 연다.

학원 복합기는 스캔본을 PDF로 공유 폴더(예: smb://192.168.0.100)에 넣는다. 강사는 두 창을 띄워
파일을 작업공간으로 옮긴 뒤 다워니에서 판독한다.

비밀번호는 다루지 않는다: 운영체제가 처음 연결할 때 묻고 키체인(맥)·자격 증명 관리자(윈도우)에
저장한다. 다워니는 주소와 사용자 이름만 안다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

from dawoni.config import ScannerSettings


class ScannerError(ValueError):
    pass


def finder_url(s: ScannerSettings) -> str:
    """맥 Finder용: smb://사용자@호스트/공유 (사용자 이름이 있으면 로그인 창에 미리 채워진다)."""
    if not s.smb_url:
        raise ScannerError("복합기 주소가 설정되지 않았습니다 (설정 → 복합기 스캔 폴더).")
    u = urlsplit(s.smb_url)
    user = f"{quote(s.username)}@" if s.username and not u.username else ""
    return f"smb://{user}{u.netloc}{u.path}"


def windows_unc(s: ScannerSettings) -> str:
    """윈도우 탐색기용: \\\\호스트\\공유."""
    if not s.smb_url:
        raise ScannerError("복합기 주소가 설정되지 않았습니다 (설정 → 복합기 스캔 폴더).")
    u = urlsplit(s.smb_url)
    host = u.hostname or ""
    rest = u.path.strip("/").replace("/", "\\")
    return f"\\\\{host}" + (f"\\{rest}" if rest else "")


@dataclass
class Opened:
    scanner: str
    inbox: Path
    notes: list[str] = field(default_factory=lambda: list[str]())


def _mac_tile_inbox(inbox: Path) -> None:
    """작업공간 창을 화면 오른쪽 절반에 연다 (복합기 창은 로그인 뒤에 뜬다 — 왼쪽에 두고 쓴다)."""
    script = (
        'tell application "Finder"\n'
        f'  set w to make new Finder window to (POSIX file "{inbox}" as alias)\n'
        "  set b to bounds of window of desktop\n"
        "  set bounds of w to {(item 3 of b) div 2, 60, item 3 of b, item 4 of b}\n"
        "  activate\n"
        "end tell"
    )
    subprocess.run(["osascript", "-e", script], capture_output=True, check=False, timeout=10)


def open_scan_folders(s: ScannerSettings, inbox: Path) -> Opened:
    inbox.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        target = finder_url(s)
        subprocess.run(["open", target], check=False)
        _mac_tile_inbox(inbox)
        notes = [
            "처음 연결할 때 Finder가 비밀번호를 묻습니다 — '키체인에 저장'을 켜 두면 "
            "다음부터는 묻지 않습니다."
        ]
    elif sys.platform == "win32":
        target = windows_unc(s)
        os.startfile(target)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
        os.startfile(inbox)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
        notes = [
            "처음 연결할 때 윈도우가 사용자 이름·비밀번호를 묻습니다 — "
            "'내 자격 증명 기억'을 켜 두면 다음부터는 묻지 않습니다."
        ]
    else:
        target = s.smb_url or ""
        subprocess.run(["xdg-open", target], check=False)
        subprocess.run(["xdg-open", str(inbox)], check=False)
        notes = []
    return Opened(target, inbox, notes)


def inbox_pdfs(inbox: Path) -> list[Path]:
    """작업공간 '스캔본' 폴더의 PDF (새것 먼저)."""
    if not inbox.is_dir():
        return []
    return sorted(inbox.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
