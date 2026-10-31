"""강사가 직접 넣는 문항 (AI 없음).

DB화에 실패한 문항은 레이아웃 분석(영역 이미지·그림 크롭·PDF 글자)까지는 되어 있다 — 강사가 원본을
보며 글을 고쳐 쓰고 그림을 고르면 문항이 된다. DB화한 문항을 원본과 비교해 고칠 때도 같은
화면을 쓴다.
실측(2026-10-31): 실패한 9쪽 중 회로 문항은 "합성저항을 구하라" 한 줄 + 회로 그림 7개였다.

본문은 줄 단위 글로 다룬다:
- 한 줄 = 문단 하나. 식은 `\\( … \\)`, 한 줄 전체가 식이면 `\\[ … \\]`
- `[그림 F1]` 줄 = 그 자리에 그림
- `[블록 3]` 줄 = 원래 문항의 <보기>·표·상자·굵은 글씨 문단 (여기서 고치지 않고 그대로 둔다)
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationError

from dawoni.bank import BankError
from dawoni.bank.ingest import (
    _chunk,  # pyright: ignore[reportPrivateUsage]
    _set_chunk,  # pyright: ignore[reportPrivateUsage]
    default_points,
    item_id_for,
    original_level,
    work_dir,
)
from dawoni.bank.store import BankStore
from dawoni.context.extract import describe_validation_error
from dawoni.grading.store import safe_name
from dawoni.ingest.exam import prepare_regions
from dawoni.ingest.layout import DocumentLayout, Rect, Region, analyze, page_count, render_clip
from dawoni.ingest.layout import Figure as Figure_
from dawoni.ingest.sources import get_source, source_file
from dawoni.models.answer_key import KeyPart
from dawoni.models.bank import BankItem, BankSourceRef, OriginalSolution
from dawoni.models.base import DawoniModel, utcnow
from dawoni.models.content import (
    Block,
    DisplayMathBlock,
    FigureBlock,
    ParagraphBlock,
    Run,
    TextRun,
    runs_from_text,
)
from dawoni.models.figure import Figure
from dawoni.models.grading import ExamPart, ExamQuestion, PartKind
from dawoni.models.item import Choice, ItemContent, ItemType
from dawoni.models.source import Source, SourceChunk
from dawoni.services import Services

MANUAL_NOTE = "강사 직접 입력"
_FIG = re.compile(r"^\[그림 (F[1-9]\d*)\]$")
_BLOCK = re.compile(r"^\[블록 (\d+)\]$")
_DISPLAY = re.compile(r"^\\\[(.+)\\\]$")
_FIG_FILE = re.compile(r"^F([1-9]\d*)\.png$")
_PART_LINE = re.compile(r"^\s*\(?(\d+)\)\s*$")
_KEY_FAILED = re.compile(r"해설 ([\d, ]+)쪽 못 읽음")


class DraftPart(DawoniModel):
    label: str = Field(min_length=1)
    points: float = Field(gt=0)
    kind: PartKind | None = None
    answer: str = ""


class ManualDraft(DawoniModel):
    """편집기 한 장. 소문항이 있으면 정답은 소문항마다, 없으면 `answer`."""

    number: str = Field(min_length=1)
    item_type: ItemType
    points: float = Field(gt=0)
    text: str
    choices: list[str] = Field(default_factory=lambda: list[str]())
    """선택형 선지 글 (①부터). 한 칸이 `[그림 F3]`이면 그림 선지."""
    answer: str = ""
    parts: list[DraftPart] = Field(default_factory=lambda: list[DraftPart]())


class DraftFigure(DawoniModel):
    id: str
    path: str
    """그림 파일 (새 문항: 구간 작업 폴더 기준, 있는 문항: `bank/` 기준)."""


class ManualQuestion(DawoniModel):
    """DB화에 실패한 문항 하나의 초안 (PDF 글자로 미리 채움)."""

    number: str
    pages: list[int]
    draft: ManualDraft
    figures: list[DraftFigure]
    images: list[str]
    """문항 영역 이미지 (구간 작업 폴더 기준)."""
    part_labels: list[str]
    """PDF 글자에서 찾은 소문항 표시 ((1), (2) …) — '그림마다 소문항'에 쓴다."""


class ChunkManual(DawoniModel):
    questions: list[ManualQuestion]
    need_answer: list[str]
    """해설 쪽을 못 읽어 원본 정답이 없는 문항 ID."""
    key_failed_pages: list[int]


class ItemManual(DawoniModel):
    draft: ManualDraft
    figures: list[DraftFigure]
    source_id: str | None
    pages: list[int]
    locked: list[str]
    """`[블록 k]`의 내용 (보여 주기만)."""


# ---------------------------------------------------------------- 글 ↔ 블록


def _runs_text(runs: list[Run]) -> str:
    return "".join(r.v if isinstance(r, TextRun) else f"\\({r.v}\\)" for r in runs)


def text_from_stem(stem: list[Block]) -> tuple[str, list[str]]:
    """본문 블록 → 편집할 글, 그리고 `[블록 k]`의 내용 설명."""
    from dawoni.grading.render import blocks_text

    lines: list[str] = []
    locked: list[str] = []
    for k, b in enumerate(stem, start=1):
        match b:
            case ParagraphBlock() if not any(
                isinstance(r, TextRun) and (r.bold or r.underline) for r in b.runs
            ):
                lines.append(_runs_text(b.runs))
            case FigureBlock():
                lines.append(f"[그림 {b.figure_id}]")
            case DisplayMathBlock():
                lines.append(f"\\[{b.latex}\\]")
            case _:
                lines.append(f"[블록 {k}]")
                locked.append(f"[블록 {k}] {blocks_text([b])}")
    return "\n".join(lines), locked


def stem_from_text(text: str, original: list[Block] | None = None) -> list[Block]:
    out: list[Block] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if m := _FIG.match(line):
            out.append(FigureBlock(type="figure", figure_id=m.group(1)))
        elif m := _BLOCK.match(line):
            k = int(m.group(1))
            if original is None or not 1 <= k <= len(original):
                raise BankError(f"{line}: 원래 문항에 없는 블록입니다")
            out.append(original[k - 1])
        elif m := _DISPLAY.match(line):
            out.append(DisplayMathBlock(type="display_math", latex=m.group(1).strip()))
        else:
            out.append(ParagraphBlock(type="paragraph", runs=list(runs_from_text(line))))
    if not out:
        raise BankError("본문이 비어 있습니다")
    return out


def _choice(label: int, s: str) -> Choice:
    s = s.strip()
    if m := _FIG.match(s):
        return Choice(label=label, figure_id=m.group(1))
    if not s:
        raise BankError(f"{label}번 선지가 비어 있습니다")
    return Choice(label=label, runs=list(runs_from_text(s)))


def _choice_text(c: Choice) -> str:
    return f"[그림 {c.figure_id}]" if c.figure_id and not c.runs else _runs_text(c.runs)


def build_question(
    d: ManualDraft,
    figure_orig: dict[str, str],
    *,
    subject: str | None,
    original_stem: list[Block] | None = None,
) -> ExamQuestion:
    """편집기 → 문항. 본문·선지에서 쓴 그림만 넣는다.

    `figure_orig`: 그림 ID → 문항 폴더 기준 경로."""
    stem = stem_from_text(d.text, original_stem)
    choices = [_choice(k, s) for k, s in enumerate(d.choices, start=1)]
    used = [b.figure_id for b in stem if isinstance(b, FigureBlock)]
    used += [c.figure_id for c in choices if c.figure_id]
    unknown = sorted(set(used) - set(figure_orig))
    if unknown:
        raise BankError(f"없는 그림: {unknown}")
    figures = [
        Figure(id=f, mode="raster", orig=figure_orig[f]) for f in sorted(set(used), key=_fig_no)
    ]
    try:
        item = ItemContent(
            item_type=d.item_type,
            subject=subject,
            number_label=d.number,
            points=d.points,
            stem=stem,
            choices=choices,
            figures=figures,
        )
        return ExamQuestion(
            number=d.number,
            points=d.points,
            item=item,
            parts=[ExamPart(label=p.label, points=p.points, kind=p.kind) for p in d.parts],
        )
    except ValidationError as e:
        raise BankError("; ".join(describe_validation_error(e))) from e


def key_parts(d: ManualDraft, old: list[KeyPart] | None = None) -> list[KeyPart]:
    """편집기의 정답 → 원본 정답 (원래 해설 본문은 그대로 둔다)."""
    keep = {p.label: p for p in old or []}
    answers = [(p.label, p.answer) for p in d.parts] if d.parts else [("", d.answer)]
    out: list[KeyPart] = []
    for label, answer in answers:
        prev = keep.get(label)
        a = answer.strip() or None
        if prev is not None:
            expr = prev.answer_expr if a == prev.answer else None  # 답을 고치면 식은 버린다
            out.append(prev.model_copy(update={"answer": a, "answer_expr": expr}))
        elif a:
            out.append(KeyPart(label=label, answer=a, credit_policy="unspecified"))
    return out


def _fig_no(fid: str) -> int:
    return int(fid[1:])


# ---------------------------------------------------------------- 구간: 실패한 문항


def _layout(svc: Services, src: Source, chunk: SourceChunk) -> DocumentLayout:
    pdf = source_file(svc.workspace, src)
    layout = _analyze(pdf, pdf.stat().st_mtime_ns, tuple(chunk.pages))
    if layout.scanned:
        raise BankError("스캔 PDF는 아직 직접 입력을 지원하지 않습니다")
    return layout


@lru_cache(maxsize=8)
def _analyze(pdf: Path, mtime: int, pages: tuple[int, ...]) -> DocumentLayout:
    """구간 레이아웃 (결정적). 화면 열기·저장·상태 갱신이 같은 분석을 되풀이하지 않게
    (실측: 105쪽 교재에서 저장 한 번에 약 10초)."""
    del mtime  # 캐시 키 (PDF가 바뀌면 다시)
    keep = set(pages)
    return analyze(pdf, exclude=[p for p in range(1, page_count(pdf) + 1) if p not in keep])


def _missing(src: Source, chunk: SourceChunk, layout: DocumentLayout) -> list[Region]:
    have = set(chunk.item_ids)
    return [r for r in layout.regions if item_id_for(src, chunk, r.label) not in have]


def _figure_files(d: Path) -> dict[str, Path]:
    found = {f.stem: f for f in d.glob("F*.png") if _FIG_FILE.match(f.name)}
    return {k: found[k] for k in sorted(found, key=_fig_no)}


def chunk_key_failed(chunk: SourceChunk) -> list[int]:
    """읽지 못한 해설 쪽. 필드가 생기기 전(2026-10-31)의 구간은 메시지에서 읽는다."""
    if chunk.key_failed_pages:
        return chunk.key_failed_pages
    m = _KEY_FAILED.search(chunk.message or "")
    return [int(x) for x in re.findall(r"\d+", m.group(1))] if m else []


def _reading_order(r: Region, layout: DocumentLayout) -> list[str]:
    """그림 ID를 읽는 순서로 (쪽 → 단 → 위에서 아래). 레이아웃의 F 번호는 이 순서가 아닐 수 있다
    (실측: 기초물리학 51쪽 F1이 오른쪽 단의 (5)번 회로)."""
    from dawoni.ingest.layout import _column_of  # pyright: ignore[reportPrivateUsage]

    pages = {p.number: p for p in layout.pages}

    def key(f: Figure_) -> tuple[int, int, float]:
        cols = pages[f.page].columns if f.page in pages else []
        return (f.page, _column_of(f.rect, cols) if cols else 0, f.rect.y0)

    return [f.id for f in sorted(r.figures, key=key)]


def _prefill(r: Region, src: Source, figures: list[str]) -> ManualDraft:
    lines = [ln.strip() for ln in r.text.splitlines()]
    # 문항 번호 줄("5.")·소문항 표시만 있는 줄·끝의 각주 번호("구하라.5)")는 빼고 한 문단으로
    # — 강사가 원본을 보며 고친다
    skip = {f"{r.label}.", r.label}
    words = [ln for ln in lines if ln and ln not in skip and not _PART_LINE.match(ln)]
    body = re.sub(rf"\s*{re.escape(r.label)}\)$", "", " ".join(words)).strip()
    choice = re.search(r"[①②③④⑤]", r.text) is not None
    return ManualDraft(
        number=r.label,
        item_type="multiple_choice" if choice else "short_answer",
        points=r.points or default_points(src),
        text="\n".join([body, *(f"[그림 {f}]" for f in figures)]),
        choices=["", "", "", "", ""] if choice else [],
    )


def chunk_manual(svc: Services, source_id: str, index: int) -> ChunkManual:
    ws = svc.workspace
    src = get_source(ws, source_id)
    chunk = _chunk(src, index)
    layout = _layout(svc, src, chunk)
    work = work_dir(svc, source_id, index)
    missing = _missing(src, chunk, layout)
    prepared = prepare_regions(layout, work, missing)
    questions: list[ManualQuestion] = []
    for pr in prepared:
        r = pr.region
        d = work / safe_name(r.label)
        figs = _figure_files(d)
        labels = [f"({m.group(1)})" for ln in r.text.splitlines() if (m := _PART_LINE.match(ln))]
        order = _reading_order(r, layout)
        rest = [k for k in figs if k not in order]  # 강사가 다시 자른 그림
        questions.append(
            ManualQuestion(
                number=r.label,
                pages=r.pages,
                draft=_prefill(r, src, order),
                figures=[
                    DraftFigure(id=k, path=figs[k].relative_to(work).as_posix())
                    for k in [*order, *rest]
                    if k in figs
                ],
                images=[p.relative_to(work).as_posix() for p in pr.images],
                part_labels=labels,
            )
        )
    need: list[str] = []
    key_failed = chunk_key_failed(chunk)
    if key_failed:
        bank = BankStore(ws)
        need = [i for i in chunk.item_ids if bank.exists(i) and _no_answer(bank.get(i))]
    return ChunkManual(questions=questions, need_answer=need, key_failed_pages=key_failed)


def _no_answer(it: BankItem) -> bool:
    return it.original is None or it.original.level == "none"


def _refresh_chunk(svc: Services, src: Source, chunk: SourceChunk) -> SourceChunk:
    """직접 입력 뒤 구간 상태. 빠진 문항이 없으면 완료 — 해설을 못 읽은 쪽이 있으면 모든 문항에
    정답이 있어야 완료."""
    bank = BankStore(svc.workspace)
    missing = [r.label for r in _missing(src, chunk, _layout(svc, src, chunk))]
    key_failed = chunk_key_failed(chunk)
    no_answer = [i for i in chunk.item_ids if bank.exists(i) and _no_answer(bank.get(i))]
    key_pending = bool(key_failed) and bool(no_answer)
    if not missing and not key_pending:
        done = f"{len(chunk.item_ids)}문항 (강사 직접 입력 포함)"
        update: dict[str, object] = {"status": "ingested", "key_failed_pages": [], "message": done}
    else:
        bits: list[str] = []
        if missing:
            bits.append(f"남은 문항 {', '.join(missing)}")
        if key_pending:
            pages = ", ".join(map(str, key_failed))
            bits.append(f"해설 {pages}쪽 못 읽음 — 정답 없는 문항 {len(no_answer)}")
        todo = " · ".join(bits) + " — 다시 DB화하거나 '직접 입력'으로 채우세요"
        update = {"status": "failed", "key_failed_pages": key_failed, "message": todo}
    chunk = chunk.model_copy(update={**update, "updated_at": utcnow()})
    _set_chunk(svc, src.source_id, chunk)
    return chunk


def save_new(
    svc: Services, source_id: str, index: int, d: ManualDraft, *, by: str | None = None
) -> BankItem:
    ws = svc.workspace
    src = get_source(ws, source_id)
    chunk = _chunk(src, index)
    region = next(
        (r for r in _missing(src, chunk, _layout(svc, src, chunk)) if r.label == d.number), None
    )
    if region is None:
        raise BankError(f"구간 {index}에 직접 입력할 문항 {d.number}번이 없습니다")
    item_id = item_id_for(src, chunk, d.number)
    bank = BankStore(ws)
    files = _figure_files(work_dir(svc, source_id, index) / safe_name(region.label))
    # 쓴 그림만 문항 폴더로 복사 (편집기에서 확인한 크롭)
    used = {f for f in re.findall(r"\[그림 (F[1-9]\d*)\]", d.text + "\n" + "\n".join(d.choices))}
    subject = _subject(svc, src.unit_trees)
    q = build_question(d, {f: f"{item_id}/figures/{f}.png" for f in files}, subject=subject)
    bank.add_figures(item_id, {f: files[f] for f in used if f in files})
    parts = key_parts(d)
    it = BankItem(
        id=item_id,
        question=q,
        subject=subject,
        unit_ids=[chunk.unit_hint] if chunk.unit_hint else [],
        origin="ingested",
        source=BankSourceRef(
            source_id=src.source_id,
            title=src.title or src.filename,
            kind=src.kind if src.kind != "handwriting" else "other",
            pages=region.pages,
            number_label=d.number,
        ),
        original=OriginalSolution(level=original_level(parts), parts=parts, pages=chunk.key_pages)
        if parts
        else OriginalSolution(level="none"),
        note=MANUAL_NOTE,
        reviewed=True,
        reviewed_by=by,
    )
    it = bank.save(it, by=by)
    chunk = chunk.model_copy(update={"item_ids": sorted({*chunk.item_ids, item_id})})
    _refresh_chunk(svc, src, chunk)
    return it


def _subject(svc: Services, tree_ids: list[str]) -> str | None:
    from dawoni.bank.units import UnitStore

    subjects = {UnitStore(svc.workspace).get(t).subject for t in tree_ids}
    return subjects.pop() if len(subjects) == 1 else None


# ---------------------------------------------------------------- 있는 문항 고치기


def _item_chunk(svc: Services, it: BankItem) -> tuple[Source, SourceChunk] | None:
    if it.source.source_id is None:
        return None
    try:
        src = get_source(svc.workspace, it.source.source_id)
    except KeyError, FileNotFoundError:
        return None
    chunk = next((c for c in src.chunks if it.id in c.item_ids), None)
    return (src, chunk) if chunk else None


def item_manual(svc: Services, item_id: str) -> ItemManual:
    it = BankStore(svc.workspace).get(item_id)
    item = it.question.item
    text, locked = text_from_stem(item.stem)
    old = {p.label: p.answer or "" for p in (it.original.parts if it.original else [])}
    parts = [
        DraftPart(label=p.label, points=p.points, kind=p.kind, answer=old.get(p.label, ""))
        for p in it.question.parts
    ]
    draft = ManualDraft(
        number=it.question.number,
        item_type=item.item_type,
        points=it.question.points,
        text=text,
        choices=[_choice_text(c) for c in item.choices],
        answer="" if parts else old.get("", ""),
        parts=parts,
    )
    figs = _figure_files(BankStore(svc.workspace).item_dir(item_id) / "figures")
    return ItemManual(
        draft=draft,
        figures=[DraftFigure(id=k, path=f"{item_id}/figures/{p.name}") for k, p in figs.items()],
        source_id=it.source.source_id,
        pages=it.source.pages,
        locked=locked,
    )


def _without_review_notes(note: str | None) -> str | None:
    """강사가 원본과 비교해 저장했으니 'DB화: 원본과 비교하세요' 메모는 지운다."""
    lines = [
        ln
        for ln in (note or "").splitlines()
        if "원본과 비교" not in ln and "원본과 확인" not in ln
    ]
    return "\n".join(lines) or None


def save_existing(
    svc: Services, item_id: str, d: ManualDraft, *, base_revision: int, by: str | None = None
) -> BankItem:
    bank = BankStore(svc.workspace)
    it = bank.get(item_id)
    files = _figure_files(bank.item_dir(item_id) / "figures")
    orig = {f.id: f.orig for f in it.question.item.figures if f.orig}
    orig |= {k: f"{item_id}/figures/{p.name}" for k, p in files.items() if k not in orig}
    d = d.model_copy(update={"number": it.question.number})
    q = build_question(
        d, orig, subject=it.question.item.subject, original_stem=it.question.item.stem
    )
    parts = key_parts(d, it.original.parts if it.original else None)
    original = (
        (it.original or OriginalSolution(level="none")).model_copy(
            update={"parts": parts, "level": original_level(parts)}
        )
        if parts or it.original
        else None
    )
    saved = bank.save(
        it.model_copy(
            update={
                "question": q,
                "original": original,
                "reviewed": True,
                "reviewed_by": by,
                "note": _without_review_notes(it.note),
            }
        ),
        by=by,
        base_revision=base_revision,
    )
    if (found := _item_chunk(svc, saved)) and chunk_key_failed(found[1]):
        _refresh_chunk(svc, *found)
    return saved


# ---------------------------------------------------------------- 쪽 이미지 · 다시 자르기


def page_image(svc: Services, source_id: str, page: int) -> Path:
    from dawoni.ingest.layout import render_page

    src = get_source(svc.workspace, source_id)
    pdf = source_file(svc.workspace, src)
    if not 1 <= page <= page_count(pdf):
        raise BankError(f"{page}쪽은 없습니다")
    ws_key = hashlib.sha256(str(svc.workspace.root.resolve()).encode()).hexdigest()[:12]
    out = svc.paths.cache / "bank-work" / ws_key / "pages" / f"{source_id[:10]}-p{page}.png"
    if not out.is_file():
        out.parent.mkdir(parents=True, exist_ok=True)
        render_page(pdf, page, out, dpi=110)
    return out


def _crop(pdf: Path, page: int, box: tuple[float, float, float, float], d: Path) -> str:
    """쪽 비율 좌표(0~1)의 네모 → 다음 그림 번호의 PNG."""
    import pymupdf

    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise BankError("자를 영역이 쪽 밖입니다")
    with pymupdf.open(pdf) as doc:
        r = doc.load_page(page - 1).rect  # pyright: ignore[reportUnknownMemberType]
    rect = Rect(r.width * x0, r.height * y0, r.width * x1, r.height * y1)
    have = _figure_files(d)
    fid = f"F{max((_fig_no(k) for k in have), default=0) + 1}"
    d.mkdir(parents=True, exist_ok=True)
    render_clip(pdf, page, rect, d / f"{fid}.png", dpi=300, pad=0)
    return fid


def crop_new(
    svc: Services,
    source_id: str,
    index: int,
    number: str,
    page: int,
    box: tuple[float, float, float, float],
) -> DraftFigure:
    src = get_source(svc.workspace, source_id)
    _chunk(src, index)
    work = work_dir(svc, source_id, index)
    d = work / safe_name(number)
    fid = _crop(source_file(svc.workspace, src), page, box, d)
    return DraftFigure(id=fid, path=(d / f"{fid}.png").relative_to(work).as_posix())


def crop_item(
    svc: Services, item_id: str, page: int, box: tuple[float, float, float, float]
) -> DraftFigure:
    bank = BankStore(svc.workspace)
    it = bank.get(item_id)
    if it.source.source_id is None:
        raise BankError("원본 자료가 없는 문항입니다")
    src = get_source(svc.workspace, it.source.source_id)
    fid = _crop(source_file(svc.workspace, src), page, box, bank.item_dir(item_id) / "figures")
    return DraftFigure(id=fid, path=f"{item_id}/figures/{fid}.png")
