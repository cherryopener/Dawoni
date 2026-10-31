"""묶은 앱 스모크 검사 (AI 호출 없음): `python packaging/smoke.py <Dawoni 실행 파일>`

묶을 때 빠지기 쉬운 것들을 실제 실행 파일로 확인한다 — 패키지 데이터(역할·템플릿·화면),
matplotlib 그림, pandoc 수식이 든 Word, uvicorn 서버.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"


def run(exe: Path, *args: str | Path) -> str:
    r = subprocess.run(
        [str(exe), *map(str, args)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    out = r.stdout + r.stderr
    print(f"$ Dawoni {' '.join(map(str, args))}\n{out.strip()}\n")
    if r.returncode != 0:
        raise SystemExit(f"실패 (코드 {r.returncode})")
    return out


def check_server(exe: Path) -> None:
    proc = subprocess.Popen(
        [str(exe), "ui", "--no-window", "--port", "8799"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        print(line.strip())
        m = re.search(r"http://127\.0\.0\.1:\d+", line)
        if not m:
            raise SystemExit("서버 주소가 나오지 않음")
        for _ in range(60):
            try:
                with urllib.request.urlopen(m.group(0) + "/", timeout=2) as r:
                    body = r.read().decode("utf-8")
                if "<div id=" in body or "<script" in body:
                    print("화면 index.html 응답 확인\n")
                    return
                raise SystemExit("index.html이 아닌 응답")
            except urllib.error.URLError, ConnectionError:
                time.sleep(1)
        raise SystemExit("서버가 응답하지 않음")
    finally:
        proc.kill()
        proc.wait(timeout=10)


def main() -> None:
    exe = Path(sys.argv[1]).resolve()
    run(exe, "version")
    run(exe, "agent", "detect")  # CI에는 CLI가 없음 → '미설치'로 끝나야 함
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        spec = tmp / "spec.json"
        spec.write_text(
            '{"kind": "function_graph", "functions": [{"expr": "(x-2)**2 - 1"}],'
            ' "x_range": {"min": -1, "max": 5}, "y_range": {"min": -2, "max": 5}}',
            encoding="utf-8",
        )
        run(exe, "fig", "render", spec, tmp / "fig.png")
        ws = tmp / "작업공간"
        run(exe, "workspace", "init", ws)
        run(exe, "scope", "--workspace", ws, "import", FIX / "grading" / "scope.json")
        out = run(
            exe,
            "exam",
            "--workspace",
            ws,
            "create",
            "--title",
            "스모크",
            "--scope",
            "scope-quadratic-minmax",
            "--questions",
            FIX / "grading" / "questions.json",
        )
        m = re.search(r"exam-[0-9a-f]+", out)
        if not m:
            raise SystemExit("시험 ID를 찾지 못함")
        paper = tmp / "시험지.docx"
        out = run(exe, "export", "--workspace", ws, "paper", m.group(0), "--out", paper)
        if not paper.is_file() or "수식 0개" in out:
            raise SystemExit("Word 출력 실패 또는 수식 없음")
    check_server(exe)
    print("스모크 검사 통과")


if __name__ == "__main__":
    main()
