"""M3 실측 보조: 한글(COM)로 다시 저장만 한 판독본을 다시 읽어, 앱 판독문과 다르게 읽히는 줄을 찾는다.

판독본을 복사해 한글로 열고 그대로 저장한 뒤 `read_back` → `reconcile`. 고친 줄이 0이어야 한다.

    uv run --extra windows python experiments/m3_resave_probe.py [--limit 8]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m3_batch import OUT, _hancom, exam_store, services  # pyright: ignore[reportPrivateUsage]

from dawoni.export.exam_files import student_file
from dawoni.export.hwpx import canon, expected_text, read_back, reconcile, sheet_lines


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=8)
    args = ap.parse_args(argv)
    svc = services()
    store = exam_store(svc)
    work = OUT / "resave"
    work.mkdir(parents=True, exist_ok=True)
    pairs: list[tuple[str, str, Path]] = []
    for sid in store.students():
        for q in store.exam().questions:
            src = student_file(svc.workspace, store, sid, q.number)
            if src.is_file() and len(pairs) < args.limit:
                pairs.append((sid, q.number, shutil.copy(src, work / src.name)))
    with _hancom() as hwp:
        for _, _, path in pairs:
            if not hwp.Open(str(path), "HWPX", "forceopen:true"):
                raise RuntimeError(f"한글이 열지 못함: {path}")
            hwp.SaveAs(str(path), "HWPX", "")
            hwp.Clear(1)
    bad = 0
    for sid, q, path in pairs:
        t = store.transcript(sid, q)
        assert t is not None
        rec = reconcile(q, read_back(path).parts, t.lines)
        want = expected_text(sheet_lines(q, t.lines))
        got = read_back(path).parts
        print(f"{path.name}: 고친 줄 {rec.changed} {rec.problems or ''}")
        if rec.changed:
            bad += 1
            for part, lines in want.items():
                a, b = [canon(x) for x in lines], [canon(x) for x in got.get(part, [])]
                for x, y in zip(a, b, strict=False):
                    if x != y:
                        print(f"   앱  : {x}\n   한글: {y}")
                if len(a) != len(b):
                    print(f"   줄 수 다름: 앱 {len(a)}, 한글 {len(b)}")
    print(f"다시 저장만 했는데 다르게 읽힌 파일 {bad}/{len(pairs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
