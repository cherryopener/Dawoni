"""시험지 DB화를 끝까지: PDF·DOCX·HWPX(·HWP) → 시험 + 해설지. 명령줄·서버 공용."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dawoni.context import situation
from dawoni.grading.store import ExamStore, ScopeStore, safe_name
from dawoni.ingest.document import DocAnalysis, KeyPiece, analyze_document, is_document
from dawoni.ingest.layout import DocumentLayout, analyze, find_key_pages, page_count
from dawoni.ingest.sources import find_source, register_source, update_source
from dawoni.models.answer_key import AnswerKey
from dawoni.models.base import new_id
from dawoni.models.grading import Exam
from dawoni.models.source import SourceIngestState
from dawoni.services import Services

Progress = Callable[[int, int, str], None]

EXAM_SUFFIXES = (".pdf", ".docx", ".hwpx", ".hwp")


def _noop(done: int, total: int, message: str) -> None:
    pass


class IngestRefused(ValueError):
    """이미 DB화한 자료, 스캔 PDF 등 — 강사에게 이유를 보여 준다."""


class AlreadyIngested(IngestRefused):
    """같은 PDF(해시)로 이미 만든 시험이 있다 — 팀의 기존 시험(해설·루브릭 포함)을 연다."""

    def __init__(self, message: str, exam_id: str | None) -> None:
        super().__init__(message)
        self.exam_id = exam_id


def existing_exam(svc: Services, path: Path) -> AlreadyIngested | None:
    """같은 파일로 이미 시험을 만들었으면 그 안내 (시험 폴더가 지워졌으면 None)."""
    src = find_source(svc.workspace, path)
    if src is None or src.ingest.status != "ingested":
        return None
    exam_id = src.ingest.message
    if not exam_id or not (svc.workspace.exams / exam_id).is_dir():
        return None
    title = ExamStore(svc.workspace, exam_id).exam().title
    return AlreadyIngested(
        f"이미 등록된 시험지입니다 — '{title}' ({src.ingest.item_count}문항, {exam_id}). "
        "해설·루브릭이 있으면 그대로 볼 수 있습니다. 새로 만들려면 '다시 DB화'를 선택하세요.",
        exam_id,
    )


def readable_copy(path: Path, work_dir: Path) -> Path:
    """앱이 직접 읽는 형식으로: `.hwp`는 설치된 한글로 HWPX를 만든다 (나머지는 그대로)."""
    suffix = path.suffix.lower()
    if suffix not in EXAM_SUFFIXES:
        raise IngestRefused(f"PDF·DOCX·HWPX·HWP 파일만 읽습니다: {path.name}")
    if suffix != ".hwp":
        return path
    from dawoni.ingest.convert import ConvertUnavailable, hwp_to_hwpx

    try:
        return hwp_to_hwpx(path, work_dir / f"{safe_name(path.stem)}.hwpx")
    except ConvertUnavailable as e:
        raise IngestRefused(str(e)) from e


@dataclass
class ExamIngest:
    exam: Exam
    layout: DocumentLayout
    warnings: list[str] = field(default_factory=lambda: list[str]())
    key_pages: list[int] = field(default_factory=lambda: list[int]())
    keys: list[AnswerKey] = field(default_factory=lambda: list[AnswerKey]())
    key_source: str | None = None
    """해설지를 어디서 읽었나 ("6쪽", "문항별 미주") — 없으면 None."""


@dataclass
class KeyAttach:
    keys: list[AnswerKey]
    warnings: list[str]


def _doc_key_pieces(analysis: DocAnalysis, numbers: set[str]) -> dict[str, KeyPiece]:
    """문서의 문항별 해설. 미주·해설 구간이 없으면 문서 전체를 해설지로 본다 (따로 온 해설 파일)."""
    pieces = analysis.keys or {
        r.label: KeyPiece(r.text, [f.file for f in r.figures if f.file is not None])
        for r in analysis.layout.regions
    }
    return {n: p for n, p in pieces.items() if n in numbers}


def attach_answer_key(
    svc: Services,
    store: ExamStore,
    path: Path,
    pages: list[int] | None = None,
    *,
    progress: Progress = _noop,
) -> KeyAttach:
    """해설지 파일(또는 시험지 파일의 해설 부분)을 읽어 문항별 모범 답안으로 저장.

    PDF는 `pages`가 없으면 "부분 점수"·"정답 및 해설" 표시가 있는 쪽, 그것도 없으면 전체 쪽.
    DOCX·HWPX는 미주·해설 구간, 없으면 문서 전체를 문항 번호로 나눠 읽는다.
    이미 만든 루브릭은 그대로 둔다 — 해설지를 반영하려면 루브릭을 다시 만든다.
    """
    from dawoni.ingest.answer_key import DocKey, extract_answer_key

    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    work = store.root / "key_work"
    path = readable_copy(path, work)
    if is_document(path):
        pieces = _doc_key_pieces(analyze_document(path, work), {q.number for q in exam.questions})
        if not pieces:
            raise IngestRefused(f"{path.name}에서 이 시험 문항 번호의 해설을 찾지 못했습니다")
        progress(0, 1, f"해설 읽기 ({len(pieces)}문항)")
        res = extract_answer_key(svc.engine, DocKey(path, pieces), None, exam, work,
                                 level=scope.level)  # fmt: skip
    else:
        if not pages:
            pages = find_key_pages(path)
        if not pages:
            pages = list(range(1, page_count(path) + 1))
        progress(0, 1, f"해설지 {', '.join(map(str, pages))}쪽 읽기")
        res = extract_answer_key(svc.engine, path, pages, exam, work, level=scope.level)
    for k in res.keys:
        store.save_key(k)
    progress(1, 1, f"해설지 저장 ({len(res.keys)}문항)")
    return KeyAttach(res.keys, res.warnings)


def ingest_exam(
    svc: Services,
    path: Path,
    *,
    title: str,
    scope_id: str,
    subject: str | None = None,
    force: bool = False,
    key_pages: list[int] | None = None,
    progress: Progress = _noop,
) -> ExamIngest:
    """`key_pages`: PDF의 해설지 쪽. None이면 자동으로 찾고("부분 점수" 등), []이면 해설지 없음.

    DOCX·HWPX(·HWP)는 쪽이 없다: 미주와 "정답 및 해설" 뒤 문단을 문항별 해설로 읽는다
    (`key_pages=[]`이면 읽지 않음)."""
    from dawoni.ingest.answer_key import DocKey, KeySource, PdfKey, extract_answer_key
    from dawoni.ingest.exam import extract_exam

    scope = ScopeStore(svc.workspace).get(scope_id)
    if not force and (refused := existing_exam(svc, path)) is not None:
        raise refused
    exam_id = new_id("exam")
    exam_dir = svc.workspace.exams / exam_id
    readable = readable_copy(path, exam_dir / "ingest")
    key: KeySource | None = None
    if is_document(readable):
        progress(0, 3, "문서 읽기 (문항·그림·미주)")
        analysis = analyze_document(readable, exam_dir / "ingest")
        layout = analysis.layout
        if not layout.regions:
            raise IngestRefused(f"{path.name}에서 문항 번호(1. 2. …)를 찾지 못했습니다")
        if key_pages != []:
            labels = {r.label for r in layout.regions}
            if pieces := _doc_key_pieces_from(analysis, labels):
                key = DocKey(readable, pieces)
    else:
        if key_pages is None:
            key_pages = find_key_pages(path)
        progress(0, 3, "레이아웃 분석 (문항 영역·그림)")
        layout = analyze(path, exclude=key_pages)
        if layout.scanned:
            from dawoni.ocr.local import INSTALL_HINT, analyze_scanned

            ocr = analyze_scanned(path, layout, exam_dir / "ingest", exclude=key_pages)
            if ocr is None:
                raise IngestRefused(f"글자 정보가 없는 스캔 PDF입니다. {INSTALL_HINT}")
            layout = ocr
        if key_pages:
            key = PdfKey(path, key_pages)
    steps = 3 if key else 2
    src = register_source(svc.workspace, path, "exam", title=title)
    progress(1, steps, f"문항 {len(layout.regions)}개 구조화")
    with situation(path.name, title, subject or scope.subject):
        result = extract_exam(
            svc.engine, layout, exam_dir / "ingest", subject=subject or scope.subject,
            level=scope.level, workers=svc.config.parallel_questions,
        )  # fmt: skip
    for label, figs in result.figure_files.items():
        target = exam_dir / "figures" / safe_name(label)
        target.mkdir(parents=True, exist_ok=True)
        for fid, f in figs.items():
            shutil.copy2(f, target / f"{fid}.png")
    exam = Exam(
        id=exam_id,
        title=title,
        scope_id=scope.id,
        questions=result.questions,
        source_id=src.source_id,
    )
    store = ExamStore.create(svc.workspace, exam)
    src.ingest = SourceIngestState(
        status="ingested", item_count=len(exam.questions), message=exam_id
    )
    update_source(svc.workspace, src)
    out = ExamIngest(exam, layout, result.warnings, key_pages=list(key_pages or []))
    if isinstance(key, DocKey):  # 구조화하지 못한 문항의 해설은 빼고 (시험에 없는 번호)
        numbers = {q.number for q in exam.questions}
        key = DocKey(readable, {n: p for n, p in key.pieces.items() if n in numbers})
    if key is not None and key.pages:
        where = f"{', '.join(map(str, key_pages or []))}쪽" if not key.doc else "문항별 미주·해설"
        progress(2, steps, f"해설지 읽기 ({where})")
        with situation(path.name, title, subject or scope.subject):
            keys = extract_answer_key(
                svc.engine, key, key_pages, exam, exam_dir / "key_work", level=scope.level,
                workers=svc.config.parallel_questions,
            )  # fmt: skip
        for k in keys.keys:
            store.save_key(k)
        out.keys = keys.keys
        out.warnings += keys.warnings
        out.key_source = where
    progress(steps, steps, f"시험 등록 ({len(exam.questions)}문항)")
    return out


def _doc_key_pieces_from(analysis: DocAnalysis, labels: set[str]) -> dict[str, KeyPiece]:
    """시험지 문서 안의 해설만 (미주·해설 구간). 없으면 해설지 없음 — 본문을 해설로 보지 않는다."""
    return {n: p for n, p in analysis.keys.items() if n in labels}
