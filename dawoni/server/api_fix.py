"""시험 읽기 고치기 API: 문항·소문항·배점·본문·해설을 강사가 고치고, 원본 영역을 지정해 다시 읽는다.

규칙과 이유는 `ingest/fix.py`. 영역 읽기는 AI를 부르므로 작업으로 돌린다.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from dawoni.grading.pipeline import PipelineError
from dawoni.grading.store import ExamStore
from dawoni.ingest import fix
from dawoni.models.answer_key import AnswerKey
from dawoni.models.grading import ExamQuestion
from dawoni.models.job import Job
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


@router.get("/{exam_id}/source")
def source(exam_id: str, st: AppState = State) -> dict[str, Any]:
    """원본 PDF의 쪽 크기·해설지 쪽 (영역 지정 화면). PDF가 아니면 그림 붙여 넣기만."""
    svc, store = _store(st, exam_id)
    return fix.source_info(svc, store)


@router.get("/{exam_id}/source/pages/{page}")
def source_page(exam_id: str, page: int, st: AppState = State) -> FileResponse:
    svc, store = _store(st, exam_id)
    return FileResponse(fix.page_image(svc, store, page), media_type="image/png")


@router.put("/{exam_id}/questions/{number}/structure")
def structure(
    exam_id: str, number: str, body: fix.StructureEdit, st: AppState = State
) -> dict[str, Any]:
    """소문항 더하기·빼기·표시·유형·배점. 해설지의 소문항이 따라간다."""
    _, store = _store(st, exam_id)
    q, notes = fix.edit_structure(store, number, body)
    return {"question": q.model_dump(mode="json"), "notes": notes}


@router.post("/{exam_id}/questions")
def add_question(exam_id: str, body: fix.NewQuestion, st: AppState = State) -> ExamQuestion:
    _, store = _store(st, exam_id)
    return fix.add_question(store, body)


@router.delete("/{exam_id}/questions/{number}")
def delete_question(exam_id: str, number: str, st: AppState = State) -> dict[str, bool]:
    _, store = _store(st, exam_id)
    fix.delete_question(store, number)
    return {"ok": True}


class StemBody(BaseModel):
    text: str = Field(min_length=1)


@router.put("/{exam_id}/questions/{number}/stem")
def put_stem(exam_id: str, number: str, body: StemBody, st: AppState = State) -> ExamQuestion:
    _, store = _store(st, exam_id)
    return fix.set_stem(store, number, body.text)


class KeyTextBody(BaseModel):
    parts: list[fix.KeyEdit]
    by: str | None = None


@router.put("/{exam_id}/questions/{number}/key-text")
def put_key_text(exam_id: str, number: str, body: KeyTextBody, st: AppState = State) -> AnswerKey:
    """해설을 타이핑·붙여 넣기 (없던 해설지도 만든다)."""
    _, store = _store(st, exam_id)
    return fix.set_key(store, number, body.parts, st.who(body.by))


_BOXES = TypeAdapter(list[fix.Box])


@router.post("/{exam_id}/questions/{number}/read-region")
def read_region(
    exam_id: str,
    number: str,
    what: Annotated[Literal["question", "key"], Form()],
    boxes: Annotated[str, Form()] = "[]",
    files: Annotated[list[UploadFile] | None, File()] = None,
    st: AppState = State,
) -> Job:
    """원본 쪽의 상자(쪽 비율)나 붙여 넣은 그림에서 문항 본문(`question`)·해설(`key`)을 읽는다."""
    svc, store = _store(st, exam_id)
    store.exam().question(number)
    try:
        regions = _BOXES.validate_json(boxes)
    except ValidationError as e:
        raise PipelineError("영역 상자를 읽지 못했습니다.") from e
    pasted = [save_upload(f, upload_dir(st)) for f in files or []]
    if not regions and not pasted:
        raise PipelineError("원본에서 영역을 끌어 지정하거나 그림을 붙여 넣어 주세요.")

    def run(ctx: JobContext) -> dict[str, Any]:
        if what == "question":
            ctx.progress(0, 1, f"문항 {number} 본문 읽기")
            res = fix.read_question_region(svc, store, number, regions, pasted)
            ctx.progress(1, 1, f"문항 {number} 본문 저장")
            return {"question": res.question.model_dump(mode="json"), "notes": res.notes}
        ctx.progress(0, 1, f"문항 {number} 해설 읽기")
        key = fix.read_key_region(svc, store, number, regions, pasted)
        ctx.progress(1, 1, f"문항 {number} 해설 저장")
        return {"key": key.key.model_dump(mode="json"), "notes": key.warnings}

    return st.jobs.submit("read_region", run, {"exam": exam_id, "question": number, "what": what})
