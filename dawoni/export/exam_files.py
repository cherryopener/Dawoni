"""시험의 엑셀 점수표를 작업공간 `exports/<날짜_시험>/`에 쓴다 — 코드만, 토큰 0.

    exports/2026-11-03_중간고사 물리/
        (점수표)_중간고사 물리.xlsx
        채점 답안지/2반_채점답안지.pdf     강사가 만들 때만 (`export.red_pen`)

- 점수표 `(점수표)_<시험>.xlsx`(`export.xlsx`, PLAN.md §9-2)는 판독·채점·검토가 바뀔 때마다 전체를
  다시 만든다 (앱 데이터만 읽는다).
- **엑셀에서 고친 점수표는 덮어쓰지 않는다.** 앱이 쓴 파일의 해시를 시험 폴더 `exports.json`에
  두고, 파일이 그와 다르면 강사가 고친 것으로 보고 건너뛴다 (경고). 앱은 점수표를 읽지 않는다.
- 쓰기 실패(엑셀에서 열어 둠 등)는 채점을 멈추지 않고 경고로 돌려준다.

2026-11-18까지는 학생 × 문항 판독본·채점 HWPX와 반별 묶음도 여기서 썼다. 판독문은 이제 앱
화면에서 고치고 확인한다 (PLAN.md §16). 예전에 쓴 HWPX는 그 폴더에 그대로 남는다.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dawoni.export.sheet import PartGrade, part_grades
from dawoni.export.xlsx import Cell, Column, Row, State, build_scores
from dawoni.fsio import sha256_file
from dawoni.grading.store import ExamStore, safe_name
from dawoni.models.grading import Exam
from dawoni.paths import WorkspacePaths

NO_CLASS = "반없음"
_lock = threading.RLock()
"""`exports.json`을 두 작업이 동시에 고치지 않게."""


@dataclass
class ExportResult:
    written: list[Path] = field(default_factory=lambda: list[Path]())
    warnings: list[str] = field(default_factory=lambda: list[str]())
    edited: list[Path] = field(default_factory=lambda: list[Path]())
    """강사가 고쳐 두어 덮어쓰지 않은 파일."""


def exam_dir(ws: WorkspacePaths, exam: Exam) -> Path:
    """`exports/2026-11-03_중간고사 물리/` (PLAN.md §5-4: 시험마다 폴더 하나)."""
    return ws.exports / safe_name(f"{exam.created_at:%Y-%m-%d}_{exam.title}")


def _class_of(store: ExamStore, student: str) -> str:
    r = store.student_record(student)
    return (r.class_name if r and r.class_name else None) or NO_CLASS


def _name_of(store: ExamStore, student: str) -> str:
    """표지(또는 명부)의 이름. 다른 반 동명이인은 학생 ID만 다르고 이름은 같다. 이름 확인 전이면
    표시를 붙여 점수표에 넣는다 (2026-11-18: 이름 때문에 멈추지 않고 나중에 고친다)."""
    r = store.student_record(student)
    name = (r.name if r and r.name else None) or student
    return f"{name} (이름 확인)" if r is not None and r.name_pending else name


def marked_dir(ws: WorkspacePaths, store: ExamStore) -> Path:
    """채점 답안지 PDF(`export.red_pen`)를 두는 곳: 시험 폴더 안 `채점 답안지/`."""
    return exam_dir(ws, store.exam()) / "채점 답안지"


def score_file(ws: WorkspacePaths, store: ExamStore) -> Path:
    """`(점수표)_<시험>.xlsx`."""
    return exam_dir(ws, store.exam()) / safe_name(f"(점수표)_{store.exam().title}.xlsx")


def score_table(store: ExamStore) -> tuple[list[Column], list[Row]]:
    """엑셀 점수표에 넣을 열(소문항)과 학생 줄 (PLAN.md §9-2)."""
    exam = store.exam()
    cols: dict[str, list[Column]] = {
        q.number: [Column(f"{q.number}-{p.label}", p.points) for p in q.parts]
        or [Column(q.number, q.points)]
        for q in exam.questions
    }
    rows: list[Row] = []
    for student in _roster(store):
        r = store.student_record(student)
        row = Row(_class_of(store, student), _name_of(store, student), r.school if r else None)
        rows.append(row)
        for q in exam.questions:
            g = store.grade(student, q.number)
            t = store.transcript(student, q.number)
            state: State
            if t is None or g is None:
                state = "ungraded"
            elif g.status == "needs_review":
                state = "review"
            elif not g.transcript_confirmed and g.status != "teacher_confirmed":
                state = "unconfirmed"
            else:
                state = "ok"
            if g is None or state == "ungraded":
                row.totals[q.number] = Cell(None, state)
                row.cells |= {c.key: Cell(None, state) for c in cols[q.number]}
                continue
            row.totals[q.number] = Cell(g.total, state)
            rubric = store.rubric(q.number)
            parts = part_grades(q.number, g, rubric) if rubric else {}
            if not q.parts and not parts:
                parts = {q.number: PartGrade(g.total, g.max_points)}
            for key, pg in parts.items():
                if key not in {c.key for c in cols[q.number]}:  # 시험에 없는 소문항 표시 (드묾)
                    cols[q.number].append(Column(key, pg.points))
                row.cells[key] = Cell(pg.awarded, state)
    return [c for q in exam.questions for c in cols[q.number]], rows


def export_scores(
    ws: WorkspacePaths, store: ExamStore, *, by_class: bool | None = None, force: bool = False
) -> ExportResult:
    """점수표를 다시 쓴다. by_class를 주면 시험에 저장해 다음 저절로 쓸 때도 그렇게 쓴다.
    `force`: 엑셀에서 고친 점수표도 덮어쓴다."""
    res = ExportResult()
    with _lock:
        exam = store.exam()
        if by_class is not None and by_class != exam.scores_by_class:
            exam = exam.model_copy(update={"scores_by_class": by_class})
            store.save_exam(exam)
        hashes = store.export_hashes()
        columns, rows = score_table(store)
        _write(res, hashes, score_file(ws, store),
               lambda p: build_scores(p, exam.title, columns, rows, by_class=exam.scores_by_class),
               force=force)  # fmt: skip
        store.save_export_hashes(hashes)
    return res


def is_edited(store: ExamStore, path: Path, hashes: dict[str, str] | None = None) -> bool:
    """앱이 쓴 뒤 강사가 (엑셀에서) 고친 파일인가. 앱이 쓴 기록이 없는 파일은 고친 것으로 보지
    않는다."""
    hashes = store.export_hashes() if hashes is None else hashes
    known = hashes.get(path.name)
    return known is not None and path.is_file() and sha256_file(path) != known


def _roster(store: ExamStore) -> list[str]:
    return sorted(set(store.students()) | {r.student_id for r in store.student_records()})


def _write(
    res: ExportResult,
    hashes: dict[str, str],
    path: Path,
    build: Callable[[Path], Path],
    *,
    force: bool,
) -> None:
    """`exports.json`의 열쇠는 시험 폴더 안의 파일 이름."""
    known = hashes.get(path.name)
    if not force and known is not None and path.is_file() and sha256_file(path) != known:
        res.edited.append(path)
        res.warnings.append(
            f"{path.name}: 점수표를 엑셀에서 고쳐 두어 다시 쓰지 않음 — 앱은 점수표를 읽지 않으니 "
            "점수는 앱에서 고치고, 이 파일을 지우거나 다른 이름으로 저장하면 다시 만듭니다"
        )
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        build(path)
        res.written.append(path)
        hashes[path.name] = sha256_file(path)
    except OSError as e:
        res.warnings.append(
            f"{path.name}을 쓰지 못함 ({e.strerror or e}) — 엑셀에서 닫고 다시 내보내세요"
        )
