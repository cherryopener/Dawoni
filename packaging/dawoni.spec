# -*- mode: python ; coding: utf-8 -*-
"""다워니 PyInstaller 사양 (macOS .app / Windows 폴더 + Dawoni.exe).

먼저 화면을 빌드해야 한다: `cd frontend && npm ci && npm run build` (→ dawoni/web/)
빌드: `uv run --group build pyinstaller packaging/dawoni.spec --noconfirm`

- 역할 지시서·템플릿·자체 시험 그림·화면은 `dawoni/` 안 상대 경로로 찾으므로 같은 자리에 넣는다.
- pandoc은 pypandoc-binary가 동봉한 것을 쓴다.
- Windows는 콘솔 창을 남긴다 (Edge 앱 창과 따로 떠서, 콘솔을 닫으면 끝남 + 오류가 보임).
"""

import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
WEB = ROOT / "dawoni" / "web" / "index.html"
if not WEB.is_file():
    raise SystemExit("화면 빌드가 없습니다: cd frontend && npm ci && npm run build")

datas = collect_data_files("dawoni", excludes=["**/__pycache__/**", "**/.DS_Store"])
datas += collect_data_files("pypandoc")  # files/pandoc(.exe)
datas += collect_data_files("docx")  # 기본 템플릿
datas += collect_data_files("youtube_transcript_api")
datas += copy_metadata("dawoni")  # __version__

hiddenimports = collect_submodules("dawoni") + [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]
if sys.platform == "darwin":
    hiddenimports += collect_submodules("webview")

a = Analysis(
    [str(ROOT / "packaging" / "dawoni_app.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    # 선택 설치(로컬 OCR)·개발 도구는 넣지 않는다
    excludes=["rapidocr", "onnxruntime", "pytest", "tkinter", "IPython"],
    # python-docx는 `docx/parts/../templates`로 찾는다 → parts 폴더가 실제로 있어야 함
    module_collection_mode={"docx": "py"},
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Dawoni",
    icon=str(ROOT / "packaging" / "icon.ico"),  # Windows exe (macOS는 BUNDLE의 icns)
    console=sys.platform == "win32",
    upx=False,
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Dawoni", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Dawoni.app",
        icon=str(ROOT / "packaging" / "icon.icns"),
        bundle_identifier="com.cherryopener.dawoni",
        info_plist={
            "CFBundleDisplayName": "다워니",
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
        },
    )
