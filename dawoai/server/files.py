"""파일 열기·폴더에서 보기·다른 이름으로 저장.

웹뷰는 `<a download>`를 처리하지 않는다(AIHelper에서 겪음) → 파일은 서버가 디스크에 만들고,
화면은 이 기능들만 부른다. 경로는 작업공간 안의 것만 허용한다.
"""

from __future__ import annotations

import base64
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
    # 여러 줄·따옴표가 든 스크립트도 명령줄에서 깨지지 않게 UTF-16 base64로 넘긴다
    encoded = base64.b64encode((_PS_UTF8 + script).encode("utf-16-le")).decode("ascii")
    r = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
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
            "$d.Filter='모든 파일 (*.*)|*.*';"
            f"if($d.ShowDialog({_OWNER}) -eq 'OK'){{Write-Output $d.FileName}}"
        )
        return _powershell(ps)
    return None


# Windows 폴더 고르기: 브라우저에서 내려받을 위치를 정할 때와 같은 탐색기 모양 창(IFileOpenDialog,
# 폴더 고르기 모드). .NET의 FolderBrowserDialog는 옛 나무 모양이라 쓰기 어렵다.
# 창이 앱 창 뒤에 숨어 '안 뜨는' 것처럼 보이지 않게, 지금 맨 앞 창(다워이 창)을 부모로 띄운다.
_PICKER_CS = r"""
using System;
using System.Runtime.InteropServices;
public static class DawoaiPicker {
  [ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")] class FileOpenDialog {}
  [ComImport, Guid("42f85136-db7e-439c-85f1-e4075d135fc8")]
  [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IFileDialog {
    [PreserveSig] int Show(IntPtr parent);
    void SetFileTypes(uint n, IntPtr spec);
    void SetFileTypeIndex(uint i);
    void GetFileTypeIndex(out uint i);
    void Advise(IntPtr sink, out uint cookie);
    void Unadvise(uint cookie);
    void SetOptions(uint fos);
    void GetOptions(out uint fos);
    void SetDefaultFolder(IShellItem si);
    void SetFolder(IShellItem si);
    void GetFolder(out IShellItem si);
    void GetCurrentSelection(out IShellItem si);
    void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
    void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string name);
    void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
    void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
    void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
    void GetResult(out IShellItem si);
  }
  [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE")]
  [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IShellItem {
    void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
    void GetParent(out IShellItem si);
    void GetDisplayName(uint sigdn, [MarshalAs(UnmanagedType.LPWStr)] out string name);
  }
  [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
  public static string Pick(string title) {
    IFileDialog d = (IFileDialog)new FileOpenDialog();
    uint fos; d.GetOptions(out fos);
    d.SetOptions(fos | 0x20 | 0x40);  // FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM
    d.SetTitle(title);
    d.SetOkButtonLabel("이 폴더 선택");
    if (d.Show(GetForegroundWindow()) != 0) return null;  // 취소
    IShellItem si; d.GetResult(out si);
    string path; si.GetDisplayName(0x80058000, out path);  // SIGDN_FILESYSPATH
    return path;
  }
}
"""

# 위 창을 만들지 못하면(드문 경우) 예전 창으로 — 맨 위에 뜨게 TopMost 부모를 준다
_OWNER = "(New-Object System.Windows.Forms.Form -Property @{TopMost=$true})"


def choose_folder() -> Path | None:
    """운영체제 폴더 선택 대화상자 (작업공간 고르기). 취소하면 None. 관리자 권한 필요 없음."""
    if sys.platform == "darwin":
        script = 'POSIX path of (choose folder with prompt "다워이 작업공간 폴더")'
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
        return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    if sys.platform == "win32":
        ps = (
            "$ErrorActionPreference='Stop';"
            "try{"
            f"Add-Type -TypeDefinition @'\n{_PICKER_CS}\n'@;"
            "$p=[DawoaiPicker]::Pick('다워이 작업공간 폴더 고르기');if($p){Write-Output $p}"
            "}catch{"
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
            f"if($d.ShowDialog({_OWNER}) -eq 'OK'){{Write-Output $d.SelectedPath}}"
            "}"
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
