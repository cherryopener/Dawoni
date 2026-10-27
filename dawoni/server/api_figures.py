"""파라미터 그림 API (M10): 재현본 보기, AI로 스펙 만들기(원본 비교 루프), 모드 바꾸기."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel

from dawoni.figures.common import FigureError
from dawoni.figures.render import item_values, render_figure
from dawoni.generate.workflow import GenStore
from dawoni.grading.render import question_text
from dawoni.grading.store import ExamStore
from dawoni.models.figure import Figure, FigureSpec
from dawoni.models.item import ItemContent
from dawoni.models.job import Job
from dawoni.server.api_exams import _store  # pyright: ignore[reportPrivateUsage]
from dawoni.server.api_setup import State
from dawoni.server.jobs import JobContext
from dawoni.server.state import AppState

router = APIRouter(prefix="/api")


def _figure(item: ItemContent, fid: str) -> Figure:
    for f in item.figures:
        if f.id == fid:
            return f
    raise KeyError(f"그림 {fid} 없음")


def _render(item: ItemContent, fid: str, cache: Path) -> FileResponse:
    fig = _figure(item, fid)
    try:
        return FileResponse(render_figure(fig, item, cache), headers={"Cache-Control": "no-store"})
    except FigureError as e:
        raise ValueError(f"그림 {fid}: {e}") from e


@router.get("/exams/{exam_id}/questions/{number}/figures/{fid}/render")
def render_exam_figure(exam_id: str, number: str, fid: str, st: AppState = State) -> FileResponse:
    _, store = _store(st, exam_id)
    q = store.exam().question(number)
    return _render(q.item, fid, store.root / "figures" / "_render")


@router.get("/generated/{gen_id}/figures/{fid}/render")
def render_generated_figure(gen_id: str, fid: str, st: AppState = State) -> FileResponse:
    ws = st.services().workspace
    s = GenStore(ws).get(gen_id)
    return _render(s.item, fid, ws.bank / "generated" / "_render")


def _replace_figure(store: ExamStore, number: str, fig: Figure) -> Figure:
    exam = store.exam()
    q = exam.question(number)
    figs = [fig if f.id == fig.id else f for f in q.item.figures]
    item = ItemContent.model_validate(
        {**q.item.model_dump(), "figures": [f.model_dump() for f in figs]}
    )
    questions = [
        x.model_copy(update={"item": item}) if x.number == number else x for x in exam.questions
    ]
    store.save_exam(exam.model_copy(update={"questions": questions}))
    return fig


class FigureUpdate(BaseModel):
    mode: Literal["parametric", "raster"] | None = None
    spec: FigureSpec | None = None


@router.put("/exams/{exam_id}/questions/{number}/figures/{fid}")
def update_figure(
    exam_id: str, number: str, fid: str, body: FigureUpdate, st: AppState = State
) -> Figure:
    _, store = _store(st, exam_id)
    fig = _figure(store.exam().question(number).item, fid)
    data = fig.model_dump()
    if body.spec is not None:
        data["spec"] = body.spec.model_dump()
    if body.mode is not None:
        data["mode"] = body.mode
    return _replace_figure(store, number, Figure.model_validate(data))


@router.post("/exams/{exam_id}/questions/{number}/figures/{fid}/extract")
def extract(exam_id: str, number: str, fid: str, st: AppState = State) -> Job:
    from dawoni.figures.extract import extract_figure

    svc, store = _store(st, exam_id)
    q = store.exam().question(number)
    fig = _figure(q.item, fid)
    original = store.root / fig.orig if fig.orig else None
    if original is None or not original.is_file():
        raise ValueError(f"그림 {fid}: 원본 이미지가 없습니다")

    def run(ctx: JobContext) -> dict[str, Any]:
        ctx.progress(0, 2, "AI가 그림 종류를 판별하고 스펙 작성")
        ex = extract_figure(
            svc.engine,
            original,
            store.root / "figures" / "_extract" / f"{number}_{fid}",
            item_text=question_text(q.number, q.points, q.item),
            caption=fig.caption,
            values=item_values(q.item),
        )
        ctx.progress(2, 2, "완료 — 원본과 나란히 보고 정하세요")
        if ex.spec is not None:
            _replace_figure(store, number, fig.model_copy(update={"spec": ex.spec}))
        return {
            "verdict": ex.verdict,
            "reason": ex.reason,
            "matched": ex.matched,
            "rounds": ex.rounds,
            "differences": ex.differences,
        }

    return st.jobs.submit(
        "figure_extract", run, {"exam": exam_id, "question": number, "figure": fid}
    )
