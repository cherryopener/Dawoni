"""문항 제작 API (M9): 동형·신규 A·B 제작, 확장 개념 후보, 검토, 새 시험으로 묶기."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from dawoni.generate import pipeline as gp
from dawoni.generate import workflow as gw
from dawoni.grading.pipeline import PipelineError
from dawoni.models.generation import AnswerFormat, GeneratedSet, GenStatus, VariantKind
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
    kind: VariantKind
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


# ---------------------------------------------------------------- 문제 만들기 (문제은행 기준)


@router.get("/generated")
def list_all_generated(st: AppState = State) -> list[GeneratedSet]:
    return gw.GenStore(st.services().workspace).all()


@router.get("/bank/items/{item_id}/generated")
def list_bank_generated(item_id: str, st: AppState = State) -> list[GeneratedSet]:
    return gw.GenStore(st.services().workspace).for_bank_item(item_id)


class BankGenerateBody(BaseModel):
    kind: VariantKind
    scope_id: str | None = Field(default=None, description="없으면 기준 문항 단원의 단원표 범위")
    count: int = Field(default=2, ge=1, le=6)
    max_den: int = Field(default=20, ge=1, le=1000)
    allow_sqrt: bool = False
    max_overlap: float = Field(default=0.5, ge=0, le=1)
    extension: ExtensionConcept | None = None


@router.post("/bank/items/{item_id}/generate")
def start_bank_generate(item_id: str, body: BankGenerateBody, st: AppState = State) -> Job:
    from dawoni.bank.make import generate_from_bank
    from dawoni.bank.store import BankStore

    svc = st.services()
    if BankStore(svc.workspace).get(item_id).needs_check:
        raise PipelineError("답 확인이 필요한 문항입니다 — 문제은행에서 맞는 답을 먼저 고르세요")
    opts = gp.Options(count=body.count, max_den=body.max_den, allow_sqrt=body.allow_sqrt,
                      max_overlap=body.max_overlap)  # fmt: skip

    def run(ctx: JobContext) -> dict[str, Any]:
        sets = generate_from_bank(svc, item_id, body.kind, scope_id=body.scope_id, opts=opts,
                                  extension=body.extension, progress=ctx.progress)  # fmt: skip
        return {"ids": [s.id for s in sets], "verified": sum(s.solution.verified for s in sets)}

    return st.jobs.submit("generate", run, {"bank_item": item_id, "kind": body.kind})


class BankExtBody(BaseModel):
    scope_id: str | None = None


@router.post("/bank/items/{item_id}/extensions")
def propose_bank_extensions(item_id: str, body: BankExtBody, st: AppState = State) -> Job:
    from dawoni.bank.make import propose_from_bank

    svc = st.services()

    def run(ctx: JobContext) -> dict[str, Any]:
        ctx.progress(0, 1, "확장 개념 후보 찾는 중")
        p = propose_from_bank(svc, item_id, body.scope_id)
        ctx.progress(1, 1, "후보 완료")
        return p.model_dump(mode="json")

    return st.jobs.submit("propose_extensions", run, {"bank_item": item_id})


class ToBankBody(BaseModel):
    by: str | None = None


@router.post("/generated/{gen_id}/to-bank")
def generated_to_bank(gen_id: str, body: ToBankBody, st: AppState = State) -> dict[str, str]:
    """승인 + 문제은행에 넣기."""
    from dawoni.bank.make import approve_to_bank

    return {"item_id": approve_to_bank(st.services(), gen_id, body.by).id}


# ---------------------------------------------------------------- 새 문제 (강사 아이디어)


class ExtDraftBody(BaseModel):
    concept: str = Field(min_length=1, description="강사가 말로 적은 교과 밖 개념")
    idea: str = ""
    unit_ids: list[str] = Field(default_factory=lambda: list[str]())
    scope_id: str | None = None


@router.post("/make/extension")
def draft_extension(body: ExtDraftBody, st: AppState = State) -> Job:
    from dawoni.bank.make import draft_extension_for

    svc = st.services()

    def run(ctx: JobContext) -> dict[str, Any]:
        ctx.progress(0, 1, "추가 개념을 제시문용으로 정리하는 중")
        x = draft_extension_for(svc, body.scope_id, body.unit_ids, body.concept, body.idea)
        ctx.progress(1, 1, "정리 완료 — 확인·수정하세요")
        return x.model_dump(mode="json")

    return st.jobs.submit("draft_extension", run, {})


class NewProblemBody(BaseModel):
    idea: str = Field(min_length=1)
    answer_format: AnswerFormat
    points: float = Field(gt=0)
    unit_ids: list[str] = Field(default_factory=lambda: list[str]())
    scope_id: str | None = None
    difficulty: int = Field(default=3, ge=1, le=5)
    n_parts: int = Field(default=1, ge=1, le=5)
    extension: ExtensionConcept | None = None
    reference_ids: list[str] = Field(default_factory=lambda: list[str]())


@router.post("/make/new")
def start_new_problem(body: NewProblemBody, st: AppState = State) -> Job:
    from dawoni.bank.make import make_new_problem

    svc = st.services()
    b = body

    def run(ctx: JobContext) -> dict[str, Any]:
        s = make_new_problem(
            svc, idea=b.idea, answer_format=b.answer_format, points=b.points,
            unit_ids=b.unit_ids, scope_id=b.scope_id, difficulty=b.difficulty,
            n_parts=b.n_parts, extension=b.extension, reference_ids=b.reference_ids,
            progress=ctx.progress,
        )  # fmt: skip
        return {"ids": [s.id], "verified": int(s.solution.verified)}

    return st.jobs.submit("new_problem", run, {"format": b.answer_format})
