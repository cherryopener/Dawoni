"""Windows 배포 묶음 만들기 — `dist/Dawoni/` 폴더와 `dist/Dawoni-<버전>-windows.zip`.

    cd frontend && npm run build          # 화면 → dawoni/web/
    uv run python packaging/build_bundle.py [--access access.json]

PyInstaller exe 대신 이렇게 하는 이유 (2026-11-05 실측): Windows 11 **스마트 앱 컨트롤**이 켜진 PC는
서명 없는 새 exe를 막는다 (PyInstaller로 만든 Dawoni.exe, 인터넷에서 받은 .cmd 모두 막힘 —
"An Application Control policy has blocked this file"). python.org의 python.exe·pythonw.exe·
python313.dll은 Python Software Foundation 서명이 있어 통과하고, 널리 쓰이는 PyPI 패키지의
.pyd도 인터넷 표시(MOTW)가 붙은 채로 불러와졌다. 그래서:

- 서명된 **pythonw.exe를 `Dawoni.exe`로 복사**한다 (이름을 바꿔도 서명은 그대로).
- `python313._pth`가 이 폴더의 표준 라이브러리·패키지만 보게 한다 (격리 모드 — 이 PC에 설치된
  파이썬·PYTHONPATH와 섞이지 않음).
- `_dawoni_boot.pth`가 파이썬이 켜질 때 `_dawoni_boot`를 부른다: `Dawoni.exe`가 인자 없이
  켜졌으면(더블클릭) `Dawoni.exe -m dawoni`를 새로 띄우고 끝낸다 → 화면이 열린다.
- `python.exe`도 함께 둔다 — 명령줄 확인용 (`python.exe -m dawoni version`).

빌드에 쓰는 파이썬은 python.org 설치본이어야 한다 (서명 확인 — 아니면 멈춘다).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
OUT = DIST / "Dawoni"

ROOT_FILES = ("python.exe", "pythonw.exe")
SKIP_LIB = {"site-packages", "test", "idlelib", "tkinter", "turtledemo", "ensurepip", "__pycache__"}
SKIP_DLLS = {"_tkinter.pyd", "tcl86t.dll", "tk86t.dll"}

PTH = "Lib\r\nDLLs\r\nLib\\site-packages\r\nimport site\r\n"

BOOT = '''"""다워니 묶음: Dawoni.exe(서명된 pythonw.exe 사본)를 인자 없이 켜면 앱 화면을 띄운다."""

import sys

if sys.orig_argv[1:] == [] and sys.executable.lower().endswith("dawoni.exe"):
    import os
    import subprocess

    subprocess.Popen([sys.executable, "-m", "dawoni"], close_fds=True)
    os._exit(0)
'''

README = """다워니 {version}

시작: 이 폴더의 Dawoni.exe를 두 번 누르세요.
처음 켜면 기본 브라우저에서 Google 로그인 창이 열립니다 (허가된 계정만).

폴더는 통째로 두세요 — Dawoni.exe만 바탕화면으로 옮기면 켜지지 않습니다.
바탕화면에는 Dawoni.exe를 오른쪽 클릭 → '바로 가기 만들기'로 만든 바로 가기를 두세요.

오류 기록: %APPDATA%\\Dawoni\\logs\\app.log
"""


def _run(*args: str | Path) -> None:
    print("$", " ".join(map(str, args)), flush=True)
    subprocess.run([str(a) for a in args], check=True, cwd=ROOT)


def _signed(path: Path) -> str:
    """Authenticode 서명 상태 (Valid면 통과)."""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-AuthenticodeSignature -LiteralPath '{path}').Status"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    return r.stdout.strip()


def _version() -> str:
    import tomllib

    return str(
        tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    )


def _copy_runtime(base: Path) -> None:
    for name in ROOT_FILES:
        shutil.copy2(base / name, OUT / name)
    for dll in base.glob("*.dll"):  # python313.dll, python3.dll, vcruntime140*.dll
        shutil.copy2(dll, OUT / dll.name)
    (OUT / "DLLs").mkdir()
    for f in (base / "DLLs").iterdir():
        if f.is_file() and f.name not in SKIP_DLLS:
            shutil.copy2(f, OUT / "DLLs" / f.name)
    shutil.copytree(
        base / "Lib", OUT / "Lib",
        ignore=lambda d, names: [n for n in names if n in SKIP_LIB],
    )  # fmt: skip
    (OUT / "Lib" / "site-packages").mkdir()


def _install(wheel: Path) -> None:
    py = OUT / "python.exe"
    req = DIST / "requirements.txt"
    with req.open("w", encoding="utf-8") as f:
        subprocess.run(
            ["uv", "export", "--frozen", "--no-dev", "--extra", "windows", "--no-hashes",
             "--no-emit-project", "-q"],
            stdout=f, check=True, cwd=ROOT,
        )  # fmt: skip
    common = ["--python", str(py), "--compile-bytecode", "--link-mode", "copy"]
    _run("uv", "pip", "install", *common, "-r", req)
    _run("uv", "pip", "install", *common, "--no-deps", wheel)
    shutil.rmtree(OUT / "Scripts", ignore_errors=True)  # 패키지 명령 exe(서명 없음) — 쓰지 않는다


def _access_json(arg: str | None) -> str | None:
    if arg:
        return Path(arg).read_text(encoding="utf-8-sig")
    text = os.environ.get("DAWONI_ACCESS_JSON", "").strip()
    return text or None


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("Windows에서만 만든다")
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Windows 배포 묶음 만들기")
    ap.add_argument("--access", help="로그인 설정 access.json (없으면 비밀값 DAWONI_ACCESS_JSON)")
    ap.add_argument(
        "--require-access", action="store_true", help="로그인 설정이 없으면 멈춤 (배포)"
    )
    ap.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""))
    args = ap.parse_args()

    if not (ROOT / "dawoni" / "web" / "index.html").is_file():
        raise SystemExit("화면 빌드가 없습니다: cd frontend && npm run build")
    base = Path(sys.base_prefix)
    if _signed(base / "pythonw.exe") != "Valid":
        raise SystemExit(f"python.org 설치본이 아닙니다 (서명 없음): {base}")
    access = _access_json(args.access)
    if access is not None:
        json.loads(access)  # 형식만 확인 — 내용은 찍지 않는다
    elif args.require_access:
        raise SystemExit("로그인 설정이 없습니다 (--access 또는 비밀값 DAWONI_ACCESS_JSON)")

    version = _version()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    wheel_dir = DIST / "wheel"
    shutil.rmtree(wheel_dir, ignore_errors=True)
    _run("uv", "build", "--wheel", "-o", wheel_dir)
    wheel = next(wheel_dir.glob("dawoni-*.whl"))

    _copy_runtime(base)
    _install(wheel)
    site = OUT / "Lib" / "site-packages"
    (OUT / "python313._pth").write_text(PTH, encoding="ascii")
    (site / "_dawoni_boot.py").write_text(BOOT, encoding="utf-8")
    (site / "_dawoni_boot.pth").write_text("import _dawoni_boot\n", encoding="ascii")
    shutil.copy2(OUT / "pythonw.exe", OUT / "Dawoni.exe")
    assets = site / "dawoni" / "assets"
    if not (site / "dawoni" / "web" / "index.html").is_file():
        raise SystemExit("묶음에 화면 파일이 빠졌습니다 (pyproject의 hatch artifacts 확인)")
    if access is not None:
        (assets / "access.json").write_text(access, encoding="utf-8")  # BOM 없이
    stamp = {"version": version, "commit": args.commit, "built_at": datetime.now(UTC).isoformat()}
    (assets / "bundled.json").write_text(json.dumps(stamp, ensure_ascii=False), encoding="utf-8")
    (OUT / "읽어 주세요.txt").write_text(README.format(version=version), encoding="utf-8-sig")

    status = _signed(OUT / "Dawoni.exe")
    if status != "Valid":
        raise SystemExit(f"Dawoni.exe 서명이 {status} — 스마트 앱 컨트롤에서 막힌다")
    zpath = DIST / f"Dawoni-{version}-windows.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(OUT.rglob("*")):
            if f.is_file():
                z.write(f, Path("Dawoni") / f.relative_to(OUT))
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"묶음: {OUT} ({size / 1e6:.0f} MB), zip: {zpath} ({zpath.stat().st_size / 1e6:.0f} MB)")
    print(f"로그인: {'켜짐' if access is not None else '없음 (로그인 없는 앱)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
