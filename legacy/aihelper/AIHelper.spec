# -*- mode: python ; coding: utf-8 -*-
"""AIHelper PyInstaller 사양 파일 (Spec).

Mac 및 Windows에서 터미널 없이 더블클릭으로 가동되는 독립 실행 번들을 생성한다.
FastAPI 백엔드 + Vite React 번들(web/) + PyMuPDF + Sympy + Matplotlib 내장.
"""
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs

block_cipher = None

BASE_DIR = Path(SPECPATH).resolve()

# 정적 웹 번들 및 폰트 데이터 포함
datas = [
    (str(BASE_DIR / 'aihelper' / 'web'), 'aihelper/web'),
]
datas += collect_data_files('docx')
datas += collect_data_files('matplotlib')
datas += collect_data_files('pymupdf')
datas += collect_data_files('sympy')
try:
    datas += collect_data_files('math2docx')
except Exception:
    pass
try:
    datas += collect_data_files('latex2mathml')
except Exception:
    pass
try:
    datas += collect_data_files('mathml2omml_as')
except Exception:
    pass


# pythonnet, clr_loader 및 webview 데이터(Python.Runtime.dll 등) 수집
try:
    import pythonnet
    pnet_dir = Path(pythonnet.__file__).resolve().parent
    pnet_runtime = pnet_dir / 'runtime'
    if pnet_runtime.is_dir():
        datas.append((str(pnet_runtime), 'pythonnet/runtime'))
except Exception:
    pass

try:
    datas += collect_data_files('pythonnet')
except Exception:
    pass
try:
    datas += collect_data_files('clr_loader')
except Exception:
    pass
try:
    datas += collect_data_files('webview')
except Exception:
    pass

# C# / C++ 바이너리 DLL 라이브러리 수집
binaries = []
try:
    binaries += collect_dynamic_libs('pythonnet')
except Exception:
    pass
try:
    binaries += collect_dynamic_libs('clr_loader')
except Exception:
    pass
try:
    binaries += collect_dynamic_libs('webview')
except Exception:
    pass

# 필요한 서브모듈 수집
hiddenimports = [
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespans',
    'uvicorn.lifespans.on',
    'fitz',
    'pymupdf',
    'docx',
    'sympy',
    'matplotlib',
    'matplotlib.backends.backend_agg',
    'numpy',
    'openpyxl',
    'fastapi',
    'starlette',
    'pydantic',
    'httpx',
    'webview',
    'webview.platforms.winforms',
    'webview.platforms.edgechromium',
    'clr',
    'pythonnet',
    'clr_loader',
    'clr_loader.ffi',
    'unittest',
    'unittest.mock',
    'math2docx',
    'latex2mathml',
    'mathml2omml_as',
]
hiddenimports += collect_submodules('aihelper')
try:
    hiddenimports += collect_submodules('math2docx')
except Exception:
    pass
try:
    hiddenimports += collect_submodules('latex2mathml')
except Exception:
    pass
try:
    hiddenimports += collect_submodules('mathml2omml_as')
except Exception:
    pass

try:
    hiddenimports += collect_submodules('webview')
except Exception:
    pass
try:
    hiddenimports += collect_submodules('pythonnet')
except Exception:
    pass
try:
    hiddenimports += collect_submodules('clr_loader')
except Exception:
    pass

a = Analysis(
    ['run.py'],
    pathex=[str(BASE_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AIHelper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # 초기 디버그 및 로그 확인용 콘솔
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AIHelper',
)

# Mac OS 전용 .app 번들
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='AIHelper.app',
        icon=None,
        bundle_identifier='com.aihelper.app',
    )
