"""파일 대화상자 (dawoni/server/files.py) — 운영체제 창은 띄우지 않고 보내는 명령만 본다."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from dawoni.server import files


def test_windows_save_dialog_opens_over_the_dawoni_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-11-18 학원 PC: '다른 이름으로 저장' 창이 다워니 창 뒤에 떠서 보이지 않았다 — 폴더
    고르기처럼 탐색기 모양 창(IFileSaveDialog)을 맨 앞 창(다워니)을 부모로 띄운다. 안 되면 예전
    창."""
    sent: list[str] = []
    monkeypatch.setattr(sys, "platform", "win32")

    def powershell(script: str) -> Path:
        sent.append(script)
        return Path("C:/x/a.hwpx")

    monkeypatch.setattr(files, "_powershell", powershell)
    assert files._ask_save_path("월례's_다듬음.hwpx") == Path("C:/x/a.hwpx")  # pyright: ignore[reportPrivateUsage]
    (script,) = sent
    assert "[DawoniPicker]::Save('다른 이름으로 저장','월례''s_다듬음.hwpx')" in script
    assert "class FileSaveDialog" in script and "d.Show(GetForegroundWindow())" in script
    assert "catch{" in script and "SaveFileDialog" in script, "예전 창은 예비로"


def test_mac_dialogs_come_to_the_front(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "/Users/t/a.hwpx\n", "")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(files.subprocess, "run", run)
    assert files._ask_save_path("a.hwpx") == Path("/Users/t/a.hwpx")  # pyright: ignore[reportPrivateUsage]
    assert calls[0][:3] == ["osascript", "-e", "activate"]
    files.choose_folder()
    assert calls[1][:3] == ["osascript", "-e", "activate"]
