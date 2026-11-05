"""PC에 이미 설치된 한글로 파일 형식 바꾸기 (PLAN.md §3 — 변환 프로그램을 싣지 않는다).

학원 시험지는 대부분 `.hwp`(한글 바이너리)다. HWPX(zip + XML)만 직접 읽으므로, 한글이 설치된
Windows PC에서는 한글(COM)로 HWPX로 저장해 읽는다. 한글이 없으면 강사에게 HWPX로 저장해 달라고 한다.

- 한글은 파일을 열 때 '접근 허용' 창을 띄울 수 있다. 자동화 보안 모듈이 등록된 PC면 뜨지 않는다.
  `RegisterModule`에 주는 이름은 강사가 레지스트리(HKCU 아래 `MODULES_KEY`)에
  적은 값 이름이어야 한다 — 한컴 예제 DLL은 `FilePathCheckerModuleExample`(2026-11-06: 예전엔
  `FilePathCheckerModule`로 불러 늘 실패해 창이 떴다). 그래서 레지스트리에 있는 이름을 읽어 쓴다.
  창을 숨기면 아무도 답할 수 없어 멈추므로 한글 창을 보이게 연다 (experiments/hwpx_probe.py).
- 한글이 깔려 있어도 자동화 등록(`HWPFrame.HwpObject`)이 빠진 PC가 있다 (2026-11-05 다른 PC 실측:
  한글 2024 설치됨, `CO_E_CLASSSTRING` — 복구 설치로 해결). docs/HWP_HANGUL.md.
- 서버 작업은 스레드에서 돈다 — COM은 스레드마다 `CoInitialize`가 필요하다.
"""

from __future__ import annotations

import contextlib
import importlib
import sys
import threading
from pathlib import Path
from typing import Any

HWP_HINT = "한글에서 [다른 이름으로 저장] → 'HWPX 문서'(또는 PDF)로 저장한 파일을 넣어 주세요."
NOT_REGISTERED = (-2147221005, -2147221164)
"""CO_E_CLASSSTRING(잘못된 클래스 문자열), REGDB_E_CLASSNOTREG — 한글 자동화가 등록되지 않음."""
MODULES_KEY = r"Software\HNC\HwpAutomation\Modules"
KNOWN_MODULES = ("FilePathCheckerModuleExample", "FilePathCheckerModule")
"""레지스트리를 못 읽을 때 시도할 이름 (한컴 예제 DLL의 이름이 먼저)."""
_lock = threading.Lock()
"""한글은 한 번에 하나만 띄운다 (둘째 연결이 꺼지는 중인 한글에 붙어 죽는다 — 실측)."""


class ConvertUnavailable(RuntimeError):
    """이 PC에서는 바꿀 수 없음 (한글·pywin32 없음, 한글이 열지 못함)."""


def security_modules() -> list[str]:
    """강사가 등록한 한글 자동화 보안 모듈의 이름 (레지스트리 값 이름) + 알려진 이름."""
    names: list[str] = []
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, MODULES_KEY) as key:
            i = 0
            while True:
                try:
                    names.append(winreg.EnumValue(key, i)[0])
                except OSError:
                    break
                i += 1
    except (ImportError, OSError):
        pass  # Windows가 아니거나 등록한 모듈이 없음
    return [n for n in dict.fromkeys([*names, *KNOWN_MODULES]) if n]


def register_security_module(hwp: Any) -> str | None:
    """보안 모듈을 붙인다 — 붙으면 '접근 허용' 창이 뜨지 않는다. 붙은 이름 (없으면 None)."""
    for name in security_modules():
        with contextlib.suppress(Exception):
            if hwp.RegisterModule("FilePathCheckDLL", name):
                return name
    return None


def _dispatch_error(e: Exception) -> ConvertUnavailable:
    code = getattr(e, "hresult", None)
    if code is None and e.args and isinstance(e.args[0], int):
        code = e.args[0]
    if code in NOT_REGISTERED:
        return ConvertUnavailable(
            "이 PC의 한글에 자동화 등록이 없습니다 (한글이 없거나 설치가 덜 됨). "
            "한글이 깔려 있으면 [설정 → 앱 → 한컴오피스 → 수정]으로 관리자 권한 복구 설치를 해 "
            "주세요. 아니면 " + HWP_HINT
        )
    return ConvertUnavailable(f"한글을 띄우지 못했습니다 ({e}). {HWP_HINT}")


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
                raise _dispatch_error(e) from e
            try:
                register_security_module(hwp)
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
