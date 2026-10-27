"""문항 제작 API (M9): 동형·신규 A·B 제작, 확장 개념 후보, 검토, 새 시험으로 묶기."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from dawoni.generate import pipeline as gp
from dawoni.generate import workflow as gw
from dawoni.models.generation import GeneratedSet, GenKind, GenStatus
from dawoni.models.grading import Exam
from dawoni.models.job import Job
from dawoni.models.scope import ExtensionConcept
from dawoni.server.api_exams import _store  # pyright: ignore[reportPrivateUsage]
from dawoni.server.api_setup import State
from dawoni.server.jobs import JobContext
from dawoni.server.state import AppState

router = APIRouter(prefix="/api")


@router.get("/exams/{exam_id}/generated")
def list_generated(exam_id: str, st: AppState = State) -> list[GeneratedSet]:
    svc, _ = _store(st, exam_id)
    return gw.GenStore(svc.workspace).for_exam(exam_id)


class GenerateBody(BaseModel):
    question: str
    kind: GenKind
    count: int = Field(default=2, ge=1, le=6)
    max_den: int = Field(default=20, ge=1, le=1000)
    allow_sqrt: bool = False
    max_overlap: float = Field(default=0.5, ge=0, le=1)
    extension: ExtensionConcept | None = None


@router.post("/exams/{exam_id}/generate")
def start_generate(exam_id: str, body: GenerateBody, st: AppState = State) -> Job:
    svc, store = _store(st, exam_id)
    store.exam().question(body.question)
    opts = gp.Options(
        count=body.count,
        max_den=body.max_den,
        allow_sqrt=body.allow_sqrt,
        max_overlap=body.max_overlap,
    )

    def run(ctx: JobContext) -> dict[str, Any]:
        sets = gw.generate(
            svc,
            store,
            body.question,
            body.kind,
            opts=opts,
            extension=body.extension,
            progress=ctx.progress,
        )
        return {
            "ids": [s.id for s in sets],
            "verified": sum(s.solution.verified for s in sets),
        }

    return st.jobs.submit("generate", run, {"exam": exam_id, "kind": body.kind})


@router.post("/exams/{exam_id}/questions/{number}/extensions")
def propose_extensions(exam_id: str, number: str, st: AppState = State) -> Job:
    svc, store = _store(st, exam_id)
    store.exam().question(number)

    def run(ctx: JobContext) -> dict[str, Any]:
        ctx.progress(0, 1, "확장 개념 후보 찾는 중")
        p = gw.propose(svc, store, number)
        ctx.progress(1, 1, "후보 완료")
        return p.model_dump(mode="json")

    return st.jobs.submit("propose_extensions", run, {"exam": exam_id, "question": number})


class ReviewBody(BaseModel):
    status: GenStatus
    by: str | None = None


@router.put("/generated/{gen_id}/review")
def review_generated(gen_id: str, body: ReviewBody, st: AppState = State) -> GeneratedSet:
    return gw.review(gw.GenStore(st.services().workspace), gen_id, body.status, body.by)


class ToExamBody(BaseModel):
    ids: list[str] = Field(min_length=1)
    title: str = Field(min_length=1)


@router.post("/generated/to-exam")
def to_exam(body: ToExamBody, st: AppState = State) -> Exam:
    return gw.exam_from_sets(st.services().workspace, body.ids, body.title)
