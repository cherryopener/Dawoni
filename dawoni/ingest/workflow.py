"""PDF 자료 DB화를 끝까지: 시험지 → 시험, 교재 → 범위 프로필 초안. 명령줄·서버 공용."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dawoni.grading.store import ExamStore, ScopeStore, safe_name
from dawoni.ingest.layout import DocumentLayout, analyze, find_key_pages, page_count
from dawoni.ingest.sources import find_source, register_source, update_source
from dawoni.models.answer_key import AnswerKey
from dawoni.models.base import new_id
from dawoni.models.grading import Exam
from dawoni.models.scope import ScopeProfile
from dawoni.models.source import SourceIngestState
from dawoni.services import Services

Progress = Callable[[int, int, str], None]


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
    """같은 PDF로 이미 시험을 만들었으면 그 안내 (시험 폴더가 지워졌으면 None)."""
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


@dataclass
class ExamIngest:
    exam: Exam
    layout: DocumentLayout
    warnings: list[str] = field(default_factory=lambda: list[str]())
    key_pages: list[int] = field(default_factory=lambda: list[int]())
    keys: list[AnswerKey] = field(default_factory=lambda: list[AnswerKey]())


@dataclass
class KeyAttach:
    keys: list[AnswerKey]
    warnings: list[str]


def attach_answer_key(
    svc: Services,
    store: ExamStore,
    path: Path,
    pages: list[int] | None = None,
    *,
    progress: Progress = _noop,
) -> KeyAttach:
    """해설지 PDF(또는 시험지 PDF의 해설 쪽)를 읽어 문항별 모범 답안으로 저장.

    `pages`가 없으면 "부분 점수"·"정답 및 해설" 표시가 있는 쪽, 그것도 없으면 전체 쪽.
    이미 만든 루브릭은 그대로 둔다 — 해설지를 반영하려면 루브릭을 다시 만든다.
    """
    from dawoni.ingest.answer_key import extract_answer_key

    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    if not pages:
        pages = find_key_pages(path)
    if not pages:
        pages = list(range(1, page_count(path) + 1))
    progress(0, 1, f"해설지 {', '.join(map(str, pages))}쪽 읽기")
    res = extract_answer_key(
        svc.engine, path, pages, exam, store.root / "key_work", level=scope.level
    )
    for k in res.keys:
        store.save_key(k)
    progress(1, 1, f"해설지 저장 ({len(res.keys)}문항)")
    return KeyAttach(res.keys, res.warnings)


def ingest_exam_pdf(
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
    """`key_pages`: 해설지 쪽. None이면 자동으로 찾고("부분 점수" 등), []이면 해설지 없음."""
    from dawoni.ingest.exam import extract_exam

    scope = ScopeStore(svc.workspace).get(scope_id)
    if not force and (refused := existing_exam(svc, path)) is not None:
        raise refused
    if key_pages is None:
        key_pages = find_key_pages(path)
    steps = 3 if key_pages else 2
    progress(0, steps, "레이아웃 분석 (문항 영역·그림)")
    layout = analyze(path, exclude=key_pages)
    exam_id = new_id("exam")
    exam_dir = svc.workspace.exams / exam_id
    if layout.scanned:
        from dawoni.ocr.local import INSTALL_HINT, analyze_scanned

        ocr = analyze_scanned(path, layout, exam_dir / "ingest", exclude=key_pages)
        if ocr is None:
            raise IngestRefused(f"글자 정보가 없는 스캔 PDF입니다. {INSTALL_HINT}")
        layout = ocr
    src = register_source(svc.workspace, path, "exam", title=title)
    progress(1, steps, f"문항 {len(layout.regions)}개 구조화")
    result = extract_exam(
        svc.engine, layout, exam_dir / "ingest", subject=subject or scope.subject,
        level=scope.level, workers=svc.config.parallel_questions,
    )  # fmt: skip
    for label, figs in result.figure_files.items():
        target = exam_dir / "figures" / safe_name(label)
        target.mkdir(parents=True, exist_ok=True)
        for fid, f in figs.items():
            shutil.copy2(f, target / f"{fid}.png")
    exam = Exam(id=exam_id, title=title, scope_id=scope.id, questions=result.questions)
    store = ExamStore.create(svc.workspace, exam)
    src.ingest = SourceIngestState(
        status="ingested", item_count=len(exam.questions), message=exam_id
    )
    update_source(svc.workspace, src)
    out = ExamIngest(exam, layout, result.warnings, key_pages=list(key_pages))
    if key_pages:
        from dawoni.ingest.answer_key import extract_answer_key

        progress(2, steps, f"해설지 {', '.join(map(str, key_pages))}쪽 읽기")
        key = extract_answer_key(
            svc.engine, path, key_pages, exam, exam_dir / "key_work", level=scope.level,
            workers=svc.config.parallel_questions,
        )  # fmt: skip
        for k in key.keys:
            store.save_key(k)
        out.keys = key.keys
        out.warnings += key.warnings
    progress(steps, steps, f"시험 등록 ({len(exam.questions)}문항)")
    return out


@dataclass
class TextbookIngest:
    scope: ScopeProfile
    warnings: list[str] = field(default_factory=lambda: list[str]())


def ingest_textbook_pdf(
    svc: Services,
    path: Path,
    *,
    title: str,
    level: str,
    subject: str | None = None,
    pages: list[int] | None = None,
    unit_ids: list[str] | None = None,
    progress: Progress = _noop,
) -> TextbookIngest:
    from dawoni.ingest.textbook import extract_scope

    src = register_source(svc.workspace, path, "textbook", title=title)
    progress(0, 1, "교재에서 개념·기법 추출")
    draft = extract_scope(svc.engine, path, title=title, level=level, subject=subject, pages=pages)
    if unit_ids:
        draft.scope = draft.scope.model_copy(update={"unit_ids": unit_ids})
    ScopeStore(svc.workspace).save(draft.scope)
    src.ingest = SourceIngestState(
        status="ingested", item_count=len(draft.scope.concepts), message=draft.scope.id
    )
    update_source(svc.workspace, src)
    progress(1, 1, "범위 프로필 초안 완료 — 검토하세요")
    return TextbookIngest(draft.scope, list(draft.warnings))
