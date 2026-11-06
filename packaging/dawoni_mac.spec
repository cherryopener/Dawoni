# PyInstaller spec — 맥 배포 묶음 Dawoni.app (packaging/build_mac.py가 부른다, 직접 부르지 않는다)
#
# build_mac.py가 환경 변수로 넘기는 것:
#   DAWONI_STAGE  로그인 설정 access.json·묶음 표식 bundled.json을 둔 폴더 (→ dawoni/assets/)
#   DAWONI_ICON   앱 아이콘 PNG (PyInstaller가 Pillow로 .icns로 바꾼다)
#   DAWONI_VERSION
# ruff: noqa
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
stage = Path(os.environ["DAWONI_STAGE"])
version = os.environ["DAWONI_VERSION"]

# 앱 패키지: 화면 빌드(dawoni/web)·역할·템플릿·selftest 그림. 개발 PC의 access.json은 넣지 않는다
# (로그인 설정은 stage에서만 — 빌드 PC에 있던 파일이 섞여 나가지 않게)
datas = collect_data_files("dawoni", excludes=["assets/access.json", "assets/bundled.json"])
datas += [(str(f), "dawoni/assets") for f in stage.iterdir() if f.is_file()]
hiddenimports = collect_submodules("dawoni")  # 서버가 필요할 때 부르는 모듈까지
binaries = []
# 패키지 안 템플릿·자료 파일 (HWPX 빈 문서, Word 템플릿 …)과 지연 import
for pkg in ("hwpx", "docx", "webview"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

a = Analysis(
    [str(ROOT / "packaging" / "mac_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Dawoni",
    console=False,
    argv_emulation=False,
    target_arch=None,  # 빌드하는 맥과 같은 것 (GitHub macos-latest = Apple 실리콘)
    codesign_identity=None,  # 서명·공증 안 함 — PyInstaller가 임시(ad-hoc) 서명만 한다
)
coll = COLLECT(exe, a.binaries, a.datas, name="Dawoni")
app = BUNDLE(
    coll,
    name="Dawoni.app",
    icon=os.environ.get("DAWONI_ICON") or None,
    bundle_identifier="io.github.cherryopener.dawoni",
    version=version,
    info_plist={
        "CFBundleDisplayName": "다워니",
        "CFBundleName": "Dawoni",
        "CFBundleShortVersionString": version,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.education",
    },
)
