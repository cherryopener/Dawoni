"""한글(COM)로 HWP → HWPX: 실패 이유별 안내, 보안 모듈 이름 (한글 없이 가짜 COM으로)."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from dawoni.ingest import convert


class FakeComError(Exception):
    pass


def _fake_com(monkeypatch: pytest.MonkeyPatch, dispatch: Any) -> None:
    monkeypatch.setattr(convert.sys, "platform", "win32")
    pythoncom = types.SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None)
    client = types.SimpleNamespace(DispatchEx=dispatch)
    monkeypatch.setitem(sys.modules, "pythoncom", pythoncom)
    win32com = types.ModuleType("win32com")
    monkeypatch.setitem(sys.modules, "win32com", win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", client)


@pytest.mark.parametrize(
    ("code", "want"),
    [(-2147221005, "자동화 등록이 없습니다"), (-2147221164, "자동화 등록이 없습니다"),
     (-2147023174, "한글을 띄우지 못했습니다")],
)  # fmt: skip
def test_dispatch_failure_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int, want: str
) -> None:
    """2026-11-05 다른 PC: 한글 2024가 깔려 있는데 `잘못된 클래스 문자열` — 예전엔 '한글이 설치되어
    있지 않습니다'로만 알렸다. 자동화 등록이 없으면 복구 설치를 안내한다."""

    def dispatch(_: str) -> Any:
        raise FakeComError(code, "잘못된 클래스 문자열입니다.", None, None)

    _fake_com(monkeypatch, dispatch)
    with pytest.raises(convert.ConvertUnavailable, match=want) as e:
        convert.hwp_to_hwpx(tmp_path / "a.hwp", tmp_path / "a.hwpx")
    assert "HWPX 문서" in str(e.value)
    assert ("복구 설치" in str(e.value)) == (want == "자동화 등록이 없습니다")


def test_registers_security_module_by_registry_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """레지스트리에 적은 값 이름(한컴 예제는 FilePathCheckerModuleExample)으로 붙여야
    창이 안 뜬다."""
    tried: list[str] = []

    class Hwp:
        class XHwpWindows:
            @staticmethod
            def Item(_: int) -> Any:
                return types.SimpleNamespace(Visible=False)

        def RegisterModule(self, kind: str, name: str) -> bool:
            tried.append(name)
            return name == "내모듈"

        def Open(self, *_: object) -> bool:
            return True

        def SaveAs(self, path: str, *_: object) -> None:
            Path(path).write_bytes(b"PK")

        def Clear(self, _: int) -> None:
            pass

        def Quit(self) -> None:
            pass

    def dispatch(_: str) -> Hwp:
        return Hwp()

    _fake_com(monkeypatch, dispatch)
    monkeypatch.setattr(convert, "security_modules", lambda: ["내모듈", *convert.KNOWN_MODULES])
    out = convert.hwp_to_hwpx(tmp_path / "a.hwp", tmp_path / "out" / "a.hwpx")
    assert out.is_file() and tried == ["내모듈"]
    assert convert.register_security_module(Hwp()) == "내모듈"
    assert convert.KNOWN_MODULES[0] == "FilePathCheckerModuleExample"
