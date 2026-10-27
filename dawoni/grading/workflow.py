"""시험 한 개를 끝까지: 해설 → 루브릭 → 답안(입력·판독) → 채점. 명령줄과 서버(화면)가 함께 쓴다.

각 함수는 결과를 구조로 돌려주고 출력은 하지 않는다. 진행 상황은 `progress(done, total, 메시지)`로.
강사 단계(루브릭 승인, 판독문 확인)를 건너뛰는 길은 여기에도 없다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dawoni.agents import AgentError, AgentErrorCode
from dawoni.context import TaskFailed
from dawoni.grading.pipeline import (
    PipelineError,
    SecondOpinion,
    derive_rubric,
    grade_answer,
    solve_question,
)
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.models.grading import (
    Exam,
    ExamQuestion,
    GradeResult,
    Rubric,
    ScopedSolution,
    TranscribedLine,
    Transcript,
)
from dawoni.services import Services

Progress = Callable[[int, int, str], None]


def _noop(done: int, total: int, message: str) -> None:
    pass


def select_questions(exam: Exam, only: list[str] | None) -> list[ExamQuestion]:
    if not only:
        return exam.questions
    return [exam.question(n) for n in only]


# ---------------------------------------------------------------- 해설·루브릭


def solve_questions(
    svc: Services,
    store: ExamStore,
    only: list[str] | None = None,
    *,
    verify: SecondOpinion | None = None,
    progress: Progress = _noop,
) -> list[ScopedSolution]:
    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    qs = select_questions(exam, only)
    out: list[ScopedSolution] = []
    for k, q in enumerate(qs):
        progress(k, len(qs), f"문항 {q.number} 해설")
        sol = solve_question(svc.engine, q, scope, verify=verify)
        store.save_solution(sol)
        out.append(sol)
    progress(len(qs), len(qs), "해설 완료")
    return out


def rubric_questions(
    svc: Services, store: ExamStore, only: list[str] | None = None, *, progress: Progress = _noop
) -> list[Rubric]:
    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    qs = select_questions(exam, only)
    missing = [q.number for q in qs if store.solution(q.number) is None]
    if missing:
        raise PipelineError(f"문항 {', '.join(missing)}: 해설이 없습니다. 먼저 해설을 만드세요.")
    out: list[Rubric] = []
    for k, q in enumerate(qs):
        progress(k, len(qs), f"문항 {q.number} 루브릭")
        sol = store.solution(q.number)
        assert sol is not None
        r = derive_rubric(svc.engine, q, sol, scope, previous=store.rubric(q.number))
        store.save_rubric(r)
        out.append(r)
    progress(len(qs), len(qs), "루브릭 초안 완료 — 검토 후 승인하세요")
    return out


# ---------------------------------------------------------------- 손글씨 판독


@dataclass
class ScanResult:
    saved: list[Transcript] = field(default_factory=lambda: list[Transcript]())
    kept_confirmed: list[str] = field(default_factory=lambda: list[str]())
    """이미 확인된 판독문이 있어 덮어쓰지 않은 문항."""
    unassigned: list[TranscribedLine] = field(default_factory=lambda: list[TranscribedLine]())
    notes: list[str] = field(default_factory=lambda: list[str]())
    warnings: list[str] = field(default_factory=lambda: list[str]())


def scan_student(
    svc: Services,
    store: ExamStore,
    student: str,
    files: list[Path],
    *,
    cross: bool | None = None,
    cross_model: str | None = None,
    overwrite: bool = False,
    progress: Progress = _noop,
) -> ScanResult:
    """답안지 사진·PDF → 문항별 판독문(미확인). `cross=None`이면 설정(ocr.cross_check)을 따른다."""
    from dawoni.ocr.hints import collect_hints, providers_from
    from dawoni.ocr.pages import prepare_pages
    from dawoni.ocr.transcribe import transcribe_student

    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    progress(0, 2, "답안지 사진 정리")
    pages = prepare_pages(files, store.scan_dir(student))
    refs = list(dict.fromkeys(p.original.relative_to(store.root).as_posix() for p in pages))
    res = ScanResult()
    hints, res.warnings = collect_hints(providers_from(svc.config.ocr), [p.image for p in pages])
    use_cross = svc.config.ocr.cross_check if cross is None else cross
    progress(1, 2, f"{student} 답안지 {len(pages)}쪽 판독" + (" (+교차 판독)" if use_cross else ""))
    outcome = transcribe_student(
        svc.engine,
        exam,
        scope.level,
        student,
        [p.image for p in pages],
        page_refs=refs,
        cross=SecondOpinion(model=cross_model) if use_cross else None,
        hints=hints,
    )
    for t in outcome.transcripts.values():
        old = store.transcript(student, t.question_number)
        if old is not None and old.confirmed and not overwrite:
            res.kept_confirmed.append(t.question_number)
            continue
        store.save_transcript(t)
        res.saved.append(t)
    res.unassigned = outcome.unassigned
    res.notes = outcome.notes
    progress(2, 2, "판독 완료 — 원본과 비교해 확인하세요")
    return res


# ---------------------------------------------------------------- 채점


@dataclass
class Skipped:
    student: str
    question: str
    reason: str


@dataclass
class GradeRun:
    graded: list[GradeResult] = field(default_factory=lambda: list[GradeResult]())
    skipped: list[Skipped] = field(default_factory=lambda: list[Skipped]())
    failed: list[Skipped] = field(default_factory=lambda: list[Skipped]())


def grade_exam(
    svc: Services,
    store: ExamStore,
    *,
    students: list[str] | None = None,
    only: list[str] | None = None,
    double: SecondOpinion | None = None,
    regrade: bool = False,
    progress: Progress = _noop,
) -> GradeRun:
    """승인된 루브릭 × 확인된 판독문만 채점. 한 학생의 실패가 나머지를 멈추지 않는다."""
    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    qs = select_questions(exam, only)
    for q in qs:
        if store.rubric(q.number) is None:
            raise PipelineError(f"문항 {q.number}: 루브릭이 없습니다.")
    roster = students or store.students()
    run = GradeRun()
    todo: list[tuple[ExamQuestion, Rubric, Transcript]] = []
    for q in qs:
        rubric = store.rubric(q.number)
        assert rubric is not None
        for st in roster:
            t = store.transcript(st, q.number)
            if t is None:
                continue
            if not t.confirmed:
                run.skipped.append(Skipped(st, q.number, "판독문 확인 대기"))
                continue
            old = store.grade(st, q.number)
            if old is not None and not regrade and old.rubric_version == rubric.version:
                run.skipped.append(
                    Skipped(st, q.number, f"이미 채점됨 ({old.total:g}/{old.max_points:g})")
                )
                continue
            todo.append((q, rubric, t))
    for k, (q, rubric, t) in enumerate(todo):
        progress(k, len(todo), f"{t.student_id} 문항 {q.number} 채점")
        try:
            g = grade_answer(svc.engine, q, rubric, t, scope, double=double)
        except (AgentError, TaskFailed) as e:
            if isinstance(e, AgentError) and e.code == AgentErrorCode.CANCELLED:
                raise
            run.failed.append(Skipped(t.student_id, q.number, str(e)))
            continue
        store.save_grade(g)
        run.graded.append(g)
    progress(len(todo), len(todo), f"채점 {len(run.graded)}건 완료")
    return run
