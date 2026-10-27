"""시험 API: 문항 → 해설·루브릭(승인) → 답안(입력·판독, 확인) → 채점(검토) → 출력."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from dawoni.cli_exam import parse_answer_text
from dawoni.export.docx.documents import KeyOptions, PaperOptions
from dawoni.export.docx.export import ExportKind, export_exam
from dawoni.export.docx.fonts import FontSpec
from dawoni.grading import workflow
from dawoni.grading.pipeline import (
    PipelineError,
    SecondOpinion,
    approve_rubric,
    edit_rubric,
    teacher_review,
)
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.models.grading import (
    Exam,
    ExamQuestion,
    GradeResult,
    Rubric,
    RubricDraft,
    Transcript,
    TranscriptLine,
)
from dawoni.models.job import Job
from dawoni.server import files
from dawoni.server.api_content import save_upload, upload_dir
from dawoni.server.api_setup import State
from dawoni.server.jobs import JobContext
from dawoni.server.state import AppState
from dawoni.services import Services

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
        else {"status": r.status, "version": r.version, "total": r.total, "items": len(r.items)},
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
    return {
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
        "graded": sum(c["grade"] is not None for c in cells),
        "needs_review": sum(
            c["grade"] is not None and c["grade"]["status"] == "needs_review" for c in cells
        ),
    }


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


@router.post("/from-pdf")
def exam_from_pdf(
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()],
    scope_id: Annotated[str, Form()],
    subject: Annotated[str | None, Form()] = None,
    force: Annotated[bool, Form()] = False,
    st: AppState = State,
) -> Job:
    from dawoni.ingest.workflow import ingest_exam_pdf

    svc = st.services()
    ScopeStore(svc.workspace).get(scope_id)
    path = save_upload(file, upload_dir(st))

    def run(ctx: JobContext) -> dict[str, Any]:
        res = ingest_exam_pdf(
            svc, path, title=title, scope_id=scope_id, subject=subject, force=force,
            progress=ctx.progress,
        )  # fmt: skip
        return {"exam_id": res.exam.id, "warnings": res.warnings + res.layout.warnings}

    return st.jobs.submit("ingest_exam", run, {"title": title, "file": path.name})


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


@router.post("/{exam_id}/solve")
def solve(exam_id: str, body: QuestionsBody, st: AppState = State) -> Job:
    svc, store = _store(st, exam_id)
    verify = SecondOpinion(model=body.verify_model) if body.verify else None

    def run(ctx: JobContext) -> dict[str, Any]:
        sols = workflow.solve_questions(
            svc, store, body.questions, verify=verify, progress=ctx.progress
        )
        return {"solved": [s.question_number for s in sols]}

    return st.jobs.submit("solve", run, {"exam": exam_id})


@router.post("/{exam_id}/rubrics")
def make_rubrics(exam_id: str, body: QuestionsBody, st: AppState = State) -> Job:
    svc, store = _store(st, exam_id)

    def run(ctx: JobContext) -> dict[str, Any]:
        rs = workflow.rubric_questions(svc, store, body.questions, progress=ctx.progress)
        return {"rubrics": [r.question_number for r in rs]}

    return st.jobs.submit("rubric", run, {"exam": exam_id})


@router.get("/{exam_id}/questions/{number}")
def get_question(exam_id: str, number: str, st: AppState = State) -> dict[str, Any]:
    _, store = _store(st, exam_id)
    q = store.exam().question(number)
    sol = store.solution(number)
    r = store.rubric(number)
    return {
        "question": q.model_dump(mode="json"),
        "solution": sol.model_dump(mode="json") if sol else None,
        "rubric": r.model_dump(mode="json") if r else None,
    }


@router.put("/{exam_id}/questions/{number}/rubric")
def put_rubric(exam_id: str, number: str, draft: RubricDraft, st: AppState = State) -> Rubric:
    _, store = _store(st, exam_id)
    q = store.exam().question(number)
    old = store.rubric(number)
    sol = store.solution(number)
    if old is None or sol is None:
        raise PipelineError(f"문항 {number}: 해설·루브릭 초안을 먼저 만드세요.")
    r = edit_rubric(old, draft, q, sol)
    store.save_rubric(r)
    return r


class ByBody(BaseModel):
    by: str | None = None


@router.post("/{exam_id}/questions/{number}/rubric/approve")
def approve(exam_id: str, number: str, body: ByBody, st: AppState = State) -> Rubric:
    _, store = _store(st, exam_id)
    r = store.rubric(number)
    if r is None:
        raise PipelineError(f"문항 {number}: 루브릭이 없습니다.")
    r = approve_rubric(r, body.by)
    store.save_rubric(r)
    return r


# ---------------------------------------------------------------- 학생 답안


@router.get("/{exam_id}/students")
def students(exam_id: str, st: AppState = State) -> list[dict[str, Any]]:
    _, store = _store(st, exam_id)
    exam = store.exam()
    return [
        {"student": s, "cells": {q.number: _cell(store, s, q) for q in exam.questions}}
        for s in store.students()
    ]


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
        confirmed_by=body.by,
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
        }

    return st.jobs.submit("scan", run, {"exam": exam_id, "student": student})


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
        "question": store.exam().question(number).model_dump(mode="json"),
    }


class TranscriptBody(BaseModel):
    lines: list[TranscriptLine]
    confirm: bool = False
    by: str | None = None


@router.put("/{exam_id}/students/{student}/transcripts/{number}")
def put_transcript(
    exam_id: str, student: str, number: str, body: TranscriptBody, st: AppState = State
) -> Transcript:
    _, store = _store(st, exam_id)
    t = store.transcript(student, number)
    if t is None:
        raise KeyError(f"{student} 문항 {number}: 판독문 없음")
    t = t.model_copy(
        update={
            "lines": body.lines,
            "confirmed": body.confirm,
            "confirmed_by": body.by if body.confirm else None,
        }
    )
    store.save_transcript(t)
    return t


# ---------------------------------------------------------------- 채점·검토


class GradeBody(BaseModel):
    students: list[str] | None = None
    questions: list[str] | None = None
    double: bool = False
    double_model: str | None = None
    regrade: bool = False
    use_examples: bool = True


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
            double=SecondOpinion(model=body.double_model) if body.double else None,
            regrade=body.regrade,
            use_examples=body.use_examples,
            progress=ctx.progress,
        )
        return {
            "graded": len(res.graded),
            "with_examples": sum(bool(g.examples_used) for g in res.graded),
            "needs_review": sum(g.status == "needs_review" for g in res.graded),
            "skipped": [vars(s) for s in res.skipped],
            "failed": [vars(s) for s in res.failed],
        }

    return st.jobs.submit("grade", run, {"exam": exam_id})


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


class ReviewBody(BaseModel):
    awarded: dict[str, float] = Field(default_factory=lambda: dict[str, float]())
    item_notes: dict[str, str] = Field(default_factory=lambda: dict[str, str]())
    note: str | None = None
    by: str | None = None


@router.put("/{exam_id}/students/{student}/grades/{number}")
def confirm_grade(
    exam_id: str, student: str, number: str, body: ReviewBody, st: AppState = State
) -> GradeResult:
    _, store = _store(st, exam_id)
    g = store.grade(student, number)
    r = store.rubric(number)
    if g is None or r is None:
        raise KeyError(f"{student} 문항 {number}: 채점 결과 없음")
    notes = {k: v for k, v in body.item_notes.items() if v.strip()}
    g = teacher_review(g, r, body.awarded, note=body.note, by=body.by, item_notes=notes)
    store.save_grade(g)
    return g


# ---------------------------------------------------------------- 출력


class ExportBody(BaseModel):
    kind: ExportKind
    columns: Literal[1, 2] = 2
    show_points: bool = True
    answer_lines: int = Field(default=8, ge=0, le=40)
    subtitle: str | None = None
    with_questions: bool = False
    with_rubric: bool = False
    students: list[str] | None = None


@router.post("/{exam_id}/export")
def export(exam_id: str, body: ExportBody, st: AppState = State) -> dict[str, Any]:
    svc, store = _store(st, exam_id)
    d = svc.config.docx
    res = export_exam(
        store,
        body.kind,
        FontSpec(body=d.body_font, math=d.math_font, size_pt=d.body_size_pt),
        svc.workspace.exports,
        paper=PaperOptions(
            columns=body.columns,
            show_points=body.show_points,
            answer_lines=body.answer_lines,
            subtitle=body.subtitle,
        ),
        key=KeyOptions(with_questions=body.with_questions, with_rubric=body.with_rubric),
        students=body.students,
    )
    return {
        "path": Path(res.path).relative_to(svc.workspace.root).as_posix(),
        "name": res.path.name,
        "ok": res.check.ok,
        "summary": res.check.summary(),
        "errors": res.check.errors,
        "warnings": res.warnings,
        "math_fallbacks": len(res.math_fallbacks),
    }
