"""시험의 채점 HWPX를 작업공간 `exports/<날짜_시험>/`에 쓴다 — 코드만, 토큰 0.

- 학생 파일 `반_이름_문항.hwpx`는 그 문항의 채점이 끝났을 때만 쓴다. `[판독불가]`가 남아 입력을
  기다리는 학생·문항은 쓰지 않는다 (강사가 채워 확정하면 채점 → 그때 쓴다).
- 반별 묶음 `(반별)_반_문항.hwpx`는 학생 파일이 바뀔 때마다 다시 만든다. 아직 안 끝난 학생은
  "입력 대기"·"채점 전" 쪽으로 넣어, 한 명 때문에 묶음이 늦어지지 않게 한다.

쓰기 실패(강사가 한글에서 그 파일을 열어 둠 등)는 채점을 멈추지 않고 경고로 돌려준다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dawoai.export.hwpx import (
    Sheet,
    build_bundle,
    build_student,
    part_grades,
    png_scan,
    sheet_lines,
)
from dawoai.grading.store import ExamStore, safe_name
from dawoai.models.grading import UNREADABLE, Exam
from dawoai.paths import WorkspacePaths

NO_CLASS = "반없음"


@dataclass
class ExportResult:
    written: list[Path] = field(default_factory=lambda: list[Path]())
    warnings: list[str] = field(default_factory=lambda: list[str]())


def exam_dir(ws: WorkspacePaths, exam: Exam) -> Path:
    """`exports/2026-11-03_중간고사 물리/` (PLAN.md §5-4: 시험마다 폴더 하나)."""
    return ws.exports / safe_name(f"{exam.created_at:%Y-%m-%d}_{exam.title}")


def _class_of(store: ExamStore, student: str) -> str:
    r = store.student_record(student)
    return (r.class_name if r and r.class_name else None) or NO_CLASS


def student_file(ws: WorkspacePaths, store: ExamStore, student: str, q: str) -> Path:
    """반_이름_문항.hwpx — 이름은 학생 ID (동명이인은 `김다원A`처럼 이미 갈라져 있다)."""
    name = f"{_class_of(store, student)}_{student}_{q}.hwpx"
    return exam_dir(ws, store.exam()) / safe_name(name)


def bundle_file(ws: WorkspacePaths, store: ExamStore, klass: str, q: str) -> Path:
    """`(`는 탐색기 이름순(StrCmpLogicalW)에서도 학생 파일보다 위에 온다 (2026-11-04 확인)."""
    return exam_dir(ws, store.exam()) / safe_name(f"(반별)_{klass}_{q}.hwpx")


class _Sheets:
    """한 번 내보내는 동안 같은 스캔을 다시 줄이지 않게 그림을 기억한다."""

    def __init__(self, store: ExamStore) -> None:
        self.store = store
        self.exam = store.exam()
        self._png: dict[Path, bytes] = {}

    def head(self, student: str) -> str:
        r = self.store.student_record(student)
        name = (r.name if r else None) or student
        bits = [self.exam.title, _class_of(self.store, student), name, (r.school if r else None)]
        return " · ".join(b for b in bits if b)

    def _scan(self, path: Path) -> bytes | None:
        if path not in self._png:
            try:
                self._png[path] = png_scan(path)
            except OSError:
                return None
        return self._png[path]

    def sheet(self, student: str, q: str) -> Sheet:
        """채점이 끝났으면 판독본 + 채점, 아니면 묶음에 넣을 안내 쪽."""
        head = self.head(student)
        t = self.store.transcript(student, q)
        if t is None:
            return Sheet(head=head, lines=[], waiting="답안 없음 (판독한 글이 없음)")
        if t.unreadable:
            wait = (
                f"입력 대기: {UNREADABLE} {t.unreadable}곳 — 강사가 원본을 보고 채워 확정하면 채점"
            )
            return Sheet(head=head, lines=[], waiting=wait)
        g = self.store.grade(student, q)
        rubric = self.store.rubric(q)
        if g is None or rubric is None:
            return Sheet(head=head, lines=[], waiting="채점 전 (루브릭 승인 뒤 채점)")
        scans = [s for p in t.pages if (s := self._scan(self.store.root / p)) is not None]
        notes: list[str] = []
        if not g.transcript_confirmed:
            notes.append("판독문 강사 확인 전 채점 — 판독문을 고쳐 확정하면 다시 채점합니다")
        if g.status == "teacher_confirmed":
            notes.append(f"강사 확정{f' ({g.confirmed_by})' if g.confirmed_by else ''}")
        notes += [f"! {f}" for f in g.flags]
        return Sheet(
            head=head,
            lines=sheet_lines(q, t.lines),
            scans=scans,
            grades=part_grades(q, g, rubric),
            comment=g.comment,
            notes=notes,
        )


def write_files(ws: WorkspacePaths, store: ExamStore, keys: list[tuple[str, str]]) -> ExportResult:
    """채점이 바뀐 (학생, 문항)의 학생 파일을 쓰고, 그 반 × 문항 묶음을 다시 만든다."""
    res = ExportResult()
    if not keys:
        return res
    sheets = _Sheets(store)
    bundles: set[tuple[str, str]] = set()
    for student, q in dict.fromkeys(keys):
        sheet = sheets.sheet(student, q)
        bundles.add((_class_of(store, student), q))
        if sheet.waiting is not None:
            continue
        _write(res, student_file(ws, store, student, q), lambda p, s=sheet: build_student(p, s))
    for klass, q in sorted(bundles):
        members = sorted(s for s in _roster(store) if _class_of(store, s) == klass)
        page = [sheets.sheet(s, q) for s in members]
        _write(res, bundle_file(ws, store, klass, q), lambda p, pg=page: build_bundle(p, pg))
    return res


def write_all(ws: WorkspacePaths, store: ExamStore) -> ExportResult:
    """시험 전체를 다시 쓴다 (내보내기 기능이 들어오기 전에 채점한 시험, 파일을 지운 경우)."""
    keys = [(s, q.number) for s in _roster(store) for q in store.exam().questions]
    return write_files(ws, store, keys)


def _roster(store: ExamStore) -> list[str]:
    return sorted(set(store.students()) | {r.student_id for r in store.student_records()})


def _write(res: ExportResult, path: Path, build: Callable[[Path], Path]) -> None:
    try:
        build(path)
        res.written.append(path)
    except OSError as e:
        res.warnings.append(
            f"{path.name}을 쓰지 못함 ({e.strerror or e}) — 한글에서 닫고 다시 내보내세요"
        )
