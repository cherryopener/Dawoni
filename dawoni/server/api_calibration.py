"""강사 보정 API (M8): 기준 예시·일치도·보정 효과 측정·루브릭 개선 제안·강사 직접 채점."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from dawoni.grading import calibration, workflow
from dawoni.grading.pipeline import PipelineError, edit_rubric, teacher_grade
from dawoni.models.calibration import CalibrationEval, CalibrationSettings
from dawoni.models.grading import GradeResult, Rubric, RubricDraft, Transcript
from dawoni.models.job import Job
from dawoni.server.api_exams import _store  # pyright: ignore[reportPrivateUsage]
from dawoni.server.api_setup import State
from dawoni.server.jobs import JobContext
from dawoni.server.state import AppState

router = APIRouter(prefix="/api/exams")


def _rubric(store: Any, q: str) -> Rubric:
    r = store.rubric(q)
    if r is None:
        raise PipelineError(f"문항 {q}: 루브릭이 없습니다.")
    return r


@router.get("/{exam_id}/calibration/{number}")
def calibration_view(exam_id: str, number: str, st: AppState = State) -> dict[str, Any]:
    _, store = _store(st, exam_id)
    store.exam().question(number)
    r = store.rubric(number)
    settings = calibration.load_settings(store, number)
    cands = calibration.candidates(store, number, r) if r else []
    # 미리 보기: 특정 대상 없이 고른 순서 (대상이 정해지면 비슷한 답안 쪽으로 바뀔 수 있음)
    blank = Transcript(student_id="(미리 보기)", question_number=number)
    order = [c.student for c in calibration.select_examples(cands, blank, settings.max_examples)]
    rank = {s: i for i, s in enumerate(order)}
    excluded_rows: list[dict[str, Any]] = []
    for s in settings.excluded:
        g = store.grade(s, number)
        if g is not None and g.status == "teacher_confirmed":
            excluded_rows.append({"student": s, "total": g.total, "max": g.max_points})
    return {
        "settings": settings.model_dump(mode="json"),
        "candidates": [
            {
                "student": c.student,
                "kind": c.kind,
                "total": c.grade.total,
                "max": c.grade.max_points,
                "pinned": c.pinned,
                "changed": {k: list(v) for k, v in c.changed_items.items()},
                "teacher_note": c.grade.teacher_note,
                "order": rank[c.student] + 1 if c.student in rank else None,
            }
            for c in sorted(cands, key=lambda c: (rank.get(c.student, len(rank)), c.student))
        ],
        "excluded": excluded_rows,
        "agreement": calibration.question_agreement(store, number).model_dump(mode="json"),
        "evals": [
            {
                "id": e.id,
                "created_at": e.created_at.isoformat(),
                "rubric_version": e.rubric_version,
                "n": len(e.cases),
                "before": e.before.model_dump(mode="json"),
                "after": e.after.model_dump(mode="json"),
            }
            for e in calibration.list_evals(store, number)
        ],
        "rubric": r.model_dump(mode="json") if r else None,
    }


@router.put("/{exam_id}/calibration/{number}/settings")
def put_settings(
    exam_id: str, number: str, body: CalibrationSettings, st: AppState = State
) -> CalibrationSettings:
    _, store = _store(st, exam_id)
    store.exam().question(number)
    if body.question_number != number:
        raise ValueError("문항 번호가 다릅니다")
    overlap = sorted(set(body.pinned) & set(body.excluded))
    if overlap:
        raise ValueError(f"고정과 제외에 함께 있는 답안: {overlap}")
    calibration.save_settings(store, body)
    return body


@router.get("/{exam_id}/agreement")
def exam_agreement(exam_id: str, st: AppState = State) -> dict[str, Any]:
    _, store = _store(st, exam_id)
    exam = store.exam()
    per_q = {q.number: calibration.question_agreement(store, q.number) for q in exam.questions}
    pairs = [
        p
        for q in exam.questions
        for s in store.students()
        if (g := store.grade(s, q.number)) is not None
        and (p := calibration.ai_vs_teacher(g)) is not None
    ]
    return {
        "overall": calibration.agreement(pairs).model_dump(mode="json"),
        "questions": {k: v.model_dump(mode="json") for k, v in per_q.items()},
    }


class EvalBody(BaseModel):
    k: int | None = Field(default=None, ge=1, le=6)


@router.post("/{exam_id}/calibration/{number}/eval")
def run_eval(exam_id: str, number: str, body: EvalBody, st: AppState = State) -> Job:
    svc, store = _store(st, exam_id)
    r = _rubric(store, number)
    if len(calibration.candidates(store, number, r)) < 2:
        raise PipelineError("강사가 확정한 답안이 2개 이상 있어야 측정할 수 있습니다.")

    def run(ctx: JobContext) -> dict[str, Any]:
        ev = workflow.evaluate_calibration(svc, store, number, k=body.k, progress=ctx.progress)
        return {"eval_id": ev.id}

    return st.jobs.submit("calibration_eval", run, {"exam": exam_id, "question": number})


@router.get("/{exam_id}/calibration/{number}/evals/{eval_id}")
def get_eval(exam_id: str, number: str, eval_id: str, st: AppState = State) -> CalibrationEval:
    _, store = _store(st, exam_id)
    for e in calibration.list_evals(store, number):
        if e.id == eval_id:
            return e
    raise KeyError(f"측정 {eval_id} 없음")


@router.post("/{exam_id}/calibration/{number}/suggest")
def suggest(exam_id: str, number: str, st: AppState = State) -> Job:
    svc, store = _store(st, exam_id)
    _rubric(store, number)

    def run(ctx: JobContext) -> dict[str, Any]:
        ctx.progress(0, 1, "강사 수정 사례로 루브릭 개선안 만드는 중")
        res = workflow.suggest_rubric_changes(svc, store, number)
        ctx.progress(1, 1, "제안 완료")
        return {"suggestion": res.suggestion.model_dump(mode="json"), "cases": res.cases}

    return st.jobs.submit("suggest_rubric", run, {"exam": exam_id, "question": number})


class ApplyBody(BaseModel):
    revised: RubricDraft


@router.post("/{exam_id}/calibration/{number}/apply")
def apply_suggestion(exam_id: str, number: str, body: ApplyBody, st: AppState = State) -> Rubric:
    """제안을 새 루브릭 초안으로 (다시 승인해야 채점에 쓰임)."""
    _, store = _store(st, exam_id)
    q = store.exam().question(number)
    old = _rubric(store, number)
    sol = store.solution(number)
    if sol is None:
        raise PipelineError(f"문항 {number}: 해설이 없습니다.")
    r = edit_rubric(old, body.revised, q, sol)
    store.save_rubric(r)
    return r


class ManualBody(BaseModel):
    awarded: dict[str, float]
    item_notes: dict[str, str] = Field(default_factory=lambda: dict[str, str]())
    note: str | None = None
    comment: str = ""
    by: str | None = None


@router.post("/{exam_id}/students/{student}/grades/{number}/manual")
def manual_grade(
    exam_id: str, student: str, number: str, body: ManualBody, st: AppState = State
) -> GradeResult:
    """강사가 AI 없이 직접 채점 — 기준 예시가 된다."""
    _, store = _store(st, exam_id)
    t = store.transcript(student, number)
    if t is None:
        raise KeyError(f"{student} 문항 {number}: 판독문 없음")
    r = _rubric(store, number)
    g = teacher_grade(
        t,
        r,
        body.awarded,
        comment=body.comment,
        note=body.note,
        item_notes={k: v for k, v in body.item_notes.items() if v.strip()},
        by=body.by,
    )
    store.save_grade(g)
    return g
