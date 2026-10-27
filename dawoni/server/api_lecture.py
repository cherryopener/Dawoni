"""강의 요약 API (M12): 유튜브 주소 → 자막 → 요약(작업) → Word."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from dawoni.export.docx.fonts import FontSpec
from dawoni.grading.store import ScopeStore
from dawoni.lecture import workflow as lw
from dawoni.lecture.youtube import video_id_of
from dawoni.models.job import Job
from dawoni.models.lecture import LectureSummary, PageMode
from dawoni.server.api_setup import State
from dawoni.server.jobs import JobContext
from dawoni.server.state import AppState

router = APIRouter(prefix="/api/lectures")


@router.get("")
def list_summaries(st: AppState = State) -> list[LectureSummary]:
    return lw.LectureStore(st.services().workspace).all()


@router.get("/{summary_id}")
def get_summary(summary_id: str, st: AppState = State) -> LectureSummary:
    return lw.LectureStore(st.services().workspace).get(summary_id)


@router.delete("/{summary_id}")
def delete_summary(summary_id: str, st: AppState = State) -> dict[str, bool]:
    lw.LectureStore(st.services().workspace).delete(summary_id)
    return {"ok": True}


class SummarizeBody(BaseModel):
    url: str
    scope_id: str | None = None
    pages: PageMode = 1
    refresh: bool = False


@router.post("/summarize")
def start_summarize(body: SummarizeBody, st: AppState = State) -> Job:
    svc = st.services()
    video_id_of(body.url)  # 주소가 틀리면 작업을 만들기 전에 바로 알린다
    if body.scope_id:
        ScopeStore(svc.workspace).get(body.scope_id)

    def run(ctx: JobContext) -> dict[str, Any]:
        s = lw.run_summary(
            svc, body.url, scope_id=body.scope_id, pages=body.pages, refresh=body.refresh,
            loader=st.lecture_loader, progress=ctx.progress,
        )  # fmt: skip
        return {"id": s.id}

    return st.jobs.submit("lecture_summary", run, {"url": body.url})


@router.post("/{summary_id}/export")
def export_summary(summary_id: str, st: AppState = State) -> dict[str, Any]:
    svc = st.services()
    s = lw.LectureStore(svc.workspace).get(summary_id)
    d = svc.config.docx
    res = lw.export_summary(
        svc.workspace, s, FontSpec(body=d.body_font, math=d.math_font, size_pt=d.body_size_pt)
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
