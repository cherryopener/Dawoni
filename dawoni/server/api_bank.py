"""문제은행 API: 공통 단원표, 자료(교재·문제집·기출) 등록·구간 DB화, 문항 검토, DOCX·시험으로."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from dawoni.bank import BankError, manual
from dawoni.bank import ingest as bi
from dawoni.bank.export import bank_exam, export_bank
from dawoni.bank.store import BankStore, BankSummary
from dawoni.bank.units import UnitStore, merge, parse_toc
from dawoni.export.docx.documents import KeyOptions, PaperOptions
from dawoni.export.docx.fonts import FontSpec
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.ingest.sources import all_sources, get_source, source_file, source_situation
from dawoni.models.answer_key import AnswerKey
from dawoni.models.bank import BankItem
from dawoni.models.job import Job
from dawoni.models.source import Source, SourceChunk, SourceKind
from dawoni.models.units import UnitTree
from dawoni.server import files, learning
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
    level: Annotated[str | None, Form()] = None,
    key_file: Annotated[UploadFile | None, File(description="해설 PDF가 따로 있으면")] = None,
    st: AppState = State,
) -> dict[str, Any]:
    svc = st.services()
    tmp = upload_dir(st)
    try:
        path = save_upload(file, tmp)
        key_path = save_upload(key_file, tmp) if key_file and key_file.filename else None
        trees = [t.strip() for t in unit_trees.split(",") if t.strip()]
        src = bi.add_source(svc, str(path), kind, title=title, publisher=publisher, year=year,
                            level=level or None, unit_trees=trees,
                            key_path=str(key_path) if key_path else None)  # fmt: skip
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"source": src, "suggested": bi.suggest_chunk(svc, src)}


class SourceMetaBody(BaseModel):
    kind: SourceKind
    title: str
    publisher: str | None = None
    year: int | None = None
    level: str | None = None
    unit_trees: list[str]


@router.put("/bank/sources/{source_id}")
def update_source_meta(source_id: str, body: SourceMetaBody, st: AppState = State) -> Source:
    b = body
    return bi.add_source(st.services(), source_id, b.kind, title=b.title, publisher=b.publisher,
                         year=b.year, level=b.level, unit_trees=b.unit_trees)  # fmt: skip


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


@router.put("/bank/sources/{source_id}/chunks")
def set_chunks(source_id: str, body: ChunksBody, st: AppState = State) -> Source:
    """챕터(구간) 표 저장. DB화한 부분의 쪽은 바꿀 수 없다."""
    svc = st.services()
    return bi.set_chunks(svc, get_source(svc.workspace, source_id), body.chunks)


@router.post("/bank/sources/{source_id}/chapters/suggest")
def suggest_chapters(source_id: str, st: AppState = State) -> Job:
    """교재 챕터 표 제안 (AI 없음 — 목차·쪽 분류). 큰 PDF는 몇 초 걸려 작업으로 돈다."""
    from dawoni.bank.chapters import suggest_chapters as suggest

    svc = st.services()
    src = get_source(svc.workspace, source_id)
    units = UnitStore(svc.workspace)

    def run(ctx: JobContext) -> dict[str, Any]:
        ctx.progress(0, 1, "쪽 분류 (개념·문제·해설)")
        s = suggest(source_file(svc.workspace, src), [units.get(t) for t in src.unit_trees])
        return {
            "chapters": [c.model_dump(mode="json") for c in s.chapters],
            "notes": s.notes,
            "checks": s.checks,
            "pages": [{"number": p.number, "kind": p.kind} for p in s.pages],
        }

    return st.jobs.submit("bank_chapters", run, {"source": source_id})


@router.post("/bank/sources/{source_id}/chunks/check")
def check_chunks(source_id: str, st: AppState = State) -> Job:
    """지금 챕터(구간) 표 점검 (AI 없음).

    구간마다 문항 번호를 실제로 찾아 빠진 번호 등을 알린다."""
    from dawoni.bank.chapters import check_chunks as check
    from dawoni.bank.chapters import classify_pages

    svc = st.services()
    src = get_source(svc.workspace, source_id)

    def run(ctx: JobContext) -> dict[str, Any]:
        ctx.progress(0, 1, "구간마다 문항 번호 확인")
        pdf = source_file(svc.workspace, src)
        pages = {p.number: p for p in classify_pages(pdf)}
        return {"checks": check(pdf, src.chunks, pages)}

    return st.jobs.submit("bank_chapters", run, {"source": source_id})


class ChaptersBody(BaseModel):
    indexes: list[int] | None = Field(default=None, description="없으면 아직 안 한 부분 전부")
    concepts: bool = True
    problems: bool = True
    solve: bool = False
    by: str | None = None


@router.post("/bank/sources/{source_id}/chapters/ingest")
def ingest_chapters(source_id: str, body: ChaptersBody, st: AppState = State) -> Job:
    """챕터마다 개념 → 범위 프로필, 문제 → 문제은행 (풀이는 `solve`일 때만)."""
    svc = st.services()
    bi._check_lock(get_source(svc.workspace, source_id), st.machine)  # pyright: ignore[reportPrivateUsage]

    def run(ctx: JobContext) -> dict[str, Any]:
        res = bi.ingest_chapters(svc, source_id, body.indexes, concepts=body.concepts,
                                 problems=body.problems, solve=body.solve, by=body.by,
                                 machine=st.machine, progress=ctx.progress)  # fmt: skip
        return {
            "scopes": [r.scope.id for r in res if r.scope],
            "items": sum(len(r.problems.items) for r in res if r.problems),
        }

    return st.jobs.submit("bank_chapter_ingest", run, {"source": source_id})


class IngestBody(BaseModel):
    by: str | None = None
    solve: bool = Field(default=False, description="DB화하면서 풀이도 (느림 — 보통은 나중에)")


@router.post("/bank/sources/{source_id}/chunks/{index}/ingest")
def ingest_chunk(source_id: str, index: int, body: IngestBody, st: AppState = State) -> Job:
    svc = st.services()
    src = get_source(svc.workspace, source_id)
    bi._check_lock(src, st.machine)  # pyright: ignore[reportPrivateUsage]

    def run(ctx: JobContext) -> dict[str, Any]:
        res = bi.ingest_chunk(svc, source_id, index, by=body.by, machine=st.machine,
                              solve=body.solve, progress=ctx.progress)  # fmt: skip
        return {
            "added": len(res.items),
            "skipped": len(res.skipped),
            "needs_check": sum(it.needs_check for it in res.items),
            "warnings": res.warnings,
        }

    return st.jobs.submit("bank_ingest", run, {"source": source_id, "chunk": index})


class SolveBody(BaseModel):
    ids: list[str] = Field(default_factory=lambda: list[str]())
    redo: bool = Field(default=False, description="이미 풀이가 있어도 다시")
    by: str | None = None


def _solve_job(st: AppState, ids: list[str], body: SolveBody, params: dict[str, Any]) -> Job:
    svc = st.services()
    if not ids:
        raise BankError("풀 문항을 고르세요")

    def run(ctx: JobContext) -> dict[str, Any]:
        res = bi.solve_items(svc, ids, by=body.by, redo=body.redo, machine=st.machine,
                             progress=ctx.progress)  # fmt: skip
        return {
            "solved": len(res.solved),
            "skipped": len(res.skipped),
            "needs_check": sum(it.needs_check for it in res.solved),
        }

    return st.jobs.submit("bank_solve", run, params)


@router.post("/bank/items/solve")
def solve_items(body: SolveBody, st: AppState = State) -> Job:
    """고른 문항의 풀이·해설 만들기 (원본을 보지 않고 풀어 원본 답과 비교)."""
    return _solve_job(st, body.ids, body, {"items": len(body.ids)})


@router.post("/bank/sources/{source_id}/chunks/{index}/solve")
def solve_chunk(source_id: str, index: int, body: SolveBody, st: AppState = State) -> Job:
    """구간(챕터)의 DB화한 문항 전부 풀이 만들기."""
    src = get_source(st.services().workspace, source_id)
    chunk = next((c for c in src.chunks if c.index == index), None)
    if chunk is None:
        raise KeyError(f"구간 {index} 없음")
    return _solve_job(st, chunk.item_ids, body, {"source": source_id, "chunk": index})


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


# ---------------------------------------------------------------- 직접 입력 (AI 없음)


Box = tuple[float, float, float, float]


@router.get("/bank/sources/{source_id}/page/{page}")
def source_page(source_id: str, page: int, st: AppState = State) -> FileResponse:
    """원본 쪽 이미지 (직접 입력·다시 자르기에서 원본을 보며)."""
    return FileResponse(manual.page_image(st.services(), source_id, page))


@router.get("/bank/sources/{source_id}/chunks/{index}/manual")
def chunk_manual(source_id: str, index: int, st: AppState = State) -> manual.ChunkManual:
    return manual.chunk_manual(st.services(), source_id, index)


@router.get("/bank/sources/{source_id}/chunks/{index}/work-file")
def chunk_work_file(source_id: str, index: int, path: str, st: AppState = State) -> FileResponse:
    """구간 작업 폴더(이 PC의 앱 데이터)의 문항 영역 이미지·그림 크롭."""
    svc = st.services()
    full = files.inside(bi.work_dir(svc, source_id, index), path)
    if full.suffix != ".png" or not full.is_file():
        raise KeyError(f"파일 없음: {path}")
    return FileResponse(full)


class ManualBody(BaseModel):
    draft: manual.ManualDraft
    by: str | None = None


@router.post("/bank/sources/{source_id}/chunks/{index}/manual")
def save_manual(source_id: str, index: int, body: ManualBody, st: AppState = State) -> BankItem:
    return manual.save_new(st.services(), source_id, index, body.draft, by=body.by)


class CropBody(BaseModel):
    page: int = Field(ge=1)
    box: Box
    number: str | None = None
    """새 문항(구간 직접 입력)일 때 문항 번호."""


@router.post("/bank/sources/{source_id}/chunks/{index}/crop")
def crop_chunk(
    source_id: str, index: int, body: CropBody, st: AppState = State
) -> manual.DraftFigure:
    if not body.number:
        raise BankError("문항 번호가 필요합니다")
    svc = st.services()
    return manual.crop_new(svc, source_id, index, body.number, body.page, body.box)


@router.get("/bank/items/{item_id}/manual")
def item_manual(item_id: str, st: AppState = State) -> manual.ItemManual:
    return manual.item_manual(st.services(), item_id)


class ItemManualBody(ManualBody, learning.LearnFields):
    base_revision: int


@router.put("/bank/items/{item_id}/manual")
def save_item_manual(item_id: str, body: ItemManualBody, st: AppState = State) -> BankItem:
    svc = st.services()
    before = _bank(st).get(item_id)
    saved = manual.save_existing(
        svc, item_id, body.draft, base_revision=body.base_revision, by=body.by
    )
    if body.learn:
        ref = before.source
        try:
            where = source_situation(get_source(svc.workspace, ref.source_id or ""))
        except KeyError:  # 직접 만든 문항 등
            where = (ref.title,)
        c = learning.item_correction(before, saved, "\n".join(where))
        learning.from_edit(st, body, c, body.by)
    return saved


@router.post("/bank/items/{item_id}/crop")
def crop_item(item_id: str, body: CropBody, st: AppState = State) -> manual.DraftFigure:
    return manual.crop_item(st.services(), item_id, body.page, body.box)


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
