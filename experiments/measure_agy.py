"""실험 (M0): 역할마다 agy 후보 모델로 같은 실제 자료를 돌려 시간·토큰·재요청·정확도를 잰다 (PLAN.md §2-5).

다워니 엔진(실제 프롬프트, 검증·수리 루프, 호출 기록)을 그대로 쓰고 모델만 바꾼다. 자료는 옛 다워니
실측 시험(`real_data/test.pdf`: 시험지 + 해설지)과 그 시험의 학생 답안 스캔(`real_data/20261028172150.pdf`,
학생 1명 = 3쪽, 첫 쪽 표지), 옛 다워니가 읽고 강사가 확인한 시험·해설지(`--old-dawoni-exam` 폴더)다.

    uv run python experiments/measure_agy.py exam  --models gemini-3.8-flash-medium,gemini-4-argon-low
    uv run python experiments/measure_agy.py key   --models ...
    uv run python experiments/measure_agy.py rubric                # M2: 해설지 넓히기 호출 수
    uv run python experiments/measure_agy.py cover --models ... --sets 1-6
    uv run python experiments/measure_agy.py read  --models ... --sets 1-2
    uv run python experiments/measure_agy.py grade --models ... --sets 1-2 --transcripts <모델>
    uv run python experiments/measure_agy.py report       # 표 (README에 옮길 것)

결과는 `experiments/out/measure/`(git 제외 — 학생 이름·답안이 들어 있음). 같은 (역할, 모델, 대상)은
결과 파일이 있으면 건너뛴다 (`--redo`로 다시). claude 모델은 이 실험에서 돌리지 않는다 (2026-11-04
사용자 지시). 예외는 손글씨 판독(read) 한 번: 강사들이 "claude는 한글 손글씨를 잘 못 읽는다"고 하므로
체계적 판독에서는 어떤지 argon-low와 견준다 (2026-11-04 사용자 지시, claude 토큰을 아낌):

    uv run python experiments/measure_agy.py read --models claude-sonnet-5-5 --sets 1-6
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from dawoni.agents import AgentClient
from dawoni.config import AGENT_NAMES, TIER_NAMES, AgentName, AppConfig, TierModel
from dawoni.context import ContextEngine
from dawoni.context.calllog import CallLog
from dawoni.grading.pipeline import grade_answer
from dawoni.ingest.answer_key import _NUMBER as _NUM  # pyright: ignore[reportPrivateUsage]
from dawoni.ingest.answer_key import _numbers, extract_answer_key  # pyright: ignore[reportPrivateUsage]
from dawoni.ingest.exam import extract_exam
from dawoni.ingest.layout import analyze, find_key_pages
from dawoni.models.answer_key import AnswerKey
from dawoni.models.grading import Exam, Rubric, Transcript
from dawoni.models.scope import ScopeProfile
from dawoni.ocr import batch
from dawoni.ocr.transcribe import transcribe_student
from dawoni.paths import AppPaths

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments" / "out" / "measure"
CALLS = OUT / "calls.jsonl"
EXAM_PDF = ROOT / "real_data" / "test.pdf"
SCAN_PDF = ROOT / "real_data" / "20261028172150.pdf"
DAWONI_WS = Path(r"D:\Downloads\Dawoni-windows\temp")
DAWONI_EXAM = DAWONI_WS / "exams" / "exam-01a19b388c2a75f58e8cc7ffe047c0e2"
PER_STUDENT = 3
ROLE_OF = {"exam": "exam_reader", "key": "key_reader", "cover": "cover_reader",
           "read": "handwriting_reader", "grade": "grader"}  # fmt: skip
_print_lock = threading.Lock()


def say(*a: object) -> None:
    with _print_lock:
        print(*a, flush=True)


# ---------------------------------------------------------------- 준비


def engine_for(model: str) -> ContextEngine:
    """AI 하나만, 모든 등급이 이 모델. 한도에 걸려도 다른 AI로 넘어가지 않는다.
    claude 모델은 앱의 standard 등급처럼 effort medium. 실행 파일은 자동 탐지, 또는 DAWONI_CLAUDE_EXE."""
    agent: AgentName = "claude" if model.startswith("claude") else "agy"
    cfg = AppConfig()
    cfg.default_agent = agent
    cfg.auto_fallback = False
    for name in AGENT_NAMES:
        cfg.agents[name].enabled = name == agent
    tier = TierModel(model=model, effort="medium" if agent == "claude" else None)
    cfg.tiers[agent] = {t: tier for t in TIER_NAMES}
    if agent == "claude" and (exe := os.environ.get("DAWONI_CLAUDE_EXE")):
        cfg.agents["claude"].executable = exe
    paths = AppPaths(OUT / "app")
    paths.ensure()
    return ContextEngine(AgentClient(cfg, paths), calllog=CallLog(CALLS))


def load_exam() -> Exam:
    return Exam.model_validate_json((DAWONI_EXAM / "exam.json").read_text(encoding="utf-8"))


def load_scope(exam: Exam) -> ScopeProfile:
    p = DAWONI_WS / "scopes" / f"{exam.scope_id}.json"
    return ScopeProfile.model_validate_json(p.read_text(encoding="utf-8"))


def old_dawoni_keys() -> dict[str, AnswerKey]:
    out: dict[str, AnswerKey] = {}
    for p in sorted((DAWONI_EXAM / "keys").glob("*.json")):
        k = AnswerKey.model_validate_json(p.read_text(encoding="utf-8"))
        out[k.question_number] = k
    return out


def result_path(task: str, model: str, item: str) -> Path:
    return OUT / "results" / task / model / f"{item}.json"


def save(path: Path, data: Any, seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"seconds": round(seconds, 1), "data": data}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))["data"] if path.is_file() else None


def parse_sets(s: str) -> list[int]:
    out: list[int] = []
    for chunk in s.split(","):
        a, _, b = chunk.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def scan_images(set_no: int, pages: list[int], rotation: int) -> list[Path]:
    """학생 묶음 `set_no`(1부터)의 쪽(1 = 표지) 그림. 같은 그림은 다시 만들지 않는다."""
    plan = batch.plan_sets(batch.page_count(SCAN_PDF), PER_STUDENT, [set_no])[0]
    work = OUT / "scans" / f"set_{set_no:03d}_r{rotation}"
    want = [work / f"p{p}.png" for p in pages]
    if not all(p.is_file() for p in want):
        tmp = work / "tmp"
        imgs = batch.render_set(SCAN_PDF, [plan.pages[p - 1] for p in pages], tmp)
        batch.rotate(imgs, rotation)
        for src, dst in zip(imgs, want, strict=True):
            shutil.move(src, dst)
        shutil.rmtree(tmp, ignore_errors=True)
    return want


# ---------------------------------------------------------------- 역할별 작업


def run_exam(model: str, _: int) -> Any:
    keys = find_key_pages(EXAM_PDF)
    layout = analyze(EXAM_PDF, exclude=keys)
    work = OUT / "work" / "exam" / model
    shutil.rmtree(work, ignore_errors=True)  # 쪽 캐시를 쓰면 AI를 부르지 않는다
    exam = load_exam()
    scope = load_scope(exam)
    res = extract_exam(engine_for(model), layout, work, subject=scope.subject, level=scope.level)
    return [q.model_dump(mode="json") for q in res.questions]


def run_key(model: str, _: int) -> Any:
    exam = load_exam()
    work = OUT / "work" / "key" / model
    shutil.rmtree(work, ignore_errors=True)
    res = extract_answer_key(
        engine_for(model), EXAM_PDF, find_key_pages(EXAM_PDF), exam, work,
        level=load_scope(exam).level, cross=False,
    )  # fmt: skip
    return {"keys": [k.model_dump(mode="json") for k in res.keys], "warnings": res.warnings}


def run_cover(model: str, set_no: int) -> Any:
    exam = load_exam()
    img = scan_images(set_no, [1], 0)[0]
    return batch.read_cover(engine_for(model), exam, img, None).model_dump(mode="json")


def reference_rotation(set_no: int) -> int:
    ref = load_reference("covers").get(str(set_no))
    if ref and ref.get("rotation") is not None:
        return int(ref["rotation"])
    return 90


def run_read(model: str, set_no: int) -> Any:
    exam = load_exam()
    pages = scan_images(set_no, list(range(2, PER_STUDENT + 1)), reference_rotation(set_no))
    out = transcribe_student(
        engine_for(model), exam, load_scope(exam).level, f"set{set_no}", pages,
        page_refs=[p.name for p in pages],
    )  # fmt: skip
    return {
        "transcripts": {n: t.model_dump(mode="json") for n, t in out.transcripts.items()},
        "unassigned": [x.model_dump(mode="json") for x in out.unassigned],
        "notes": out.notes,
    }


def run_grade(model: str, set_no: int, *, source_model: str) -> Any:
    exam = load_exam()
    scope = load_scope(exam)
    read = load(result_path("read", source_model, f"set{set_no}"))
    if read is None:
        raise SystemExit(f"판독 결과가 없습니다: read {source_model} set{set_no}")
    engine = engine_for(model)
    out: dict[str, Any] = {}
    for q in exam.questions:
        rp = DAWONI_EXAM / "rubrics" / f"{q.number}.json"
        t = read["transcripts"].get(q.number)
        if not rp.is_file() or t is None:
            continue
        # 실험이라 강사 단계를 대신한다: 루브릭 승인·판독 확인 (실제 흐름은 강사가 한다)
        rubric = Rubric.model_validate_json(rp.read_text(encoding="utf-8"))
        rubric = rubric.model_copy(update={"status": "approved"})
        transcript = Transcript.model_validate(t).model_copy(update={"confirmed": True})
        g = grade_answer(engine, q, rubric, transcript, scope)
        out[q.number] = g.model_dump(mode="json")
    return out


def run_rubric(model: str, _: int) -> Any:
    """M2: 강사가 확인한 해설지로 해설지 넓히기 — 문항마다 호출 1번이어야 한다 (수리 포함해 셈).
    범위는 수준 프리셋(고1 물리: 미분·적분 … 금지)으로 — 옛 다워니 범위 프로필에는 금지 목록이 없다."""
    from dawoni.grading.pipeline import expand_key_rubric
    from dawoni.levels import parse_level, preset_scope

    exam = load_exam()
    scope = preset_scope(parse_level("고1 물리"))
    engine = engine_for(model)
    before = len(CallLog(CALLS).read())
    out: dict[str, Any] = {"rubrics": {}, "errors": {}}
    for q in exam.questions:
        key = old_dawoni_keys().get(q.number)
        if key is None:
            continue
        try:
            r = expand_key_rubric(engine, q, key, scope)
            out["rubrics"][q.number] = r.model_dump(mode="json")
        except Exception as e:  # 수리 끝에 실패해도 호출 수는 센다
            out["errors"][q.number] = f"{type(e).__name__}: {str(e)[:400]}"
    calls = [c for c in CallLog(CALLS).read()[before:] if c.get("task") == "expand_key_rubric"]
    out["calls"] = len(calls)
    out["questions"] = len(out["rubrics"]) + len(out["errors"])
    out["repairs"] = [c.get("errors") for c in calls if c.get("errors")]
    return out


TASKS: dict[str, Callable[[str, int], Any]] = {
    "exam": run_exam,
    "key": run_key,
    "cover": run_cover,
    "read": run_read,
    "rubric": run_rubric,
}


def item_name(task: str, set_no: int) -> str:
    return "all" if task in ("exam", "key", "rubric") else f"set{set_no}"


def execute(task: str, models: list[str], sets: list[int], *, redo: bool, workers: int,
            fn: Callable[[str, int], Any]) -> None:  # fmt: skip
    jobs = [
        (m, s) for m in models for s in (sets if task not in ("exam", "key", "rubric") else [0])
    ]

    def one(job: tuple[str, int]) -> None:
        model, set_no = job
        path = result_path(task, model, item_name(task, set_no))
        if path.is_file() and not redo:
            say(f"  건너뜀 (있음): {task} {model} {path.stem}")
            return
        say(f"  시작: {task} {model} {path.stem}")
        t0 = time.perf_counter()
        try:
            data = fn(model, set_no)
        except Exception as e:  # 실패도 결과다 (재요청 끝에 실패, 한도 등)
            data = {"error": f"{type(e).__name__}: {str(e)[:500]}"}
        save(path, data, time.perf_counter() - t0)
        say(f"  끝: {task} {model} {path.stem} {time.perf_counter() - t0:.0f}초"
            + (f" — {data['error'][:120]}" if isinstance(data, dict) and "error" in data else ""))  # fmt: skip

    # 같은 모델의 호출은 차례로 (한도), 다른 모델끼리 동시에
    by_model: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for j in jobs:
        by_model[j[0]].append(j)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda js: [one(j) for j in js], by_model.values()))


# ---------------------------------------------------------------- 기준값과 채점


def load_reference(name: str) -> dict[str, Any]:
    p = OUT / "reference" / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def score_exam(data: Any) -> tuple[float, str]:
    """옛 다워니 결과(강사 확인)와 문항 번호·소문항 표시·배점이 같은 비율."""
    want = {q.number: [(p.label, p.points) for p in q.parts] for q in load_exam().questions}
    got = {q["number"]: [(p["label"], p["points"]) for p in q.get("parts", [])] for q in data}
    total = sum(len(v) or 1 for v in want.values())
    ok = sum(1 for n, parts in want.items() for x in (parts or [None]) if x in got.get(n, []) or (
        x is None and n in got))  # fmt: skip
    return ok / total, f"소문항 {ok}/{total}"


TEACHER_FIXED_KEY = {("3", "(2)")}
"""인쇄된 해설지가 틀려 강사가 고친 소문항 (PLAN.md §1-3: θ·α를 바꿔 적음). 원본대로 읽은 모델이
맞으므로 판독 정확도에서 뺀다."""


def score_key(data: Any) -> tuple[float, str]:
    """강사가 확인한 옛 다워니 해설지와 비교 (§1-4: 숫자를 바꿔 읽는 실수).

    맞음 = 답의 숫자가 같고, 확인된 해설의 숫자가 하나도 빠지거나 바뀌지 않음. 모델이 해설을 더
    길게 옮겨 숫자가 더 있는 것은 틀림이 아니라 '더 옮김'으로 따로 센다."""
    want = old_dawoni_keys()
    got = {k["question_number"]: AnswerKey.model_validate(k) for k in data["keys"]}
    total = ok = 0
    diffs: list[str] = []
    extra: list[str] = []
    for n, k in want.items():
        g = {p.label: p for p in got[n].parts} if n in got else {}
        for p in k.parts:
            if (n, p.label) in TEACHER_FIXED_KEY:
                continue
            total += 1
            q = g.get(p.label)
            if q is None:
                diffs.append(f"{n}{p.label}(없음)")
                continue
            answer_same = Counter(_NUM.findall(q.answer or "")) == Counter(
                _NUM.findall(p.answer or "")
            )
            missing = _numbers(p) - _numbers(q)
            if answer_same and not missing:
                ok += 1
                if _numbers(q) - _numbers(p):
                    extra.append(f"{n}{p.label}")
            else:
                diffs.append(f"{n}{p.label}")
    note = f"소문항 {ok}/{total}"
    if diffs:
        note += f", 다름 {', '.join(diffs)}"
    if extra:
        note += f", 더 옮김 {len(extra)}"
    return ok / max(total, 1), note


COVER_FIELDS = ("rotation", "name", "class_name", "school", "scores")


def cover_fields(d: dict[str, Any]) -> dict[str, Any]:
    scores = {s["question"]: float(s["points"]) for s in d.get("teacher_scores", [])}
    return {"rotation": d.get("rotation"), "name": d.get("name"), "class_name": d.get("class_name"),
            "school": d.get("school"), "scores": scores}  # fmt: skip


def _loose(field: str, v: Any) -> str:
    """띄어쓰기·대소문자, 학교의 '중'/'중학교'는 같은 것으로 본다."""
    s = str(v).replace(" ", "").upper()
    if field == "school" and v is not None:
        s = s.removesuffix("학교").removesuffix("중")
    return s


def score_cover(results: dict[int, Any]) -> tuple[float, str]:
    ref = load_reference("covers")
    ok = total = 0
    wrong: Counter[str] = Counter()
    for set_no, d in results.items():
        r = ref.get(str(set_no))
        if r is None or "error" in d:
            continue
        got = cover_fields(d)
        for f in COVER_FIELDS:
            if r[f] is None:  # 사람이 봐도 확실하지 않은 칸
                continue
            total += 1
            if _loose(f, got[f]) == _loose(f, r[f]):
                ok += 1
            else:
                wrong[f] += 1
    if not total:
        return float("nan"), "기준값 없음"
    return ok / total, f"칸 {ok}/{total}" + (f" (틀림 {dict(wrong)})" if wrong else "")


def _transcript_text(d: dict[str, Any]) -> dict[str, str]:
    return {n: "\n".join(" ".join(x for x in (ln.get("text", ""), ln.get("math") or "") if x)
                         for ln in t["lines"] if not ln.get("struck"))
            for n, t in d.get("transcripts", {}).items()}  # fmt: skip


CIRCLED = re.compile("[㉠-㉭①-⑳]")


def _read_results(model: str) -> dict[int, Any]:
    base = OUT / "results" / "read" / model
    return {int(p.stem.removeprefix("set")): load(p) for p in base.glob("set*.json")}


def score_read(results: dict[int, Any], model: str) -> tuple[float, str]:
    """기준 판독문이 없으므로 (손글씨가 흐려 사람이 옮긴 기준을 만들지 못함) 다른 모델들의 같은
    답안 판독과 글자 유사도 평균, 그리고 동그라미 기호(㉠㉡㉢①②) 순서가 다수와 다른 문항 수."""
    others = {
        p.name: _read_results(p.name)
        for p in (OUT / "results" / "read").iterdir()
        if p.is_dir() and p.name != model
    }
    sims: list[float] = []
    symbol_diff = 0
    for set_no, d in results.items():
        if "error" in d:
            continue
        got = _transcript_text(d)
        for n, text in got.items():
            peers = [
                _transcript_text(o[set_no]).get(n, "")
                for o in others.values()
                if set_no in o and "error" not in o[set_no]
            ]
            if not peers:
                continue
            sims.append(statistics.mean(fuzz.ratio(text, p) / 100 for p in peers))
            mine = CIRCLED.findall(text)
            votes = Counter(tuple(CIRCLED.findall(p)) for p in peers)
            if votes and tuple(mine) != votes.most_common(1)[0][0]:
                symbol_diff += 1
    if not sims:
        return float("nan"), "비교할 판독 없음"
    return statistics.mean(
        sims
    ), f"다른 모델과 유사도, 문항 {len(sims)}개, 기호 순서 다름 {symbol_diff}"


def score_grade(results: dict[int, Any]) -> tuple[float, str]:
    """강사 빨간 펜 문항 점수(표지 기준값)와 같은 비율, 차이 합."""
    ref = load_reference("covers")
    same = total = 0
    diff = 0.0
    for set_no, d in results.items():
        r = ref.get(str(set_no))
        if r is None or "error" in d:
            continue
        for n, g in d.items():
            want = r["scores"].get(n)
            if want is None:
                continue
            total += 1
            same += abs(g["total"] - want) < 1e-9
            diff += abs(g["total"] - want)
    if not total:
        return float("nan"), "기준값 없음"
    return same / total, f"문항 {same}/{total} 일치, 차이 합 {diff:g}점"


# ---------------------------------------------------------------- 표


def report() -> str:
    calls = [json.loads(x) for x in CALLS.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows: list[str] = [
        "| 역할 | 모델 | 작업 수 | 성공 | 시간 중앙값(초) | 토큰 중앙값 | 재요청 비율 | 정확도 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    scorers: dict[str, Callable[[dict[int, Any], str], tuple[float, str]]] = {
        "cover": lambda r, _: score_cover(r),
        "read": score_read,
        "grade": lambda r, _: score_grade(r),
    }
    for task, role in ROLE_OF.items():
        base = OUT / "results" / task
        if not base.is_dir():
            continue
        for mdir in sorted(base.iterdir()):
            model = mdir.name
            items = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in mdir.glob("*.json")}
            if not items:
                continue
            ok = [
                v
                for v in items.values()
                if not (isinstance(v["data"], dict) and "error" in v["data"])
            ]
            secs = statistics.median(v["seconds"] for v in items.values())
            mine = [c for c in calls if c.get("role") == role and c.get("model") == model]
            tokens = statistics.median(
                [c["total_tokens"] for c in mine if c.get("total_tokens")] or [0]
            )
            first = [c for c in mine if c.get("attempt") == 1]
            retried = [c for c in mine if (c.get("attempt") or 1) > 1]
            retry = len(retried) / max(len(first), 1)
            if task == "exam":
                acc, note = score_exam(ok[0]["data"]) if ok else (float("nan"), "실패")
            elif task == "key":
                acc, note = score_key(ok[0]["data"]) if ok else (float("nan"), "실패")
            else:
                res = {int(k.removeprefix("set")): v["data"] for k, v in items.items()}
                acc, note = scorers[task](res, model)
            acc_s = "—" if acc != acc else f"{acc:.0%} ({note})"  # NaN
            rows.append(f"| {role} | {model} | {len(items)} | {len(ok)} | {secs:.0f} | "
                        f"{tokens:,.0f} | {retry:.0%} | {acc_s} |")  # fmt: skip
    return "\n".join(rows)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("task", choices=[*TASKS, "grade", "report"])
    # 실측은 flash-medium으로 한다 — argon은 구독 한도가 따로라 실제 작업(strong)에 남겨 둔다
    ap.add_argument("--models", default="gemini-3.8-flash-medium")
    ap.add_argument("--sets", default="1")
    ap.add_argument("--transcripts", help="grade: 이 모델의 판독 결과로 채점")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--workers", type=int, default=3, help="동시에 돌릴 모델 수")
    args = ap.parse_args(argv)
    if args.task == "report":
        say(report())
        return 0
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        raise SystemExit("--models에 모델 이름을 쉼표로")
    if any(m.startswith("claude") for m in models) and args.task != "read":
        raise SystemExit("claude 모델은 손글씨 판독(read)만 잰다 (claude 토큰을 아낌)")
    fn = TASKS.get(args.task)
    if args.task == "grade":
        if not args.transcripts:
            raise SystemExit("grade에는 --transcripts <판독 모델>이 필요합니다")
        src = args.transcripts

        def fn(m: str, s: int) -> Any:
            return run_grade(m, s, source_model=src)

        out_task = "grade"
    else:
        out_task = args.task
    assert fn is not None
    execute(out_task, models, parse_sets(args.sets), redo=args.redo, workers=args.workers, fn=fn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
