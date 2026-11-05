"""`dawoni exam calibration|calibrate|suggest-rubric …` — 강사 보정 (M8).

dawoni exam calibration <시험ID> -q 1 [--pin A] [--exclude D] [--examples 3] [--off|--on]
dawoni exam calibrate <시험ID> -q 1          # 보정 효과 측정 (예시 없이 / 예시와 함께)
dawoni exam suggest-rubric <시험ID> -q 1 [--apply]   # 강사 수정 사례 → 루브릭 개선안
dawoni exam teacher-scores <시험ID> 강사점수.csv [--by 김선생]   # 강사가 직접 매긴 소문항 점수
dawoni exam teacher-scores <시험ID> --student 홍길동 -q 2 --part "(1)①=2" [--note "(1)①=이유"]

손채점한 답안지 (M5): 판독해 둔 학생의 답안 쪽에서 빨간 펜 소문항 점수를 읽고, 강사가 확인한다.
dawoni exam read-marks <시험ID> [--student 홍길동 …] [--overwrite]
dawoni exam marks <시험ID> [홍길동] [-q 2 --part "(1)①=2"] [--confirm [--force] --by 김선생]
dawoni exam compare <시험ID> [-q 1] [--student …]      # 강사 소문항 점수 ↔ AI 채점 (소문항 일치도)
dawoni exam calibration <시험ID> -q 1 --finalize --by 김선생   # 루브릭 확정
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
    if args.finalize:
        from dawoni.grading import marks

        r = marks.finalize_rubric(store, q, by=args.by)
        _out(f"문항 {q}: 루브릭 v{r.version} 확정 — {r.finalized_note}")
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
        assert old is not None and (sol is not None or old.based_on_key)
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


def _marks_cells(parts: dict[str, float | None]) -> str:
    return ", ".join(f"{k or '전체'} {'?' if v is None else f'{v:g}'}" for k, v in parts.items())


def _cmd_read_marks(args: argparse.Namespace, svc: Any) -> int:
    from dawoni.grading import marks
    from dawoni.ocr.marks import problems

    store = ExamStore(svc.workspace, args.exam)

    def progress(done: int, total: int, msg: str) -> None:
        sys.stderr.write(f"  [{done}/{total}] {msg}\n")

    run = marks.read_teacher_marks(
        svc, store, students=args.student, overwrite=args.overwrite, progress=progress
    )
    exam = store.exam()
    for r in run.read:
        cells = " | ".join(f"{q}: {_marks_cells(p)}" for q, p in r.teacher_marks.items())
        _out(f"{r.student_id}: {cells}")
        for p in problems(r, exam):
            _out(f"  ⚠ {p}")
        for u in r.marks_uncertain:
            _out(f"  ? {u}")
    for k in run.skipped:
        _out(f"  건너뜀 {k.student}: {k.reason}")
    for f in run.failed:
        _out(f"  ✗ {f.student}: {f.reason[:160]}")
    _out("확인한 뒤 `exam marks <시험> <학생> --confirm`으로 확정하세요 (확정한 점수만 보정에 씀).")
    return 1 if run.failed else 0


def _cmd_marks(args: argparse.Namespace, svc: Any) -> int:
    from dawoni.grading import marks
    from dawoni.grading.pipeline import PipelineError
    from dawoni.ocr.marks import problems

    store = ExamStore(svc.workspace, args.exam)
    exam = store.exam()
    if args.part:
        if not args.student or not args.q:
            raise PipelineError("--part에는 학생과 -q가 필요합니다")
        parts: dict[str, float | None] = {}
        for spec in list[str](args.part):
            label, _, value = spec.rpartition("=")
            parts[label.strip()] = None if value.strip() in ("", "?") else float(value)
        marks.set_marks(store, args.student[0], args.q[0], parts)
    if args.confirm:
        targets = args.student or [r.student_id for r in store.student_records()
                                   if r.teacher_marks and not r.marks_confirmed]  # fmt: skip
        run = marks.confirm_marks(svc, store, targets, by=args.by, force=args.force)
        _out(f"확정: {', '.join(run.confirmed) or '없음'}")
        for b in run.blocked:
            _out(f"  ✗ {b.student}: {b.reason}")
        if run.left_out:
            _out(f"  승인된 루브릭이 없어 강사 채점으로 넣지 않은 문항: {', '.join(run.left_out)}")
        if run.imported is not None:
            for f in run.imported.failed:
                _out(f"  ✗ {f.student} 문항 {f.question}: {f.reason}")
        return 1 if run.blocked else 0
    wanted = set(args.student or [])
    for r in store.student_records():
        if not r.teacher_marks or (wanted and r.student_id not in wanted):
            continue
        state = "확정" if r.marks_confirmed else "확인 전"
        cells = " | ".join(f"{q}: {_marks_cells(p)}" for q, p in r.teacher_marks.items())
        _out(f"{r.student_id} [{state}]: {cells}")
        for p in problems(r, exam):
            _out(f"  ⚠ {p}")
    return 0


def _cmd_compare(args: argparse.Namespace, svc: Any) -> int:
    from dawoni.grading import marks

    store = ExamStore(svc.workspace, args.exam)
    if not any(r.teacher_marks for r in store.student_records()):
        from dawoni.cli_exam import cover_compare

        _out("소문항 강사 점수가 없어 표지의 문항 점수와 비교합니다 (소문항은 `exam read-marks`).")
        return cover_compare(args)
    qs = args.q or [q.number for q in store.exam().questions]
    stats = [marks.part_agreement(store, q, students=args.student) for q in qs]
    for s in stats:
        _out(f"문항 {s.question_number} (루브릭 v{s.rubric_version}): {marks.agreement_line(s)}")
        if s.per_part:
            _out("  다른 소문항: " + ", ".join(f"{k} {v}명" for k, v in s.per_part.items()))
        if s.stale:
            _out(f"  루브릭이 바뀌어 비교 못 함 (다시 채점): {', '.join(s.stale)}")
        if args.cases:
            for c in s.cases:
                diff = [f"{k} 강사 {v:g}/AI {c.ai.get(k, 0):g}" for k, v in c.teacher.items()
                        if abs(c.ai.get(k, 0) - v) > 1e-9]  # fmt: skip
                if diff:
                    _out(f"    {c.student_id}: " + ", ".join(diff))
    if len(stats) > 1:
        _out(f"합계: {marks.agreement_line(marks.combine(stats))}")
    return 0


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
    c.add_argument("--finalize", action="store_true", help="강사 손채점과 맞춰 본 루브릭 확정")
    c.add_argument("--by")
    c.set_defaults(func=bind(_cmd_calibration))

    rm = with_q(es.add_parser("read-marks", help="손채점 답안지의 빨간 펜 소문항 점수 읽기"))
    rm.add_argument("--student", action="append", help="학생 (여러 번, 없으면 전체)")
    rm.add_argument("--overwrite", action="store_true", help="이미 읽은 학생도 다시")
    rm.set_defaults(func=bind(_cmd_read_marks))

    mk = with_q(es.add_parser("marks", help="읽은 강사 점수 보기·고치기·확정"))
    mk.add_argument("student", nargs="*", help="학생 (없으면 전체)")
    mk.add_argument("--part", action="append", help='소문항=점수 (예: "(1)①=2", 모르면 "(1)①=?")')
    mk.add_argument("--confirm", action="store_true", help="확인한 점수를 강사 채점으로 확정")
    mk.add_argument("--force", action="store_true", help="표지 총점과 달라도 확정")
    mk.add_argument("--by")
    mk.set_defaults(func=bind(_cmd_marks))

    cp = with_q(
        es.add_parser("compare", help="강사 손채점 ↔ AI 채점 (소문항 점수, 없으면 표지 점수)")
    )
    cp.add_argument("--student", action="append", help="이 학생들만 (보정 전후 같은 학생으로)")
    cp.add_argument("--cases", action="store_true", help="다른 소문항을 학생마다")
    cp.set_defaults(func=bind(_cmd_compare))

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
