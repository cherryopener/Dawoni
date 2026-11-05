"""M6 실측: 채점 HWPX의 강조가 채점 근거 인용과 맞는가, 엑셀 점수표 (PLAN.md §9, §13).

    uv run python experiments/m6_output.py quotes            # 인용 → 판독문 자리 (세 채점 묶음)
    uv run python experiments/m6_output.py render 김민규 송주헌 [--hancom]   # 채점 HWPX → 한글 PDF·PNG
    uv run python experiments/m6_output.py xlsx               # 엑셀 점수표 (한 시트·반별)

- M5 작업공간(`experiments/out/m5/ws`, git 제외 — 학생 이름·답안)의 판독문·보정 루브릭과 세 채점 묶음
  (M5 보정 후 flash-medium·argon-low, 보정 전 argon-low)을 그대로 쓴다. 토큰 0.
- `quotes`: 인용을 `…`로 나눈 조각마다 판독문에서 찾았는가, 찾은 자리가 그 채점 항목의 소문항 안인가.
- `render`: 앱 모듈(`export.exam_files`)로 학생 파일을 `out/m6/`에 쓰고, `--hancom`이면 한글로 열어
  PDF·쪽 PNG(80dpi)로 저장하고 한글이 다시 저장한 파일을 다시 읽어 판독문이 같은지 본다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hwpx_probe import hancom, hancom_roundtrip  # pyright: ignore[reportPrivateUsage]

from dawoni.export.exam_files import _Sheets, score_table  # pyright: ignore[reportPrivateUsage]
from dawoni.export.hwpx import (
    Sheet,
    build_student,
    grade_spots,
    part_grades,
    read_back,
    reconcile,
    sheet_lines,
)
from dawoni.export.quotes import locate
from dawoni.export.xlsx import build_scores
from dawoni.grading.store import ExamStore
from dawoni.models.grading import GradeResult
from dawoni.paths import WorkspacePaths

ROOT = Path(__file__).resolve().parents[1]
M5 = ROOT / "experiments" / "out" / "m5"
OUT = ROOT / "experiments" / "out" / "m6"
GRADE_SETS = {
    "flash-medium (보정 후)": M5 / "flash-medium",
    "argon-low (보정 후)": M5 / "argon-low" / "grades",
    "argon-low (보정 전)": M5 / "argon-before-grades",
}


def _store() -> tuple[WorkspacePaths, ExamStore]:
    ws = WorkspacePaths(M5 / "ws")
    return ws, ExamStore(ws, ExamStore.list_ids(ws)[0])


def quotes() -> int:
    _, store = _store()
    report: dict[str, dict[str, int]] = {}
    for label, base in GRADE_SETS.items():
        c: Counter[str] = Counter()
        for f in sorted(base.rglob("*.json")):
            try:
                g = GradeResult.model_validate_json(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            q = f.stem
            t, rubric = store.transcript(g.student_id, q), store.rubric(q)
            if t is None or rubric is None:
                continue
            sheet = Sheet(head="", lines=sheet_lines(q, t.lines), grades=part_grades(q, g, rubric))
            spots = grade_spots(sheet)
            c["조각 찾음"] += spots.found
            c["조각 못 찾음"] += len(spots.missed)
            for m in spots.missed:
                print(f"  못 찾음 {label} {g.student_id} {q}: {m[:80]}")
            for key, pg in (sheet.grades or {}).items():
                for quote, _ in pg.good + pg.bad:
                    if not quote:
                        continue
                    parts = {
                        sheet.lines[i].part
                        for i in locate(sheet.lines, [(quote, "good", key)]).lines
                    }
                    c["인용 제 소문항" if parts == {key} else "인용 다른 소문항" if parts
                      else "인용 칠할 곳 없음"] += 1  # fmt: skip
                    if parts and parts != {key}:
                        print(f"  다른 소문항 {label} {g.student_id} {key} → {parts}: {quote[:60]}")
        report[label] = dict(c)
        print(label, dict(c))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "quotes.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


def render(students: list[str], *, use_hancom: bool) -> int:
    _, store = _store()
    sheets = _Sheets(store)
    made: list[tuple[Path, str, str]] = []
    OUT.mkdir(parents=True, exist_ok=True)
    for s in students:
        for q in ("1", "2"):
            sheet = sheets.sheet(s, q)
            if sheet.grades is None:
                print(f"  {s} {q}: 채점 없음")
                continue
            path = build_student(OUT / f"{s}_{q}.hwpx", sheet)
            made.append((path, s, q))
            print(f"  {path.name}: 인용 조각 {grade_spots(sheet).found}개 칠함")
    if not use_hancom:
        return 0
    failed = False
    with hancom() as hwp:
        for path, s, q in made:
            _, resaved, pngs = hancom_roundtrip(hwp, path)
            t = store.transcript(s, q)
            assert t is not None
            # 앱의 '확인 완료'와 같은 길: 다시 읽은 판독본 → 판독문, 고친 줄이 0이어야 한다
            r = reconcile(q, read_back(resaved).parts, t.lines)
            failed |= bool(r.changed or r.problems)
            print(f"  한글 {path.name}: {len(pngs)}쪽, 다시 저장해 다시 읽기 — 바뀐 줄 {r.changed}"
                  f"{f', 문제 {r.problems}' if r.problems else ''}")  # fmt: skip
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("quotes")
    r = sub.add_parser("render")
    r.add_argument("students", nargs="+")
    r.add_argument("--hancom", action="store_true")
    sub.add_parser("xlsx")
    args = ap.parse_args(argv)
    if args.cmd == "quotes":
        return quotes()
    if args.cmd == "render":
        return render(args.students, use_hancom=args.hancom)
    return xlsx()


def xlsx() -> int:
    """M5 작업공간의 채점으로 점수표 (한 시트·반별) — 작업공간 대신 `out/m6/`에 쓴다. 소문항 칸의
    합이 채점에 저장된 문항 총점과 같은지, 정렬이 규칙대로인지 본다."""
    from openpyxl import load_workbook

    _, store = _store()
    columns, rows = score_table(store)
    bad = 0
    states: Counter[str] = Counter()
    for row in rows:
        for q in store.exam().questions:
            cell = row.totals[q.number]
            states[cell.state] += 1
            keys = [c.key for c in columns if c.key == q.number or c.key.startswith(f"{q.number}-")]
            parts = sum(row.cells[k].value or 0 for k in keys if k in row.cells)
            if cell.value is not None and abs(parts - cell.value) > 1e-9:
                bad += 1
                print(f"  합 다름 {row.name} {q.number}: 소문항 {parts:g} ≠ 총점 {cell.value:g}")
    one = build_scores(OUT / "점수표.xlsx", store.exam().title, columns, rows)
    split = build_scores(OUT / "점수표_반별.xlsx", store.exam().title, columns, rows, by_class=True)
    table = [list(r) for r in load_workbook(one)["점수"].iter_rows(values_only=True)]
    print("열:", table[0])
    for line in table[1:6]:
        print("  ", line)
    names = [str(r[1]) for r in table[1:]]
    print(f"학생 {len(rows)}명, 문항 칸 상태 {dict(states)}, 소문항 합 ≠ 총점 {bad}건")
    print(f"이름순: {names == sorted(names)} · 반별 시트: {load_workbook(split).sheetnames}")
    return 1 if bad or names != sorted(names) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
