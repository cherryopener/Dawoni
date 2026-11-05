"""PC에 이미 설치된 한글로 파일 형식 바꾸기 (PLAN.md §3 — 변환 프로그램을 싣지 않는다).

학원 시험지는 대부분 `.hwp`(한글 바이너리)다. HWPX(zip + XML)만 직접 읽으므로, 한글이 설치된
Windows PC에서는 한글(COM)로 HWPX로 저장해 읽는다. 한글이 없으면 강사에게 HWPX로 저장해 달라고 한다.

- 한글은 파일을 열 때 '접근 허용' 창을 띄울 수 있다. 자동화 보안 모듈(`FilePathCheckerModule`)이
  등록된 PC면 뜨지 않는다. 창을 숨기면 아무도 답할 수 없어 멈추므로 한글 창을 보이게 연다
  (experiments/hwpx_probe.py, 2026-11-04 실측).
- 서버 작업은 스레드에서 돈다 — COM은 스레드마다 `CoInitialize`가 필요하다.
"""

from __future__ import annotations

import contextlib
import importlib
import sys
import threading
from pathlib import Path
from typing import Any

HWP_HINT = "한글에서 [다른 이름으로 저장] → 'HWPX 문서'로 저장한 파일을 넣어 주세요."
_lock = threading.Lock()
"""한글은 한 번에 하나만 띄운다 (둘째 연결이 꺼지는 중인 한글에 붙어 죽는다 — 실측)."""


class ConvertUnavailable(RuntimeError):
    """이 PC에서는 바꿀 수 없음 (한글·pywin32 없음, 한글이 열지 못함)."""


def hwp_to_hwpx(src: Path, dest: Path) -> Path:
    """`.hwp` → `.hwpx` (한글 COM). 실패하면 `ConvertUnavailable` (안내 문구 포함)."""
    if sys.platform != "win32":
        raise ConvertUnavailable(f"HWP 파일은 Windows 한글로만 바꿀 수 있습니다. {HWP_HINT}")
    try:  # pywin32는 `--extra windows`에만 있다
        pythoncom: Any = importlib.import_module("pythoncom")
        client: Any = importlib.import_module("win32com.client")
    except ImportError as e:
        raise ConvertUnavailable(f"한글 자동화(pywin32)가 설치되지 않았습니다. {HWP_HINT}") from e
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        pythoncom.CoInitialize()
        try:
            try:
                hwp: Any = client.DispatchEx("HWPFrame.HwpObject")
            except Exception as e:
                raise ConvertUnavailable(f"한글이 설치되어 있지 않습니다. {HWP_HINT}") from e
            try:
                with contextlib.suppress(Exception):
                    hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
                hwp.XHwpWindows.Item(0).Visible = True
                if not hwp.Open(str(src.resolve()), "HWP", "forceopen:true"):
                    raise ConvertUnavailable(f"한글이 {src.name}을 열지 못했습니다. {HWP_HINT}")
                hwp.SaveAs(str(dest.resolve()), "HWPX", "")
            finally:
                with contextlib.suppress(Exception):
                    hwp.Clear(1)
                with contextlib.suppress(Exception):
                    hwp.Quit()
        finally:
            pythoncom.CoUninitialize()
    if not dest.is_file():
        raise ConvertUnavailable(f"한글이 HWPX로 저장하지 못했습니다. {HWP_HINT}")
    return dest
