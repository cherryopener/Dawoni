"""`.hwp` → `.hwpx` (PLAN.md §3 — 따로 변환 프로그램을 싣지 않는다).

학원 시험지는 대부분 `.hwp`(한글 바이너리)다. 앱은 HWPX(zip + XML)만 직접 읽으므로 먼저 바꾼다.

1. python-hwpx(HWPX 쓰기에 이미 쓰는 라이브러리)의 HWP5 변환 — 한글 없이, 맥에서도, 창 없이.
   실측(2026-11-10, 11월 월례평가 HWP): 자동 번호 문항·수식·그림·미주가 모두 그대로 나왔다.
2. 그것이 실패하거나 내용(수식·표·그림 등)을 다 옮기지 못했다고 하면, Windows에서는 설치된
   한글(COM)로 HWPX로 저장한다. 한글로도 안 되면 python-hwpx 결과를 쓴다 (못 옮긴 것은 로그에).

한글(COM)에 대해:
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
import logging
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

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
    if sys.platform != "win32":  # 맥: winreg 없음 (pyright도 이 분기로 winreg를 건너뛴다)
        return list(KNOWN_MODULES)
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


COSMETIC = frozenset(
    {"master-page", "memo-body", "track-changes", "presentation", "picture-effects",
     "compose-frame", "textart-code"}
)  # fmt: skip
"""python-hwpx가 옮기지 못해도 시험 읽기와 상관없는 것 (바탕쪽·메모·변경 추적·꾸밈)."""


def hwp_to_hwpx(src: Path, dest: Path) -> Path:
    """`.hwp` → `.hwpx`: python-hwpx로, 안 되면 (Windows) 한글로.
    실패하면 `ConvertUnavailable` (안내 문구 포함)."""
    try:
        data, skipped = python_hwpx(src)
    except Exception as e:  # 깨진 파일, python-hwpx가 모르는 구조
        log.warning("python-hwpx가 %s을 HWPX로 바꾸지 못함: %s", src.name, e)
        if sys.platform != "win32":
            raise ConvertUnavailable(f"HWP 파일을 읽지 못했습니다 ({e}). {HWP_HINT}") from e
        return _hangul_save_as(src, dest, "HWPX")
    missing = {k: n for k, n in skipped.items() if k not in COSMETIC}
    if missing:
        log.warning("python-hwpx가 %s에서 옮기지 못한 것: %s", src.name, missing)
        if sys.platform == "win32":
            try:
                return _hangul_save_as(src, dest, "HWPX")
            except ConvertUnavailable as e:
                log.warning("한글로도 바꾸지 못해 python-hwpx 결과를 씀: %s", e)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def python_hwpx(src: Path) -> tuple[bytes, Counter[str]]:
    """한글 없이 HWPX 바이트로 + 옮기지 못한 것 (종류 → 개수)."""
    from hwpx.hwp5.package import convert, to_hwpx_bytes

    converted = convert(src.read_bytes())
    return to_hwpx_bytes(converted.files), converted.report.unconverted


def _hangul_save_as(src: Path, dest: Path, fmt: str) -> Path:
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
            for attempt in range(1, ATTEMPTS + 1):
                try:
                    _save_once(client, src, dest, fmt)
                    break
                except ConvertUnavailable:
                    raise
                except Exception as e:
                    # 실측(2026-11-06): 바로 앞 변환의 한글이 아직 꺼지는 중이면 거기에 붙어
                    # "The server threw an exception"(RPC_E_SERVERFAULT) — 잠시 뒤 새 한글로 다시
                    if attempt == ATTEMPTS:
                        raise ConvertUnavailable(
                            f"한글로 바꾸다 오류가 났습니다 ({e}). {HWP_HINT}"
                        ) from e
                    time.sleep(RETRY_WAIT_S * attempt)
        finally:
            pythoncom.CoUninitialize()
    if not dest.is_file():
        raise ConvertUnavailable(f"한글이 {fmt}로 저장하지 못했습니다. {HWP_HINT}")
    return dest


ATTEMPTS = 3
RETRY_WAIT_S = 1.5


def _save_once(client: Any, src: Path, dest: Path, fmt: str) -> None:
    try:
        hwp: Any = client.DispatchEx("HWPFrame.HwpObject")
    except Exception as e:
        raise _dispatch_error(e) from e
    try:
        register_security_module(hwp)
        hwp.XHwpWindows.Item(0).Visible = True
        kind = "HWPX" if src.suffix.lower() == ".hwpx" else "HWP"
        if not hwp.Open(str(src.resolve()), kind, "forceopen:true"):
            raise ConvertUnavailable(f"한글이 {src.name}을 열지 못했습니다. {HWP_HINT}")
        hwp.SaveAs(str(dest.resolve()), fmt, "")
    finally:
        with contextlib.suppress(Exception):
            hwp.Clear(1)
        with contextlib.suppress(Exception):
            hwp.Quit()
