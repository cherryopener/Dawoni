"""맥 배포 묶음 만들기 — `dist/Dawoni-mac/Dawoni.app`과 `dist/Dawoni-<버전>-macos.zip`.

    npm --prefix frontend run build        # 화면 → dawoni/web/
    uv sync --group bundle-mac
    uv run python packaging/build_mac.py [--access access.json]

Windows 묶음(build_bundle.py)과 달리 PyInstaller로 묶는다: 맥 앱은 실행 파일이 .app 안에 있어야
Dock·메뉴에 '다워니'로 나온다 (python을 그대로 두면 'Python'). 창은 pywebview(WebKit).

서명·공증은 하지 않는다 (2026-11-06 결정 — 학원 안 소수 강사용). PyInstaller가 임시(ad-hoc) 서명만
해서 Apple 실리콘에서 실행은 되지만, 인터넷에서 받은 앱이라 처음 열 때 macOS가 막는다 →
'읽어 주세요.txt'의 방법으로 연다. 빌드한 맥과 같은 칩(GitHub macos-latest = Apple 실리콘)에서만
돈다.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from build_bundle import _access_json, _check_access, _version  # pyright: ignore[reportMissingImports]

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
OUT = DIST / "Dawoni-mac"
APP = OUT / "Dawoni.app"
EXE = APP / "Contents" / "MacOS" / "Dawoni"
LOGO = ROOT / "frontend" / "src" / "assets" / "logo-mark.png"

README = """다워니 {version} (맥, Apple 실리콘)

1. Dawoni.app을 '응용 프로그램' 폴더로 옮기세요.
2. 처음 한 번은 macOS가 "확인되지 않은 개발자"라며 막습니다 (서명하지 않은 학원용 앱).
   - Dawoni.app을 두 번 눌러 경고가 뜨면 [완료]를 누르고,
   - 시스템 설정 → 개인정보 보호 및 보안 → 아래쪽 "Dawoni.app이(가) 차단되었습니다" 옆
     [그래도 열기] → 암호 입력. 다음부터는 그냥 열립니다.
   - 또는 터미널에서 한 줄:  xattr -dr com.apple.quarantine /Applications/Dawoni.app
3. 처음 켜면 기본 브라우저에서 Google 로그인 창이 열립니다 (허가된 계정만).

한글(.hwp) 시험지는 맥에서 읽을 수 없습니다 — 한글에서 HWPX(또는 PDF)로 저장해 넣으세요.
오류 기록: ~/Library/Application Support/Dawoni/logs/app.log
"""


def _run(*args: str | Path, env: dict[str, str] | None = None) -> None:
    print("$", " ".join(map(str, args)), flush=True)
    subprocess.run([str(a) for a in args], check=True, cwd=ROOT, env=env)


def _icon(tmp: Path) -> Path:
    """로고(가로가 긴 그림)를 정사각형 1024 캔버스 가운데에 — PyInstaller가 .icns로 바꾼다."""
    from PIL import Image

    logo = Image.open(LOGO).convert("RGBA")
    side = 1024
    inner = round(side * 0.8)
    scale = inner / max(logo.size)
    logo = logo.resize((round(logo.width * scale), round(logo.height * scale)), Image.LANCZOS)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(logo, ((side - logo.width) // 2, (side - logo.height) // 2), logo)
    out = tmp / "Dawoni.png"
    canvas.save(out)
    return out


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def smoke() -> None:
    """묶은 앱을 켜 본다: 버전, 화면 서버가 뜨는지, 토큰 없이 API가 막히는지."""
    out = subprocess.run([str(EXE), "version"], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise SystemExit(f"version 실패: {out.stdout}{out.stderr}")
    print("version:", out.stdout.strip())
    port = _free_port()
    home = Path(tempfile.mkdtemp(prefix="dawoni-home-"))
    env = {**os.environ, "DAWONI_HOME": str(home)}
    p = subprocess.Popen([str(EXE), "ui", "--no-window", "--port", str(port)], env=env)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(120):
            try:
                with urllib.request.urlopen(f"{base}/", timeout=2) as r:
                    if r.status == 200 and b"<title>" in r.read():
                        break
            except OSError:
                time.sleep(0.5)
        else:
            raise SystemExit("화면이 열리지 않음")
        try:
            urllib.request.urlopen(f"{base}/api/status", timeout=2).close()
            raise SystemExit("토큰 없이 API가 열림")
        except urllib.error.HTTPError:
            pass
    finally:
        p.terminate()
        p.wait(timeout=10)
        shutil.rmtree(home, ignore_errors=True)
    print("켜 보기: 화면 열림, 토큰 없는 API 막힘")


def _check_certificates() -> None:
    """묶을 파이썬이 macOS 기본 인증서(/etc/ssl/cert.pem)로 HTTPS를 확인하는지.

    python.org 파이썬은 제 폴더의 cert.pem을 찾아, 그 파일이 없는 강사 맥에서 Google·명단 접속이
    CERTIFICATE_VERIFY_FAILED였다 (2026-11-07, GitHub 러너의 python.org 파이썬으로 만든 앱)."""
    import ssl

    cafile = ssl.get_default_verify_paths().openssl_cafile
    if cafile not in ("/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem"):
        raise SystemExit(
            f"이 파이썬은 인증서를 {cafile}에서 찾습니다 — 다른 맥에서 HTTPS가 실패합니다. "
            "uv가 받은 파이썬으로 만드세요 (UV_PYTHON_PREFERENCE=only-managed)"
        )


def main() -> int:
    if sys.platform != "darwin":
        raise SystemExit("맥에서만 만든다")
    _check_certificates()
    ap = argparse.ArgumentParser(description="맥 배포 묶음 만들기")
    ap.add_argument("--access", help="로그인 설정 access.json (없으면 비밀값 DAWONI_ACCESS_JSON)")
    ap.add_argument(
        "--require-access", action="store_true", help="로그인 설정이 없으면 멈춤 (배포)"
    )
    ap.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""))
    ap.add_argument("--no-smoke", action="store_true", help="묶은 앱을 켜 보지 않음")
    args = ap.parse_args()

    if not (ROOT / "dawoni" / "web" / "index.html").is_file():
        raise SystemExit("화면 빌드가 없습니다: npm --prefix frontend run build")
    access = _access_json(args.access)
    if access is not None:
        _check_access(access, "--access 파일" if args.access else "비밀값 DAWONI_ACCESS_JSON")
    elif args.require_access:
        raise SystemExit("로그인 설정이 없습니다 (--access 또는 비밀값 DAWONI_ACCESS_JSON)")

    version = _version()
    shutil.rmtree(OUT, ignore_errors=True)
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        stage = tmp / "stage"
        stage.mkdir()
        if access is not None:
            (stage / "access.json").write_text(access, encoding="utf-8")
        stamp = {
            "version": version,
            "commit": args.commit,
            "built_at": datetime.now(UTC).isoformat(),
        }
        (stage / "bundled.json").write_text(json.dumps(stamp, ensure_ascii=False), "utf-8")
        env = {
            **os.environ,
            "DAWONI_STAGE": str(stage),
            "DAWONI_ICON": str(_icon(tmp)),
            "DAWONI_VERSION": version,
        }
        _run(
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", OUT, "--workpath", tmp / "work",
            ROOT / "packaging" / "dawoni_mac.spec",
            env=env,
        )  # fmt: skip
    shutil.rmtree(OUT / "Dawoni", ignore_errors=True)  # .app 안에 같은 것이 있다
    if not EXE.is_file():
        raise SystemExit(f"앱이 만들어지지 않음: {EXE}")
    (OUT / "읽어 주세요.txt").write_text(README.format(version=version), encoding="utf-8")
    if not args.no_smoke:
        smoke()

    zpath = DIST / f"Dawoni-{version}-macos.zip"
    zpath.unlink(missing_ok=True)
    # ditto: 앱 속 심볼릭 링크·실행 권한을 그대로 (zipfile은 링크를 풀어 서명이 깨진다)
    _run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", OUT, zpath)
    size = sum(f.stat().st_size for f in APP.rglob("*") if f.is_file() and not f.is_symlink())
    print(f"묶음: {APP} ({size / 1e6:.0f} MB), zip: {zpath} ({zpath.stat().st_size / 1e6:.0f} MB)")
    print(f"로그인: {'켜짐' if access is not None else '없음 (로그인 없는 앱)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
