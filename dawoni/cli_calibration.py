"""`dawoni exam calibration|calibrate|suggest-rubric …` — 강사 보정 (M8).

dawoni exam calibration <시험ID> -q 1 [--pin A] [--exclude D] [--examples 3] [--off|--on]
dawoni exam calibrate <시험ID> -q 1          # 보정 효과 측정 (예시 없이 / 예시와 함께)
dawoni exam suggest-rubric <시험ID> -q 1 [--apply]   # 강사 수정 사례 → 루브릭 개선안
dawoni exam teacher-scores <시험ID> 강사점수.csv [--by 김선생]   # 강사가 직접 매긴 소문항 점수
dawoni exam teacher-scores <시험ID> --student 홍길동 -q 2 --part "(1)①=2" [--note "(1)①=이유"]
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from dawoni.grading import calibration, workflow
from dawoni.grading.pipeline import edit_rubric
from dawoni.grading.render import rubric_text
from dawoni.grading.store import ExamStore
from dawoni.models.calibration import AgreementStats


def _out(s: str = "") -> None:
    sys.stdout.write(s + "\n")


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def stats_line(s: AgreementStats) -> str:
    if not s.n:
        return "비교할 사례 없음"
    bias = f"{s.bias:+.2g}" if s.bias is not None else "-"
    return (
        f"{s.n}건 · 총점 일치 {_pct(s.exact)} · ±1점 {_pct(s.within1)} · "
        f"항목 일치 {_pct(s.item_agreement)} · 평균 차 {s.mean_abs_diff or 0:.2g} · AI−강사 {bias}"
    )


def _cmd_calibration(args: argparse.Namespace, svc: Any) -> int:
    store = ExamStore(svc.workspace, args.exam)
    q = args.q[0] if args.q else store.exam().questions[0].number
    s = calibration.load_settings(store, q)
    pinned, excluded = set(s.pinned), set(s.excluded)
    pinned |= set(args.pin or [])
    pinned -= set(args.unpin or [])
    excluded |= set(args.exclude or [])
    excluded -= set(args.include or [])
    pinned -= excluded
    update: dict[str, Any] = {"pinned": sorted(pinned), "excluded": sorted(excluded)}
    if args.examples is not None:
        update["max_examples"] = args.examples
    if args.off or args.on:
        update["use_examples"] = bool(args.on)
    s = s.model_copy(update=update)
    calibration.save_settings(store, s)
    rubric = store.rubric(q)
    _out(f"문항 {q}: 기준 예시 {'사용' if s.use_examples else '끔'} (최대 {s.max_examples}개)")
    if rubric is not None:
        for c in calibration.candidates(store, q, rubric):
            pin = " [고정]" if c.pinned else ""
            fix = ", ".join(f"{k} {a:g}→{b:g}" for k, (a, b) in c.changed_items.items())
            score = f"{c.grade.total:g}/{c.grade.max_points:g}"
            tail = f" ({fix})" if fix else ""
            _out(f"  {c.student}: {score} {calibration.KIND_LABEL[c.kind]}{pin}{tail}")
    if s.excluded:
        _out(f"  제외: {', '.join(s.excluded)}")
    _out(f"일치도: {stats_line(calibration.question_agreement(store, q))}")
    for ev in calibration.list_evals(store, q)[:3]:
        _out(f"  측정 {ev.created_at:%m-%d %H:%M}  전 {stats_line(ev.before)}")
        _out(f"  {' ' * 15}후 {stats_line(ev.after)}")
    return 0


def _cmd_calibrate(args: argparse.Namespace, svc: Any) -> int:
    store = ExamStore(svc.workspace, args.exam)
    q = args.q[0] if args.q else store.exam().questions[0].number

    def progress(done: int, total: int, msg: str) -> None:
        if done < total:
            sys.stderr.write(f"  … {msg}\n")

    ev = workflow.evaluate_calibration(svc, store, q, k=args.k, progress=progress)
    for c in ev.cases:
        if c.error:
            _out(f"  {c.student_id}: 실패 — {c.error[:120]}")
            continue
        used = ", ".join(c.examples_used) or "-"
        _out(
            f"  {c.student_id}: 강사 {c.teacher_total:g} · 예시 없이 {c.before_total:g} · "
            f"예시와 함께 {c.after_total:g}  (예시: {used})"
        )
    _out(f"보정 전: {stats_line(ev.before)}")
    _out(f"보정 후: {stats_line(ev.after)}")
    return 0


def _cmd_suggest(args: argparse.Namespace, svc: Any) -> int:
    store = ExamStore(svc.workspace, args.exam)
    q = args.q[0] if args.q else store.exam().questions[0].number
    run = workflow.suggest_rubric_changes(svc, store, q)
    s = run.suggestion
    _out(f"근거 사례: {', '.join(run.cases)}")
    for d in s.transcript_doubts:
        _out(f"  ⚠ 판독 확인 필요 (루브릭에 반영 안 함): {d}")
    if not s.changes:
        _out(f"바꿀 것 없음: {s.no_change_reason}")
        return 0
    for c in s.changes:
        _out(f"- [{c.kind}] {c.item_id or '새 규정'}: {c.summary}  (근거: {', '.join(c.evidence)})")
    assert s.revised is not None
    _out("\n" + rubric_text(s.revised, store.exam().question(q).parts))
    if args.apply:
        old = store.rubric(q)
        sol = store.solution(q)
        assert old is not None and sol is not None
        r = edit_rubric(old, s.revised, store.exam().question(q), sol)
        store.save_rubric(r)
        _out(
            f"\n루브릭 v{r.version} 초안으로 저장 — `exam approve`로 다시 승인해야 채점에 쓰입니다."
        )
    return 0


def _cmd_teacher_scores(args: argparse.Namespace, svc: Any) -> int:
    from pathlib import Path

    from dawoni.grading.pipeline import PipelineError

    store = ExamStore(svc.workspace, args.exam)
    rows: list[workflow.TeacherScore] = []
    if args.file:
        rows += workflow.parse_teacher_csv(Path(args.file).read_text(encoding="utf-8-sig"))
    if args.part:
        if not args.student or not args.q:
            raise PipelineError("--part에는 --student와 -q가 필요합니다")
        notes: dict[str, str] = {}
        for n in list[str](args.note or []):
            if "=" in n:
                k, v = n.split("=", 1)
                notes[k.strip()] = v.strip()
        for spec in list[str](args.part):
            label, _, value = spec.rpartition("=")
            rows.append(
                workflow.TeacherScore(
                    args.student, args.q[0], label.strip(), float(value), notes.get(label, "")
                )
            )
    if not rows:
        raise PipelineError("넣을 점수가 없습니다 (CSV 파일 또는 --part)")

    def progress(done: int, total: int, msg: str) -> None:
        if done < total:
            sys.stderr.write(f"  … {msg}\n")

    res = workflow.import_teacher_scores(svc, store, rows, by=args.by, progress=progress)
    for g in res.saved:
        r = store.rubric(g.question_number)
        assert r is not None
        ai = {j.rubric_item_id: j.awarded for j in (g.ai_judgements or g.judgements)}
        cells: list[str] = []
        for label, v in g.teacher_part_scores.items():
            before = sum(ai.get(i.id, 0) for i in r.part_items(label))
            cells.append(
                f"{label or '전체'} {v:g}" + ("" if abs(before - v) < 1e-9 else f" (AI {before:g})")
            )
        _out(f"{g.student_id} 문항 {g.question_number}: 강사 {g.total:g}점  " + ", ".join(cells))
    if res.graded_first:
        _out(f"AI 채점부터 한 답안: {', '.join(res.graded_first)}")
    for f in res.failed:
        _out(f"  ✗ {f.student} 문항 {f.question}: {f.reason}")
    _out("AI와 다른 소문항은 기준 예시가 되고, `exam suggest-rubric`의 근거 사례가 됩니다.")
    return 1 if res.failed else 0


def register(es: Any, with_q: Any, wrap: Any, svc_of: Any) -> None:
    def bind(fn: Any) -> Any:
        def run(a: argparse.Namespace) -> int:
            return int(fn(a, svc_of(a)))

        return wrap(run)

    c = with_q(es.add_parser("calibration", help="기준 예시·일치도 보기·설정"))
    c.add_argument("--pin", action="append", help="항상 넣을 예시 (학생)")
    c.add_argument("--unpin", action="append")
    c.add_argument("--exclude", action="append", help="예시로 쓰지 않을 답안 (학생)")
    c.add_argument("--include", action="append")
    c.add_argument("--examples", type=int, choices=range(0, 7), help="채점에 넣을 예시 수")
    c.add_argument("--off", action="store_true", help="이 문항은 기준 예시 없이 채점")
    c.add_argument("--on", action="store_true")
    c.set_defaults(func=bind(_cmd_calibration))

    e = with_q(es.add_parser("calibrate", help="보정 효과 측정 (강사 확정 답안 다시 채점)"))
    e.add_argument("--k", type=int, help="예시 수")
    e.set_defaults(func=bind(_cmd_calibrate))

    s = with_q(es.add_parser("suggest-rubric", help="강사 수정 사례 → 루브릭 개선안"))
    s.add_argument("--apply", action="store_true", help="새 루브릭 초안으로 저장 (재승인 필요)")
    s.set_defaults(func=bind(_cmd_suggest))

    t = with_q(es.add_parser("teacher-scores", help="강사가 직접 매긴 소문항 점수 넣기"))
    t.add_argument("file", nargs="?", help="CSV: 학생,문항,소문항,점수[,이유]")
    t.add_argument("--student")
    t.add_argument("--part", action="append", help='소문항=점수 (예: "(1)①=2", 소문항 없으면 "=5")')
    t.add_argument(
        "--note", action="append", help='소문항=이유 (예: "(1)①=전류로만 반박해도 인정")'
    )
    t.add_argument("--by")
    t.set_defaults(func=bind(_cmd_teacher_scores))
