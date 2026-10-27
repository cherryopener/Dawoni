"""시험 자료 내보내기 한 번: 문서 조립 → 저장 → 자동 검사. 명령줄·서버 공용."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dawoni.export.docx.check import CheckReport, check_docx
from dawoni.export.docx.documents import (
    KeyOptions,
    PaperOptions,
    build_answer_key,
    build_exam_paper,
    build_grading_report,
    build_score_sheets,
)
from dawoni.export.docx.fonts import FontSpec
from dawoni.grading.store import ExamStore, safe_name

ExportKind = Literal["paper", "key", "report", "scores"]
KIND_NAMES: dict[ExportKind, str] = {
    "paper": "시험지",
    "key": "정답및해설",
    "report": "채점리포트",
    "scores": "성적표",
}


@dataclass
class ExportOutcome:
    path: Path
    check: CheckReport
    warnings: list[str] = field(default_factory=lambda: list[str]())
    math_fallbacks: list[str] = field(default_factory=lambda: list[str]())


def export_exam(
    store: ExamStore,
    kind: ExportKind,
    fonts: FontSpec,
    out_dir: Path,
    *,
    out: Path | None = None,
    paper: PaperOptions | None = None,
    key: KeyOptions | None = None,
    students: list[str] | None = None,
) -> ExportOutcome:
    exam = store.exam()
    match kind:
        case "paper":
            w = build_exam_paper(exam, store.root, fonts, paper)
        case "key":
            w = build_answer_key(exam, store, fonts, key)
        case "report":
            w = build_grading_report(exam, store, fonts, students)
        case "scores":
            w = build_score_sheets(exam, store, fonts, students)
    path = out or out_dir / f"{safe_name(exam.title)}_{KIND_NAMES[kind]}.docx"
    w.save(path)
    return ExportOutcome(path, check_docx(path), w.warnings, w.math_fallbacks)
