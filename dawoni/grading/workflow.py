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
from dawoni.grading import calibration
from dawoni.grading.pipeline import (
    PipelineError,
    SecondOpinion,
    derive_rubric,
    grade_answer,
    solve_question,
    suggest_rubric,
)
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.models.calibration import CalibrationEval, EvalCase, RubricSuggestion
from dawoni.models.grading import (
    Exam,
    ExamQuestion,
    GradeResult,
    Rubric,
    ScopedSolution,
    StudentRecord,
    TranscribedLine,
    Transcript,
)
from dawoni.ocr.pages import PageError
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
        key = store.answer_key(q.number)
        basis = " (해설지 기준)" if key else " (해설지 없음)"
        progress(k, len(qs), f"문항 {q.number} 루브릭{basis}")
        r = derive_rubric(svc.engine, q, sol, scope, key=key, previous=store.rubric(q.number))
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


@dataclass
class BatchResult:
    students: list[StudentRecord] = field(default_factory=lambda: list[StudentRecord]())
    skipped: list[str] = field(default_factory=lambda: list[str]())
    """이미 판독한 학생 (다시 하려면 overwrite)."""
    failed: list[Skipped] = field(default_factory=lambda: list[Skipped]())
    warnings: list[str] = field(default_factory=lambda: list[str]())


def scan_batch(
    svc: Services,
    store: ExamStore,
    pdf: Path,
    *,
    per_student: int,
    classes: list[str] | None = None,
    only_sets: list[int] | None = None,
    cross: bool | None = None,
    overwrite: bool = False,
    progress: Progress = _noop,
) -> BatchResult:
    """학생 여러 명의 답안지 PDF (학생마다 `per_student`쪽, 첫 쪽은 표지) → 학생별 판독문(미확인).

    표지에서 이름·반·학교·강사 점수와 돌려 세울 각도를 읽는다. 학생 한 명이 실패해도 나머지는 계속.
    """
    from dawoni.ocr import batch
    from dawoni.ocr.transcribe import transcribe_student

    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    sets = batch.plan_sets(batch.page_count(pdf), per_student, only_sets)
    use_cross = svc.config.ocr.cross_check if cross is None else cross
    res = BatchResult()
    by_source = {r.source: r for r in store.student_records()}
    taken = {r.student_id: r.source for r in store.student_records()}
    for k, plan in enumerate(sets):
        source = f"{pdf.name} {plan.pages[0]}-{plan.pages[-1]}쪽"
        old = by_source.get(source)
        if old is not None and not overwrite:  # 학생 정보는 판독을 다 저장한 뒤에 남긴다
            res.skipped.append(old.student_id)
            continue
        progress(k, len(sets), f"{plan.number}번째 학생 ({source}) 표지 읽기")
        work = store.root / "scans" / "_batch" / f"set_{plan.number:03d}"
        try:
            images = batch.render_set(pdf, plan.pages, work)
            cover = batch.read_cover(svc.engine, exam, images[0], classes)
            batch.rotate(images, int(cover.rotation))
            sid = batch.student_id_of(cover.name, plan.number)
            if taken.get(sid, source) != source:
                res.warnings.append(
                    f"{source}: 이름 '{sid}'가 다른 묶음({taken[sid]})과 같음 — "
                    f"'{sid}#{plan.number}'로 저장, 동명이인이면 이름 뒤 알파벳을 확인하세요"
                )
                sid = f"{sid}#{plan.number}"
            taken[sid] = source
            record = StudentRecord(
                student_id=sid,
                name=cover.name,
                class_name=cover.class_name,
                school=cover.school,
                source=source,
                teacher_scores={c.question: c.points for c in cover.teacher_scores},
                uncertain=list(cover.uncertain),
            )
            if cover.name is None:
                res.warnings.append(f"{source}: 이름을 읽지 못해 '{sid}'로 저장 — 고쳐 주세요")
            progress(k, len(sets), f"{sid} 답안 {len(images) - 1}쪽 판독")
            scan_dir = store.scan_dir(sid)
            scan_dir.mkdir(parents=True, exist_ok=True)
            pages: list[Path] = []
            for i, img in enumerate(images, start=1):
                dst = scan_dir / f"page_{i:02d}.png"
                img.replace(dst)
                pages.append(dst)
            refs = [p.relative_to(store.root).as_posix() for p in pages]
            outcome = transcribe_student(
                svc.engine,
                exam,
                scope.level,
                sid,
                pages[1:],
                page_refs=refs,
                cross=SecondOpinion() if use_cross else None,
            )
            for t in outcome.transcripts.values():
                prev = store.transcript(sid, t.question_number)
                if prev is not None and prev.confirmed and not overwrite:
                    continue
                store.save_transcript(t.model_copy(update={"notes": t.notes + outcome.notes}))
            store.save_student(record)
            res.students.append(record)
        except (AgentError, TaskFailed, PageError, OSError) as e:
            res.failed.append(Skipped(source, "-", str(e)))
    progress(len(sets), len(sets), "판독 완료 — 원본과 비교해 확인하세요")
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
    use_examples: bool = True,
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
        pick = calibration.pick_for(store, q.number, rubric, t) if use_examples else None
        try:
            g = grade_answer(
                svc.engine,
                q,
                rubric,
                t,
                scope,
                double=double,
                examples=pick.layer if pick else None,
                example_students=pick.students if pick else None,
            )
        except (AgentError, TaskFailed) as e:
            if isinstance(e, AgentError) and e.code == AgentErrorCode.CANCELLED:
                raise
            run.failed.append(Skipped(t.student_id, q.number, str(e)))
            continue
        store.save_grade(g)
        run.graded.append(g)
    progress(len(todo), len(todo), f"채점 {len(run.graded)}건 완료")
    return run


# ---------------------------------------------------------------- 보정


def evaluate_calibration(
    svc: Services,
    store: ExamStore,
    q_number: str,
    *,
    k: int | None = None,
    progress: Progress = _noop,
) -> CalibrationEval:
    """보정 효과 측정: 강사가 확정한 답안마다 (가) 예시 없이 (나) 자기 답안을 뺀 예시와 함께
    다시 채점해 강사 점수와 비교한다. 저장된 채점 결과는 건드리지 않는다."""
    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    q = exam.question(q_number)
    rubric = store.rubric(q_number)
    if rubric is None or rubric.status != "approved":
        raise PipelineError(f"문항 {q_number}: 승인된 루브릭이 필요합니다.")
    settings = calibration.load_settings(store, q_number)
    k = k if k is not None else max(settings.max_examples, 1)
    truth = calibration.candidates(store, q_number, rubric)
    if len(truth) < 2:
        raise PipelineError(
            "강사가 확정한 답안이 2개 이상 있어야 측정할 수 있습니다 (하나는 예시, 하나는 대상)."
        )
    ev = CalibrationEval(question_number=q_number, rubric_version=rubric.version)
    total = len(truth) * 2
    for n, ex in enumerate(truth):
        t = ex.transcript.model_copy(update={"confirmed": True})
        case = EvalCase(
            student_id=ex.student,
            teacher_total=ex.grade.total,
            teacher_items={j.rubric_item_id: j.awarded for j in ex.grade.judgements},
        )
        try:
            progress(2 * n, total, f"{ex.student}: 예시 없이 채점")
            before = grade_answer(svc.engine, q, rubric, t, scope)
            case.before_total = before.total
            case.before_items = {j.rubric_item_id: j.awarded for j in before.judgements}
            others = calibration.candidates(store, q_number, rubric, exclude_student=ex.student)
            chosen = calibration.select_examples(others, t, k)
            progress(2 * n + 1, total, f"{ex.student}: 기준 예시 {len(chosen)}개와 채점")
            after = grade_answer(
                svc.engine,
                q,
                rubric,
                t,
                scope,
                examples=calibration.examples_layer(chosen, rubric),
                example_students=[c.student for c in chosen],
            )
            case.after_total = after.total
            case.after_items = {j.rubric_item_id: j.awarded for j in after.judgements}
            case.examples_used = after.examples_used
            ev.agent, ev.model = after.agent, after.model
        except (AgentError, TaskFailed) as e:
            if isinstance(e, AgentError) and e.code == AgentErrorCode.CANCELLED:
                raise
            case.error = str(e)
        ev.cases.append(case)
    ok = [c for c in ev.cases if c.error is None]
    ev.before = calibration.agreement([(c.before_items, c.teacher_items) for c in ok])
    ev.after = calibration.agreement([(c.after_items, c.teacher_items) for c in ok])
    calibration.save_eval(store, ev)
    progress(total, total, "측정 완료")
    return ev


@dataclass
class SuggestionRun:
    suggestion: RubricSuggestion
    cases: list[str]


def suggest_rubric_changes(svc: Services, store: ExamStore, q_number: str) -> SuggestionRun:
    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    q = exam.question(q_number)
    rubric = store.rubric(q_number)
    sol = store.solution(q_number)
    if rubric is None or sol is None:
        raise PipelineError(f"문항 {q_number}: 해설·루브릭이 필요합니다.")
    cases = [c for c in calibration.candidates(store, q_number, rubric) if c.kind == "correction"]
    if not cases:
        raise PipelineError(
            "강사가 AI 점수를 고친 사례가 없습니다. 채점 검토에서 점수를 고쳐 확정하면 생깁니다."
        )
    s = suggest_rubric(
        svc.engine,
        q,
        rubric,
        sol,
        scope,
        calibration.corrections_text(cases, rubric),
        {c.student for c in cases},
    )
    return SuggestionRun(s, [c.student for c in cases])
