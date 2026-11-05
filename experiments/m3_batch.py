"""M3 실측: 24명 스캔 PDF 1개 → 학생별 판독본 HWPX, 한글에서 고친 내용 → 확정 판독문 (PLAN.md §13).

앱 모듈을 그대로 쓴다 (`workflow.scan_batch`, `confirm_from_files`). 작업공간·앱 데이터는
`experiments/out/m3/`(git 제외 — 학생 이름·답안이 들어 있음). 모델은 agy flash-medium(§15 결정).

    uv run python experiments/m3_batch.py scan [--sets 1-24] [--parallel 3]
    uv run --extra windows python experiments/m3_batch.py hancom-edit [--student 장우혁]
    uv run python experiments/m3_batch.py report

`scan`은 명부를 표지 기준값(`experiments/out/measure/reference/covers.json`, 사람이 본 이름·반)으로
먼저 넣어 명부 연결까지 잰다. 이미 판독한 학생은 건너뛴다 (중간에 멈춰도 다시 하면 이어서).

`hancom-edit`은 한글(COM)로 학생 판독본 하나를 열어 강사처럼 한 곳을 고쳐 저장하고(찾아 바꾸기),
다른 하나는 고치지 않고 한글로 다시 저장만 한 뒤 확인 완료를 돌려 확정 판독문에 반영됐는지 본다.
한글이 '접근 허용' 창을 띄우면 [모두 허용]을 누른다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from dawoni.config import AGENT_NAMES, TIER_NAMES, AppConfig, TierModel, save_config
from dawoni.export.exam_files import exam_dir, is_edited, student_file
from dawoni.grading import workflow
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.models.grading import Exam
from dawoni.models.scope import ScopeProfile
from dawoni.paths import WORKSPACE_MARKER, AppPaths
from dawoni.services import Services, open_services
from dawoni.students import Roster
from dawoni.workspace import create_workspace

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments" / "out" / "m3"
SCAN_PDF = ROOT / "real_data" / "20261028172150.pdf"
REFERENCE = ROOT / "experiments" / "out" / "measure" / "reference" / "covers.json"
DAWONI_WS = Path(r"D:\Downloads\Dawoni-windows\temp")
DAWONI_EXAM = DAWONI_WS / "exams" / "exam-01a19b388c2a75f58e8cc7ffe047c0e2"
MODEL = "gemini-3.8-flash-medium"
PER_STUDENT = 3


def services(parallel: int = 3) -> Services:
    paths = AppPaths(OUT / "app")
    paths.ensure()
    ws_root = OUT / "ws"
    if not (ws_root / WORKSPACE_MARKER).exists():
        create_workspace(ws_root)
    cfg = AppConfig()
    cfg.default_agent = "agy"
    cfg.auto_fallback = False
    for name in AGENT_NAMES:
        cfg.agents[name].enabled = name == "agy"
    cfg.tiers["agy"] = {t: TierModel(model=MODEL) for t in TIER_NAMES}
    cfg.ocr.cross_check = False
    cfg.parallel_students = parallel
    cfg.workspace = ws_root
    save_config(paths, cfg)
    return open_services(ws_root, paths=paths)


def exam_store(svc: Services) -> ExamStore:
    exam = Exam.model_validate_json((DAWONI_EXAM / "exam.json").read_text(encoding="utf-8"))
    store = ExamStore(svc.workspace, exam.id)
    if not (store.root / "exam.json").exists():
        scope_file = DAWONI_WS / "scopes" / f"{exam.scope_id}.json"
        ScopeStore(svc.workspace).save(
            ScopeProfile.model_validate_json(scope_file.read_text(encoding="utf-8"))
        )
        store = ExamStore.create(svc.workspace, exam)
    return store


def seed_roster(svc: Services) -> int:
    """표지 기준값의 이름·반을 명부로 (사람이 봐도 애매해 null인 이름은 뺀다 — 명부에 없음으로 나온다)."""
    ref: dict[str, Any] = json.loads(REFERENCE.read_text(encoding="utf-8"))
    rows: list[dict[str, str | None]] = [
        {"name": v["name"], "class_name": v.get("class_name"), "school": v.get("school")}
        for k, v in ref.items()
        if k.isdigit() and v.get("name")
    ]
    return len(Roster(svc.workspace).import_rows(rows).added)


def _progress(done: int, total: int, message: str) -> None:
    print(f"  [{time.strftime('%H:%M:%S')}] {message}", flush=True)


def cmd_scan(args: argparse.Namespace) -> int:
    svc = services(args.parallel)
    store = exam_store(svc)
    added = seed_roster(svc)
    print(f"명부 {len(Roster(svc.workspace).all())}명 (이번에 {added}명 넣음)", flush=True)
    sets = None
    if args.sets:
        a, _, b = args.sets.partition("-")
        sets = list(range(int(a), int(b or a) + 1))
    t0 = time.perf_counter()
    res = workflow.scan_batch(
        svc, store, SCAN_PDF, per_student=PER_STUDENT, only_sets=sets, progress=_progress
    )
    took = time.perf_counter() - t0
    print(f"\n{len(res.students)}명 판독, 건너뜀 {len(res.skipped)}, 실패 {len(res.failed)}, "
          f"{took:.0f}초")  # fmt: skip
    for f in res.failed:
        print(f"  ✗ {f.student}: {f.reason[:300]}")
    for w in res.warnings:
        print(f"  ! {w}")
    print(f"판독본 {len(res.export.written)}개 → {exam_dir(svc.workspace, store.exam())}")
    for w in res.export.warnings:
        print(f"  ! {w}")
    return cmd_report(args)


def cmd_report(args: argparse.Namespace) -> int:
    svc = services()
    store = exam_store(svc)
    exam = store.exam()
    folder = exam_dir(svc.workspace, exam)
    files = sorted(folder.glob("*.hwpx")) if folder.is_dir() else []
    students = [f for f in files if not f.name.startswith("(")]
    bundles = [f for f in files if f.name.startswith("(")]
    records = store.student_records()
    with_any = {s for s in store.students()}
    per_student = Counter(f.name.rsplit("_", 1)[0] for f in students)
    print(f"\n# M3 실측 — {exam.title}")
    print(f"- 학생 기록 {len(records)}명, 판독문이 있는 학생 {len(with_any)}명")
    by_q = Counter(f.stem.rsplit("_", 1)[1] for f in students)
    print(f"- 학생 판독본 HWPX {len(students)}개 (학생 {len(per_student)}명; 문항별 "
          + ", ".join(f"{q}번 {by_q.get(q, 0)}"
                      for q in [x.number for x in exam.questions])
          + f"), 반별 묶음 {len(bundles)}개")  # fmt: skip
    print(f"- 명부 연결 {sum(r.roster_id is not None for r in records)}명, 명부와 못 맞춤 "
          f"{sum(r.roster_note is not None for r in records)}명, 이름 확인 대기 "
          f"{sum(r.needs_rename for r in records)}명")  # fmt: skip
    for r in records:
        if r.roster_note or r.needs_rename:
            print(f"  - {r.student_id} ({r.class_name}, 표지 '{r.cover_name}'): "
                  f"{r.roster_note or '같은 반 동명이인'}")  # fmt: skip
    unreadable = sum(
        t.unreadable for s in with_any for q in exam.questions
        if (t := store.transcript(s, q.number)) is not None
    )  # fmt: skip
    print(f"- [판독불가] {unreadable}곳")
    calls = [json.loads(x) for x in (svc.paths.logs / "calls.jsonl").read_text(encoding="utf-8")
             .splitlines() if x.strip()] if (svc.paths.logs / "calls.jsonl").exists() else []  # fmt: skip
    for task in ("read_cover", "transcribe_answer"):
        cs = [c for c in calls if c["task"] == task]
        if not cs:
            continue
        ok = [c for c in cs if c["ok"]]
        secs = [c["duration_s"] for c in cs if c["duration_s"] is not None]  # 시간 초과는 None
        toks = [c["total_tokens"] or 0 for c in cs]
        print(f"- {task}: 호출 {len(cs)} (성공 {len(ok)}, 재요청 {len(cs) - len(ok)}), "
              f"시간 중앙값 {statistics.median(secs):.0f}초, 토큰 중앙값 "
              f"{statistics.median(toks) / 1000:.1f}k, 합 {sum(toks) / 1000:.0f}k")  # fmt: skip
    return 0


# ---------------------------------------------------------------- 한글에서 고치기


def _hancom() -> Any:
    sys.path.insert(0, str(ROOT / "experiments"))
    from hwpx_probe import hancom  # pyright: ignore[reportMissingImports, reportUnknownVariableType]

    return hancom()  # pyright: ignore[reportUnknownVariableType]


def _replace_all(hwp: Any, find: str, repl: str) -> None:
    pset = hwp.HParameterSet.HFindReplace
    hwp.HAction.GetDefault("AllReplace", pset.HSet)
    pset.FindString = find
    pset.ReplaceString = repl
    pset.IgnoreMessage = 1
    pset.Direction = 2  # 문서 전체
    hwp.HAction.Execute("AllReplace", pset.HSet)


def cmd_hancom_edit(args: argparse.Namespace) -> int:
    svc = services()
    store = exam_store(svc)
    q = store.exam().questions[0].number
    candidates = [s for s in store.students() if student_file(svc.workspace, store, s, q).is_file()]
    edit_sid = args.student or candidates[0]
    keep_sid = next(s for s in candidates if s != edit_sid)
    t = store.transcript(edit_sid, q)
    assert t is not None
    # 강사가 고칠 곳: 식 밖 글에서 두 글자 이상 한글 낱말 하나 (판독본에서 한 번만 나오는 것)
    import re

    words = [w for line in t.lines for w in re.findall(r"[가-힣]{2,}", re.sub(r"\\\(.+?\\\)", "",
             line.text))]  # fmt: skip
    counts = Counter(words)
    target = next(w for w in words if counts[w] == 1)
    fixed = target + "(강사)"
    edit_path = student_file(svc.workspace, store, edit_sid, q)
    keep_path = student_file(svc.workspace, store, keep_sid, q)
    print(f"고칠 파일: {edit_path.name} — '{target}' → '{fixed}'")
    print(f"다시 저장만 할 파일: {keep_path.name}")
    with _hancom() as hwp:
        for path, change in ((edit_path, True), (keep_path, False)):
            print(f"  한글로 여는 중: {path.name} — '접근 허용' 창이 뜨면 [모두 허용]", flush=True)
            if not hwp.Open(str(path), "HWPX", "forceopen:true"):
                raise RuntimeError(f"한글이 열지 못함: {path}")
            if change:
                _replace_all(hwp, target, fixed)
            hwp.SaveAs(str(path), "HWPX", "")
            hwp.Clear(1)
    print(f"한글 저장 뒤 고친 파일로 보임: {edit_path.name}={is_edited(store, edit_path)}, "
          f"{keep_path.name}={is_edited(store, keep_path)}")  # fmt: skip
    run = workflow.confirm_from_files(svc, store, students=[edit_sid, keep_sid], only=[q],
                                      by="실측")  # fmt: skip
    for c in run.confirmed:
        print(f"✓ {c.transcript.student_id}: 확정, 고친 줄 {c.changed}")
    for c in run.waiting:
        print(f"… {c.transcript.student_id}: [판독불가] {c.transcript.unreadable}곳 남음")
    for p in run.problems:
        print(f"✗ {p.student}: {p.reason}")
    after = store.transcript(edit_sid, q)
    assert after is not None
    hit = [x.text for x in after.lines if fixed in x.text]
    print(f"확정 판독문에 '{fixed}' 반영: {'예' if hit else '아니오'} {hit[:1]}")
    kept = store.transcript(keep_sid, q)
    keep_changed = [c.changed for c in run.confirmed if c.transcript.student_id == keep_sid]
    print(f"다시 저장만 한 파일: 확정 {kept is not None and kept.confirmed}, "
          f"고친 줄로 읽힌 것 {keep_changed}")  # fmt: skip
    return 0 if hit and keep_changed == [0] else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--sets")
    s.add_argument("--parallel", type=int, default=3)
    s.set_defaults(func=cmd_scan)
    sub.add_parser("report").set_defaults(func=cmd_report)
    h = sub.add_parser("hancom-edit")
    h.add_argument("--student")
    h.set_defaults(func=cmd_hancom_edit)
    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
