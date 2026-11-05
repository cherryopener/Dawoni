"""화면용 API (M7): 알림 모음, 채점 검토 한 장, 배점 고치기, 내보낸 파일 자리.

다른 API를 화면이 여러 번 불러 맞추지 않게, 한 화면에 필요한 것을 코드로 모아 준다 (토큰 0).
"""

from __future__ import annotations

import shutil
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field

from dawoni.export import exam_files
from dawoni.export.hwpx import Sheet, grade_spots, part_grades, sheet_lines
from dawoni.export.quotes import LineSpots, segments
from dawoni.grading import workflow
from dawoni.grading.pipeline import PipelineError
from dawoni.grading.store import ExamStore
from dawoni.models.grading import UNREADABLE, ExamPart, ExamQuestion, TranscriptLine
from dawoni.models.job import Job
from dawoni.server.api_exams import exam_summary
from dawoni.server.api_setup import State
from dawoni.server.jobs import JobContext
from dawoni.server.state import AppState, NeedsSetup

router = APIRouter(prefix="/api")

JOB_NEWS = 10
"""알림에 넣는 최근 끝난 작업 수 (화면이 열린 동안의 것 — 서버를 다시 켜면 비어 있다)."""

JOB_TITLES = {
    "ingest_exam": "시험 읽기",
    "answer_key": "해설지 읽기",
    "rubric": "루브릭 만들기",
    "scan": "손글씨 판독",
    "scan_batch": "답안지 판독",
    "grade": "채점",
    "confirm_files": "한글에서 고친 것 반영",
    "export": "HWPX 쓰기",
    "read_marks": "빨간 펜 점수 읽기",
    "suggest_rubric": "보정 제안",
    "agent_test": "AI 연결 시험",
}


def _name(store: ExamStore, student: str) -> tuple[str, str | None]:
    r = store.student_record(student)
    if r is None:
        return student, None
    return (r.name or student), r.class_name


def _who(store: ExamStore, items: list[tuple[str, str]]) -> str:
    """('김다원', '1') 목록 → "2반 김다원 1번 · 박서준 2번" (앞 세 개만)."""
    parts: list[str] = []
    for s, q in items[:3]:
        name, klass = _name(store, s)
        parts.append(f"{klass + ' ' if klass else ''}{name} {q}번")
    more = f" 외 {len(items) - 3}" if len(items) > 3 else ""
    return " · ".join(parts) + more


def _ago(at: datetime | None) -> str:
    if at is None:
        return ""
    s = (datetime.now(UTC) - at).total_seconds()
    if s < 60:
        return "방금"
    if s < 3600:
        return f"{int(s // 60)}분 전"
    if s < 86400:
        return f"{int(s // 3600)}시간 전"
    return f"{int(s // 86400)}일 전"


def exam_notices(store: ExamStore) -> list[dict[str, Any]]:
    """한 시험에서 강사가 할 일 (처리하면 사라진다)."""
    exam = store.exam()
    base = {"exam_id": exam.id, "exam": exam.title, "time": ""}
    out: list[dict[str, Any]] = []

    waiting = [(t.student_id, t.question_number) for t in workflow.needs_input(store)]
    if waiting:
        n = len({s for s, _ in waiting})
        out.append({**base, "id": f"unreadable:{exam.id}", "kind": "unreadable",
                    "title": f"판독불가 {n}명", "count": len(waiting),
                    "detail": _who(store, waiting) + " — 한글에서 채우면 그 문항만 채점합니다",
                    "link": ["exams", exam.id, "scan"]})  # fmt: skip

    rename = [r for r in store.student_records() if r.needs_rename]
    if rename:
        names = sorted({f"{r.class_name or ''} {r.name or r.student_id}".strip() for r in rename})
        out.append({**base, "id": f"rename:{exam.id}", "kind": "rename",
                    "title": "같은 반 같은 이름", "count": len(rename),
                    "detail": f"{', '.join(names)} — 이름을 나눠야 판독본을 만듭니다",
                    "link": ["exams", exam.id, "scan"]})  # fmt: skip

    review = [
        (s, q.number)
        for s in store.students()
        for q in exam.questions
        if (g := store.grade(s, q.number)) is not None and g.status == "needs_review"
    ]
    if review:
        title = f"채점 검토 필요 {len(review)}건"
        out.append({**base, "id": f"review:{exam.id}", "kind": "review", "title": title,
                    "count": len(review), "detail": _who(store, review),
                    "link": ["exams", exam.id, "grade"]})  # fmt: skip

    doubtful = [
        f"{q.number}-{p}" if p else q.number
        for q in exam.questions
        if (k := store.answer_key(q.number)) is not None and not k.confirmed
        for p in k.doubtful
    ]
    if doubtful:
        out.append({**base, "id": f"key:{exam.id}", "kind": "key",
                    "title": f"해설 원본 확인 {len(doubtful)}곳", "count": len(doubtful),
                    "detail": f"{', '.join(doubtful[:4])} — 두 번 읽은 숫자가 다릅니다",
                    "link": ["exams", exam.id, "read"]})  # fmt: skip

    drafts = [
        q.number
        for q in exam.questions
        if (r := store.rubric(q.number)) is not None and r.status == "draft"
    ]
    if drafts:
        out.append({**base, "id": f"rubric:{exam.id}", "kind": "rubric",
                    "title": f"루브릭 승인 대기 {len(drafts)}문항", "count": len(drafts),
                    "detail": ", ".join(f"{n}번" for n in drafts) + " — 확인하고 승인해 주세요",
                    "link": ["exams", exam.id, "rubric"]})  # fmt: skip
    return out


def _job_notice(j: Job) -> dict[str, Any]:
    title = JOB_TITLES.get(j.kind, j.kind)
    exam = j.params.get("exam")
    link = ["exams", str(exam)] if exam else ["exams"]
    if j.status == "failed":
        return {"id": j.id, "kind": "job_failed", "title": f"{title} 멈춤",
                "detail": j.error.message if j.error else "", "count": 0,
                "time": _ago(j.finished_at), "exam_id": exam, "link": link}  # fmt: skip
    res = j.result or {}
    files = res.get("files")
    bits = [f"채점 {res['graded']}건"] if res.get("graded") else []
    if isinstance(files, list) and files:
        bits.append(f"파일 {len(files)}개")
    return {"id": j.id, "kind": "job_done", "title": f"{title} 끝남", "detail": " · ".join(bits),
            "count": 0, "time": _ago(j.finished_at), "exam_id": exam, "link": link}  # fmt: skip


@router.get("/notifications")
def notifications(st: AppState = State) -> list[dict[str, Any]]:
    """종 버튼·메뉴·단계 표시줄의 숫자: 모든 시험의 할 일 + 배우기 제안 + 최근 끝난 작업."""
    try:
        svc = st.services()
    except NeedsSetup:
        return []
    out: list[dict[str, Any]] = []
    titles: dict[str, str] = {}
    for eid in reversed(ExamStore.list_ids(svc.workspace)):
        store = ExamStore(svc.workspace, eid)
        titles[eid] = store.exam().title
        out += exam_notices(store)
    notes = [n for n in svc.memory.all() if n.proposed]
    skills = [s for s in svc.skills.all() if s.proposed]
    if notes or skills:
        n = len(notes) + len(skills)
        out.append({"id": "proposal", "kind": "proposal", "title": f"배우기 제안 {n}개",
                    "detail": f"메모리 {len(notes)} · 스킬 {len(skills)} — 승인해야 쓰입니다",
                    "count": n, "time": "", "link": ["learning"]})  # fmt: skip
    done = [j for j in st.jobs.list() if j.status in ("succeeded", "failed")][:JOB_NEWS]
    for j in done:
        item = _job_notice(j)
        if item["exam_id"] in titles:
            item["exam"] = titles[item["exam_id"]]
        out.append(item)
    return out


# ---------------------------------------------------------------- 채점 검토 한 장


def _pieces(line: TranscriptLine, spots: LineSpots | None) -> list[dict[str, Any]]:
    """한 줄 → 그릴 조각 [{text, math, kind}] — kind: good(연두)·bad(빨간 밑줄)·None."""
    out: list[dict[str, Any]] = []
    for si, (piece, is_math) in enumerate(segments(line)):
        if is_math:
            kinds = (spots.maths.get(si) if spots else None) or set()
            out.append({"text": piece, "math": True, "kind": _kind(kinds)})
            continue
        chars = (spots.chars.get(si) if spots else None) or {}
        cur: list[str] = []
        cur_kind: str | None = None
        for i, ch in enumerate(piece):
            k = _kind(chars.get(i) or set())
            if cur and k != cur_kind:
                out.append({"text": "".join(cur), "math": False, "kind": cur_kind})
                cur = []
            cur.append(ch)
            cur_kind = k
        if cur:
            out.append({"text": "".join(cur), "math": False, "kind": cur_kind})
    return out


def _kind(kinds: Collection[str]) -> str | None:
    if "bad" in kinds:
        return "bad"
    return "good" if "good" in kinds else None


@router.get("/exams/{exam_id}/review/{student}/{number}")
def review_sheet(exam_id: str, student: str, number: str, st: AppState = State) -> dict[str, Any]:
    """채점 검토 화면 한 장: 소문항마다 판독문(근거 인용 자리 표시)·점수·맞은/감점 이유, 원본 쪽."""
    svc = st.services()
    store = ExamStore(svc.workspace, exam_id)
    q = store.exam().question(number)
    t = store.transcript(student, number)
    g = store.grade(student, number)
    r = store.rubric(number)
    lines = sheet_lines(number, t.lines) if t else []
    grades = part_grades(number, g, r) if g and r else {}
    spots = grade_spots(Sheet(head="", lines=lines, grades=grades))
    labels = [f"{number}-{p.label}" for p in q.parts] or [number]
    for x in lines:  # 판독문에만 있는 표시 (문항을 알 수 없는 줄 등)
        if x.part and x.part not in labels:
            labels.append(x.part)
    parts: list[dict[str, Any]] = []
    for label in labels:
        pg = grades.get(label)
        idx = [i for i, x in enumerate(lines) if x.part == label]
        part = q.part(label.split("-", 1)[1]) if "-" in label else None
        parts.append(
            {
                "label": label,
                "points": pg.points if pg else (part.points if part else q.points),
                "awarded": pg.awarded if pg else None,
                "good": [{"quote": a, "reason": b} for a, b in (pg.good if pg else [])],
                "bad": [{"quote": a, "reason": b} for a, b in (pg.bad if pg else [])],
                "lines": [
                    {
                        "pieces": _pieces(lines[i], spots.lines.get(i)),
                        "struck": lines[i].struck,
                        "uncertain": lines[i].uncertain,
                        "unreadable": UNREADABLE in lines[i].text,
                    }
                    for i in idx
                ],
            }
        )
    name, klass = _name(store, student)
    images = sorted(
        p.relative_to(store.root).as_posix() for p in store.scan_dir(student).glob("page_*.png")
    )
    if t and t.pages:
        images = [p for p in images if any(p.endswith(x) for x in t.pages)] or images
    return {
        "student": student,
        "name": name,
        "class_name": klass,
        "question": number,
        "max": q.points,
        "total": g.total if g else None,
        "status": g.status if g else None,
        "transcript_confirmed": (t.confirmed if t else False),
        "graded_before_confirm": bool(g and not g.transcript_confirmed),
        "flags": g.flags if g else [],
        "disagreements": g.disagreements if g else [],
        "comment": g.comment if g else "",
        "unreadable": t.unreadable if t else 0,
        "parts": parts,
        "page_images": images,
    }


# ---------------------------------------------------------------- 배점 고치기 (M1에서 남긴 것)


class PointsBody(BaseModel):
    parts: dict[str, float] = Field(
        default_factory=lambda: dict[str, float](), description="소문항 표시 → 배점"
    )
    points: float | None = Field(default=None, gt=0, description="소문항이 없는 문항의 배점")


@router.put("/exams/{exam_id}/questions/{number}/points")
def put_points(exam_id: str, number: str, body: PointsBody, st: AppState = State) -> ExamQuestion:
    """배점 표시가 없던 시험지의 자리값 배점(소문항마다 2점)을 강사가 고친다. 루브릭을 만들기 전에만
    — 루브릭 항목 배점이 문항 배점에 맞춰져 있기 때문."""
    store = ExamStore(st.services().workspace, exam_id)
    exam = store.exam()
    q = exam.question(number)
    if store.rubric(number) is not None:
        raise PipelineError(f"문항 {number}: 루브릭이 이미 있어 배점을 고칠 수 없습니다.")
    if q.parts:
        if set(body.parts) != {p.label for p in q.parts}:
            raise PipelineError(f"소문항 표시가 다릅니다: {[p.label for p in q.parts]}")
        parts = [ExamPart(label=p.label, points=body.parts[p.label], kind=p.kind) for p in q.parts]
        new = q.model_copy(update={"parts": parts, "points": sum(p.points for p in parts)})
    else:
        if body.points is None:
            raise PipelineError("배점을 주세요.")
        new = q.model_copy(update={"points": body.points})
    new = ExamQuestion.model_validate(new.model_dump())
    questions = [new if x.number == number else x for x in exam.questions]
    store.save_exam(exam.model_copy(update={"questions": questions}))
    return new


# ---------------------------------------------------------------- 내보낸 파일 자리


@router.get("/exams/{exam_id}/outputs")
def outputs(exam_id: str, st: AppState = State) -> dict[str, Any]:
    """내보내기 화면: 시험의 HWPX 폴더·점수표 (작업공간 기준 경로 — `/files/open`·`reveal`에)."""
    ws = st.services().workspace
    store = ExamStore(ws, exam_id)
    exam = store.exam()
    folder = exam_files.exam_dir(ws, exam)
    scores = exam_files.score_file(ws, store)
    hwpx = sorted(folder.glob("*.hwpx")) if folder.is_dir() else []
    hashes = store.export_hashes()
    edited = [p.name for p in hwpx if exam_files.is_edited(store, p, hashes)]
    return {
        "folder": folder.relative_to(ws.root).as_posix(),
        "folder_exists": folder.is_dir(),
        "scores": scores.relative_to(ws.root).as_posix(),
        "scores_exists": scores.is_file(),
        "scores_by_class": exam.scores_by_class,
        "students": sum(not p.name.startswith("(") for p in hwpx),
        "bundles": sum(p.name.startswith("(반별)") for p in hwpx),
        "edited": edited,
        "summary": exam_summary(store, exam),
    }


# ---------------------------------------------------------------- 학생 검색 (§5-4)


@router.get("/students/history")
def student_history(st: AppState = State) -> list[dict[str, Any]]:
    """명부 학생 + 시험에 나온 학생마다 본 시험·점수. 파일이 원본이라 시험 폴더를 읽어 모은다
    (학원 한 곳의 시험 수십 개면 충분히 빠르다 — 느려지면 PC 로컬 SQLite 색인으로, §5-4)."""
    from dawoni.students import Roster

    svc = st.services()
    people: dict[str, dict[str, Any]] = {}
    for s in Roster(svc.workspace).all():
        people[s.id] = {"key": s.id, "name": s.name, "class_name": s.class_name,
                        "school": s.school, "roster_id": s.id, "exams": []}  # fmt: skip
    for eid in reversed(ExamStore.list_ids(svc.workspace)):
        store = ExamStore(svc.workspace, eid)
        exam = store.exam()
        for r in store.student_records():
            key = r.roster_id or f"{r.class_name or ''}|{r.name or r.student_id}"
            p = people.setdefault(
                key,
                {"key": key, "name": r.name or r.student_id, "class_name": r.class_name,
                 "school": r.school, "roster_id": r.roster_id, "exams": []},
            )  # fmt: skip
            grades = [store.grade(r.student_id, q.number) for q in exam.questions]
            done = [g for g in grades if g is not None]
            p["exams"].append(
                {
                    "exam_id": exam.id,
                    "title": exam.title,
                    "date": f"{exam.created_at:%Y-%m-%d}",
                    "student_id": r.student_id,
                    "total": sum(g.total for g in done) if len(done) == len(grades) else None,
                    "max": sum(q.points for q in exam.questions),
                    "graded": len(done),
                    "questions": len(grades),
                }
            )
    return sorted(people.values(), key=lambda p: (p["class_name"] or "", p["name"]))


@router.get("/exams/{exam_id}/student-files")
def student_files(exam_id: str, st: AppState = State) -> dict[str, dict[str, str]]:
    """학생 → 문항 → 판독본·채점본 HWPX (있는 것만, 작업공간 기준 경로) — "한글로 열기"."""
    ws = st.services().workspace
    store = ExamStore(ws, exam_id)
    out: dict[str, dict[str, str]] = {}
    for s in store.students():
        for q in store.exam().questions:
            p = exam_files.student_file(ws, store, s, q.number)
            if p.is_file():
                out.setdefault(s, {})[q.number] = p.relative_to(ws.root).as_posix()
    return out


@router.post("/scanner/upload")
def upload_scan(file: Annotated[UploadFile, File()], st: AppState = State) -> dict[str, Any]:
    """끌어다 놓은 답안지 PDF를 작업공간 '스캔본' 폴더에 둔다 (이름이 겹치면 뒤에 번호)."""
    ws = st.services().workspace
    inbox = ws.scan_inbox
    inbox.mkdir(parents=True, exist_ok=True)
    name = Path(file.filename or "scan.pdf").name
    if Path(name).suffix.lower() != ".pdf":
        raise PipelineError("PDF 파일만 넣을 수 있습니다.")
    dest = inbox / name
    k = 2
    while dest.exists():
        dest = inbox / f"{Path(name).stem}_{k}.pdf"
        k += 1
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"path": dest.relative_to(ws.root).as_posix(), "name": dest.name}


# ---------------------------------------------------------------- 역할별 토큰 (§2-3)


@router.get("/usage")
def usage(days: int = 30, st: AppState = State) -> dict[str, Any]:
    """역할별 AI 호출 수·토큰 (최근 `days`일, 이 PC의 `calls.jsonl`) — 설정의 역할별 모델 표."""
    from dawoni.context.calllog import CallLog

    cutoff = datetime.now(UTC).timestamp() - days * 86400
    roles: dict[str, dict[str, int]] = {}
    for rec in CallLog(st.paths.logs / "calls.jsonl").read():
        at = rec.get("at")
        try:
            ts = datetime.fromisoformat(str(at)).timestamp()
        except ValueError:
            continue
        if ts < cutoff:
            continue
        role = str(rec.get("role") or rec.get("task") or "?")
        r = roles.setdefault(role, {"calls": 0, "tokens": 0, "repairs": 0})
        r["calls"] += 1
        tokens = rec.get("total_tokens")
        r["tokens"] += tokens if isinstance(tokens, int) else 0
        attempt = rec.get("attempt")
        r["repairs"] += 1 if isinstance(attempt, int) and attempt > 1 else 0
    return {"days": days, "roles": roles}


# ---------------------------------------------------------------- 총괄 AI 요청창 (§2-1)


class PlanBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    exam_id: str | None = Field(default=None, description="지금 화면의 시험")


def _failures(st: AppState) -> list[str]:
    """총괄 AI에 줄 최근 실패 요약 — 작업 오류와 검증에 떨어진 호출 (원문 없이)."""
    from dawoni.context.calllog import CallLog

    out = [
        f"작업 {JOB_TITLES.get(j.kind, j.kind)} {j.params}: {j.error.code} {j.error.message}"
        for j in st.jobs.list()[:30]
        if j.status == "failed" and j.error
    ]
    calls = [r for r in CallLog(st.paths.logs / "calls.jsonl").read()[-200:] if not r.get("ok")]
    for r in calls[-8:]:
        errors = r.get("errors")
        first = [str(x) for x in errors[:2]] if isinstance(errors, list) else []  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        out.append(
            f"호출 {r.get('task')} {r.get('error_code') or ''} 시도 {r.get('attempt')}: {first}"
        )
    return out


@router.post("/coordinator/plan")
def coordinator_plan(body: PlanBody, st: AppState = State) -> Job:
    """부탁 → "이렇게 할까요?" 카드 (AI 호출 1번, 작업으로). 실행은 `/coordinator/run`."""
    from dawoni.coordinator import plan_request, resolve_students

    svc = st.services()
    cfg = st.config()
    failures = _failures(st)

    def run(ctx: JobContext) -> dict[str, Any]:
        ctx.progress(0, 1, "부탁을 작업으로 바꾸는 중")
        plan = plan_request(
            svc, body.text, current=body.exam_id, failures=failures, agent=cfg.coordinator_agent
        )
        students = None
        if plan.kind == "action" and plan.exam_id:
            students = resolve_students(ExamStore(svc.workspace, plan.exam_id), plan)
        ctx.progress(1, 1, "끝")
        return {"plan": plan.model_dump(mode="json"), "students": students}

    return st.jobs.submit("coordinate", run, {"text": body.text})


class RunBody(BaseModel):
    plan: dict[str, Any]
    by: str | None = None


@router.post("/coordinator/run")
def coordinator_run(body: RunBody, st: AppState = State) -> dict[str, Any]:
    """강사가 "실행"을 누른 카드 → 정해진 API 하나. 돌려주는 `job`은 작업, `link`는 열 화면."""
    from dawoni.coordinator import CoordinatorPlan, check_plan, exam_briefs, resolve_students
    from dawoni.server import api_calibration, api_exams

    svc = st.services()
    plan = CoordinatorPlan.model_validate(body.plan)
    if plan.kind != "action" or plan.action is None:
        raise PipelineError("실행할 작업이 없습니다.")
    if problems := check_plan(plan, exam_briefs(svc)):
        raise PipelineError(" ".join(problems))
    eid = plan.exam_id or ""
    qs = plan.questions or None
    link = ["exams", eid, plan.screen or ""] if plan.exam_id else [plan.screen or "exams"]
    job: Job | None = None
    match plan.action:
        case "open":
            pass
        case "grade":
            students = resolve_students(ExamStore(svc.workspace, eid), plan)
            if students == []:
                raise PipelineError("그 반·이름의 학생이 이 시험에 없습니다.")
            gb = api_exams.GradeBody(
                students=students, questions=qs, regrade=plan.regrade, by=st.who(body.by)
            )
            job = api_exams.grade(eid, gb, st)
            link = ["exams", eid, "grade"]
        case "rubric":
            job = api_exams.make_rubrics(
                eid, api_exams.QuestionsBody(questions=qs, by=st.who(body.by)), st
            )
            link = ["exams", eid, "rubric"]
        case "confirm_files":
            cb = api_exams.ConfirmFilesBody(by=st.who(body.by))
            job = api_exams.confirm_files(eid, cb, st)
            link = ["exams", eid, "scan"]
        case "read_marks":
            job = api_calibration.read_marks(eid, api_calibration.ReadMarksBody(), st)
            link = ["exams", eid, "rubric"]
        case "export":
            job = api_exams.export(eid, api_exams.ExportBody(), st)
            link = ["exams", eid, "export"]
        case "scores":
            api_exams.scores(eid, api_exams.ScoresBody(by_class=plan.by_class), st)
            link = ["exams", eid, "export"]
    return {"job": job.model_dump(mode="json") if job else None, "link": [x for x in link if x]}
