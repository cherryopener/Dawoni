"""HWP → HWPX: python-hwpx 먼저, 한글(COM)은 그 다음 — 실패 이유별 안내, 보안 모듈 이름
(한글 없이 가짜 COM으로)."""

from __future__ import annotations

import sys
import types
from collections import Counter
from pathlib import Path
from typing import Any, ClassVar

import pytest

from dawoni.ingest import convert

REAL = Path(__file__).parents[1] / "real_data" / "docs" / "11월 월례평가 문제-물리 (1).hwp"


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

        calls: ClassVar[list[tuple[str, str]]] = []
        opened = ""

        def Open(self, _path: str, kind: str, *_: object) -> bool:
            self.opened = kind
            return True

        def SaveAs(self, path: str, fmt: str, *_: object) -> None:
            Hwp.calls.append((self.opened, fmt))
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
    assert Hwp.calls[-1] == ("HWP", "HWPX")
    assert convert.register_security_module(Hwp()) == "내모듈"
    assert convert.KNOWN_MODULES[0] == "FilePathCheckerModuleExample"


def test_python_hwpx_first_hangul_only_for_missing_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """python-hwpx로 바꾸고, 수식·표 같은 내용을 못 옮겼다고 할 때만 (Windows) 한글을 띄운다.
    한글로도 안 되면 python-hwpx 결과를 쓴다. 맥은 한글을 부르지 않는다."""
    calls: list[str] = []

    def hangul(src: Path, dest: Path, fmt: str) -> Path:
        calls.append(fmt)
        dest.write_bytes(b"HANGUL")
        return dest

    def no_hangul(src: Path, dest: Path, fmt: str) -> Path:
        raise convert.ConvertUnavailable("한글 없음")

    skipped: Counter[str] = Counter({"master-page": 1})

    def python_hwpx(src: Path) -> tuple[bytes, Counter[str]]:
        return b"PYHWPX", skipped

    monkeypatch.setattr(convert, "python_hwpx", python_hwpx)
    monkeypatch.setattr(convert, "_hangul_save_as", hangul)
    monkeypatch.setattr(convert.sys, "platform", "win32")
    src = tmp_path / "a.hwp"

    out = convert.hwp_to_hwpx(src, tmp_path / "o" / "1.hwpx")
    assert out.read_bytes() == b"PYHWPX" and calls == [], "바탕쪽만 빠지면 한글을 띄우지 않음"
    skipped["control-eqed"] = 2
    out = convert.hwp_to_hwpx(src, tmp_path / "o" / "2.hwpx")
    assert out.read_bytes() == b"HANGUL" and calls == ["HWPX"], "수식을 못 옮기면 한글로"
    monkeypatch.setattr(convert, "_hangul_save_as", no_hangul)
    out = convert.hwp_to_hwpx(src, tmp_path / "o" / "3.hwpx")
    assert out.read_bytes() == b"PYHWPX", "한글도 안 되면 python-hwpx 결과"
    monkeypatch.setattr(convert, "_hangul_save_as", hangul)
    monkeypatch.setattr(convert.sys, "platform", "darwin")
    out = convert.hwp_to_hwpx(src, tmp_path / "o" / "4.hwpx")
    assert out.read_bytes() == b"PYHWPX" and calls == ["HWPX"], "맥은 한글을 부르지 않음"


# ---------------------------------------------------------------- 워터마크 (채우기 그림) 밝기·명암


def _hwpx_with_page_background(path: Path, bright: int, contrast: int) -> Path:
    import zipfile

    header = (
        '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
        'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core"><hh:refList><hh:borderFills>'
        '<hh:borderFill id="6"><hc:fillBrush><hc:imgBrush mode="ZOOM">'
        f'<hc:img binaryItemIDRef="image6" bright="{bright}" contrast="{contrast}" '
        'effect="REAL_PIC" alpha="0"/></hc:imgBrush></hc:fillBrush></hh:borderFill>'
        "</hh:borderFills></hh:refList></hh:head>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/header.xml", header)
        z.writestr("Contents/section0.xml", "<hs:sec/>")
    return path


def test_watermark_brightness_and_contrast_are_put_back(tmp_path: Path) -> None:
    """2026-11-18 11월 월례평가: 한글이 저장한 워터마크(쪽 배경 그림)는 밝기 70·명암 −50인데
    python-hwpx로 바꾸면 밝기 −50·명암 70 — 다듬은 해설지에서 워터마크가 진하게 앞으로 나와 글을
    가렸다."""
    import io
    import zipfile

    assert convert.fill_images_swapped(), "python-hwpx를 고치면 이 바로잡기는 저절로 꺼진다"
    src = _hwpx_with_page_background(tmp_path / "a.hwpx", bright=-50, contrast=70)
    fixed = convert.fix_fill_images(src.read_bytes())
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(io.BytesIO(fixed)) as b:
        header = b.read("Contents/header.xml").decode()
        assert 'bright="70" contrast="-50"' in header
        assert b.read("Contents/section0.xml") == a.read("Contents/section0.xml")
        assert b.namelist() == a.namelist()


@pytest.mark.skipif(not REAL.is_file(), reason="real_data 없음")
def test_real_hwp_watermark_matches_hangul() -> None:
    import io
    import zipfile

    data, _ = convert.python_hwpx(REAL)
    header = zipfile.ZipFile(io.BytesIO(data)).read("Contents/header.xml").decode()
    assert 'bright="70" contrast="-50"' in header, "한글이 저장한 _revision.hwpx와 같게"
