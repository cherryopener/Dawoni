"""0단계 해설 다듬기 API (2026-11-18) — `dawoni.keyprep`.

POST   /api/keyprep                          시험지·해설 HWP/HWPX → 작업 "keyprep" (매개변수 `prep`)
GET    /api/keyprep                          초안 목록 (첫 화면)
GET    /api/keyprep/{id}                     초안 (문항·해설·다듬은 기준·점검)
PUT    /api/keyprep/{id}/questions/{번호}     강사가 고친 기준 저장
POST   /api/keyprep/{id}/refine              다듬기 다시 (문항을 고르면 그 문항만) → 작업
POST   /api/keyprep/{id}/export              다듬은 HWPX 쓰기 (exports/해설 다듬기/)
GET    /api/keyprep/{id}/hwpx                다듬은 HWPX 파일 (1단계에 바로 넘길 때)
GET    /api/keyprep/{id}/file?path=…         문항·해설 그림
DELETE /api/keyprep/{id}
"""

from __future__ import annotations

import shutil
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from dawoni import keyprep
from dawoni.models.job import Job
from dawoni.models.keyprep import KeyPrep, PrepPart, PrepQuestion
from dawoni.server import files
from dawoni.server.api_content import save_upload, upload_dir
from dawoni.server.api_setup import State
from dawoni.server.jobs import JobContext
from dawoni.server.state import AppState

router = APIRouter(prefix="/api/keyprep")


def _store(st: AppState, prep_id: str) -> keyprep.PrepStore:
    store = keyprep.PrepStore(st.services().workspace, prep_id)
    if not store.exists():
        raise KeyError(f"해설 다듬기 {prep_id} 없음")
    return store


def _view_question(q: PrepQuestion) -> dict[str, Any]:
    return {**q.model_dump(mode="json"), "problems": keyprep.question_problems(q),
            "pending": q.pending, "blanks": q.blanks, "has_key": q.has_key}  # fmt: skip


def _running(st: AppState, prep_id: str) -> bool:
    return any(
        j.kind == "keyprep"
        and j.params.get("prep") == prep_id
        and j.status in ("queued", "running")
        for j in st.jobs.list()
    )


def _view(p: KeyPrep) -> dict[str, Any]:
    data = p.model_dump(mode="json")
    data["questions"] = [_view_question(q) for q in p.questions]
    return data


def _summary(p: KeyPrep) -> dict[str, Any]:
    return {
        "id": p.id,
        "title": p.title,
        "source_name": p.source_name,
        "created_at": p.created_at.isoformat(),
        "state": p.state,
        "questions": len(p.questions),
        "done": sum(q.status == "done" for q in p.questions),
        "failed": sum(q.status == "failed" for q in p.questions),
        "pending": sum(q.pending for q in p.questions),
        "blanks": sum(q.blanks for q in p.questions),
        "exported": p.exported,
    }


def _refine_job(st: AppState, store: keyprep.PrepStore, numbers: list[str] | None) -> Job:
    svc = st.services()

    def run(ctx: JobContext) -> dict[str, Any]:
        p = keyprep.refine(svc, store, numbers, progress=ctx.progress)
        return _summary(p)

    prep = store.load()
    return st.jobs.submit("keyprep", run, {"prep": store.id, "title": prep.title})


@router.post("")
def start(
    file: Annotated[UploadFile, File()],
    level: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
    st: AppState = State,
) -> Job:
    """시험지(해설 포함) HWP·HWPX를 문항·해설로 나누고(AI 없음, 바로) AI 다듬기 작업을 띄운다."""
    svc = st.services()
    tmp = upload_dir(st)
    try:
        src = save_upload(file, tmp)
        store = keyprep.create(svc, src, level=level or "물리", title=title or None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return _refine_job(st, store, None)


def _now(st: AppState, p: KeyPrep) -> KeyPrep:
    """작업이 없는데 '다듬는 중'이면 멈춘 것으로 (앱을 닫았다 켬)."""
    return p if _running(st, p.id) else keyprep.stale(p)


@router.get("")
def list_preps(st: AppState = State) -> list[dict[str, Any]]:
    return [_summary(_now(st, p)) for p in keyprep.list_preps(st.services().workspace)]


@router.get("/{prep_id}")
def get_prep(prep_id: str, st: AppState = State) -> dict[str, Any]:
    return _view(_now(st, _store(st, prep_id).load()))


class QuestionBody(BaseModel):
    parts: list[PrepPart]
    explanation: str | None = None
    explanation_in: bool = True
    by: str | None = None


@router.put("/{prep_id}/questions/{number}")
def edit_question(
    prep_id: str, number: str, body: QuestionBody, st: AppState = State
) -> dict[str, Any]:
    q = keyprep.edit_question(
        _store(st, prep_id),
        number,
        body.parts,
        explanation=body.explanation,
        explanation_in=body.explanation_in,
        by=st.who(body.by),
    )
    return _view_question(q)


class RefineBody(BaseModel):
    questions: list[str] | None = None


@router.post("/{prep_id}/refine")
def refine_again(prep_id: str, body: RefineBody | None = None, st: AppState = State) -> Job:
    """다듬기 다시: 문항을 고르면 그 문항만 (강사가 고친 것은 사라진다)."""
    return _refine_job(st, _store(st, prep_id), body.questions if body else None)


@router.post("/{prep_id}/export")
def export(prep_id: str, st: AppState = State) -> dict[str, Any]:
    svc = st.services()
    res = keyprep.export(svc, _store(st, prep_id))
    return {
        "path": res.path.relative_to(svc.workspace.root).as_posix(),
        "name": res.path.name,
        "warnings": res.warnings,
        "skipped": res.skipped,
        "pending": res.pending,
    }


@router.get("/{prep_id}/hwpx")
def hwpx(prep_id: str, st: AppState = State) -> FileResponse:
    svc = st.services()
    prep = _store(st, prep_id).load()
    if not prep.exported:
        raise KeyError("아직 다듬은 HWPX를 쓰지 않았습니다")
    path = files.inside(svc.workspace.root, prep.exported)
    if not path.is_file():
        raise KeyError(f"{path.name}이 없습니다 — 다시 써 주세요")
    return FileResponse(path, filename=path.name, media_type="application/hwp+zip")


@router.get("/{prep_id}/file")
def prep_file(prep_id: str, path: str, st: AppState = State) -> FileResponse:
    store = _store(st, prep_id)
    full = files.inside(store.root, path)
    if not full.is_file():
        raise KeyError(path)
    return FileResponse(full)


@router.delete("/{prep_id}")
def delete(prep_id: str, st: AppState = State) -> dict[str, bool]:
    """초안을 지운다 (내보낸 HWPX는 남는다)."""
    store = _store(st, prep_id)
    shutil.rmtree(store.root)
    return {"ok": True}
