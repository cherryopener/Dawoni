"""시험 읽기 고치기: 강사가 문항·소문항·배점·본문·해설을 직접 고치거나, 원본에서 영역을 지정해
그 부분만 다시 읽힌다.

2026-11-06 사용자 요청 — 잘못 읽힌 시험지를 같은 방법으로 다시 읽혀도 같은 결과다 (실측 공통형3회:
레이아웃이 문항을 잘못 나눠 AI는 1·2번을 보지도 못함). 새 정보가 있어야 결과가 달라진다:
강사가 정한 구조(소문항·배점)와 강사가 정한 영역.

- 고치기는 그 문항의 루브릭을 만들기 전에만 (루브릭 항목 배점·소문항이 문항에 맞춰져 있다).
  해설은 언제든 (지금의 해설 고치기와 같음 — 루브릭에 반영하려면 다시 만든다).
- 문항·소문항 표시를 더하고 빼는 것은 학생 답안을 판독하기 전에만 (판독문이 그 표시를 쓴다).
- 소문항 표시를 바꾸면 해설지의 그 소문항도 따라간다 (`PartEdit.was`). 새 소문항은 빈 해설.
- 영역 읽기는 강사가 정한 소문항을 그대로 두고 (AI 출력이 그 표시를 지켜야 통과) 본문·그림을 읽는다.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import Field

from dawoni.context import situation
from dawoni.grading.pipeline import PipelineError
from dawoni.grading.render import blocks_text
from dawoni.grading.store import ExamStore, ScopeStore, safe_name
from dawoni.ingest.answer_key import CropKey, extract_answer_key, key_warnings
from dawoni.ingest.exam import PreparedRegion, prepare_regions, read_region, to_item
from dawoni.ingest.layout import (
    Rect,
    Region,
    clip_text,
    crop_region,
    find_key_pages,
    page_sizes,
    render_clip,
    render_page,
)
from dawoni.ingest.sources import all_sources, get_source, source_file
from dawoni.models.answer_key import AnswerKey, KeyPart
from dawoni.models.base import DawoniModel, utcnow
from dawoni.models.content import (
    Block,
    DisplayMathBlock,
    FigureBlock,
    ParagraphBlock,
    paragraph,
    runs_from_text,
    text,
)
from dawoni.models.grading import Exam, ExamPart, ExamQuestion, PartKind
from dawoni.models.item import ItemContent
from dawoni.services import Services

EMPTY_STEM = "(본문 없음)"
"""강사가 더한 문항의 본문 자리 — 영역 지정해서 읽거나 붙여 넣기 전."""
EDITABLE_TYPES = ("essay", "short_answer")


class PartEdit(DawoniModel):
    label: str = Field(min_length=1, max_length=20, description="소문항 표시 (예: (1), (1)①)")
    points: float = Field(gt=0)
    kind: PartKind | None = None
    was: str | None = Field(
        default=None,
        description="고치기 전 표시 — 해설지의 그 소문항이 따라온다. 새 소문항이면 null, "
        '소문항이 없던 문항의 해설이면 ""',
    )


class StructureEdit(DawoniModel):
    item_type: Literal["essay", "short_answer"] | None = None
    points: float | None = Field(default=None, gt=0, description="소문항이 없을 때 문항 배점")
    parts: list[PartEdit] = Field(default_factory=lambda: list[PartEdit]())


class NewQuestion(DawoniModel):
    number: str = Field(min_length=1, max_length=20)
    points: float = Field(gt=0)
    item_type: Literal["essay", "short_answer"] = "essay"


class KeyEdit(DawoniModel):
    label: str = Field(default="", description='소문항 표시 (소문항이 없으면 "")')
    answer: str | None = None
    solution: str = ""
    credit_text: str | None = Field(default=None, description="부분 점수 표기 (적힌 그대로)")


class Box(DawoniModel):
    """원본 쪽 위의 상자 — 쪽 너비·높이에 대한 비율 (0~1)."""

    page: int = Field(ge=1)
    x0: float = Field(ge=0, le=1)
    y0: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)


# ---------------------------------------------------------------- 공통


def _locked(store: ExamStore, number: str) -> None:
    if store.rubric(number) is not None:
        raise PipelineError(f"문항 {number}: 루브릭이 이미 있어 고칠 수 없습니다.")


def _no_transcripts(store: ExamStore) -> None:
    if store.has_transcripts():
        raise PipelineError(
            "학생 답안을 이미 판독해 문항·소문항 표시를 바꿀 수 없습니다 "
            "(판독문이 그 표시를 씁니다). 배점·본문·해설은 고칠 수 있습니다."
        )


def _labels(q: ExamQuestion) -> list[str]:
    return [p.label for p in q.parts]


def _save(store: ExamStore, exam: Exam, new: ExamQuestion) -> ExamQuestion:
    new = ExamQuestion.model_validate(new.model_dump())
    questions = [new if q.number == new.number else q for q in exam.questions]
    store.save_exam(exam.model_copy(update={"questions": questions}))
    return new


def _stale(store: ExamStore, number: str) -> None:
    """문항이 바뀌어 맞지 않게 된 AI 풀이·정답 후보 (루브릭을 만들 때 다시 만든다)."""
    store.drop("solutions", number)
    store.drop("candidate_matches", number)


def _empty_part(label: str) -> KeyPart:
    return KeyPart(label=label, credit_policy="unspecified")


def follow_key(store: ExamStore, new: ExamQuestion, was: dict[str, str | None]) -> list[str]:
    """소문항이 바뀐 문항의 해설지를 새 표시에 맞춘다. `was`: 새 표시 → 예전 표시 (없으면 새 것).

    돌려주는 것: 해설이 있었는데 갈 곳이 없어 뺀 예전 소문항 (강사에게 알림)."""
    key = store.answer_key(new.number)
    if key is None:
        return []
    old = {p.label: p for p in key.parts}
    labels = _labels(new) or [""]
    parts: list[KeyPart] = []
    used: set[str] = set()
    for label in labels:
        src = was.get(label)
        kp = old.get(src) if src is not None else None
        if kp is None:
            parts.append(_empty_part(label))
            continue
        used.add(kp.label)
        parts.append(kp.model_copy(update={"label": label}))
    lost = [
        p.label or "(전체)" for p in key.parts if p.label not in used and (p.answer or p.solution)
    ]
    doubtful = [label for label in labels if was.get(label) in key.doubtful]
    key = key.model_copy(update={"parts": parts, "doubtful": doubtful})
    key.warnings = key_warnings(key, new)
    store.save_key(key)
    return lost


# ---------------------------------------------------------------- 구조·문항


def edit_structure(
    store: ExamStore, number: str, edit: StructureEdit
) -> tuple[ExamQuestion, list[str]]:
    """소문항 더하기·빼기·표시·유형·배점, 문항 유형(서술형·단답형). (고친 문항, 알림)."""
    exam = store.exam()
    q = exam.question(number)
    _locked(store, number)
    labels = [p.label.strip() for p in edit.parts]
    if any(not label for label in labels):
        raise PipelineError("소문항 표시를 적어 주세요.")
    if len(set(labels)) != len(labels):
        raise PipelineError(f"소문항 표시가 겹칩니다: {', '.join(labels)}")
    if labels != _labels(q):
        _no_transcripts(store)
    parts = [
        ExamPart(label=label, points=p.points, kind=p.kind)
        for label, p in zip(labels, edit.parts, strict=True)
    ]
    if parts:
        points = sum(p.points for p in parts)
    elif edit.points is not None:
        points = edit.points
    elif not q.parts:
        points = q.points
    else:
        raise PipelineError("소문항을 모두 지우면 문항 배점을 주세요.")
    item = q.item
    if edit.item_type and edit.item_type != item.item_type:
        if item.item_type not in EDITABLE_TYPES:
            raise PipelineError(
                "객관식 문항의 유형은 '영역 지정해서 읽기'로 다시 읽어 바꿔 주세요."
            )
        item = item.model_copy(update={"item_type": edit.item_type})
    item = item.model_copy(update={"points": points})
    new = _save(store, exam, q.model_copy(update={"points": points, "parts": parts, "item": item}))
    notes: list[str] = []
    if labels != _labels(q):
        was = {label: p.was for label, p in zip(labels, edit.parts, strict=True)}
        if not labels:  # 소문항을 모두 지움 → 문항 전체 해설 (소문항이 하나였으면 그 해설)
            was = {"": q.parts[0].label if len(q.parts) == 1 else None}
        lost = follow_key(store, new, was)
        if lost:
            notes.append(f"해설지의 {', '.join(lost)} 해설은 맞는 소문항이 없어 뺐습니다")
        _stale(store, number)
    else:
        key = store.answer_key(number)
        if key is not None:
            key.warnings = key_warnings(key, new)
            store.save_key(key)
    return new, notes


def _order(number: str) -> tuple[int, int, str]:
    m = re.match(r"\d+", number)
    return (0, int(m.group()), number) if m else (1, 0, number)


def add_question(store: ExamStore, body: NewQuestion) -> ExamQuestion:
    """빠진 문항 더하기 — 본문은 비워 두고 영역 지정해서 읽거나 붙여 넣는다."""
    exam = store.exam()
    number = body.number.strip()
    if any(q.number == number for q in exam.questions):
        raise PipelineError(f"문항 {number}은(는) 이미 있습니다.")
    _no_transcripts(store)
    item = ItemContent(
        item_type=body.item_type,
        number_label=number,
        points=body.points,
        stem=[paragraph(text(EMPTY_STEM))],
    )
    new = ExamQuestion(number=number, points=body.points, item=item)
    questions = sorted([*exam.questions, new], key=lambda q: _order(q.number))
    store.save_exam(exam.model_copy(update={"questions": questions}))
    return new


def delete_question(store: ExamStore, number: str) -> None:
    """잘못 생긴 문항 지우기 (해설지·풀이·그림도)."""
    exam = store.exam()
    exam.question(number)
    _locked(store, number)
    _no_transcripts(store)
    if len(exam.questions) == 1:
        raise PipelineError("문항이 하나뿐이라 지울 수 없습니다.")
    for kind in ("keys", "solutions", "candidate_matches"):
        store.drop(kind, number)
    shutil.rmtree(store.root / "figures" / safe_name(number), ignore_errors=True)
    questions = [q for q in exam.questions if q.number != number]
    store.save_exam(exam.model_copy(update={"questions": questions}))


# ---------------------------------------------------------------- 본문


_FIGURE_LINE = re.compile(r"^\[그림\s*(F\d+)\]$")
_DISPLAY = re.compile(r"^\\\[(.+)\\\]$", re.DOTALL)


def stem_text(item: ItemContent) -> str:
    """본문을 고칠 수 있는 글로: 식은 `\\( \\)`, 그림 자리는 `[그림 F1]`."""
    return blocks_text(item.stem)


def stem_missing(item: ItemContent) -> bool:
    return stem_text(item).strip() in ("", EMPTY_STEM)


def stem_from_text(s: str, item: ItemContent) -> list[Block]:
    """강사가 고치거나 붙여 넣은 본문 → 블록. 줄마다 문단, `\\[ \\]` 줄은 별행 수식,
    `[그림 F1]` 줄은 그 그림 자리 (이 문항의 그림만)."""
    figures = {f.id for f in item.figures}
    out: list[Block] = []
    for line in s.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        if (m := _FIGURE_LINE.match(line)) and m.group(1) in figures:
            out.append(FigureBlock(type="figure", figure_id=m.group(1)))
        elif m := _DISPLAY.match(line):
            out.append(DisplayMathBlock(type="display_math", latex=m.group(1).strip()))
        elif runs := runs_from_text(line):
            out.append(ParagraphBlock(type="paragraph", runs=runs))
    if not out:
        raise PipelineError("본문을 적어 주세요.")
    return out


def set_stem(store: ExamStore, number: str, s: str) -> ExamQuestion:
    exam = store.exam()
    q = exam.question(number)
    _locked(store, number)
    item = ItemContent.model_validate(
        q.item.model_copy(update={"stem": stem_from_text(s, q.item)}).model_dump()
    )
    new = _save(store, exam, q.model_copy(update={"item": item}))
    _stale(store, number)
    return new


# ---------------------------------------------------------------- 해설 직접 넣기


def set_key(store: ExamStore, number: str, edits: list[KeyEdit], by: str | None) -> AnswerKey:
    """해설을 타이핑하거나 붙여 넣는다. 없던 해설지도 만든다. 강사가 넣은 것이라 '확인됨'."""
    q = store.exam().question(number)
    labels = _labels(q) or [""]
    got = [e.label for e in edits]
    if len(set(got)) != len(got):
        raise PipelineError(f"소문항 표시가 겹칩니다: {got}")
    if unknown := [x for x in got if x not in labels]:
        raise PipelineError(f"시험에 없는 소문항 표시: {unknown} (있는 것: {labels})")
    key = store.answer_key(number)
    old = {p.label: p for p in key.parts} if key else {}
    by_label = {e.label: e for e in edits}
    parts: list[KeyPart] = []
    for label in labels:
        base = old.get(label) or _empty_part(label)
        e = by_label.get(label)
        if e is not None:
            update: dict[str, object] = {
                "answer": (e.answer or "").strip() or None,
                "solution": e.solution.strip(),
            }
            credit = (e.credit_text or "").strip() or None
            if credit != base.credit_text:  # 고친 표기는 구조를 다시 읽지 않는다 — 원문으로만
                update |= {"credit_text": credit, "credit_policy": "unspecified", "credits": [],
                           "credit_min": None, "credit_max": None}  # fmt: skip
            base = KeyPart.model_validate(base.model_copy(update=update).model_dump())
        parts.append(base)
    new = AnswerKey(
        question_number=number,
        parts=parts,
        source=key.source if key else "강사 입력",
        pages=key.pages if key else [],
        confirmed=True,
        confirmed_by=by,
        doubtful=[x for x in (key.doubtful if key else []) if x not in by_label],
        notes=key.notes if key else [],
    )
    new.warnings = key_warnings(new, q)
    store.save_key(new)
    return new


# ---------------------------------------------------------------- 원본 쪽


def exam_source(svc: Services, exam: Exam) -> Path | None:
    """시험지 원본 파일 (작업공간 sources/). 예전 시험은 등록 기록에서 시험 ID로 찾는다."""
    ws = svc.workspace
    src = None
    if exam.source_id:
        try:
            src = get_source(ws, exam.source_id)
        except KeyError:
            src = None
    if src is None:
        src = next((s for s in all_sources(ws) if s.ingest.message == exam.id), None)
    if src is None:
        return None
    path = source_file(ws, src)
    return path if path.is_file() else None


def source_pdf(svc: Services, exam: Exam) -> Path | None:
    """원본 PDF. 한글 파일로 만든 시험이면 읽을 때 한글로 만든 PDF (`workflow.readable_copy`)."""
    from dawoni.ingest.workflow import HANGUL_SUFFIXES, hangul_pdf

    path = exam_source(svc, exam)
    if path is None:
        return None
    if path.suffix.lower() == ".pdf":
        return path
    if path.suffix.lower() in HANGUL_SUFFIXES:
        made = hangul_pdf(path, svc.workspace.exams / exam.id / "ingest")
        return made if made.is_file() else None
    return None


def source_info(svc: Services, store: ExamStore) -> dict[str, object]:
    """영역 지정 화면: 원본 PDF의 쪽 크기·해설지 쪽. PDF가 아니면(문서) 그림 붙여 넣기만."""
    exam = store.exam()
    path = exam_source(svc, exam)
    pdf = source_pdf(svc, exam)
    if pdf is None:
        return {"name": path.name if path else None, "pdf": False, "pages": [], "key_pages": []}
    return {
        "name": pdf.name,
        "pdf": True,
        "pages": [{"width": w, "height": h} for w, h in page_sizes(pdf)],
        "key_pages": find_key_pages(pdf),
    }


def page_image(svc: Services, store: ExamStore, page: int) -> Path:
    pdf = source_pdf(svc, store.exam())
    if pdf is None:
        raise PipelineError("시험지 원본 PDF가 없습니다 — 캡처한 그림을 붙여 넣어 주세요.")
    if not 1 <= page <= len(page_sizes(pdf)):
        raise PipelineError(f"{page}쪽이 없습니다.")
    out = store.root / "source_pages" / f"p{page}.png"
    if not out.is_file():
        out.parent.mkdir(parents=True, exist_ok=True)
        render_page(pdf, page, out, dpi=110)
    return out


def _rects(pdf: Path, boxes: list[Box]) -> list[tuple[int, Rect]]:
    sizes = page_sizes(pdf)
    out: list[tuple[int, Rect]] = []
    for b in boxes:
        if b.page > len(sizes):
            raise PipelineError(f"{b.page}쪽이 없습니다.")
        w, h = sizes[b.page - 1]
        x0, x1 = sorted((b.x0, b.x1))
        y0, y1 = sorted((b.y0, b.y1))
        if (x1 - x0) * w < 10 or (y1 - y0) * h < 10:
            raise PipelineError("너무 작은 상자가 있습니다 — 영역을 다시 끌어 주세요.")
        out.append((b.page, Rect(x0 * w, y0 * h, x1 * w, y1 * h)))
    return out


def _work(store: ExamStore, kind: str, number: str) -> Path:
    stamp = utcnow().strftime("%Y%m%d-%H%M%S-%f")
    d = store.root / kind / f"{safe_name(number)}-{stamp}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _copy_pasted(pasted: list[Path], work: Path) -> list[Path]:
    out: list[Path] = []
    for k, f in enumerate(pasted, start=1):
        dest = work / f"pasted_{k}{f.suffix.lower() or '.png'}"
        shutil.copy2(f, dest)
        out.append(dest)
    return out


# ---------------------------------------------------------------- 영역 지정해서 읽기


@dataclass
class RegionResult:
    question: ExamQuestion
    notes: list[str] = field(default_factory=lambda: list[str]())


def read_question_region(
    svc: Services, store: ExamStore, number: str, boxes: list[Box], pasted: list[Path]
) -> RegionResult:
    """문항 본문을 강사가 지정한 영역에서 다시 읽는다. 강사가 정한 소문항·배점은 그대로."""
    if not boxes and not pasted:
        raise PipelineError("원본에서 영역을 끌어 지정하거나 그림을 붙여 넣어 주세요.")
    exam = store.exam()
    q = exam.question(number)
    _locked(store, number)
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    subject = q.item.subject or scope.subject
    work = _work(store, "region_reads", number)
    files = _copy_pasted(pasted, work)
    where = exam.title
    if boxes:
        pdf = source_pdf(svc, exam)
        if pdf is None:
            raise PipelineError("시험지 원본 PDF가 없습니다 — 캡처한 그림을 붙여 넣어 주세요.")
        where = pdf.name
        layout = crop_region(pdf, number, _rects(pdf, boxes))
        prepared = prepare_regions(layout, work)[0]
        prepared.images += files
    else:
        prepared = PreparedRegion(Region(label=number), files, {})
    with situation(where, exam.title, subject):
        res = read_region(svc.engine, prepared, subject=subject, level=scope.level,
                          fixed=q.parts or None)  # fmt: skip
    item = to_item(res.question, res.region, subject)
    figures = store.root / "figures" / safe_name(number)
    shutil.rmtree(figures, ignore_errors=True)
    if res.figure_files:
        figures.mkdir(parents=True, exist_ok=True)
        for fid, f in res.figure_files.items():
            shutil.copy2(f, figures / f"{fid}.png")
    notes = [res.note] if res.note else []
    parts = q.parts or res.question.parts
    if parts:
        points = sum(p.points for p in parts)
    else:
        points = res.question.points or res.region.points or q.points
    if not q.parts and abs(points - q.points) > 1e-9:
        notes.append(
            f"배점을 {q.points:g}점에서 {points:g}점으로 바꿈 (그림에서 읽음) — 확인하세요"
        )
    new = q.model_copy(
        update={
            "points": points,
            "parts": parts,
            "item": item.model_copy(update={"points": points}),
            "source_pages": sorted({b.page for b in boxes}) or q.source_pages,
        }
    )
    new = _save(store, exam, new)
    if _labels(new) != _labels(q):
        lost = follow_key(store, new, {label: label for label in _labels(new)})
        if lost:
            notes.append(f"해설지의 {', '.join(lost)} 해설은 맞는 소문항이 없어 뺐습니다")
    _stale(store, number)
    return RegionResult(new, notes)


@dataclass
class KeyRegionResult:
    key: AnswerKey
    warnings: list[str] = field(default_factory=lambda: list[str]())


def read_key_region(
    svc: Services, store: ExamStore, number: str, boxes: list[Box], pasted: list[Path]
) -> KeyRegionResult:
    """한 문항의 해설을 강사가 지정한 영역에서 읽는다 (그 문항의 소문항 표시로)."""
    if not boxes and not pasted:
        raise PipelineError("원본에서 해설 영역을 끌어 지정하거나 그림을 붙여 넣어 주세요.")
    exam = store.exam()
    q = exam.question(number)
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    work = _work(store, "key_crops", number)
    files = _copy_pasted(pasted, work)
    clip = ""
    where = "붙여 넣은 그림"
    if boxes:
        pdf = source_pdf(svc, exam)
        if pdf is None:
            raise PipelineError("시험지 원본 PDF가 없습니다 — 캡처한 그림을 붙여 넣어 주세요.")
        rects = _rects(pdf, boxes)
        crops = [
            render_clip(pdf, p, r, work / f"crop_{k}.png", dpi=200)
            for k, (p, r) in enumerate(rects, start=1)
        ]
        files = crops + files
        clip = clip_text(pdf, rects)
        where = f"{pdf.name} {', '.join(str(p) for p in sorted({b.page for b in boxes}))}쪽 일부"
    one = exam.model_copy(update={"questions": [q]})
    with situation(where, exam.title, q.item.subject or scope.subject):
        res = extract_answer_key(svc.engine, CropKey(number, files, clip, where), None, one,
                                 work, level=scope.level, cross=False)  # fmt: skip
    if not res.keys:
        raise PipelineError(f"문항 {number}의 해설을 읽지 못했습니다. {' '.join(res.warnings)}")
    key = res.keys[0].model_copy(
        update={"pages": [f.relative_to(store.root).as_posix() for f in files]}
    )
    store.save_key(key)
    return KeyRegionResult(key, res.warnings)
