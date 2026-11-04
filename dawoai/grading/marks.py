"""손채점 답안지 → 강사 소문항 점수 → 강사 확정 채점·소문항 일치도 (M5 보정, PLAN.md §6).

1. `read_teacher_marks`: 판독해 둔 학생의 답안 쪽(세운 그림)에서 빨간 펜 소문항 점수를 읽는다
   (`ocr.marks`, 학생당 호출 1번, fast). 학생 기록(`StudentRecord.teacher_marks`)에 둔다.
2. 강사가 확인한다: 못 읽은 칸·표지 총점과 다른 문항은 `marks.problems`로 보여 주고, 강사가
   `set_marks`로 고친 뒤 `confirm_marks`로 확정한다. 확정한 점수만 강사 확정 채점이 된다
   (`workflow.import_teacher_scores` — AI와 다른 소문항은 기준 예시·루브릭 개선 사례).
3. `part_agreement`: 강사 점수(확정 전이어도)와 AI 채점(강사가 고치기 전 점수)을 소문항마다 맞춰
   본다 — 다워니 95%(182/192)와 같은 잣대. 보정 전후를 같은 학생으로 비교하려면 `students`를 준다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from dawoai.agents import AgentError, AgentErrorCode
from dawoai.context import TaskFailed
from dawoai.grading.pipeline import PipelineError
from dawoai.grading.store import ExamStore
from dawoai.models.base import utcnow
from dawoai.models.calibration import PartAgreement, PartCase
from dawoai.models.grading import Exam, GradeResult, Rubric, StudentRecord
from dawoai.ocr import marks as ocr_marks
from dawoai.ocr.pages import PageError
from dawoai.parallel import each_in_parallel
from dawoai.services import Services

if TYPE_CHECKING:
    from dawoai.grading.workflow import TeacherImport

Progress = Callable[[int, int, str], None]


def _noop(done: int, total: int, message: str) -> None:
    pass


@dataclass
class Skip:
    student: str
    reason: str


# ---------------------------------------------------------------- 읽기


def answer_pages(store: ExamStore, student: str) -> list[Path]:
    """학생의 답안 쪽 그림 (판독문의 원본 쪽, 묶음 순서). 없으면 스캔 폴더의 표지 뒤 쪽."""
    refs: list[str] = []
    for q in store.exam().questions:
        t = store.transcript(student, q.number)
        refs += [p for p in (t.pages if t else []) if p not in refs]
    pages = [store.root / r for r in refs if (store.root / r).exists()]
    if pages:
        return sorted(pages, key=lambda p: p.name)
    d = store.scan_dir(student)
    return [p for p in sorted(d.glob("page_*.png")) if p.name != "page_01.png"]


@dataclass
class MarksRun:
    read: list[StudentRecord] = field(default_factory=lambda: list[StudentRecord]())
    skipped: list[Skip] = field(default_factory=lambda: list[Skip]())
    failed: list[Skip] = field(default_factory=lambda: list[Skip]())


def read_teacher_marks(
    svc: Services,
    store: ExamStore,
    *,
    students: list[str] | None = None,
    overwrite: bool = False,
    progress: Progress = _noop,
) -> MarksRun:
    """판독해 둔 학생들의 답안 쪽에서 강사 소문항 점수를 읽는다 (`parallel_students`명씩 동시에).
    이미 읽은 학생은 건너뛴다 (`overwrite`면 다시 — 강사가 확인한 점수는 덮어쓰지 않는다)."""
    exam = store.exam()
    run = MarksRun()
    todo: list[tuple[str, StudentRecord, list[Path]]] = []
    for sid in students or store.students():
        record = store.student_record(sid) or StudentRecord(student_id=sid, source="-")
        if record.marks_confirmed:
            run.skipped.append(Skip(sid, "강사가 확인한 점수가 있음"))
            continue
        if record.teacher_marks and not overwrite:
            run.skipped.append(Skip(sid, "이미 읽음"))
            continue
        pages = answer_pages(store, sid)
        if not pages:
            run.skipped.append(Skip(sid, "답안 쪽 그림이 없음 (판독부터)"))
            continue
        todo.append((sid, record, pages))

    def one(job: tuple[str, StudentRecord, list[Path]]) -> StudentRecord | None:
        sid, record, pages = job
        try:
            out = ocr_marks.read_marks(svc.engine, exam, pages)
        except (AgentError, TaskFailed, PageError, OSError) as e:
            if isinstance(e, AgentError) and e.code == AgentErrorCode.CANCELLED:
                raise
            run.failed.append(Skip(sid, str(e)))
            return None
        filled = ocr_marks.fill_from_cover(out.marks, record.teacher_scores, exam)
        new = record.model_copy(
            update={
                "teacher_marks": out.marks,
                "marks_uncertain": out.uncertain + out.notes + filled,
                "marks_confirmed": False,
                "marks_confirmed_by": None,
            }
        )
        store.save_student(new)
        return new

    done = each_in_parallel(
        todo, one, name=lambda j: j[0], what="강사 점수 읽기",
        workers=svc.config.parallel_students, progress=progress,
    )  # fmt: skip
    run.read = [r for r in done if r is not None]
    return run


# ---------------------------------------------------------------- 강사 확인


def set_marks(
    store: ExamStore, student: str, q: str, parts: dict[str, float | None]
) -> StudentRecord:
    """강사가 읽은 점수를 고친다 (확인 표시는 풀림 — 다시 확정해야 채점에 들어감)."""
    exam = store.exam()
    record = store.student_record(student)
    if record is None:
        if student not in store.students():
            raise PipelineError(f"이 시험에 '{student}' 학생이 없습니다")
        record = StudentRecord(student_id=student, source="-")
    question = exam.question(q)
    caps = {p.label: p.points for p in question.parts} or {"": question.points}
    for label, pts in parts.items():
        if label not in caps:
            raise PipelineError(f"문항 {q}에 없는 소문항: {label!r} (있는 것: {sorted(caps)})")
        if pts is not None and not 0 <= pts <= caps[label] + 1e-9:
            raise PipelineError(f"{q} {label} 점수 {pts:g}는 0~{caps[label]:g} 사이여야 합니다")
    current = dict(record.teacher_marks.get(q) or dict.fromkeys(caps))
    current.update(parts)
    record = record.model_copy(
        update={
            "teacher_marks": {**record.teacher_marks, q: current},
            "marks_confirmed": False,
            "marks_confirmed_by": None,
        }
    )
    store.save_student(record)
    return record


@dataclass
class ConfirmMarksRun:
    confirmed: list[str] = field(default_factory=lambda: list[str]())
    blocked: list[Skip] = field(default_factory=lambda: list[Skip]())
    """확정하지 못한 학생 (못 읽은 칸, 표지와 다름, 판독문 확인 전)."""
    left_out: list[str] = field(default_factory=lambda: list[str]())
    """승인된 루브릭이 없어 강사 채점으로 넣지 않은 문항."""
    imported: TeacherImport | None = None
    """`workflow.TeacherImport` (강사 확정 채점)."""


def confirm_marks(
    svc: Services,
    store: ExamStore,
    students: list[str],
    *,
    by: str | None = None,
    force: bool = False,
    progress: Progress = _noop,
) -> ConfirmMarksRun:
    """강사가 확인한 소문항 점수 → 강사 확정 채점 (루브릭이 승인된 문항만).

    못 읽은 칸이 있으면 확정하지 않는다. 표지 총점과 다르면 `force`일 때만 (강사가 원본을 보고
    소문항 점수가 맞다고 했을 때). 판독문은 강사가 확인한 것이어야 한다 — 강사 채점은 확인한
    판독문만 쓴다 (PLAN.md §1-8).
    """
    from dawoai.grading import workflow

    exam = store.exam()
    run = ConfirmMarksRun()
    rows: list[workflow.TeacherScore] = []
    graded = [q.number for q in exam.questions if _approved(store.rubric(q.number))]
    run.left_out = [q.number for q in exam.questions if q.number not in graded]
    for sid in students:
        record = store.student_record(sid)
        if record is None or not record.teacher_marks:
            run.blocked.append(Skip(sid, "읽은 강사 점수가 없음 (`exam read-marks`)"))
            continue
        issues = ocr_marks.problems(record, exam, graded)
        hard = [p for p in issues if "≠ 표지" not in p]
        if hard or (issues and not force):
            run.blocked.append(Skip(sid, "; ".join(issues)))
            continue
        unconfirmed = [
            q
            for q in graded
            if (t := store.transcript(sid, q)) is not None and t.lines and not t.confirmed
        ]
        if unconfirmed:
            run.blocked.append(
                Skip(sid, f"문항 {', '.join(unconfirmed)} 판독문 확인 전 — 판독본을 확인 완료한 뒤")
            )
            continue
        for q in graded:
            for part, pts in (record.teacher_marks.get(q) or {}).items():
                assert pts is not None
                rows.append(workflow.TeacherScore(sid, q, part, pts))
        store.save_student(record.model_copy(update={"marks_confirmed": True,
                                                     "marks_confirmed_by": by}))  # fmt: skip
        run.confirmed.append(sid)
    if rows:
        run.imported = workflow.import_teacher_scores(svc, store, rows, by=by, progress=progress)
    return run


def _approved(r: Rubric | None) -> bool:
    return r is not None and r.status == "approved"


# ---------------------------------------------------------------- 소문항 일치도


def ai_part_scores(g: GradeResult, rubric: Rubric, exam: Exam) -> dict[str, float] | None:
    """AI 채점 → 소문항 점수 (강사가 고친 채점이면 고치기 전 AI 점수). 강사가 직접 채점한 것,
    지금 루브릭과 항목 구조가 다른 채점은 None."""
    from dawoai.grading.calibration import compatible

    if g.agent is None and g.status == "teacher_confirmed":
        return None
    if not compatible(g, rubric):
        return None
    js = (g.ai_judgements or g.judgements) if g.status == "teacher_confirmed" else g.judgements
    awarded = {j.rubric_item_id: j.awarded for j in js}
    q = exam.question(g.question_number)
    labels = [p.label for p in q.parts] or [""]
    return {lb: sum(awarded.get(i.id, 0.0) for i in rubric.part_items(lb)) for lb in labels}


def part_agreement(store: ExamStore, q: str, *, students: list[str] | None = None) -> PartAgreement:
    exam = store.exam()
    rubric = store.rubric(q)
    out = PartAgreement(question_number=q, rubric_version=rubric.version if rubric else None)
    if rubric is None:
        return out
    wanted = set(students) if students is not None else None
    for record in store.student_records():
        sid = record.student_id
        teacher = {k: v for k, v in (record.teacher_marks.get(q) or {}).items() if v is not None}
        if not teacher or (wanted is not None and sid not in wanted):
            continue
        g = store.grade(sid, q)
        if g is None:
            continue
        ai = ai_part_scores(g, rubric, exam)
        if ai is None:
            out.stale.append(sid)
            continue
        out.n += 1
        out.cases.append(PartCase(student_id=sid, teacher=teacher, ai=ai))
        for part, tv in teacher.items():
            av = ai.get(part, 0.0)
            d = av - tv
            out.parts += 1
            out.diff_sum += d
            out.abs_diff_sum += abs(d)
            if abs(d) < 1e-9:
                out.same += 1
            else:
                out.per_part[part] = out.per_part.get(part, 0) + 1
                out.ai_lower += d < 0
                out.ai_higher += d > 0
            out.within1 += abs(d) <= 1 + 1e-9
    out.per_part = dict(sorted(out.per_part.items()))
    return out


def combine(stats: list[PartAgreement]) -> PartAgreement:
    out = PartAgreement()
    for s in stats:
        out.n = max(out.n, s.n)
        for f in ("parts", "same", "within1", "ai_lower", "ai_higher"):
            setattr(out, f, getattr(out, f) + getattr(s, f))
        out.diff_sum += s.diff_sum
        out.abs_diff_sum += s.abs_diff_sum
        out.per_part |= {f"{s.question_number}-{k}": v for k, v in s.per_part.items()}
        out.stale += [x for x in s.stale if x not in out.stale]
    return out


def agreement_line(s: PartAgreement) -> str:
    if not s.parts:
        return "비교할 소문항 없음"
    rate = s.rate or 0.0
    return (
        f"소문항 {s.same}/{s.parts} 일치 ({rate:.0%}) · ±1점 {s.within1}/{s.parts} · "
        f"AI 낮음 {s.ai_lower} · 높음 {s.ai_higher} · AI−강사 {s.diff_sum:+g}점 (학생 {s.n}명)"
    )


def finalize_rubric(store: ExamStore, q: str, *, by: str | None) -> Rubric:
    """강사 손채점과 맞춰 본 루브릭을 확정한다 (PLAN.md §6-5). 확정한 버전은 채점 결과의
    `rubric_version`과 루브릭의 `finalized_*`로 남는다. 루브릭을 고치면 새 버전은 다시 초안."""
    rubric = store.rubric(q)
    if rubric is None or rubric.status != "approved":
        raise PipelineError(f"문항 {q}: 승인된 루브릭만 확정할 수 있습니다")
    s = part_agreement(store, q)
    note = agreement_line(s) if s.parts else "강사 손채점 없이 확정"
    out = rubric.model_copy(
        update={"finalized_by": by, "finalized_at": utcnow(), "finalized_note": note}
    )
    store.save_rubric(out)
    return out
