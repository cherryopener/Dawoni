"""PDF 자료 DB화를 끝까지: 시험지 → 시험, 교재 → 범위 프로필 초안. 명령줄·서버 공용."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dawoni.grading.store import ExamStore, ScopeStore, safe_name
from dawoni.ingest.layout import DocumentLayout, analyze
from dawoni.ingest.sources import find_source, register_source, update_source
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


@dataclass
class ExamIngest:
    exam: Exam
    layout: DocumentLayout
    warnings: list[str] = field(default_factory=lambda: list[str]())


def ingest_exam_pdf(
    svc: Services,
    path: Path,
    *,
    title: str,
    scope_id: str,
    subject: str | None = None,
    force: bool = False,
    progress: Progress = _noop,
) -> ExamIngest:
    from dawoni.ingest.exam import extract_exam

    scope = ScopeStore(svc.workspace).get(scope_id)
    existing = find_source(svc.workspace, path)
    if existing and existing.ingest.status == "ingested" and not force:
        raise IngestRefused(
            f"이미 DB화한 자료입니다 ({existing.ingest.item_count}문항, "
            f"{existing.ingest.message}). 다시 하려면 '다시 DB화'를 선택하세요."
        )
    progress(0, 2, "레이아웃 분석 (문항 영역·그림)")
    layout = analyze(path)
    if layout.scanned:
        raise IngestRefused("글자 정보가 없는 스캔 PDF입니다. 아직 디지털 PDF만 지원합니다.")
    src = register_source(svc.workspace, path, "exam", title=title)
    exam_id = new_id("exam")
    exam_dir = svc.workspace.exams / exam_id
    progress(1, 2, f"문항 {len(layout.regions)}개 구조화")
    result = extract_exam(
        svc.engine, layout, exam_dir / "ingest", subject=subject or scope.subject, level=scope.level
    )
    for label, figs in result.figure_files.items():
        target = exam_dir / "figures" / safe_name(label)
        target.mkdir(parents=True, exist_ok=True)
        for fid, f in figs.items():
            shutil.copy2(f, target / f"{fid}.png")
    exam = Exam(id=exam_id, title=title, scope_id=scope.id, questions=result.questions)
    ExamStore.create(svc.workspace, exam)
    src.ingest = SourceIngestState(
        status="ingested", item_count=len(exam.questions), message=exam_id
    )
    update_source(svc.workspace, src)
    progress(2, 2, f"시험 등록 ({len(exam.questions)}문항)")
    return ExamIngest(exam, layout, result.warnings)


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
    progress: Progress = _noop,
) -> TextbookIngest:
    from dawoni.ingest.textbook import extract_scope

    src = register_source(svc.workspace, path, "textbook", title=title)
    progress(0, 1, "교재에서 개념·기법 추출")
    draft = extract_scope(svc.engine, path, title=title, level=level, subject=subject, pages=pages)
    ScopeStore(svc.workspace).save(draft.scope)
    src.ingest = SourceIngestState(
        status="ingested", item_count=len(draft.scope.concepts), message=draft.scope.id
    )
    update_source(svc.workspace, src)
    progress(1, 1, "범위 프로필 초안 완료 — 검토하세요")
    return TextbookIngest(draft.scope, list(draft.warnings))
