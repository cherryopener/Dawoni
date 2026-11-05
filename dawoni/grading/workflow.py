"""시험 한 개를 끝까지: 해설 → 루브릭 → 답안(입력·판독) → 채점 → HWPX. 명령줄과 서버(화면)가 쓴다.

각 함수는 결과를 구조로 돌려주고 출력은 하지 않는다. 진행 상황은 `progress(done, total, 메시지)`로.
루브릭 승인을 건너뛰는 길은 없다. 판독문 확인은 채점 뒤에 해도 된다 (`[판독불가]`만 기다린다 —
PLAN.md §1 원칙 8의 예외, 2026-11-04).
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path

from dawoni import sharing
from dawoni.agents import AgentError, AgentErrorCode
from dawoni.context import TaskFailed, situation
from dawoni.export.exam_files import ExportResult, write_files
from dawoni.grading import assign, calibration
from dawoni.grading.answers import code_gradable, code_rubric, grade_by_code
from dawoni.grading.pipeline import (
    PipelineError,
    SecondOpinion,
    answer_sides,
    derive_rubric,
    expand_key_rubric,
    grade_answer,
    solve_question,
    suggest_rubric,
)
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.models.base import utcnow
from dawoni.models.calibration import CalibrationEval, EvalCase, RubricSuggestion
from dawoni.models.grading import (
    UNREADABLE,
    CandidateMatches,
    Exam,
    ExamQuestion,
    GradeResult,
    Rubric,
    ScopedSolution,
    StudentRecord,
    TranscribedLine,
    Transcript,
)
from dawoni.ocr.batch import CoverRead, SetPlan
from dawoni.ocr.pages import PageError
from dawoni.parallel import Throttle, each_in_parallel, stops_all
from dawoni.services import Services
from dawoni.students import Roster, same_class, same_name

Progress = Callable[[int, int, str], None]
log = logging.getLogger(__name__)


def _noop(done: int, total: int, message: str) -> None:
    pass


def select_questions(exam: Exam, only: list[str] | None) -> list[ExamQuestion]:
    if not only:
        return exam.questions
    return [exam.question(n) for n in only]


# ---------------------------------------------------------------- 해설·루브릭


@contextlib.contextmanager
def _working(svc: Services, store: ExamStore, task: str, q: str, by: str | None) -> Generator[None]:
    """다른 컴퓨터에 작업 중임을 알리고, 스킬에 시험 이름을 상황으로 알린다."""
    with (
        sharing.working_on(
            svc.workspace, store.exam_id, task, q, sharing.machine_id(svc.paths), by
        ),
        situation(store.exam().title),
    ):
        yield


def solve_questions(
    svc: Services,
    store: ExamStore,
    only: list[str] | None = None,
    *,
    verify: SecondOpinion | None = None,
    by: str | None = None,
    progress: Progress = _noop,
) -> list[ScopedSolution]:
    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    qs = select_questions(exam, only)

    def one(q: ExamQuestion) -> ScopedSolution:
        with _working(svc, store, "solve", q.number, by):
            sol = solve_question(svc.engine, q, scope, verify=verify)
            sol = sol.model_copy(update={"created_by": by, "created_at": utcnow()})
            store.save_solution(sol)
            return sol

    out = each_in_parallel(
        qs, one, name=lambda q: f"문항 {q.number}", what="해설",
        workers=svc.config.parallel_questions, progress=progress,
    )  # fmt: skip
    progress(len(qs), len(qs), "해설 완료")
    return out


def rubric_questions(
    svc: Services,
    store: ExamStore,
    only: list[str] | None = None,
    *,
    ai_solve: bool = False,
    by: str | None = None,
    progress: Progress = _noop,
) -> list[Rubric]:
    """객관식·단답형은 코드로 (AI 호출 0회 — 정답은 해설지나 시험지에서). 해설지가 있는 서술형은
    해설지 넓히기(호출 1번). 해설지가 없거나 `ai_solve`("AI 풀기 (해설지 검산)")면 AI 풀이 →
    루브릭 → 검토 → 검산 — 해설이 없으면 먼저 푼다(`ai_solve`). 해설지도 해설도 없는 서술형은
    거절한다."""
    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    qs = select_questions(exam, only)
    coded = {q.number for q in qs if code_gradable(q)}
    keyed = {q.number for q in qs if store.answer_key(q.number) is not None and not ai_solve}
    missing = [
        q.number for q in qs if q.number not in keyed | coded and store.solution(q.number) is None
    ]
    if missing and not ai_solve:
        raise PipelineError(
            f"문항 {', '.join(missing)}: 해설지도 해설도 없습니다. 해설지를 붙이거나 먼저 해설을"
            " 만드세요."
        )

    def one(q: ExamQuestion) -> Rubric:
        with _working(svc, store, "rubric", q.number, by):
            key = store.answer_key(q.number)
            previous = store.rubric(q.number)
            if q.number in coded:
                r = code_rubric(q, key, previous).model_copy(update={"created_by": by})
                store.save_rubric(r)
                return r
            if q.number in keyed:
                assert key is not None
                r = expand_key_rubric(svc.engine, q, key, scope, previous=previous)
                r = r.model_copy(update={"created_by": by, "created_at": utcnow()})
                store.save_rubric(r)
                return r
            sol = store.solution(q.number)
            if sol is None:  # AI 풀기: 해설지를 보지 않고 먼저 푼다
                sol = solve_question(svc.engine, q, scope)
                sol = sol.model_copy(update={"created_by": by, "created_at": utcnow()})
                store.save_solution(sol)
            r = derive_rubric(svc.engine, q, sol, scope, key=key, previous=previous)
            r = r.model_copy(update={"created_by": by, "created_at": utcnow()})
            store.save_rubric(r)
            return r

    out = each_in_parallel(
        qs, one, name=lambda q: f"문항 {q.number}", what="루브릭",
        workers=svc.config.parallel_questions, progress=progress,
    )  # fmt: skip
    progress(len(qs), len(qs), "루브릭 초안 완료 — 검토 후 승인하세요")
    return out


def match_candidates(
    svc: Services, store: ExamStore, q_number: str, *, progress: Progress = _noop
) -> CandidateMatches:
    """정답 후보를 정하기 전에: 판독한 학생 답이 해설지 답·후보 중 어느 쪽인지 (AI, 저장)."""
    exam = store.exam()
    q = exam.question(q_number)
    rubric = store.rubric(q_number)
    if rubric is None:
        raise PipelineError(f"문항 {q_number}: 루브릭이 없습니다.")
    ts = [t for s in store.students() if (t := store.transcript(s, q_number)) is not None]
    if not ts:
        raise PipelineError(f"문항 {q_number}: 판독한 답안이 없습니다. 먼저 답안을 판독하세요.")
    progress(0, 1, f"문항 {q_number}: 학생 {len(ts)}명 답을 정답 후보와 비교")
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    m = answer_sides(svc.engine, q, rubric, ts, scope)
    m = m.model_copy(update={"unconfirmed": sorted(t.student_id for t in ts if not t.confirmed)})
    store.save_candidate_matches(m)
    progress(1, 1, "비교 완료")
    return m


# ---------------------------------------------------------------- 손글씨 판독


BLANK_NOTE = (
    "판독한 글이 없음 — 빈 답안으로 보고 AI 없이 0점 (원본에 답이 있으면 판독본에 채우세요)"
)


@dataclass
class ScanResult:
    saved: list[Transcript] = field(default_factory=lambda: list[Transcript]())
    kept_confirmed: list[str] = field(default_factory=lambda: list[str]())
    """이미 확인된 판독문이 있어 덮어쓰지 않은 문항."""
    unassigned: list[TranscribedLine] = field(default_factory=lambda: list[TranscribedLine]())
    notes: list[str] = field(default_factory=lambda: list[str]())
    warnings: list[str] = field(default_factory=lambda: list[str]())
    export: ExportResult = field(default_factory=ExportResult)
    """판독본 HWPX (강사가 한글에서 확인할 파일)."""


def export_readings(svc: Services, store: ExamStore, keys: list[tuple[str, str]]) -> ExportResult:
    """판독이 끝난 학생·문항의 판독본 HWPX를 쓴다 (코드만, 토큰 0 — PLAN.md §5-1 5단계)."""
    res = write_files(svc.workspace, store, keys)
    for w in res.warnings:
        log.warning(w)
    return res


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
    """답안지 사진·PDF → 문항별 판독문(미확인) + 판독본 HWPX. `cross=None`이면
    설정(ocr.cross_check)을 따른다."""
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
    res.export = export_readings(svc, store, [(student, t.question_number) for t in res.saved])
    progress(2, 2, "판독 완료 — 판독본 HWPX를 원본과 비교해 확인하세요")
    return res


@dataclass
class BatchResult:
    students: list[StudentRecord] = field(default_factory=lambda: list[StudentRecord]())
    skipped: list[str] = field(default_factory=lambda: list[str]())
    """이미 판독한 학생 (다시 하려면 overwrite)."""
    failed: list[Skipped] = field(default_factory=lambda: list[Skipped]())
    warnings: list[str] = field(default_factory=lambda: list[str]())
    export: ExportResult = field(default_factory=ExportResult)
    """판독본 HWPX (강사가 한글에서 확인할 파일)."""

    @property
    def needs_rename(self) -> list[StudentRecord]:
        return [r for r in self.students if r.needs_rename]

    @property
    def not_in_roster(self) -> list[StudentRecord]:
        return [r for r in self.students if r.roster_note]


@dataclass
class _SetJob:
    """묶음 안의 학생 한 명 (표지 → 학생 ID → 판독)."""

    plan: SetPlan
    source: str
    record: StudentRecord | None
    need_cover: bool
    wanted: list[int]
    """판독할 쪽 (묶음 안의 쪽 번호, 표지 = 1)."""
    work: Path
    cover_image: Path | None = None
    cover: CoverRead | None = None
    failed: bool = False


def _cover_record(
    job: _SetJob, roster: Roster, taken: dict[str, StudentRecord], per_student: int
) -> tuple[StudentRecord, list[str]]:
    """표지에서 읽은 것 → 학생 기록. 명부가 있으면 명부의 이름·반으로 맞추고, 같은 반에 같은 이름의
    시험지가 이미 있으면 `needs_rename`(이름을 바꾸기 전에는 HWPX를 만들지 않음, PLAN.md §5-1)."""
    from dawoni.ocr import batch

    cover = job.cover
    assert cover is not None
    warnings: list[str] = []
    name, klass, school = cover.name, cover.class_name, cover.school
    roster_id = roster_note = None
    if roster.all():
        m = roster.match(cover.name, cover.class_name)
        if m.student is not None:
            roster_id, name = m.student.id, m.student.name
            klass = m.student.class_name or klass
            school = m.student.school or school
        else:
            roster_note = m.note
            warnings.append(f"{job.source}: {cover.name or '이름 못 읽음'} — {m.note}")
    sid = batch.student_id_of(name, job.plan.number)
    needs_rename = False
    other = taken.get(sid)
    if other is not None and other.source != job.source:
        if same_class(other.class_name, klass):
            needs_rename = True
            warnings.append(
                f"{job.source}: 같은 반({klass or '반 없음'})에 '{sid}' 시험지가 또 있음"
                f" ({other.source}) — 김다원A·김다원B처럼 이름을 바꾸기 전에는 HWPX를"
                " 만들지 않습니다"
                f" (`exam student … {sid}#{job.plan.number} --name …`)"
            )
            if not other.needs_rename:
                taken[other.student_id] = other.model_copy(update={"needs_rename": True})
            sid = f"{sid}#{job.plan.number}"
        else:  # 다른 반의 동명이인: 파일 이름은 반이 달라 겹치지 않는다. 학생 ID만 가른다
            sid = f"{sid}@{klass or job.plan.number}"
    record = StudentRecord(
        student_id=sid,
        name=name,
        class_name=klass,
        school=school,
        source=job.source,
        teacher_scores={c.question: c.points for c in cover.teacher_scores},
        uncertain=list(cover.uncertain),
        rotation=int(cover.rotation),
        pages=per_student,
        cover_name=cover.name,
        roster_id=roster_id,
        roster_note=roster_note,
        needs_rename=needs_rename,
    )
    if cover.name is None:
        warnings.append(f"{job.source}: 이름을 읽지 못해 '{sid}'로 저장 — 고쳐 주세요")
    taken[sid] = record
    return record, warnings


def scan_batch(
    svc: Services,
    store: ExamStore,
    pdf: Path,
    *,
    per_student: int,
    classes: list[str] | None = None,
    only_sets: list[int] | None = None,
    only_questions: list[str] | None = None,
    covers_only: bool = False,
    cross: bool | None = None,
    overwrite: bool = False,
    progress: Progress = _noop,
) -> BatchResult:
    """학생 여러 명의 답안지 PDF (학생마다 `per_student`쪽, 첫 쪽은 표지) → 학생별 판독문(미확인)
    + 판독본 HWPX.

    1. 표지에서 이름·반·학교·강사 점수와 돌려 세울 각도를 읽는다 (한 번만 — 다음부터는 저장한 것을
       쓴다). 반 목록을 주지 않으면 학생 명부의 반 중에서 고르게 한다.
    2. 명부와 맞춘다 (PLAN.md §7). 명부에 없거나 후보가 여럿이면 알린다. 같은 반에 같은 이름이 또
       있으면 강사가 이름을 바꿀 때까지 HWPX를 만들지 않는다.
    3. 답안 쪽을 판독한다. `only_questions`: 그 문항의 쪽(`ExamQuestion.answer_pages`)만 그
       문항만 두고 판독 — 강사마다 한 문항씩 나눠 맡아 토큰을 나눠 쓸 때.
    학생 한 명이 실패해도 나머지는 계속. 표지·판독은 `parallel_students`명씩 동시에.
    """
    from dawoni.ocr import batch
    from dawoni.ocr.transcribe import transcribe_student

    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    qs = select_questions(exam, only_questions)
    if only_questions:
        missing = [q.number for q in qs if not q.answer_pages]
        if missing:
            raise PipelineError(
                f"문항 {', '.join(missing)}의 답안 쪽을 모릅니다 — `exam answer-pages`로 정하세요 "
                "(예: 1=2 2=3 3=4, 표지가 1쪽)"
            )
        too_far = [q.number for q in qs if max(q.answer_pages) > per_student]
        if too_far:
            raise PipelineError(f"문항 {', '.join(too_far)}의 답안 쪽이 한 명의 쪽 수를 넘습니다")
    part_exam = exam.model_copy(update={"questions": qs})
    roster = Roster(svc.workspace)
    classes = classes or roster.classes() or None
    sets = batch.plan_sets(batch.page_count(pdf), per_student, only_sets)
    use_cross = svc.config.ocr.cross_check if cross is None else cross
    workers = svc.config.parallel_students
    res = BatchResult()
    records = store.student_records()
    by_source = {r.source: r for r in records}
    jobs: list[_SetJob] = []
    for plan in sets:
        source = f"{pdf.name} {plan.pages[0]}-{plan.pages[-1]}쪽"
        record = by_source.get(source)
        if record is not None and not overwrite:
            todo = [
                q
                for q in qs
                if q.number not in record.transcribed
                and store.transcript(record.student_id, q.number) is None
            ]
            if covers_only or not todo:
                res.skipped.append(record.student_id)
                continue
        # 필요한 쪽만: 표지(새 학생일 때) + 맡은 문항의 쪽 (문항을 정하지 않으면 전부)
        wanted = (
            sorted({p for q in qs for p in q.answer_pages})
            if only_questions
            else list(range(2, per_student + 1))
        )
        work = store.root / "scans" / "_batch" / f"set_{plan.number:03d}"
        pages = [] if covers_only else [p for p in wanted if p != 1]
        jobs.append(_SetJob(plan, source, record, record is None or overwrite, pages, work))
    lock = threading.Lock()

    def guarded(job: _SetJob, fn: Callable[[], None]) -> None:
        try:
            fn()
        except (AgentError, TaskFailed, PageError, OSError) as e:
            if isinstance(e, AgentError) and e.code == AgentErrorCode.CANCELLED:
                raise
            with lock:
                job.failed = True
                res.failed.append(Skipped(job.source, "-", str(e)))

    # 1) 표지
    def read_cover(job: _SetJob) -> None:
        def run() -> None:
            job.cover_image = batch.render_set(pdf, [job.plan.pages[0]], job.work)[0]
            job.cover = batch.read_cover(svc.engine, exam, job.cover_image, classes)

        guarded(job, run)

    covers = [j for j in jobs if j.need_cover]
    each_in_parallel(
        covers, read_cover, name=lambda j: f"{j.plan.number}번째", what="표지 읽기",
        workers=workers, progress=progress,
    )  # fmt: skip

    # 1-1) 각도 — AI 답을 다른 표지와 맞춰 본다 (코드만). 이미 세워 저장한 표지도 함께 비교
    read = [j for j in covers if not j.failed and j.cover is not None and j.cover_image]
    again = {j.record.student_id for j in read if j.record is not None}
    saved = [
        p
        for r in records
        if r.student_id not in again
        and (p := store.scan_dir(r.student_id) / "page_01.png").is_file()
    ][:12]
    fixes = batch.settle_rotations(
        [j.cover_image for j in read if j.cover_image],
        [int(j.cover.rotation) for j in read if j.cover],
        saved,
    )
    for f in fixes:
        job = read[f.index]
        assert job.cover is not None
        job.cover = job.cover.model_copy(update={"rotation": str(f.chosen)})
        res.warnings.append(
            f"{job.source}: 표지 각도를 {f.proposed}° → {f.chosen}°로 고침 (다른 표지와 맞춰 봄)"
        )

    # 2) 학생 ID — 차례대로 (동명이인을 가르려면 앞 학생을 알아야 한다)
    taken = {r.student_id: r for r in records}
    for job in covers:
        if job.failed:
            continue
        if job.record is not None:  # 다시 읽기(overwrite): 자기 자신과 겹치지 않게
            taken.pop(job.record.student_id, None)
        job.record, warns = _cover_record(job, roster, taken, per_student)
        res.warnings += warns
    for job in covers:  # 뒤 학생 때문에 동명이인이 된 앞 학생 (이번에 읽은 학생)
        if job.record is not None:
            job.record = taken.get(job.record.student_id, job.record)
    for r in taken.values():  # 앞서 저장해 둔 학생이 동명이인이 된 경우
        if r.needs_rename and (old := store.student_record(r.student_id)) and not old.needs_rename:
            store.save_student(r)

    # 3) 답안 판독
    def read_answers(job: _SetJob) -> None:
        record = job.record
        assert record is not None

        def run() -> None:
            sid = record.student_id
            images: dict[int, Path] = {}
            if job.cover_image is not None:
                images[1] = job.cover_image
            if job.wanted:
                rendered = batch.render_set(pdf, [job.plan.pages[i - 1] for i in job.wanted],
                                            job.work)  # fmt: skip
                images.update(zip(job.wanted, rendered, strict=True))
            batch.rotate(list(images.values()), record.rotation)
            scan_dir = store.scan_dir(sid)
            scan_dir.mkdir(parents=True, exist_ok=True)
            pages: dict[int, Path] = {}
            for i, img in images.items():
                dst = scan_dir / f"page_{i:02d}.png"  # 묶음 안의 쪽 번호 — 강사마다 같게
                img.replace(dst)
                pages[i] = dst
            if job.need_cover:
                store.save_student(record)
            answer = [pages[i] for i in sorted(pages) if i != 1]
            if not answer:
                return
            what = f"문항 {', '.join(q.number for q in qs)}" if only_questions else "답안"
            progress(0, 1, f"{sid} {what} {len(answer)}쪽 판독")
            outcome = transcribe_student(
                svc.engine,
                part_exam,
                scope.level,
                sid,
                answer,
                page_refs=[p.relative_to(store.root).as_posix() for p in answer],
                cross=SecondOpinion() if use_cross else None,
            )
            for t in outcome.transcripts.values():
                prev = store.transcript(sid, t.question_number)
                if prev is not None and prev.confirmed and not overwrite:
                    continue
                store.save_transcript(t.model_copy(update={"notes": t.notes + outcome.notes}))
            for q in qs:  # 빈 답안: 원본 쪽만 단 빈 판독문 — 코드 0점, 강사가 판독본에서 채운다
                if q.number in outcome.transcripts or store.transcript(sid, q.number) is not None:
                    continue
                own = [pages[i] for i in q.answer_pages if i in pages] or answer
                store.save_transcript(
                    Transcript(
                        student_id=sid,
                        question_number=q.number,
                        lines=[],
                        source="ocr",
                        pages=[x.relative_to(store.root).as_posix() for x in own],
                        notes=[BLANK_NOTE],
                    )
                )
            # 빈 답안(판독문 없음)도 판독한 것으로 적어 둔다 — 다시 하면 건너뛰게
            done = sorted({*record.transcribed, *(q.number for q in qs)})
            job.record = record.model_copy(update={"transcribed": done})
            store.save_student(job.record)

        guarded(job, run)

    ready = [j for j in jobs if not j.failed and j.record is not None]
    each_in_parallel(
        ready, read_answers, name=lambda j: j.record.student_id if j.record else "?",
        what="답안 판독", workers=workers, progress=progress,
    )  # fmt: skip
    res.students = [j.record for j in ready if not j.failed and j.record is not None]
    keys = [(r.student_id, q.number) for r in res.students for q in qs]
    res.export = export_readings(svc, store, [k for k in keys if store.transcript(*k) is not None])
    progress(len(sets), len(sets), "판독 완료 — 판독본 HWPX를 원본과 비교해 확인하세요")
    return res


def set_answer_pages(store: ExamStore, pages: dict[str, list[int]]) -> Exam:
    """문항마다 답안지 묶음의 어느 쪽에 답이 있는지 (표지가 1쪽)."""
    exam = store.exam()
    unknown = sorted(set(pages) - {q.number for q in exam.questions})
    if unknown:
        raise PipelineError(f"시험에 없는 문항: {unknown}")
    bad = sorted(n for n, ps in pages.items() if any(p < 2 for p in ps))
    if bad:
        raise PipelineError(f"문항 {bad}: 답안 쪽은 2쪽부터 (1쪽은 표지)")
    qs = [
        q.model_copy(update={"answer_pages": sorted(set(pages[q.number]))})
        if q.number in pages
        else q
        for q in exam.questions
    ]
    exam = exam.model_copy(update={"questions": qs})
    store.save_exam(exam)
    return exam


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
    export: ExportResult = field(default_factory=ExportResult)
    """채점한 학생의 HWPX와 그 반 묶음 (채점이 끝날 때 한 번에 쓴다)."""
    wall_s: float = 0.0
    """AI 채점에 걸린 실제 시간 (동시 실행 포함)."""
    workers: int = 1
    """끝날 때의 동시 호출 수 (한도 신호로 줄었으면 줄어든 값)."""
    rate_signals: int = 0

    @property
    def calls(self) -> int:
        return sum(g.calls for g in self.graded)

    @property
    def repairs(self) -> int:
        return sum(g.repairs for g in self.graded)

    @property
    def tokens(self) -> int:
        return sum(g.tokens or 0 for g in self.graded)


def export_grades(svc: Services, store: ExamStore, grades: list[GradeResult]) -> ExportResult:
    """채점이 바뀐 학생·문항의 HWPX를 쓰고 그 반 묶음을 다시 만든다 (코드만, 토큰 0).
    쓰지 못해도(한글에서 열어 둠 등) 채점은 그대로 두고 경고만 돌려준다."""
    res = write_files(svc.workspace, store, [(g.student_id, g.question_number) for g in grades])
    for w in res.warnings:
        log.warning(w)
    return res


def second_from_config(svc: Services) -> SecondOpinion | None:
    """설정의 재채점 (기본: 확신도 낮음·수리·확인할 점이 있는 채점만, 같은 AI 새 세션)."""
    c = svc.config
    if c.second_opinion == "off":
        return None
    return SecondOpinion(agent=c.second_agent, model=c.second_model, when=c.second_opinion)


def grade_exam(
    svc: Services,
    store: ExamStore,
    *,
    students: list[str] | None = None,
    only: list[str] | None = None,
    double: SecondOpinion | None = None,
    regrade: bool = False,
    use_examples: bool = True,
    by: str | None = None,
    progress: Progress = _noop,
) -> GradeRun:
    """승인된 루브릭으로 채점. 한 학생의 실패·입력 대기가 나머지를 멈추지 않는다.

    `[판독불가]`가 남은 판독문만 강사 입력을 기다린다 (2026-11-04 사용자 결정: 판독이 끝난 학생은
    강사 확인 전에도 채점까지 간다. 강사는 결과를 보며 판독문을 고치고, 고쳐 확정하면 다시 채점).

    M4: AI 채점은 `parallel_grading`개씩 동시에(한도 신호를 보면 줄임). 한도 초과·로그인 필요는
    남은 채점을 시작하지 않고 멈춘다 — 끝난 채점은 저장돼 있어 다시 실행하면 남은 것만 한다.
    `double`이 없으면 설정의 재채점(`second_opinion`). `by`를 주면 다른 강사가 맡은 문항은
    건너뛴다 (§8 문항 맡기)."""
    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    qs = select_questions(exam, only)
    for q in qs:
        if store.rubric(q.number) is None:
            raise PipelineError(f"문항 {q.number}: 루브릭이 없습니다.")
    double = double or second_from_config(svc)
    roster = students or store.students()
    run = GradeRun()
    todo: list[tuple[ExamQuestion, Rubric, Transcript]] = []
    for q in qs:
        if owner := assign.locked_for(store, q.number, by):
            run.skipped += [
                Skipped(st, q.number, f"{owner} 선생님이 맡은 문항 — 가져오기로 넘겨받으세요")
                for st in roster
                if store.transcript(st, q.number) is not None
            ]
            continue
        rubric = store.rubric(q.number)
        assert rubric is not None
        for st in roster:
            t = store.transcript(st, q.number)
            if t is None:
                continue
            if t.unreadable:
                run.skipped.append(
                    Skipped(st, q.number, f"{UNREADABLE} {t.unreadable}곳 입력 대기")
                )
                continue
            old = store.grade(st, q.number)
            if old is not None and old.status == "teacher_confirmed":
                # 강사가 확정한 점수는 다시 채점해도 덮어쓰지 않는다 (기준 예시·보정 사례)
                run.skipped.append(Skipped(st, q.number, f"강사 확정 ({old.total:g}점)"))
                continue
            if old is not None and not regrade and old.rubric_version == rubric.version:
                run.skipped.append(
                    Skipped(st, q.number, f"이미 채점됨 ({old.total:g}/{old.max_points:g})")
                )
                continue
            todo.append((q, rubric, t))
    coded: dict[str, list[tuple[ExamQuestion, Rubric, Transcript]]] = {}
    for job in todo:
        if job[1].code_graded:
            coded.setdefault(job[0].number, []).append(job)
    todo = [job for job in todo if not job[1].code_graded]
    for jobs in coded.values():  # 객관식·단답형: 문항마다 한 번에 (AI는 애매한 단답만, 많아야 1번)
        q, rubric, _ = jobs[0]
        progress(0, len(todo) + 1, f"문항 {q.number} 코드 채점 ({len(jobs)}명)")
        by_student = {t.student_id: t for _, _, t in jobs}
        for g in grade_by_code(svc.engine, q, rubric, [t for _, _, t in jobs], scope):
            g = g.model_copy(update={"transcript_confirmed": by_student[g.student_id].confirmed})
            store.save_grade(g)
            run.graded.append(g)
    lock = threading.Lock()

    def one(job: tuple[ExamQuestion, Rubric, Transcript]) -> None:
        q, rubric, t = job
        pick = calibration.pick_for(store, q.number, rubric, t) if use_examples else None
        try:
            with situation(exam.title):
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
            if isinstance(e, AgentError) and stops_all(e):
                raise
            with lock:
                run.failed.append(Skipped(t.student_id, q.number, str(e)))
            return
        g = g.model_copy(update={"transcript_confirmed": t.confirmed})
        store.save_grade(g)
        with lock:
            run.graded.append(g)

    started = time.monotonic()
    throttle = Throttle(svc.config.parallel_grading)
    try:
        each_in_parallel(
            todo, one, name=lambda j: f"{j[2].student_id}·{j[0].number}", what="채점",
            workers=svc.config.parallel_grading, progress=progress, throttle=throttle,
        )  # fmt: skip
    finally:
        run.wall_s = time.monotonic() - started
        run.rate_signals = throttle.signals
        run.workers = throttle.limit
        if run.graded:  # 한도로 멈춰도 끝난 채점의 파일은 쓴다
            run.export = export_grades(svc, store, run.graded)
    progress(len(todo), len(todo), f"채점 {len(run.graded)}건 완료")
    return run


def grade_ready(
    svc: Services, store: ExamStore, students: list[str], *, progress: Progress = _noop
) -> GradeRun | None:
    """판독이 끝난 학생부터 바로 채점한다 (판독 작업 끝에 이어서). 루브릭이 승인된 문항만 —
    하나도 없으면 None (승인 뒤 `grade_exam`). `[판독불가]`가 남은 학생·문항만 건너뛴다."""
    exam = store.exam()
    ready = [
        q.number for q in exam.questions if (r := store.rubric(q.number)) and r.status == "approved"
    ]
    if not ready or not students:
        return None
    return grade_exam(svc, store, students=students, only=ready, progress=progress)


def needs_input(store: ExamStore) -> list[Transcript]:
    """`[판독불가]`가 남아 강사 입력을 기다리는 판독문 (알림 숫자)."""
    exam = store.exam()
    return [
        t
        for s in store.students()
        for q in exam.questions
        if (t := store.transcript(s, q.number)) is not None and t.unreadable
    ]


# ---------------------------------------------------------------- 강사 확인 (HWPX 다시 읽기)


@dataclass
class ConfirmedFile:
    transcript: Transcript
    path: Path
    changed: int
    """강사가 고친 줄 수."""
    before: Transcript | None = None
    """고치기 전 앱 판독문 (배우기에 고친 줄만 넘긴다)."""


@dataclass
class ConfirmRun:
    confirmed: list[ConfirmedFile] = field(default_factory=lambda: list[ConfirmedFile]())
    waiting: list[ConfirmedFile] = field(default_factory=lambda: list[ConfirmedFile]())
    """고친 것은 받았지만 `[판독불가]`가 남아 확정하지 못한 판독문."""
    unchanged: list[str] = field(default_factory=lambda: list[str]())
    """이미 확정했고 그 뒤 파일을 고치지 않은 것 ('학생 문항')."""
    problems: list[Skipped] = field(default_factory=lambda: list[Skipped]())
    graded: list[GradeResult] = field(default_factory=lambda: list[GradeResult]())
    failed: list[Skipped] = field(default_factory=lambda: list[Skipped]())
    export: ExportResult = field(default_factory=ExportResult)


def confirm_from_files(
    svc: Services,
    store: ExamStore,
    *,
    students: list[str] | None = None,
    only: list[str] | None = None,
    by: str | None = None,
    progress: Progress = _noop,
) -> ConfirmRun:
    """ "확인 완료" (PLAN.md §5-1 6단계): 강사가 한글에서 원본과 대조해 고친 판독본 HWPX를 다시 읽어
    **확정 판독문**으로 저장한다. 고치지 않은 줄은 앱에 둔 원래 줄(원래 LaTeX)을 그대로 쓴다.

    - 확정했고 그 뒤 파일이 그대로면 다시 읽지 않는다 (파일 해시, §2-4 6번).
    - 소문항 표식(`■ 1-(1)`)이 없어진 파일, 다른 학생의 머리가 달린 파일은 확정하지 않고 알린다.
    - `[판독불가]`가 남으면 고친 줄만 받아 두고 확정하지 않는다 (강사가 채운다).
    - 확정하면 그 학생·문항을 맞춘다: 고쳤으면 다시 채점, 그대로면 채점의 '판독문 확인' 표시만
      (`grade_after_confirm`). 끝나면 파일을 다시 써 "확인 전" 표시를 지운다.
    """
    from dawoni.export.exam_files import accept_file, is_edited, student_file
    from dawoni.export.hwpx import read_back, reconcile

    exam = store.exam()
    qs = select_questions(exam, only)
    roster = students or store.students()
    run = ConfirmRun()
    hashes = store.export_hashes()
    todo = [(s, q) for s in roster for q in qs if store.transcript(s, q.number) is not None]
    rewrite: list[tuple[str, str]] = []
    for k, (st, q) in enumerate(todo):
        t = store.transcript(st, q.number)
        assert t is not None
        record = store.student_record(st)
        if record is not None and record.needs_rename:
            run.problems.append(Skipped(st, q.number, "같은 반 동명이인 — 이름을 먼저 바꾸세요"))
            continue
        path = student_file(svc.workspace, store, st, q.number)
        if not path.is_file():
            run.problems.append(
                Skipped(st, q.number, f"{path.name}이 없습니다 — `exam export`로 다시 쓰세요")
            )
            continue
        if t.confirmed and not is_edited(store, path, hashes):
            run.unchanged.append(f"{st} {q.number}")
            continue
        progress(k, len(todo), f"{st} 문항 {q.number} 판독본 읽기")
        try:
            back = read_back(path)
        except Exception as e:  # 한글이 아닌 프로그램으로 저장해 깨진 파일 등
            run.problems.append(Skipped(st, q.number, f"{path.name}을 읽을 수 없습니다 ({e})"))
            continue
        name = (record.name if record and record.name else None) or st
        if name not in back.head.split(" · "):
            run.problems.append(
                Skipped(
                    st,
                    q.number,
                    f"{path.name}의 머리 '{back.head}'에 {name}이 없습니다 "
                    "— 다른 학생의 파일을 붙여 넣었는지 확인하세요",
                )
            )
            continue
        rec = reconcile(q.number, back.parts, t.lines)
        if rec.problems:
            run.problems += [Skipped(st, q.number, p) for p in rec.problems]
            continue
        before = t
        t = t.model_copy(update={"lines": rec.lines})
        if rec.changed:
            note = f"한글에서 {rec.changed}줄 고침 ({by or '강사'})"
            t = t.model_copy(update={"notes": [*t.notes, note]})
        item = ConfirmedFile(t, path, rec.changed, before)
        if t.unreadable:
            store.save_transcript(t.model_copy(update={"confirmed": False, "confirmed_by": None}))
            accept_file(store, path)
            run.waiting.append(item)
            continue
        t = t.model_copy(update={"confirmed": True, "confirmed_by": by})
        item.transcript = t
        store.save_transcript(t)
        accept_file(store, path)  # 강사가 고친 내용을 받았으니 다음부터 다시 써도 된다
        run.confirmed.append(item)
        try:
            g = grade_after_confirm(svc, store, before, t, progress=progress, export=False)
        except (AgentError, TaskFailed, PipelineError) as e:
            if isinstance(e, AgentError) and e.code == AgentErrorCode.CANCELLED:
                raise
            run.failed.append(Skipped(st, q.number, str(e)))
            g = None
        if g is not None:
            run.graded += g.graded
            run.failed += g.failed
            run.export.written += g.export.written
            run.export.warnings += g.export.warnings
        rewrite.append((st, q.number))
    written = {p.name for p in run.export.written}
    left = [k for k in rewrite if student_file(svc.workspace, store, *k).name not in written]
    if left:
        more = write_files(svc.workspace, store, left)
        run.export.written += more.written
        run.export.warnings += more.warnings
    progress(len(todo), len(todo), f"확정 {len(run.confirmed)}건")
    return run


@dataclass
class RenameResult:
    record: StudentRecord
    warnings: list[str] = field(default_factory=lambda: list[str]())
    export: ExportResult = field(default_factory=ExportResult)


def rename_student(
    svc: Services,
    store: ExamStore,
    student: str,
    *,
    name: str | None = None,
    class_name: str | None = None,
    roster_id: str | None = None,
) -> RenameResult:
    """이 시험의 학생 이름·반을 고치거나 명부의 학생과 잇는다 (표지를 잘못 읽었을 때, 같은 반
    동명이인을 `김다원A`·`김다원B`로 가를 때). 판독문·채점·스캔을 새 학생 ID로 옮기고, 옛 HWPX는
    지우고(강사가 고친 파일은 남김) 새 이름으로 다시 쓴다."""
    from dawoni.export.exam_files import remove_student_files
    from dawoni.ocr import batch

    old = store.student_record(student)
    if old is None and student not in store.students():
        raise PipelineError(f"이 시험에 '{student}' 학생이 없습니다")
    record = old or StudentRecord(student_id=student, source="-")
    linked = None
    if roster_id is not None:
        linked = Roster(svc.workspace).get(roster_id)
        if linked is None:
            raise PipelineError(f"명부에 {roster_id}가 없습니다 (`students list`)")
    new_name = linked.name if linked else (name or record.name or student)
    klass = (linked.class_name if linked else None) or class_name or record.class_name
    new_sid = batch.student_id_of(new_name, 0)
    others = [r for r in store.student_records() if r.student_id != student]
    clash = [r for r in others if same_name(r.name, new_name) and same_class(r.class_name, klass)]
    if clash:
        raise PipelineError(
            f"{klass or '반 없음'}에 {new_name}이(가) 이미 있습니다 ({clash[0].source}) — "
            f"{new_name}A, {new_name}B처럼 알파벳을 붙이세요"
        )
    if any(r.student_id == new_sid for r in others):  # 다른 반의 동명이인
        new_sid = f"{new_sid}@{klass or 'x'}"
    res = RenameResult(record)
    res.warnings += remove_student_files(svc.workspace, store, student)
    store.rename_student(student, new_sid)
    record = record.model_copy(
        update={
            "student_id": new_sid,
            "name": new_name,
            "class_name": klass,
            "school": (linked.school if linked else None) or record.school,
            "roster_id": linked.id if linked else record.roster_id,
            "roster_note": None if linked else record.roster_note,
            "needs_rename": False,
        }
    )
    store.save_student(record)
    res.record = record
    keys = [(new_sid, q.number) for q in store.exam().questions]
    for r in others:  # 짝이던 학생: 같은 반 같은 이름이 더 없으면 HWPX를 만들 수 있다
        twins = [
            o
            for o in [*others, record]
            if o.student_id != r.student_id
            and o.name == r.name
            and same_class(o.class_name, r.class_name)
        ]
        if r.needs_rename and not twins:
            store.save_student(r.model_copy(update={"needs_rename": False}))
            keys += [(r.student_id, q.number) for q in store.exam().questions]
    res.export = write_files(svc.workspace, store, keys)
    return res


# ---------------------------------------------------------------- 보정


@dataclass
class TeacherScore:
    """강사가 소문항 단위로 준 점수 한 줄 (CSV: 학생, 문항, 소문항, 점수, 이유)."""

    student: str
    question: str
    part: str
    points: float
    note: str = ""


@dataclass
class TeacherImport:
    saved: list[GradeResult] = field(default_factory=lambda: list[GradeResult]())
    graded_first: list[str] = field(default_factory=lambda: list[str]())
    """강사 점수를 넣기 전에 AI 채점부터 한 답안 ('학생 문항')."""
    failed: list[Skipped] = field(default_factory=lambda: list[Skipped]())


def wants_grading(store: ExamStore, before: Transcript | None, t: Transcript) -> bool:
    """강사가 판독문을 확정했을 때 AI 채점이 필요한가: `[판독불가]`를 다 채웠고, 루브릭이 승인됐고,
    채점이 아직 없거나 판독문을 고쳤을 때. 저장만(확정 안 함) 할 때는 채점하지 않는다 — 고칠 때마다
    토큰을 쓰지 않게."""
    rubric = store.rubric(t.question_number)
    if not t.confirmed or t.unreadable or rubric is None or rubric.status != "approved":
        return False
    return store.grade(t.student_id, t.question_number) is None or (
        before is None or before.lines != t.lines
    )


def grade_after_confirm(
    svc: Services,
    store: ExamStore,
    before: Transcript | None,
    t: Transcript,
    *,
    progress: Progress = _noop,
    export: bool = True,
) -> GradeRun | None:
    """강사가 판독문을 확정하면 그 학생·문항을 맞춘다. 고친 것 없이 확정만 했으면 AI를 부르지 않고
    채점의 '판독문 강사 확인' 표시만 켠다. 고쳤으면 다시 채점한다 (강사가 확정한 점수는
    `grade_exam`이 지킨다). 채점하지 않을 때도 판독본 HWPX를 다시 써 고친 판독문·"확인 전" 표시를
    맞춘다 (`export=False`면 부른 쪽이 쓴다)."""
    old = store.grade(t.student_id, t.question_number)
    if not wants_grading(store, before, t):
        if t.confirmed and old is not None and not old.transcript_confirmed:
            store.save_grade(old.model_copy(update={"transcript_confirmed": True}))
        if export:
            export_readings(svc, store, [(t.student_id, t.question_number)])
        return None
    return grade_exam(
        svc,
        store,
        students=[t.student_id],
        only=[t.question_number],
        regrade=old is not None,
        progress=progress,
    )


def parse_teacher_csv(text: str) -> list[TeacherScore]:
    """`학생,문항,소문항,점수[,이유]` — 머리 줄은 있어도 없어도 된다. 소문항이 없는 문항은 빈 칸."""
    import csv
    import io

    out: list[TeacherScore] = []
    for n, row in enumerate(csv.reader(io.StringIO(text)), start=1):
        cells = [c.strip() for c in row]
        if not any(cells) or cells[0].startswith("#"):
            continue
        if len(cells) < 4:
            raise PipelineError(f"{n}번째 줄: 학생,문항,소문항,점수[,이유] 형식이어야 합니다")
        try:
            pts = float(cells[3])
        except ValueError:
            if n == 1:
                continue  # 머리 줄
            raise PipelineError(f"{n}번째 줄: 점수 '{cells[3]}'가 숫자가 아닙니다") from None
        part = "" if cells[2] in ("", "-", "전체") else cells[2]
        out.append(TeacherScore(cells[0], cells[1], part, pts, cells[4] if len(cells) > 4 else ""))
    return out


def import_teacher_scores(
    svc: Services,
    store: ExamStore,
    rows: list[TeacherScore],
    *,
    by: str | None = None,
    grade_missing: bool = True,
    progress: Progress = _noop,
) -> TeacherImport:
    """강사가 직접 채점한 소문항 점수 → 강사 확정 채점 (AI와 다른 곳은 보정 사례가 된다).

    AI 채점이 아직 없으면 먼저 AI로 채점한다 (기준 예시 없이) — 어디서 어긋나는지 알아야
    루브릭을 고칠 수 있다.
    """
    from dawoni.grading.pipeline import teacher_part_review

    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    groups: dict[tuple[str, str], list[TeacherScore]] = {}
    for r in rows:
        groups.setdefault((r.student, r.question), []).append(r)
    res = TeacherImport()
    for k, ((st, qn), rs) in enumerate(groups.items()):
        progress(k, len(groups), f"{st} 문항 {qn} 강사 점수")
        try:
            q = exam.question(qn)
            rubric = store.rubric(qn)
            if rubric is None or rubric.status != "approved":
                raise PipelineError(f"문항 {qn}: 승인된 루브릭이 없습니다")
            g = store.grade(st, qn)
            if g is None or g.rubric_version != rubric.version:
                t = store.transcript(st, qn)
                if t is None and all(r.points == 0 for r in rs):
                    continue  # 빈 답안의 0점 — 남길 것이 없다
                if t is None or not t.confirmed:
                    raise PipelineError("확인된 판독문이 없습니다")
                if not grade_missing:
                    raise PipelineError("AI 채점이 없습니다 (먼저 채점)")
                progress(k, len(groups), f"{st} 문항 {qn} AI 채점부터")
                g = grade_answer(svc.engine, q, rubric, t, scope)
                res.graded_first.append(f"{st} {qn}")
            g = teacher_part_review(
                g,
                rubric,
                q,
                {r.part: r.points for r in rs},
                part_notes={r.part: r.note for r in rs if r.note},
                by=by,
            )
            store.save_grade(g)
            res.saved.append(g)
        except (PipelineError, KeyError, AgentError, TaskFailed) as e:
            res.failed.append(Skipped(st, qn, str(e).strip("'\"")))
    progress(len(groups), len(groups), f"강사 점수 {len(res.saved)}건 저장")
    export_grades(svc, store, res.saved)
    return res


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
    key = store.answer_key(q_number)
    if rubric is None or (sol is None and not (rubric.based_on_key and key)):
        raise PipelineError(f"문항 {q_number}: 해설(또는 해설지)·루브릭이 필요합니다.")
    cases = [
        c
        for c in calibration.candidates(store, q_number, rubric)
        if c.kind == "correction" or c.grade.teacher_part_scores
    ]
    if not any(c.kind == "correction" for c in cases):
        raise PipelineError(
            "강사가 AI 점수를 고친 사례가 없습니다. 채점 검토에서 점수를 고쳐 확정하거나 "
            "`exam teacher-scores`로 강사 점수를 넣으면 생깁니다."
        )
    s = suggest_rubric(
        svc.engine,
        q,
        rubric,
        sol,
        scope,
        calibration.corrections_text(cases, rubric),
        {c.student for c in cases},
        key=key,
    )
    return SuggestionRun(s, [c.student for c in cases])
