"""파일 열기·폴더에서 보기·다른 이름으로 저장.

웹뷰는 `<a download>`를 처리하지 않는다(AIHelper에서 겪음) → 파일은 서버가 디스크에 만들고,
화면은 이 기능들만 부른다. 경로는 작업공간 안의 것만 허용한다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


class PathRefused(ValueError):
    pass


def inside(root: Path, rel_or_abs: str | Path) -> Path:
    """root 안의 경로로 풀어 준다. 밖을 가리키면(`..`, 다른 드라이브) 거부."""
    root = root.resolve()
    p = Path(rel_or_abs)
    full = (p if p.is_absolute() else root / p).resolve()
    if full != root and root not in full.parents:
        raise PathRefused(f"작업공간 밖의 경로는 열 수 없습니다: {rel_or_abs}")
    return full


def open_file(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform == "win32":
        os.startfile(path)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def reveal(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(path)], check=False)
    elif sys.platform == "win32":
        subprocess.run(["explorer", f"/select,{path}"], check=False)
    else:
        subprocess.run(["xdg-open", str(path.parent)], check=False)


# Windows PowerShell 5.1은 콘솔 코드 페이지(cp949)로 내보낸다 → 한글 경로가 깨지지 않게 UTF-8로
_PS_UTF8 = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"


def _powershell(script: str) -> Path | None:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-Command", _PS_UTF8 + script],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return Path(r.stdout.strip()) if r.stdout.strip() else None


def _ask_save_path(default_name: str) -> Path | None:
    """운영체제 저장 대화상자. 취소하면 None."""
    if sys.platform == "darwin":
        name = default_name.replace('"', "")
        script = f'POSIX path of (choose file name with prompt "저장할 위치" default name "{name}")'
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
        return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    if sys.platform == "win32":
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d=New-Object System.Windows.Forms.SaveFileDialog;"
            f"$d.FileName='{default_name.replace(chr(39), '')}';"
            "$d.Filter='Word 문서 (*.docx)|*.docx|모든 파일 (*.*)|*.*';"
            "if($d.ShowDialog() -eq 'OK'){Write-Output $d.FileName}"
        )
        return _powershell(ps)
    return None


def choose_folder() -> Path | None:
    """운영체제 폴더 선택 대화상자 (작업공간 고르기). 취소하면 None."""
    if sys.platform == "darwin":
        script = 'POSIX path of (choose folder with prompt "다워니 작업공간 폴더")'
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
        return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    if sys.platform == "win32":
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
            "if($d.ShowDialog() -eq 'OK'){Write-Output $d.SelectedPath}"
        )
        return _powershell(ps)
    return None


def save_as(src: Path) -> Path | None:
    dest = _ask_save_path(src.name)
    if dest is None:
        return None
    if dest.suffix.lower() != src.suffix.lower():
        dest = dest.with_name(dest.name + src.suffix)
    shutil.copy2(src, dest)
    return dest
