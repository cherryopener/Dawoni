"""M5 실측: 손채점 5명으로 보정한 뒤 나머지 19명의 소문항 일치도가 보정 전보다 오르는가 (PLAN.md §13).

    uv run python experiments/m5_calibrate.py setup        # M3 작업공간 복사 + M4 flash-medium 채점
    uv run python experiments/m5_calibrate.py marks        # 24명 빨간 펜 소문항 점수 읽기 (앱 모듈)
    uv run python experiments/m5_calibrate.py crops        # 점수 칸만 잘라 붙인 그림 (사람이 대조)
    uv run python experiments/m5_calibrate.py before       # 보정 전 소문항 일치도 (flash·argon)
    uv run python experiments/m5_calibrate.py calibrate    # 5명 확정 → 개선 제안(calibrator) → 적용
    uv run python experiments/m5_calibrate.py after [--model gemini-4-argon-low --label argon-low]
    uv run python experiments/m5_calibrate.py report

- 작업공간: `experiments/out/m5/`(git 제외 — 학생 이름·답안). M3 판독문(강사 확인 전)·옛 다워니 초안 루브릭·
  M4 채점(flash-medium: `out/m4/flash-medium-grades`, argon-low: M3 작업공간에 남은 것)을 그대로 쓴다.
- 손채점 5명 = 스캔 순서로 앞 5명 (강사가 먼저 채점한 몇 장을 넣는 경우). 그 5명의 판독문은 강사가
  확인한 것으로 친다(고친 것 없이) — 실제로는 강사가 판독본을 확인한 뒤 확정한다.
- 기준값: 강사 소문항 점수는 앱이 읽은 것을 사람이 점수 칸 그림(`crops`)과 대조해 고친 것
  (`out/m5/marks_reference.json`). 표지 총점과 합이 맞는지도 본다.
- 보정 전후 모두 같은 19명 · 1·2번 (3번은 인쇄 해설지가 틀려 뺌 — M4와 같음).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m3_batch import DAWONI_EXAM, MODEL  # pyright: ignore[reportPrivateUsage]

from dawoni.config import AGENT_NAMES, TIER_NAMES, AppConfig, TierModel, save_config
from dawoni.context.calllog import CallLog
from dawoni.grading import marks, workflow
from dawoni.grading.pipeline import approve_rubric, edit_rubric
from dawoni.grading.store import ExamStore
from dawoni.models.answer_key import AnswerKey
from dawoni.models.calibration import PartAgreement
from dawoni.models.grading import Exam, GradeResult, Rubric
from dawoni.ocr.marks import problems
from dawoni.paths import AppPaths
from dawoni.services import Services, open_services

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments" / "out" / "m5"
M3_WS = ROOT / "experiments" / "out" / "m3" / "ws"
FLASH_GRADES = ROOT / "experiments" / "out" / "m4" / "flash-medium-grades"
REFERENCE = OUT / "marks_reference.json"
QUESTIONS = ["1", "2"]
N_CALIB = 5
STRONG = "gemini-4-argon-low"


def _progress(done: int, total: int, message: str) -> None:
    print(f"  [{done}/{total}] {message}", flush=True)


def services(parallel: int = 4) -> Services:
    paths = AppPaths(OUT / "app")
    paths.ensure()
    cfg = AppConfig()
    cfg.default_agent = "agy"
    cfg.auto_fallback = False
    for name in AGENT_NAMES:
        cfg.agents[name].enabled = name == "agy"
    cfg.tiers["agy"] = {t: TierModel(model=MODEL) for t in TIER_NAMES}
    cfg.tiers["agy"]["strong"] = TierModel(model=STRONG)  # §15: strong = argon-low (calibrator)
    cfg.ocr.cross_check = False
    cfg.parallel_students = parallel
    cfg.parallel_grading = parallel
    cfg.workspace = OUT / "ws"
    save_config(paths, cfg)
    return open_services(OUT / "ws", paths=paths)


def store_of(svc: Services) -> ExamStore:
    exam = Exam.model_validate_json((DAWONI_EXAM / "exam.json").read_text(encoding="utf-8"))
    return ExamStore(svc.workspace, exam.id)


def calib_students(store: ExamStore) -> list[str]:
    def set_no(source: str) -> int:
        return int(source.rsplit(" ", 1)[1].split("-")[0])

    recs = sorted(store.student_records(), key=lambda r: set_no(r.source))
    return [r.student_id for r in recs[:N_CALIB]]


def holdout(store: ExamStore) -> list[str]:
    calib = set(calib_students(store))
    return [r.student_id for r in store.student_records() if r.student_id not in calib]


def _dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


# ---------------------------------------------------------------- setup


def cmd_setup(args: argparse.Namespace) -> int:
    ws = OUT / "ws"
    if ws.exists() and not args.force:
        print(f"이미 있음: {ws} (--force로 다시)")
        return 0
    if ws.exists():
        shutil.rmtree(ws)
    shutil.copytree(M3_WS, ws, ignore=shutil.ignore_patterns("exports"))
    svc = services()
    store = store_of(svc)
    (store.root / "exports.json").unlink(missing_ok=True)
    argon = OUT / "argon-before-grades"
    if argon.exists():
        shutil.rmtree(argon)
    shutil.move(store.root / "grades", argon)  # M4 argon-low 채점 (M3 작업공간에 남은 것)
    shutil.copytree(FLASH_GRADES, store.root / "grades")
    shutil.copytree(store.root / "rubrics", OUT / "before-rubrics", dirs_exist_ok=True)
    for q in QUESTIONS:  # 해설지 (루브릭이 해설지 기준 — 개선 제안의 자료)
        key = AnswerKey.model_validate_json((DAWONI_EXAM / "keys" / f"{q}.json").read_text("utf-8"))
        store.save_key(key)
    for q in QUESTIONS:
        r = store.rubric(q)
        print(f"문항 {q}: 루브릭 v{r.version if r else '-'} {r.status if r else '-'}")
    print(f"학생 {len(store.student_records())}명, 손채점 {calib_students(store)}")
    return 0


# ---------------------------------------------------------------- 빨간 펜 읽기


def cmd_marks(args: argparse.Namespace) -> int:
    svc = services(args.parallel)
    store = store_of(svc)
    before = len(CallLog(svc.paths.logs / "calls.jsonl").read())
    t0 = time.monotonic()
    run = marks.read_teacher_marks(svc, store, overwrite=args.overwrite, progress=_progress)
    wall = time.monotonic() - t0
    calls = [c for c in CallLog(svc.paths.logs / "calls.jsonl").read()[before:]
             if c.get("task") == "read_marks"]  # fmt: skip
    exam = store.exam()
    rows = [
        {"student": r.student_id, "marks": r.teacher_marks, "cover": r.teacher_scores,
         "problems": problems(r, exam), "uncertain": r.marks_uncertain}
        for r in store.student_records()
    ]  # fmt: skip
    _dump(OUT / "marks_run.json", {"wall_s": wall, "failed": [vars(f) for f in run.failed],
                                   "rows": rows, "calls": calls})  # fmt: skip
    for row in rows:
        print(row["student"], row["marks"], row["problems"] or "")
    print(f"읽음 {len(run.read)}, 실패 {len(run.failed)}, {wall:.0f}초, 호출 {len(calls)}")
    return 1 if run.failed else 0


def cmd_crops(args: argparse.Namespace) -> int:
    """점수 칸(배점·점수 열)만 잘라 학생 6명씩 한 장으로 — 사람이 기준값을 만들 때 본다."""
    from PIL import Image, ImageDraw

    svc = services()
    store = store_of(svc)
    recs = sorted(
        store.student_records(), key=lambda r: int(r.source.rsplit(" ", 1)[1].split("-")[0])
    )
    strips: list[tuple[str, Image.Image]] = []
    for r in recs:
        for page in ("page_02.png", "page_03.png"):
            p = store.scan_dir(r.student_id) / page
            with Image.open(p) as im:
                w, h = im.size
                crop = im.crop((int(w * 0.83), 0, w, h)).convert("RGB")
            crop.thumbnail((10_000, 1100))
            strips.append((f"{r.student_id} {page[5:7]}", crop))
    out = OUT / "crops"
    out.mkdir(parents=True, exist_ok=True)
    per = 12
    for k in range(0, len(strips), per):
        chunk = strips[k : k + per]
        sw = max(s.width for _, s in chunk)
        sheet = Image.new("RGB", (sw * len(chunk), 1140), "white")
        draw = ImageDraw.Draw(sheet)
        for i, (name, s) in enumerate(chunk):
            sheet.paste(s, (i * sw, 40))
            draw.text((i * sw + 4, 4), name.encode("ascii", "replace").decode(), fill="blue")
        sheet.save(out / f"sheet_{k // per + 1}.png")
        print(out / f"sheet_{k // per + 1}.png", [n for n, _ in chunk])
    return 0


# ---------------------------------------------------------------- 일치도


def _apply_reference(store: ExamStore) -> None:
    """사람이 대조한 기준값으로 읽은 점수를 덮는다 (비교 기준을 판독 오류와 떼어 놓으려고)."""
    if not REFERENCE.exists():
        return
    ref: dict[str, dict[str, dict[str, float]]] = json.loads(REFERENCE.read_text(encoding="utf-8"))
    for sid, qs in ref.items():
        if sid.startswith("_"):
            continue
        rec = store.student_record(sid)
        if rec is None:
            continue
        merged = {q: {**rec.teacher_marks.get(q, {}), **parts} for q, parts in qs.items()}
        if any(rec.teacher_marks.get(q) != v for q, v in merged.items()):
            store.save_student(rec.model_copy(update={
                "teacher_marks": {**rec.teacher_marks, **merged},
                "marks_confirmed": rec.marks_confirmed,
            }))  # fmt: skip


def _agreement_dir(store: ExamStore, grades: Path, rubrics: Path, who: list[str]) -> PartAgreement:
    """다른 폴더에 둔 채점(보정 전 argon 등)을 그때의 루브릭으로 소문항 비교."""
    exam = store.exam()
    stats: list[PartAgreement] = []
    for q in QUESTIONS:
        rubric = Rubric.model_validate_json((rubrics / f"{q}.json").read_text(encoding="utf-8"))
        s = PartAgreement(question_number=q, rubric_version=rubric.version)
        for sid in who:
            rec = store.student_record(sid)
            p = grades / sid / f"{q}.json"
            if rec is None or not p.exists():
                continue
            g = GradeResult.model_validate_json(p.read_text(encoding="utf-8"))
            ai = marks.ai_part_scores(g, rubric, exam)
            teacher = {k: v for k, v in rec.teacher_marks.get(q, {}).items() if v is not None}
            if ai is None or not teacher:
                continue
            s.n += 1
            for part, tv in teacher.items():
                d = ai.get(part, 0.0) - tv
                s.parts += 1
                s.same += abs(d) < 1e-9
                s.within1 += abs(d) <= 1 + 1e-9
                s.ai_lower += d < -1e-9
                s.ai_higher += d > 1e-9
                s.diff_sum += d
                s.abs_diff_sum += abs(d)
                if abs(d) >= 1e-9:
                    s.per_part[part] = s.per_part.get(part, 0) + 1
        stats.append(s)
    return marks.combine(stats)


def _summary(s: PartAgreement) -> dict[str, Any]:
    return {**s.model_dump(exclude={"cases"}), "rate": s.rate, "line": marks.agreement_line(s)}


def cmd_before(args: argparse.Namespace) -> int:
    svc = services()
    store = store_of(svc)
    _apply_reference(store)
    who = holdout(store)
    everyone = [r.student_id for r in store.student_records()]
    res: dict[str, Any] = {
        "holdout": who,
        "flash_holdout": _summary(_agreement_dir(store, FLASH_GRADES, OUT / "before-rubrics", who)),
        "flash_all": _summary(
            _agreement_dir(store, FLASH_GRADES, OUT / "before-rubrics", everyone)
        ),
        "argon_holdout": _summary(
            _agreement_dir(store, OUT / "argon-before-grades", OUT / "before-rubrics", who)
        ),
        "argon_all": _summary(
            _agreement_dir(store, OUT / "argon-before-grades", OUT / "before-rubrics", everyone)
        ),
    }
    _dump(OUT / "before.json", res)
    for k, v in res.items():
        if k != "holdout":
            print(f"{k}: {v['line']}  {v['per_part']}")
    return 0


# ---------------------------------------------------------------- 보정


def cmd_calibrate(args: argparse.Namespace) -> int:
    svc = services()
    store = store_of(svc)
    _apply_reference(store)
    calib = calib_students(store)
    print(f"손채점 {calib}")
    for sid in calib:  # 강사가 판독본을 확인한 것으로 (고친 것 없이)
        for q in QUESTIONS:
            t = store.transcript(sid, q)
            if t is not None and not t.confirmed:
                store.save_transcript(t.model_copy(update={"confirmed": True,
                                                           "confirmed_by": "M5 실측"}))  # fmt: skip
    before_calls = len(CallLog(svc.paths.logs / "calls.jsonl").read())
    run = marks.confirm_marks(svc, store, calib, by="M5 실측", force=True, progress=_progress)
    print(
        f"확정 {run.confirmed}, 막힘 {[vars(b) for b in run.blocked]}, 루브릭 없음 {run.left_out}"
    )
    if run.imported is not None:
        for f in run.imported.failed:
            print(f"  ✗ {f.student} {f.question}: {f.reason}")
    result: dict[str, Any] = {"calib": calib, "suggestions": {}}
    for q in QUESTIONS:
        print(f"문항 {q}: 개선 제안 (calibrator)")
        s = workflow.suggest_rubric_changes(svc, store, q)
        result["suggestions"][q] = {"cases": s.cases, **s.suggestion.model_dump(mode="json")}
        for c in s.suggestion.changes:
            print(f"  - [{c.kind}] {c.item_id or '새 규정'}: {c.summary} ({', '.join(c.evidence)})")
        for d in s.suggestion.transcript_doubts:
            print(f"  ⚠ 판독 의심: {d}")
        if s.suggestion.revised is None:
            print(f"  바꿀 것 없음: {s.suggestion.no_change_reason}")
            continue
        old = store.rubric(q)
        assert old is not None
        new = edit_rubric(old, s.suggestion.revised, store.exam().question(q), store.solution(q))
        store.save_rubric(approve_rubric(new, "M5 실측 (제안 전부 적용)"))
        print(f"  루브릭 v{old.version} → v{new.version} 승인")
    # 루브릭 항목 구조가 바뀌었으면 손채점 5명을 새 루브릭으로 다시 (AI 채점 → 강사 소문항 점수)
    rows: list[workflow.TeacherScore] = []
    for sid in calib:
        rec = store.student_record(sid)
        assert rec is not None
        for q in QUESTIONS:
            g, r = store.grade(sid, q), store.rubric(q)
            if g is not None and r is not None and g.rubric_version != r.version:
                rows += [workflow.TeacherScore(sid, q, p, v or 0.0)
                         for p, v in rec.teacher_marks.get(q, {}).items()]  # fmt: skip
    if rows:
        imp = workflow.import_teacher_scores(svc, store, rows, by="M5 실측", progress=_progress)
        print(f"새 루브릭으로 다시 넣음 {len(imp.saved)}건, 실패 {[vars(f) for f in imp.failed]}")
    result["calls"] = CallLog(svc.paths.logs / "calls.jsonl").read()[before_calls:]
    _dump(OUT / "calibrate.json", result)
    return 0


def cmd_after(args: argparse.Namespace) -> int:
    svc = services(args.parallel)
    if args.model and args.model.startswith("claude"):  # 2026-11-05: claude와 나란히 잰다
        cfg = svc.config
        cfg.default_agent = "claude"
        for name in AGENT_NAMES:
            cfg.agents[name].enabled = name == "claude"
        cfg.tiers["claude"] = {t: TierModel(model=args.model, effort="medium") for t in TIER_NAMES}
    elif args.model:
        svc.config.tiers["agy"]["strong"] = TierModel(model=args.model)
        svc.config.role_tiers["grader"] = "strong"
    store = store_of(svc)
    _apply_reference(store)
    who = holdout(store)
    out = OUT / (args.label or "flash-medium")
    before_calls = len(CallLog(svc.paths.logs / "calls.jsonl").read())
    t0 = time.monotonic()
    run = workflow.grade_exam(
        svc, store, students=who, only=QUESTIONS, regrade=True, use_examples=not args.no_examples,
        progress=_progress,
    )  # fmt: skip
    wall = time.monotonic() - t0
    calls = CallLog(svc.paths.logs / "calls.jsonl").read()[before_calls:]
    stats = [marks.part_agreement(store, q, students=who) for q in QUESTIONS]
    total = marks.combine(stats)
    used = sorted({e for sid in who for q in QUESTIONS
                   if (g := store.grade(sid, q)) for e in g.examples_used})  # fmt: skip
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(store.root / "grades", out / "grades")
    _dump(out / "after.json", {
        "wall_s": wall, "graded": len(run.graded), "failed": [vars(f) for f in run.failed],
        "examples_used": used, "total": _summary(total),
        "questions": {s.question_number: _summary(s) for s in stats},
        "cases": [c.model_dump() for s in stats for c in s.cases], "calls": calls,
    })  # fmt: skip
    for s in stats:
        print(f"문항 {s.question_number}: {marks.agreement_line(s)} {s.per_part}")
    print(f"합계: {marks.agreement_line(total)} · {wall:.0f}초 · 실패 {len(run.failed)}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    before = json.loads((OUT / "before.json").read_text(encoding="utf-8"))
    print("보정 전")
    for k in ("flash_holdout", "argon_holdout", "flash_all", "argon_all"):
        print(f"  {k}: {before[k]['line']}")
    for d in sorted(OUT.glob("*/after.json")):
        a = json.loads(d.read_text(encoding="utf-8"))
        tok = [c["total_tokens"] for c in a["calls"] if c.get("task") == "grade_answer"
               and c.get("total_tokens")]  # fmt: skip
        print(f"보정 후 ({d.parent.name}): {a['total']['line']}")
        print(
            f"  {a['wall_s']:.0f}초 · 채점 호출 {len(tok)} · 토큰 {sum(tok)} · 예시 {a['examples_used']}"
        )
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(required=True)
    s = sub.add_parser("setup")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_setup)
    m = sub.add_parser("marks")
    m.add_argument("--parallel", type=int, default=4)
    m.add_argument("--overwrite", action="store_true")
    m.set_defaults(func=cmd_marks)
    sub.add_parser("crops").set_defaults(func=cmd_crops)
    sub.add_parser("before").set_defaults(func=cmd_before)
    sub.add_parser("calibrate").set_defaults(func=cmd_calibrate)
    a = sub.add_parser("after")
    a.add_argument("--parallel", type=int, default=4)
    a.add_argument("--model")
    a.add_argument("--label")
    a.add_argument("--no-examples", action="store_true", help="루브릭 개선만 (기준 예시 없이)")
    a.set_defaults(func=cmd_after)
    sub.add_parser("report").set_defaults(func=cmd_report)
    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
