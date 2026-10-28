"""문제은행 API: 공통 단원표, 자료(교재·문제집·기출) 등록·구간 DB화, 문항 검토, DOCX·시험으로."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from dawoni.bank import BankError
from dawoni.bank import ingest as bi
from dawoni.bank.export import bank_exam, export_bank
from dawoni.bank.store import BankStore, BankSummary
from dawoni.bank.units import UnitStore, merge, parse_toc
from dawoni.export.docx.documents import KeyOptions, PaperOptions
from dawoni.export.docx.fonts import FontSpec
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.ingest.sources import all_sources, get_source, source_file
from dawoni.models.answer_key import AnswerKey
from dawoni.models.bank import BankItem
from dawoni.models.job import Job
from dawoni.models.source import Source, SourceChunk, SourceKind
from dawoni.models.units import UnitTree
from dawoni.server import files
from dawoni.server.api_content import save_upload, upload_dir
from dawoni.server.api_setup import State
from dawoni.server.jobs import JobContext
from dawoni.server.state import AppState

router = APIRouter(prefix="/api")

BANK_KINDS: tuple[SourceKind, ...] = ("problem_bank", "textbook", "mock_exam")


def _bank(st: AppState) -> BankStore:
    return BankStore(st.services().workspace, st.paths.cache)


# ---------------------------------------------------------------- 단원표


@router.get("/units")
def list_units(st: AppState = State) -> list[UnitTree]:
    return UnitStore(st.services().workspace).all()


class NewTreeBody(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9]+$")
    subject: str
    group: str
    curriculum: str | None = None
    toc: str | None = Field(default=None, description="붙여 넣은 목차 (없으면 빈 단원표)")
    by: str | None = None


@router.post("/units")
def create_units(body: NewTreeBody, st: AppState = State) -> UnitTree:
    units = parse_toc(body.toc, body.id) if body.toc and body.toc.strip() else []
    tree = UnitTree(id=body.id, subject=body.subject, group=body.group,
                    curriculum=body.curriculum, units=units, reviewed=True)  # fmt: skip
    return UnitStore(st.services().workspace).create(tree, body.by)


class TreeBody(BaseModel):
    tree: UnitTree
    base_version: int
    by: str | None = None


@router.put("/units/{tree_id}")
def save_units(tree_id: str, body: TreeBody, st: AppState = State) -> UnitTree:
    if body.tree.id != tree_id:
        raise BankError("단원표 ID는 바꿀 수 없습니다")
    svc = st.services()
    return UnitStore(svc.workspace).save(
        body.tree, base_version=body.base_version, by=body.by, in_use=_bank(st).used_unit_ids()
    )


class MergeBody(BaseModel):
    source: str
    into: str
    base_version: int
    by: str | None = None


@router.post("/units/{tree_id}/merge")
def merge_units(tree_id: str, body: MergeBody, st: AppState = State) -> UnitTree:
    store = UnitStore(st.services().workspace)
    tree = store.get(tree_id)
    return store.save(merge(tree, body.source, body.into), base_version=body.base_version,
                      by=body.by, in_use=_bank(st).used_unit_ids())  # fmt: skip


# ---------------------------------------------------------------- 자료


@router.get("/bank/sources")
def list_sources(st: AppState = State) -> list[Source]:
    return [s for s in all_sources(st.services().workspace) if s.kind in BANK_KINDS]


@router.post("/bank/sources")
def upload_source(
    file: Annotated[UploadFile, File()],
    kind: Annotated[SourceKind, Form()],
    title: Annotated[str, Form()],
    unit_trees: Annotated[str, Form(description="쉼표로 구분한 단원표 ID")],
    publisher: Annotated[str | None, Form()] = None,
    year: Annotated[int | None, Form()] = None,
    st: AppState = State,
) -> dict[str, Any]:
    svc = st.services()
    tmp = upload_dir(st)
    try:
        path = save_upload(file, tmp)
        trees = [t.strip() for t in unit_trees.split(",") if t.strip()]
        src = bi.add_source(svc, str(path), kind, title=title, publisher=publisher, year=year,
                            unit_trees=trees)  # fmt: skip
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"source": src, "suggested": bi.suggest_chunk(svc, src)}


class SourceMetaBody(BaseModel):
    kind: SourceKind
    title: str
    publisher: str | None = None
    year: int | None = None
    unit_trees: list[str]


@router.put("/bank/sources/{source_id}")
def update_source_meta(source_id: str, body: SourceMetaBody, st: AppState = State) -> Source:
    b = body
    return bi.add_source(st.services(), source_id, b.kind, title=b.title,
                         publisher=b.publisher, year=b.year, unit_trees=b.unit_trees)  # fmt: skip


@router.get("/bank/sources/{source_id}/pdf")
def source_pdf(source_id: str, st: AppState = State) -> FileResponse:
    ws = st.services().workspace
    return FileResponse(source_file(ws, get_source(ws, source_id)), media_type="application/pdf")


class ChunksBody(BaseModel):
    chunks: list[SourceChunk]


@router.post("/bank/sources/{source_id}/chunks")
def add_chunks(source_id: str, body: ChunksBody, st: AppState = State) -> Source:
    svc = st.services()
    return bi.add_chunks(svc, get_source(svc.workspace, source_id), body.chunks)


class IngestBody(BaseModel):
    by: str | None = None


@router.post("/bank/sources/{source_id}/chunks/{index}/ingest")
def ingest_chunk(source_id: str, index: int, body: IngestBody, st: AppState = State) -> Job:
    svc = st.services()
    src = get_source(svc.workspace, source_id)
    bi._check_lock(src, st.machine)  # pyright: ignore[reportPrivateUsage]

    def run(ctx: JobContext) -> dict[str, Any]:
        res = bi.ingest_chunk(svc, source_id, index, by=body.by, machine=st.machine,
                              progress=ctx.progress)  # fmt: skip
        return {
            "added": len(res.items),
            "skipped": len(res.skipped),
            "needs_check": sum(it.needs_check for it in res.items),
            "warnings": res.warnings,
        }

    return st.jobs.submit("bank_ingest", run, {"source": source_id, "chunk": index})


# ---------------------------------------------------------------- 문항


@router.get("/bank/items")
def list_items(st: AppState = State) -> list[BankSummary]:
    return _bank(st).summaries()


@router.get("/bank/items/{item_id}")
def get_item(item_id: str, st: AppState = State) -> dict[str, Any]:
    it = _bank(st).get(item_id)
    units = UnitStore(st.services().workspace)
    ids = {*it.unit_ids, *it.concept_units.values()}
    return {"item": it, "unit_labels": {u: units.label(u) for u in ids}}


class ItemBody(BaseModel):
    item: BankItem
    base_revision: int
    by: str | None = None


@router.put("/bank/items/{item_id}")
def save_item(item_id: str, body: ItemBody, st: AppState = State) -> BankItem:
    if body.item.id != item_id:
        raise BankError("문항 ID는 바꿀 수 없습니다")
    units = UnitStore(st.services().workspace)
    bad = [u for u in body.item.unit_ids if units.find(u) is None or units.label(u).startswith("(")]
    if bad:
        raise BankError(f"없는 단원: {bad}")
    return _bank(st).save(body.item, by=body.by, base_revision=body.base_revision)


class ResolveBody(BaseModel):
    side: Literal["original", "ai"]
    base_revision: int
    by: str | None = None


@router.post("/bank/items/{item_id}/resolve")
def resolve_item(item_id: str, body: ResolveBody, st: AppState = State) -> BankItem:
    """답 확인 필요 → 강사가 원본 답과 AI 답 중 맞는 쪽을 고른다."""
    bank = _bank(st)
    it = bank.get(item_id)
    if it.answer_check is None:
        raise BankError("비교 결과가 없는 문항입니다")
    check = it.answer_check.model_copy(update={"resolved": body.side, "resolved_by": body.by})
    return bank.save(it.model_copy(update={"answer_check": check}), by=body.by,
                     base_revision=body.base_revision)  # fmt: skip


@router.delete("/bank/items/{item_id}")
def delete_item(item_id: str, st: AppState = State) -> dict[str, bool]:
    _bank(st).delete(item_id)
    return {"ok": True}


@router.get("/bank/file")
def bank_file(path: str, st: AppState = State) -> FileResponse:
    """문항 그림 (`bank/` 기준 경로)."""
    full = files.inside(st.services().workspace.bank, path)
    if not full.is_file():
        raise KeyError(f"파일 없음: {path}")
    return FileResponse(full)


# ---------------------------------------------------------------- 내보내기 · 시험으로


class ExportBody(BaseModel):
    ids: list[str] = Field(min_length=1)
    kind: Literal["paper", "key"]
    title: str = "문제"
    columns: Literal[1, 2] = 2
    show_points: bool = True
    answer_lines: int = Field(default=8, ge=0, le=40)
    with_questions: bool = False


@router.post("/bank/export")
def export(body: ExportBody, st: AppState = State) -> dict[str, Any]:
    svc = st.services()
    d = svc.config.docx
    items = _bank(st).items(body.ids)
    res = export_bank(
        svc.workspace, items, body.kind, body.title,
        FontSpec(body=d.body_font, math=d.math_font, size_pt=d.body_size_pt),
        tmp_dir=st.paths.cache / "bank_export",
        paper=PaperOptions(columns=body.columns, show_points=body.show_points,
                           answer_lines=body.answer_lines),
        key=KeyOptions(with_questions=body.with_questions),
    )  # fmt: skip
    return {
        "path": Path(res.path).relative_to(svc.workspace.root).as_posix(),
        "name": res.path.name,
        "ok": res.check.ok,
        "summary": res.check.summary(),
        "errors": res.check.errors,
        "warnings": res.warnings,
        "math_fallbacks": len(res.math_fallbacks),
    }


class ToExamBody(BaseModel):
    ids: list[str] = Field(min_length=1)
    title: str
    scope_id: str


@router.post("/bank/to-exam")
def to_exam(body: ToExamBody, st: AppState = State) -> dict[str, str]:
    """고른 문항으로 채점용 시험을 만든다 (해설은 시험에서 범위 프로필로 다시 만든다)."""
    from dawoni.models.base import new_id

    svc = st.services()
    ScopeStore(svc.workspace).get(body.scope_id)
    exam_id = new_id("exam")
    root = svc.workspace.exams / exam_id
    items = _bank(st).items(body.ids)
    exam, _, _ = bank_exam(items, body.title, body.scope_id, svc.workspace.bank, root)
    store = ExamStore.create(svc.workspace, exam.model_copy(update={"id": exam_id}))
    for q, it in zip(exam.questions, items, strict=True):
        parts = it.original.parts if it.original else []
        if any(p.answer or p.answer_expr for p in parts):
            # 원본 정답·해설 → 해설지 (루브릭을 해설지 기준으로 만들 수 있게)
            store.save_key(AnswerKey(question_number=q.number, parts=parts,
                                     source=f"문제은행: {it.source.display()}"))  # fmt: skip
    return {"exam_id": exam_id}
