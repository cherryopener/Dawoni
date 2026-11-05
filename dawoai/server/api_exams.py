"""시험 API: 문항 → 해설·루브릭(승인) → 답안(입력·판독, 확인) → 채점(검토) → 출력."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from dawoai import sharing
from dawoai.cli_exam import parse_answer_text
from dawoai.export.exam_files import export_scores, write_all
from dawoai.grading import assign, workflow
from dawoai.grading.answers import set_answers
from dawoai.grading.pipeline import (
    PipelineError,
    SecondOpinion,
    approve_rubric,
    decide_candidates,
    edit_rubric,
    teacher_part_review,
    teacher_review,
)
from dawoai.grading.store import ExamStore, ScopeStore
from dawoai.models.answer_key import AnswerKey, KeyPart
from dawoai.models.grading import (
    Exam,
    ExamQuestion,
    GradeResult,
    Rubric,
    RubricDraft,
    Transcript,
    TranscriptLine,
)
from dawoai.models.job import Job
from dawoai.ocr.review import confirm_problems
from dawoai.server import files, learning
from dawoai.server.api_content import save_upload, upload_dir
from dawoai.server.api_setup import State
from dawoai.server.jobs import JobContext
from dawoai.server.state import AppState
from dawoai.services import Services
from dawoai.workspace import open_workspace

router = APIRouter(prefix="/api/exams")


def _store(st: AppState, exam_id: str) -> tuple[Services, ExamStore]:
    svc = st.services()
    store = ExamStore(svc.workspace, exam_id)
    store.exam()  # 없으면 KeyError → 404
    return svc, store


# ---------------------------------------------------------------- 요약


def question_state(store: ExamStore, q: ExamQuestion) -> dict[str, Any]:
    sol = store.solution(q.number)
    r = store.rubric(q.number)
    return {
        "number": q.number,
        "points": q.points,
        "item_type": q.item.item_type,
        "has_key": store.answer_key(q.number) is not None,
        "solution": None
        if sol is None
        else {
            "steps": len(sol.steps),
            "verified": sol.verified,
            "check_notes": sol.check_notes,
            "out_of_scope_note": sol.out_of_scope_note,
            "final_answer": sol.final_answer.latex,
        },
        "rubric": None
        if r is None
        else {
            "status": r.status,
            "version": r.version,
            "total": r.total,
            "items": len(r.items),
            "based_on_key": r.based_on_key,
            "attention": sum(c.verdict != "same" for c in r.checks)
            + sum(c.status == "proposed" for c in r.candidates)
            + sum(a.status == "proposed" for a in r.accepted_alternatives),
        },
    }


def _cell(store: ExamStore, student: str, q: ExamQuestion) -> dict[str, Any]:
    t = store.transcript(student, q.number)
    g = store.grade(student, q.number)
    r = store.rubric(q.number)
    return {
        "transcript": None
        if t is None
        else {
            "confirmed": t.confirmed,
            "source": t.source,
            "attention": t.needs_attention,
            "unreadable": t.unreadable,
            "agreement": t.agreement,
            "lines": len(t.lines),
        },
        "grade": None
        if g is None
        else {
            "total": g.total,
            "max": g.max_points,
            "status": g.status,
            "disagreements": len(g.disagreements),
            "stale": r is not None and g.rubric_version != r.version,
        },
    }


def exam_summary(store: ExamStore, exam: Exam) -> dict[str, Any]:
    students = store.students()
    cells = [_cell(store, s, q) for s in students for q in exam.questions]
    records = store.student_records()
    out: dict[str, Any] = {
        "questions": len(exam.questions),
        "solved": sum(store.solution(q.number) is not None for q in exam.questions),
        "approved": sum(
            (r := store.rubric(q.number)) is not None and r.status == "approved"
            for q in exam.questions
        ),
        "students": len(students),
        "pending_transcripts": sum(
            c["transcript"] is not None and not c["transcript"]["confirmed"] for c in cells
        ),
        "needs_input": sum(
            c["transcript"] is not None and c["transcript"]["unreadable"] > 0 for c in cells
        ),
        "graded": sum(c["grade"] is not None for c in cells),
        "needs_review": sum(
            c["grade"] is not None and c["grade"]["status"] == "needs_review" for c in cells
        ),
        "needs_rename": sum(r.needs_rename for r in records),
        "not_in_roster": sum(r.roster_note is not None for r in records),
    }
    out["step"] = current_step(exam, out)
    return out


def current_step(exam: Exam, s: dict[str, Any]) -> str:
    """시험 화면 단계 표시줄의 '지금 단계' (PLAN.md §11-1): 강사가 다음에 할 일이 있는 곳."""
    if s["approved"] < len(exam.questions):
        return "rubric"
    if s["students"] == 0 or s["needs_rename"] or s["graded"] == 0:
        return "scan"
    if s["graded"] < s["students"] * len(exam.questions) or s["needs_review"]:
        return "grade"
    return "export"


@router.get("")
def list_exams(st: AppState = State) -> list[dict[str, Any]]:
    svc = st.services()
    out: list[dict[str, Any]] = []
    for eid in reversed(ExamStore.list_ids(svc.workspace)):
        store = ExamStore(svc.workspace, eid)
        exam = store.exam()
        out.append(
            {
                "id": exam.id,
                "title": exam.title,
                "scope_id": exam.scope_id,
                "created_at": exam.created_at.isoformat(),
                "summary": exam_summary(store, exam),
            }
        )
    return out


class CreateExamBody(BaseModel):
    title: str = Field(min_length=1)
    scope_id: str
    questions: list[ExamQuestion] = Field(min_length=1)


@router.post("")
def create_exam(body: CreateExamBody, st: AppState = State) -> Exam:
    svc = st.services()
    ScopeStore(svc.workspace).get(body.scope_id)
    exam = Exam(title=body.title, scope_id=body.scope_id, questions=body.questions)
    ExamStore.create(svc.workspace, exam)
    return exam


@router.post("/from-file")
@router.post("/from-pdf")  # 예전 이름 (PDF만 받던 때)
def exam_from_file(
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()],
    scope_id: Annotated[str | None, Form()] = None,
    level: Annotated[str | None, Form()] = None,
    subject: Annotated[str | None, Form()] = None,
    force: Annotated[bool, Form()] = False,
    key_pages: Annotated[str | None, Form()] = None,
    st: AppState = State,
) -> Job:
    """시험지 PDF·DOCX·HWPX·HWP. 범위는 `scope_id` 또는 수준 프리셋 `level` ("고1 물리", "h1_phys").

    `key_pages`: PDF 해설지 쪽 ("6", "5-6"). 비우면 자동으로 찾고, "none"이면 해설지 없음.
    문서는 미주·"정답 및 해설" 구간을 해설로 읽는다 ("none"이면 읽지 않음)."""
    from dawoai.cli_ingest import parse_key_pages, resolve_scope
    from dawoai.ingest.workflow import existing_exam, ingest_exam

    svc = st.services()
    scope_id = resolve_scope(svc, scope_id, level)
    path = save_upload(file, upload_dir(st))
    if not force and (refused := existing_exam(svc, path)) is not None:
        raise refused  # 작업을 띄우기 전에 알려 기존 시험을 바로 열 수 있게
    pages = parse_key_pages(key_pages if key_pages and key_pages.strip() else None)

    def run(ctx: JobContext) -> dict[str, Any]:
        res = ingest_exam(
            svc, path, title=title, scope_id=scope_id, subject=subject, force=force,
            key_pages=pages, progress=ctx.progress,
        )  # fmt: skip
        return {
            "exam_id": res.exam.id,
            "key_source": res.key_source,
            "key_pages": res.key_pages,
            "keys": len(res.keys),
            "warnings": res.warnings + res.layout.warnings,
        }

    return st.jobs.submit("ingest_exam", run, {"title": title, "file": path.name})


@router.post("/{exam_id}/answer-key")
def answer_key_from_pdf(
    exam_id: str,
    file: Annotated[UploadFile, File()],
    pages: Annotated[str | None, Form()] = None,
    st: AppState = State,
) -> Job:
    """강사 해설지 붙이기 (PDF·DOCX·HWPX·HWP, 시험지 파일의 해설 부분이어도 됨).

    PDF 쪽을 비우면 자동으로 찾고, 문서는 미주·해설 구간(없으면 문서 전체)을 문항별로 읽는다."""
    from dawoai.cli_ingest import parse_pages
    from dawoai.ingest.workflow import attach_answer_key

    svc, store = _store(st, exam_id)
    path = save_upload(file, upload_dir(st))
    page_list = parse_pages(pages) if pages and pages.strip() else None

    def run(ctx: JobContext) -> dict[str, Any]:
        res = attach_answer_key(svc, store, path, page_list, progress=ctx.progress)
        return {"keys": [k.question_number for k in res.keys], "warnings": res.warnings}

    return st.jobs.submit("answer_key", run, {"exam": exam_id, "file": path.name})


@router.get("/{exam_id}")
def get_exam(exam_id: str, st: AppState = State) -> dict[str, Any]:
    svc, store = _store(st, exam_id)
    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    return {
        "exam": exam.model_dump(mode="json"),
        "scope": {"id": scope.id, "title": scope.title, "level": scope.level},
        "questions": [question_state(store, q) for q in exam.questions],
        "summary": exam_summary(store, exam),
    }


@router.get("/{exam_id}/file")
def exam_file(exam_id: str, path: str, st: AppState = State) -> FileResponse:
    """시험 폴더 안의 그림·답안지 이미지."""
    _, store = _store(st, exam_id)
    full = files.inside(store.root, path)
    if not full.is_file():
        raise KeyError(f"파일 없음: {path}")
    return FileResponse(full)


# ---------------------------------------------------------------- 해설·루브릭


class QuestionsBody(BaseModel):
    questions: list[str] | None = None
    verify: bool = False
    verify_model: str | None = None
    ai_solve: bool = False
    """루브릭: 해설지가 있어도 AI가 먼저 풀어 해설지를 검산 ("AI 풀기 (해설지 검산)")."""
    by: str | None = None


@router.post("/{exam_id}/solve")
def solve(exam_id: str, body: QuestionsBody, st: AppState = State) -> Job:
    svc, store = _store(st, exam_id)
    verify = SecondOpinion(model=body.verify_model) if body.verify else None

    def run(ctx: JobContext) -> dict[str, Any]:
        sols = workflow.solve_questions(
            svc, store, body.questions, verify=verify, by=st.who(body.by), progress=ctx.progress
        )
        return {"solved": [s.question_number for s in sols]}

    return st.jobs.submit("solve", run, {"exam": exam_id})


@router.post("/{exam_id}/rubrics")
def make_rubrics(exam_id: str, body: QuestionsBody, st: AppState = State) -> Job:
    svc, store = _store(st, exam_id)

    def run(ctx: JobContext) -> dict[str, Any]:
        rs = workflow.rubric_questions(
            svc,
            store,
            body.questions,
            ai_solve=body.ai_solve,
            by=st.who(body.by),
            progress=ctx.progress,
        )
        return {"rubrics": [r.question_number for r in rs]}

    return st.jobs.submit("rubric", run, {"exam": exam_id, "ai_solve": body.ai_solve})


@router.get("/{exam_id}/questions/{number}")
def get_question(exam_id: str, number: str, st: AppState = State) -> dict[str, Any]:
    _, store = _store(st, exam_id)
    q = store.exam().question(number)
    sol = store.solution(number)
    r = store.rubric(number)
    key = store.answer_key(number)
    return {
        "question": q.model_dump(mode="json"),
        "key": key.model_dump(mode="json") if key else None,
        "solution": sol.model_dump(mode="json") if sol else None,
        "rubric": r.model_dump(mode="json") if r else None,
        "candidate_matches": m.model_dump(mode="json")
        if (m := store.candidate_matches(number))
        else None,
    }


@router.put("/{exam_id}/questions/{number}/rubric")
def put_rubric(exam_id: str, number: str, draft: RubricDraft, st: AppState = State) -> Rubric:
    _, store = _store(st, exam_id)
    q = store.exam().question(number)
    old = store.rubric(number)
    sol = store.solution(number)
    if old is None or (sol is None and not old.based_on_key and not old.code_graded):
        raise PipelineError(f"문항 {number}: 해설·루브릭 초안을 먼저 만드세요.")
    r = edit_rubric(old, draft, q, sol)
    store.save_rubric(r)
    return r


class ByBody(BaseModel):
    by: str | None = None


class KeyBody(BaseModel):
    parts: list[KeyPart] | None = Field(default=None, description="고친 소문항 (없으면 그대로)")
    confirm: bool = True
    by: str | None = None


@router.put("/{exam_id}/questions/{number}/key")
def put_key(exam_id: str, number: str, body: KeyBody, st: AppState = State) -> AnswerKey:
    """해설지 판독 고치기·확인. 루브릭은 그대로 — 반영하려면 루브릭을 다시 만든다."""
    _, store = _store(st, exam_id)
    k = store.answer_key(number)
    if k is None:
        raise PipelineError(f"문항 {number}: 해설지가 없습니다.")
    if body.parts is not None:
        labels = sorted(p.label for p in k.parts)
        if sorted(p.label for p in body.parts) != labels:
            raise PipelineError(f"소문항 표시는 바꿀 수 없습니다: {labels}")
        k = k.model_copy(update={"parts": body.parts, "doubtful": []})
    k = k.model_copy(update={"confirmed": body.confirm, "confirmed_by": st.who(body.by)})
    store.save_key(k)
    return k


class CandidatesBody(BaseModel):
    accept: list[str] = Field(default_factory=lambda: list[str]())
    reject: list[str] = Field(default_factory=lambda: list[str]())


@router.post("/{exam_id}/questions/{number}/rubric/candidates")
def candidates(exam_id: str, number: str, body: CandidatesBody, st: AppState = State) -> Rubric:
    """다른 정답 후보 인정·제외. 승인된 루브릭이면 새 판이 되고 다시 승인해야 한다."""
    _, store = _store(st, exam_id)
    r = store.rubric(number)
    if r is None:
        raise PipelineError(f"문항 {number}: 루브릭이 없습니다.")
    r = decide_candidates(r, body.accept, body.reject)
    store.save_rubric(r)
    return r


@router.post("/{exam_id}/questions/{number}/rubric/candidates/match")
def match_candidates(exam_id: str, number: str, st: AppState = State) -> Job:
    """판독한 학생 답이 해설지 답·정답 후보 중 어느 쪽인지 (AI). 후보를 정하기 전에 본다."""
    svc, store = _store(st, exam_id)

    def run(ctx: JobContext) -> dict[str, Any]:
        m = workflow.match_candidates(svc, store, number, progress=ctx.progress)
        return {"parts": [p.part for p in m.parts]}

    return st.jobs.submit("match_candidates", run, {"exam": exam_id})


class AnswersBody(BaseModel):
    part: str = Field(default="", description="소문항 표시 (없으면 문항 전체)")
    answers: list[str] = Field(default_factory=lambda: list[str](), description="인정할 답들")
    all_correct: bool = False
    by: str | None = None


@router.put("/{exam_id}/questions/{number}/rubric/answers")
def put_answers(exam_id: str, number: str, body: AnswersBody, st: AppState = State) -> Rubric:
    """객관식·단답형 정답 고치기 (복수 정답·전원 정답). 승인된 루브릭이면 다시 승인."""
    _, store = _store(st, exam_id)
    r = store.rubric(number)
    if r is None or not r.code_graded:
        raise PipelineError(f"문항 {number}: 코드 채점 루브릭이 없습니다.")
    r = set_answers(r, body.part, body.answers, all_correct=body.all_correct, by=st.who(body.by))
    store.save_rubric(r)
    return r


@router.post("/{exam_id}/questions/{number}/rubric/approve")
def approve(exam_id: str, number: str, body: ByBody, st: AppState = State) -> Rubric:
    _, store = _store(st, exam_id)
    r = store.rubric(number)
    if r is None:
        raise PipelineError(f"문항 {number}: 루브릭이 없습니다.")
    r = approve_rubric(r, st.who(body.by))
    store.save_rubric(r)
    return r


# ---------------------------------------------------------------- 학생 답안


@router.get("/{exam_id}/students")
def students(exam_id: str, st: AppState = State) -> list[dict[str, Any]]:
    """학생마다 표지·명부 정보(`record`: 이름·반·학교, 명부 ID, 이름 확인 대기)와 문항별 상태."""
    _, store = _store(st, exam_id)
    exam = store.exam()
    records = {r.student_id: r for r in store.student_records()}
    return [
        {
            "student": s,
            "record": records[s].model_dump(mode="json") if s in records else None,
            "cells": {q.number: _cell(store, s, q) for q in exam.questions},
        }
        for s in sorted(set(store.students()) | set(records))
    ]


class IdentityBody(BaseModel):
    name: str | None = Field(default=None, description="새 이름 (동명이인은 김다원A처럼)")
    class_name: str | None = None
    roster_id: str | None = Field(default=None, description="명부 학생 ID — 명부의 이름·반을 쓴다")


@router.put("/{exam_id}/students/{student}/identity")
def identity(
    exam_id: str, student: str, body: IdentityBody, st: AppState = State
) -> dict[str, Any]:
    """학생 이름·반을 고치거나 명부와 잇는다 (표지 오독, 같은 반 동명이인 가르기). 판독문·채점을
    새 이름으로 옮기고 판독본 HWPX를 다시 쓴다."""
    svc, store = _store(st, exam_id)
    res = workflow.rename_student(
        svc, store, student, name=body.name, class_name=body.class_name, roster_id=body.roster_id
    )
    return {
        "record": res.record.model_dump(mode="json"),
        "warnings": res.warnings,
        "files": [p.name for p in res.export.written],
        "file_warnings": res.export.warnings,
    }


class AnswerBody(BaseModel):
    question: str
    text: str = Field(min_length=1, description="한 줄 = 답안 한 줄, `식:`=수식 줄, `~~`=지운 줄")
    by: str | None = None


@router.post("/{exam_id}/students/{student}/answers")
def typed_answer(exam_id: str, student: str, body: AnswerBody, st: AppState = State) -> Transcript:
    _, store = _store(st, exam_id)
    store.exam().question(body.question)
    t = Transcript(
        student_id=student,
        question_number=body.question,
        lines=parse_answer_text(body.text),
        source="typed",
        confirmed=True,
        confirmed_by=st.who(body.by),
    )
    store.save_transcript(t)
    return t


@router.post("/{exam_id}/students/{student}/scan")
def scan(
    exam_id: str,
    student: str,
    files_: Annotated[list[UploadFile], File(alias="files")],
    cross: Annotated[bool | None, Form()] = None,
    overwrite: Annotated[bool, Form()] = False,
    st: AppState = State,
) -> Job:
    svc, store = _store(st, exam_id)
    d = upload_dir(st)
    paths = [save_upload(f, d / f"{k:02d}") for k, f in enumerate(files_, start=1)]

    def run(ctx: JobContext) -> dict[str, Any]:
        res = workflow.scan_student(
            svc, store, student, paths, cross=cross, overwrite=overwrite, progress=ctx.progress
        )
        return {
            "saved": [t.question_number for t in res.saved],
            "kept_confirmed": res.kept_confirmed,
            "unassigned": [x.joined for x in res.unassigned],
            "notes": res.notes,
            "warnings": res.warnings,
            "files": [p.name for p in res.export.written],
            "file_warnings": res.export.warnings,
            **_grade_ready(svc, store, [student] if res.saved else [], ctx),
        }

    return st.jobs.submit("scan", run, {"exam": exam_id, "student": student})


class ExportBody(BaseModel):
    force: bool = False
    """강사가 한글에서 고친 파일도 덮어쓴다 (고친 내용은 사라진다)."""


@router.post("/{exam_id}/export")
def export(exam_id: str, body: ExportBody | None = None, st: AppState = State) -> Job:
    """판독본·채점 HWPX를 모두 다시 쓴다 (코드만, 토큰 0). 판독·채점할 때마다 저절로 쓰므로 보통은
    필요 없다 — 파일을 지웠거나 한글에서 열어 두어 못 쓴 경우. 강사가 고친 파일은 두고 알린다."""
    svc, store = _store(st, exam_id)
    force = body.force if body else False

    def run(ctx: JobContext) -> dict[str, Any]:
        ctx.progress(0, 1, "HWPX 쓰는 중")
        res = write_all(svc.workspace, store, force=force)
        return {
            "files": [p.name for p in res.written],
            "edited": [p.name for p in res.edited],
            "file_warnings": res.warnings,
        }

    return st.jobs.submit("export", run, {"exam": exam_id})


class ScoresBody(BaseModel):
    by_class: bool | None = None
    """반마다 시트 하나로 (시험에 저장). None이면 시험에 저장된 대로."""
    force: bool = False
    """엑셀에서 고친 점수표도 덮어쓴다."""


@router.post("/{exam_id}/scores")
def scores(exam_id: str, body: ScoresBody | None = None, st: AppState = State) -> dict[str, Any]:
    """엑셀 점수표만 다시 쓴다 (코드만, 토큰 0, 빠름 — 작업으로 돌리지 않는다). 채점·검토를 저장할
    때마다 저절로 쓰므로 보통은 반별 시트를 켜고 끌 때만."""
    svc, store = _store(st, exam_id)
    b = body or ScoresBody()
    res = export_scores(svc.workspace, store, by_class=b.by_class, force=b.force)
    return {
        "files": [p.name for p in res.written],
        "edited": [p.name for p in res.edited],
        "file_warnings": res.warnings,
        "by_class": store.exam().scores_by_class,
    }


class ConfirmFilesBody(learning.LearnFields):
    students: list[str] | None = None
    questions: list[str] | None = None
    by: str | None = None


@router.post("/{exam_id}/confirm-files")
def confirm_files(exam_id: str, body: ConfirmFilesBody, st: AppState = State) -> Job:
    """확인 완료 (PLAN.md §5-1 6단계): 강사가 한글에서 고친 판독본 HWPX를 다시 읽어 확정 판독문으로
    저장하고, 고친 답안은 다시 채점한다. 소문항 표식이 없어진 파일 등은 `problems`로 알린다."""
    svc, store = _store(st, exam_id)

    def run(ctx: JobContext) -> dict[str, Any]:
        res = workflow.confirm_from_files(
            svc, store, students=body.students, only=body.questions, by=st.who(body.by),
            progress=ctx.progress,
        )  # fmt: skip
        pairs = [(c.before, c.transcript) for c in res.confirmed if c.changed and c.before]
        if pairs:  # 고친 줄을 모아 배우기 한 번 (learn을 켰을 때만)
            c = learning.confirmed_files_correction(pairs, store.exam().title)
            learning.from_edit(st, body, c, st.who(body.by))
        return {
            "confirmed": [
                {"student": c.transcript.student_id, "question": c.transcript.question_number,
                 "changed": c.changed} for c in res.confirmed
            ],
            "waiting": [_waiting(c.transcript) for c in res.waiting],
            "unchanged": res.unchanged,
            "problems": [
                {"student": p.student, "question": p.question, "reason": p.reason}
                for p in res.problems
            ],
            "graded": len(res.graded),
            "failed": [f"{f.student} {f.question}: {f.reason}" for f in res.failed],
            "files": [p.name for p in res.export.written],
            "file_warnings": res.export.warnings,
        }  # fmt: skip

    return st.jobs.submit("confirm_files", run, {"exam": exam_id})


@router.get("/{exam_id}/needs-input")
def needs_input(exam_id: str, st: AppState = State) -> list[dict[str, Any]]:
    """`[판독불가]`가 남아 강사 입력을 기다리는 답안 (알림 숫자 = 시험 요약의 `needs_input`).
    누르면 `GET …/students/{학생}/transcripts/{문항}`으로 원본 그림과 판독문을 연다."""
    _, store = _store(st, exam_id)
    return [_waiting(t) for t in workflow.needs_input(store)]


@router.get("/{exam_id}/students/{student}/transcripts/{number}")
def get_transcript(exam_id: str, student: str, number: str, st: AppState = State) -> dict[str, Any]:
    _, store = _store(st, exam_id)
    t = store.transcript(student, number)
    if t is None:
        raise KeyError(f"{student} 문항 {number}: 판독문 없음")
    scan_dir = store.scan_dir(student)
    images = sorted(p.relative_to(store.root).as_posix() for p in scan_dir.glob("page_*.png"))
    return {
        "transcript": t.model_dump(mode="json"),
        "page_images": images,
        "needs_attention": t.needs_attention,
        "unreadable": t.unreadable,
        "question": store.exam().question(number).model_dump(mode="json"),
    }


class TranscriptBody(learning.LearnFields):
    lines: list[TranscriptLine]
    confirm: bool = False
    by: str | None = None


@router.put("/{exam_id}/students/{student}/transcripts/{number}")
def put_transcript(
    exam_id: str, student: str, number: str, body: TranscriptBody, st: AppState = State
) -> Transcript:
    svc, store = _store(st, exam_id)
    t = store.transcript(student, number)
    if t is None:
        raise KeyError(f"{student} 문항 {number}: 판독문 없음")
    before = t
    t = t.model_copy(
        update={
            "lines": body.lines,
            "confirmed": body.confirm,
            "confirmed_by": st.who(body.by) if body.confirm else None,
        }
    )
    if body.confirm and (problems := confirm_problems(t)):
        raise PipelineError(" ".join(problems))
    store.save_transcript(t)
    if body.learn:
        c = learning.transcript_correction(before, t, store.exam().title)
        learning.from_edit(st, body, c, st.who(body.by))
    if workflow.wants_grading(store, before, t):
        # 확정하면 그 학생·문항을 바로 채점한다 (작업 목록의 "grade", auto)

        def run(ctx: JobContext) -> dict[str, Any]:
            res = workflow.grade_after_confirm(svc, store, before, t, progress=ctx.progress)
            return _grade_summary(res) if res else {"graded": 0}

        st.jobs.submit("grade", run, {"exam": exam_id, "student": student, "question": number,
                                      "auto": True})  # fmt: skip
    else:  # AI를 부르지 않음: 고친 것 없이 확정만 했으면 채점의 '판독문 확인' 표시만 켠다
        workflow.grade_after_confirm(svc, store, before, t)
    return t


# ---------------------------------------------------------------- 채점·검토


class GradeBody(BaseModel):
    students: list[str] | None = None
    questions: list[str] | None = None
    double: bool = False
    """모든 답안 이중 채점 (기본은 설정 `second_opinion` — 확신도 낮은 것만)."""
    double_model: str | None = None
    regrade: bool = False
    use_examples: bool = True
    by: str | None = None
    """채점하는 강사 — 다른 강사가 맡은 문항은 건너뛴다 (§8)."""


@router.post("/{exam_id}/grade")
def grade(exam_id: str, body: GradeBody, st: AppState = State) -> Job:
    svc, store = _store(st, exam_id)
    # 승인 전 루브릭은 작업을 띄우기 전에 바로 알린다
    for q in workflow.select_questions(store.exam(), body.questions):
        r = store.rubric(q.number)
        if r is None or r.status != "approved":
            raise PipelineError(f"문항 {q.number}: 루브릭이 승인되지 않았습니다.")

    def run(ctx: JobContext) -> dict[str, Any]:
        res = workflow.grade_exam(
            svc,
            store,
            students=body.students,
            only=body.questions,
            double=SecondOpinion(model=body.double_model, when="all") if body.double else None,
            regrade=body.regrade,
            use_examples=body.use_examples,
            by=st.who(body.by),
            progress=ctx.progress,
        )
        return _grade_summary(res)

    return st.jobs.submit("grade", run, {"exam": exam_id})


def _grade_summary(res: workflow.GradeRun) -> dict[str, Any]:
    return {
        "graded": len(res.graded),
        "with_examples": sum(bool(g.examples_used) for g in res.graded),
        "needs_review": sum(g.status == "needs_review" for g in res.graded),
        "skipped": [vars(s) for s in res.skipped],
        "failed": [vars(s) for s in res.failed],
        "files": [p.name for p in res.export.written],
        "file_warnings": res.export.warnings,
        "calls": res.calls,
        "repairs": res.repairs,
        "tokens": res.tokens,
        "seconds": round(res.wall_s, 1),
        "workers": res.workers,
        "without_ai": sum(1 for g in res.graded if g.calls == 0),
    }


# ---------------------------------------------------------------- 문항 맡기 (§8)


class AssignBody(BaseModel):
    by: str = Field(min_length=1)
    take: bool = False
    """다른 강사가 맡은 문항 가져오기 (기록이 남음)."""


@router.get("/{exam_id}/assign")
def list_assignments(exam_id: str, st: AppState = State) -> dict[str, assign.Assignment]:
    _, store = _store(st, exam_id)
    return assign.all_assignments(store)


@router.put("/{exam_id}/assign/{number}")
def assign_question(
    exam_id: str, number: str, body: AssignBody, st: AppState = State
) -> assign.Assignment:
    _, store = _store(st, exam_id)
    return assign.assign(store, number, st.who(body.by), take=body.take)


@router.delete("/{exam_id}/assign/{number}")
def release_question(exam_id: str, number: str, by: str, st: AppState = State) -> assign.Assignment:
    _, store = _store(st, exam_id)
    return assign.release(store, number, st.who(by))


@router.get("/{exam_id}/board")
def board(exam_id: str, st: AppState = State) -> list[dict[str, Any]]:
    """진행판: 문항마다 맡은 강사, 채점 완료·검토 필요·입력 대기·채점 전 학생 수."""
    _, store = _store(st, exam_id)
    return [vars(b) for b in assign.board(store)]


_ORDER = {"needs_review": 0, "auto": 1, "teacher_confirmed": 2}


@router.get("/{exam_id}/review")
def review_list(exam_id: str, st: AppState = State) -> list[dict[str, Any]]:
    """채점 검토 목록: 검토 필요·이중 채점 불일치가 맨 위."""
    _, store = _store(st, exam_id)
    exam = store.exam()
    rows: list[dict[str, Any]] = []
    for s in store.students():
        for q in exam.questions:
            g = store.grade(s, q.number)
            if g is None:
                continue
            r = store.rubric(q.number)
            rows.append(
                {
                    "student": s,
                    "question": q.number,
                    "total": g.total,
                    "max": g.max_points,
                    "status": g.status,
                    "flags": g.flags,
                    "disagreements": g.disagreements,
                    "comment": g.comment,
                    "stale": r is not None and g.rubric_version != r.version,
                    "min_confidence": min((j.confidence for j in g.judgements), default=1.0),
                }
            )
    rows.sort(key=lambda x: (_ORDER[x["status"]], -len(x["disagreements"]), x["min_confidence"]))
    return rows


@router.get("/{exam_id}/students/{student}/grades/{number}")
def get_grade(exam_id: str, student: str, number: str, st: AppState = State) -> dict[str, Any]:
    _, store = _store(st, exam_id)
    g = store.grade(student, number)
    if g is None:
        raise KeyError(f"{student} 문항 {number}: 채점 결과 없음")
    t = store.transcript(student, number)
    r = store.rubric(number)
    return {
        "grade": g.model_dump(mode="json"),
        "transcript": t.model_dump(mode="json") if t else None,
        "rubric": r.model_dump(mode="json") if r else None,
        "question": store.exam().question(number).model_dump(mode="json"),
    }


class ReviewBody(learning.LearnFields):
    awarded: dict[str, float] = Field(default_factory=lambda: dict[str, float]())
    item_notes: dict[str, str] = Field(default_factory=lambda: dict[str, str]())
    note: str | None = None
    by: str | None = None


@router.put("/{exam_id}/students/{student}/grades/{number}")
def confirm_grade(
    exam_id: str, student: str, number: str, body: ReviewBody, st: AppState = State
) -> GradeResult:
    svc, store = _store(st, exam_id)
    g = store.grade(student, number)
    r = store.rubric(number)
    if g is None or r is None:
        raise KeyError(f"{student} 문항 {number}: 채점 결과 없음")
    notes = {k: v for k, v in body.item_notes.items() if v.strip()}
    before = g
    g = teacher_review(g, r, body.awarded, note=body.note, by=st.who(body.by), item_notes=notes)
    store.save_grade(g)
    workflow.export_grades(svc, store, [g])
    if body.learn:
        c = learning.grade_correction(before, g, r, store.exam().title)
        learning.from_edit(st, body, c, st.who(body.by))
    return g


class AnswerPagesBody(BaseModel):
    pages: dict[str, list[int]]


@router.put("/{exam_id}/answer-pages")
def answer_pages(exam_id: str, body: AnswerPagesBody, st: AppState = State) -> Exam:
    """문항마다 답안지 묶음의 몇 쪽에 답이 있는지 (표지가 1쪽) — 강사별 문항 분담에 쓴다."""
    _, store = _store(st, exam_id)
    return workflow.set_answer_pages(store, body.pages)


class BatchScanBody(BaseModel):
    path: str = Field(description="작업공간 안의 PDF (예: 스캔본/3반.pdf)")
    per_student: int = Field(ge=1)
    classes: list[str] = Field(default_factory=lambda: list[str]())
    questions: list[str] | None = None
    covers_only: bool = False
    cross: bool | None = None
    overwrite: bool = False
    grade: bool = True
    """판독이 끝난 학생은 이어서 채점 (루브릭이 승인된 문항). `[판독불가]` 답안만 기다린다."""


def _grade_ready(
    svc: Services, store: ExamStore, students: list[str], ctx: JobContext
) -> dict[str, Any]:
    """판독 작업 끝에 이어서 채점한 결과 + 입력을 기다리는 답안 (알림)."""
    res = workflow.grade_ready(svc, store, students, progress=ctx.progress)
    waiting = [t for t in workflow.needs_input(store) if t.student_id in students]
    return {
        "grading": _grade_summary(res) if res else None,
        "needs_input": [_waiting(t) for t in waiting],
    }


def _waiting(t: Transcript) -> dict[str, Any]:
    return {"student": t.student_id, "question": t.question_number, "unreadable": t.unreadable}


@router.post("/{exam_id}/scan-batch")
def scan_batch(exam_id: str, body: BatchScanBody, st: AppState = State) -> Job:
    """여러 학생 답안지 PDF 한 번에 — 표지로 학생 구분, 문항을 정하면 그 쪽만."""
    svc, store = _store(st, exam_id)
    pdf = files.inside(svc.workspace.root, body.path)
    if not pdf.is_file():
        raise KeyError(f"파일 없음: {body.path}")

    def run(ctx: JobContext) -> dict[str, Any]:
        res = workflow.scan_batch(
            svc, store, pdf, per_student=body.per_student, classes=body.classes or None,
            only_questions=body.questions, covers_only=body.covers_only, cross=body.cross,
            overwrite=body.overwrite, progress=ctx.progress,
        )  # fmt: skip
        students = [r.student_id for r in res.students]
        return {
            "students": students,
            "skipped": res.skipped,
            "warnings": res.warnings,
            "failed": [f"{f.student}: {f.reason}" for f in res.failed],
            "needs_rename": [r.student_id for r in res.needs_rename],
            "not_in_roster": [
                {"student": r.student_id, "note": r.roster_note} for r in res.not_in_roster
            ],
            "files": [p.name for p in res.export.written],
            "file_warnings": res.export.warnings,
            **_grade_ready(svc, store, students if body.grade else [], ctx),
        }

    return st.jobs.submit("scan_batch", run, {"exam": exam_id, "file": pdf.name})


class PartReviewBody(learning.LearnFields):
    part_scores: dict[str, float]
    part_notes: dict[str, str] = Field(default_factory=lambda: dict[str, str]())
    note: str | None = None
    by: str | None = None


@router.put("/{exam_id}/students/{student}/grades/{number}/parts")
def confirm_grade_parts(
    exam_id: str, student: str, number: str, body: PartReviewBody, st: AppState = State
) -> GradeResult:
    """강사가 소문항 단위로 확정 (빨간 펜처럼). 항목 점수는 앱이 나눈다."""
    svc, store = _store(st, exam_id)
    g = store.grade(student, number)
    r = store.rubric(number)
    if g is None or r is None:
        raise KeyError(f"{student} 문항 {number}: 채점 결과 없음")
    q = store.exam().question(number)
    notes = {k: v for k, v in body.part_notes.items() if v.strip()}
    before = g
    g = teacher_part_review(
        g, r, q, body.part_scores, part_notes=notes, note=body.note, by=st.who(body.by)
    )
    store.save_grade(g)
    workflow.export_grades(svc, store, [g])
    if body.learn:
        c = learning.grade_correction(before, g, r, store.exam().title)
        learning.from_edit(st, body, c, st.who(body.by))
    return g


class TeacherCsvBody(BaseModel):
    csv: str = Field(min_length=1, description="학생,문항,소문항,점수[,이유]")
    by: str | None = None


@router.post("/{exam_id}/teacher-scores")
def teacher_scores(exam_id: str, body: TeacherCsvBody, st: AppState = State) -> Job:
    """강사가 직접 매긴 소문항 점수 여러 명 (AI 채점이 없으면 먼저 채점)."""
    svc, store = _store(st, exam_id)
    rows = workflow.parse_teacher_csv(body.csv)

    def run(ctx: JobContext) -> dict[str, Any]:
        res = workflow.import_teacher_scores(
            svc, store, rows, by=st.who(body.by), progress=ctx.progress
        )
        return {
            "saved": [f"{g.student_id} {g.question_number}" for g in res.saved],
            "graded_first": res.graded_first,
            "failed": [f"{f.student} {f.question}: {f.reason}" for f in res.failed],
        }

    return st.jobs.submit("teacher_scores", run, {"exam": exam_id})


# ---------------------------------------------------------------- 함께 쓰기 (M12)


class PresenceBody(BaseModel):
    teacher: str | None = None


@router.post("/{exam_id}/presence")
def presence(exam_id: str, body: PresenceBody, st: AppState = State) -> dict[str, Any]:
    """화면이 시험을 연 동안 1분마다 부른다. 함께 쓰는 폴더면 다른 PC의 표식을 돌려준다."""
    svc, store = _store(st, exam_id)
    store.exam()
    if open_workspace(svc.workspace.root)[1].mode != "team":
        return {"mode": "personal", "others": []}
    sharing.mark_in_use(svc.workspace, exam_id, st.machine, st.who(body.teacher))
    others = sharing.others_using(svc.workspace, exam_id, st.machine)
    return {"mode": "team", "others": [o.model_dump(mode="json") for o in others]}


@router.delete("/{exam_id}/presence")
def leave(exam_id: str, st: AppState = State) -> dict[str, bool]:
    svc, _ = _store(st, exam_id)
    sharing.release(svc.workspace, exam_id, st.machine)
    return {"ok": True}
