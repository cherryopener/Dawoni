"""M4 실측: M3 판독문(학생 24명)을 다워니 초안 루브릭(1·2번)으로 채점하고 표지의 강사 점수와 비교.

    uv run python experiments/m4_grade.py grade [--parallel 4] [--regrade]
    uv run python experiments/m4_grade.py grade --regrade --model gemini-4-argon-low --label argon-low
    uv run python experiments/m4_grade.py report [--label argon-low]

- 판독문: M3 실측(`m3_batch.py scan`, flash-medium)이 만든 것. 강사 확인 전이다.
- 루브릭: 다워니의 **초안**(보정 전) — 3번은 인쇄 해설지가 틀려(PLAN.md §1-3) 뺀다.
- 기준: 표지의 빨간 펜 문항 점수 (표지 판독, 다워니 실측 68/72). 소문항 점수는 없다(M5에서 읽음).
- 결과는 `experiments/out/m4/[<label>/]report.json`·`report.md`. `--model`은 채점 역할(grader)만 바꾼다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m3_batch import DAWONI_EXAM, exam_store, services  # pyright: ignore[reportPrivateUsage]

from dawoai.config import TierModel
from dawoai.context.calllog import CallLog
from dawoai.grading import workflow
from dawoai.grading.pipeline import approve_rubric
from dawoai.models.grading import Rubric

OUT = Path(__file__).resolve().parent / "out" / "m4"
QUESTIONS = ["1", "2"]


def _progress(done: int, total: int, message: str) -> None:
    print(f"  [{done}/{total}] {message}", flush=True)


def _out(args: argparse.Namespace) -> Path:
    return OUT / args.label if args.label else OUT


def cmd_grade(args: argparse.Namespace) -> int:
    svc = services(parallel=args.parallel)
    svc.config.parallel_grading = args.parallel
    if args.model:  # 채점 역할만 strong 등급으로 올려 그 모델을 쓴다
        svc.config.tiers["agy"]["strong"] = TierModel(model=args.model)
        svc.config.role_tiers["grader"] = "strong"
    store = exam_store(svc)
    for q in QUESTIONS:
        r = store.rubric(q)
        if r is None or r.status != "approved":
            src = DAWONI_EXAM / "rubrics" / f"{q}.json"
            rubric = Rubric.model_validate_json(src.read_text(encoding="utf-8"))
            store.save_rubric(approve_rubric(rubric, "M4 실측 (다워니 초안)"))
    calls_before = len(CallLog(svc.paths.logs / "calls.jsonl").read())
    t0 = time.monotonic()
    run = workflow.grade_exam(
        svc, store, only=QUESTIONS, regrade=args.regrade, use_examples=False, progress=_progress
    )
    wall = time.monotonic() - t0
    calls = CallLog(svc.paths.logs / "calls.jsonl").read()[calls_before:]
    out = _out(args)
    out.mkdir(parents=True, exist_ok=True)
    (out / "run.json").write_text(
        json.dumps(
            {
                "wall_s": wall,
                "workers": run.workers,
                "rate_signals": run.rate_signals,
                "graded": len(run.graded),
                "skipped": [vars(s) for s in run.skipped],
                "failed": [vars(s) for s in run.failed],
                "calls": calls,
            },
            ensure_ascii=False,
            indent=1,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"채점 {len(run.graded)}건, 실패 {len(run.failed)}건, {wall:.0f}초")
    return cmd_report(args)


def cmd_report(args: argparse.Namespace) -> int:
    svc = services()
    store = exam_store(svc)
    out = _out(args)
    run: dict[str, Any] = json.loads((out / "run.json").read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for sid in store.students():
        rec = store.student_record(sid)
        for q in QUESTIONS:
            g = store.grade(sid, q)
            teacher = (rec.teacher_scores if rec else {}).get(q)
            if g is None:
                continue
            rows.append(
                {
                    "student": sid, "question": q, "ai": g.total, "teacher": teacher,
                    "status": g.status, "calls": g.calls, "repairs": g.repairs,
                    "tokens": g.tokens, "duration_s": g.duration_s, "code_items": g.code_items,
                    "second": g.second_opinion is not None, "flags": g.flags,
                }
            )  # fmt: skip
    judged = [r for r in rows if r["teacher"] is not None]
    exact = [r for r in judged if abs(r["ai"] - r["teacher"]) < 1e-9]
    near = [r for r in judged if abs(r["ai"] - r["teacher"]) <= 1 + 1e-9]
    grade_calls = [c for c in run["calls"] if c.get("task") == "grade_answer"]
    ok_calls = [c for c in grade_calls if c.get("ok")]
    first = [c for c in grade_calls if c.get("attempt") == 1]
    repaired = [c for c in grade_calls if c.get("attempt", 1) > 1]
    fixes = sum(len(c.get("fixes") or []) for c in grade_calls)
    partial = [c for c in repaired if c.get("partial")]
    tokens = [c["total_tokens"] for c in grade_calls if c.get("total_tokens")]
    durations = [c["duration_s"] for c in grade_calls if c.get("duration_s")]
    students = len({r["student"] for r in rows})
    summary = {
        "students": students,
        "grades": len(rows),
        "with_teacher_score": len(judged),
        "exact": len(exact),
        "within_1": len(near),
        "abs_diff_sum": sum(abs(r["ai"] - r["teacher"]) for r in judged),
        "needs_review": sum(r["status"] == "needs_review" for r in rows),
        "second_graded": sum(r["second"] for r in rows),
        "without_ai": sum(r["calls"] == 0 for r in rows),
        "calls": len(grade_calls),
        "first_calls": len(first),
        "repair_calls": len(repaired),
        "repair_rate": len(repaired) / len(first) if first else 0,
        "partial_repairs": len(partial),
        "code_fixes": fixes,
        "failed_calls": len(grade_calls)
        - len(ok_calls)
        - len(repaired)
        + len([c for c in repaired if not c.get("ok")]),
        "tokens_total": sum(tokens),
        "tokens_per_student": sum(tokens) / students if students else 0,
        "call_s_median": statistics.median(durations) if durations else 0,
        "wall_s": run["wall_s"],
        "workers": run["workers"],
        "rate_signals": run["rate_signals"],
        "failed": run["failed"],
    }
    (out / "report.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    lines = [
        "# M4 채점 실측",
        "",
        *[f"- {k}: {v}" for k, v in summary.items()],
        "",
        "| 학생 | 문항 | AI | 강사 | 상태 | 호출 | 수리 | 토큰 |",
        "|---|---|---|---|---|---|---|---|",
        *[
            f"| {r['student']} | {r['question']} | {r['ai']:g} | "
            f"{'' if r['teacher'] is None else format(r['teacher'], 'g')} | {r['status']} | "
            f"{r['calls']} | {r['repairs']} | {r['tokens'] or ''} |"
            for r in rows
        ],
    ]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for k, v in summary.items():
        print(f"{k}: {v}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(required=True)
    g = sub.add_parser("grade")
    g.add_argument("--parallel", type=int, default=4)
    g.add_argument("--regrade", action="store_true")
    g.add_argument("--model", help="채점 역할에 쓸 모델 (strong 등급으로)")
    g.add_argument("--label", help="결과 폴더 이름 (out/m4/<label>/)")
    g.set_defaults(func=cmd_grade)
    r = sub.add_parser("report")
    r.add_argument("--label")
    r.set_defaults(func=cmd_report)
    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
