"""시험의 판독본·채점 HWPX를 작업공간 `exports/<날짜_시험>/`에 쓴다 — 코드만, 토큰 0.

- 학생 파일 `반_이름_문항.hwpx`는 **판독이 끝나면 바로** 쓴다 (판독본, PLAN.md §5-1 5단계). 강사가
  한글에서 원본 그림과 대조해 고치고 저장한 뒤 "확인 완료"를 누르면 앱이 다시 읽는다
  (`grading.workflow.confirm_from_files`). 채점이 끝나면 같은 파일 새 쪽에 채점본을 잇는다.
  `[판독불가]`가 남은 판독본은 분홍으로 칠하고 "입력 대기"를 머리에 적는다 — 강사가 채울 곳이다.
- **강사가 고친 파일은 덮어쓰지 않는다.** 앱이 쓴 파일의 해시를 시험 폴더 `exports.json`에
  두고, 파일이 그와 다르면 강사가 고친 것으로 보고 건너뛴다 (경고). 확인 완료로 읽어 들이면 그
  해시를 받아들여 다음부터 다시 쓴다.
- 반별 묶음 `(반별)_반_문항.hwpx`는 학생 파일이 바뀔 때마다 다시 만든다. 판독문이 없는 학생은 안내
  쪽으로 넣는다. 앱은 묶음을 다시 읽지 않는다 — 묶음을 고쳐 두었으면 다시 만들지 않고 알린다.
- 같은 반에 같은 이름의 시험지가 둘 이상이면(`needs_rename`) 강사가 이름을 바꾸기 전까지 그
  학생 파일을 쓰지 않는다 (§5-1 3단계).

쓰기 실패(강사가 한글에서 그 파일을 열어 둠 등)는 채점을 멈추지 않고 경고로 돌려준다.
"""

from __future__ import annotations

import threading
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
from dawoai.fsio import sha256_file
from dawoai.grading.store import ExamStore, safe_name
from dawoai.models.grading import UNREADABLE, Exam
from dawoai.paths import WorkspacePaths

NO_CLASS = "반없음"
_lock = threading.RLock()
"""`exports.json`을 두 작업이 동시에 고치지 않게."""


@dataclass
class ExportResult:
    written: list[Path] = field(default_factory=lambda: list[Path]())
    warnings: list[str] = field(default_factory=lambda: list[str]())
    edited: list[Path] = field(default_factory=lambda: list[Path]())
    """강사가 한글에서 고쳐 두어 덮어쓰지 않은 파일."""


def exam_dir(ws: WorkspacePaths, exam: Exam) -> Path:
    """`exports/2026-11-03_중간고사 물리/` (PLAN.md §5-4: 시험마다 폴더 하나)."""
    return ws.exports / safe_name(f"{exam.created_at:%Y-%m-%d}_{exam.title}")


def _class_of(store: ExamStore, student: str) -> str:
    r = store.student_record(student)
    return (r.class_name if r and r.class_name else None) or NO_CLASS


def _name_of(store: ExamStore, student: str) -> str:
    """파일에 쓸 이름 — 표지(또는 명부)의 이름. 다른 반 동명이인은 학생 ID만 다르고 이름은 같다."""
    r = store.student_record(student)
    return (r.name if r and r.name else None) or student


def student_file(ws: WorkspacePaths, store: ExamStore, student: str, q: str) -> Path:
    """반_이름_문항.hwpx (동명이인은 `김다원A`처럼 이름이 이미 갈라져 있다)."""
    name = f"{_class_of(store, student)}_{_name_of(store, student)}_{q}.hwpx"
    return exam_dir(ws, store.exam()) / safe_name(name)


def bundle_file(ws: WorkspacePaths, store: ExamStore, klass: str, q: str) -> Path:
    """`(`는 탐색기 이름순(StrCmpLogicalW)에서도 학생 파일보다 위에 온다 (2026-11-04 확인)."""
    return exam_dir(ws, store.exam()) / safe_name(f"(반별)_{klass}_{q}.hwpx")


def is_edited(store: ExamStore, path: Path, hashes: dict[str, str] | None = None) -> bool:
    """앱이 쓴 뒤 강사가 (한글에서) 고친 파일인가. 앱이 쓴 기록이 없는 파일(내보내기 기록이
    생기기 전)은 고친 것으로 보지 않는다."""
    hashes = store.export_hashes() if hashes is None else hashes
    known = hashes.get(path.name)
    return known is not None and path.is_file() and sha256_file(path) != known


def accept_file(store: ExamStore, path: Path) -> None:
    """강사가 고친 파일을 읽어 들였다 — 이 내용을 앱이 아는 것으로 두고 다음부터 다시 쓴다."""
    with _lock:
        hashes = store.export_hashes()
        hashes[path.name] = sha256_file(path)
        store.save_export_hashes(hashes)


class _Sheets:
    """한 번 내보내는 동안 같은 스캔을 다시 줄이지 않게 그림을 기억한다."""

    def __init__(self, store: ExamStore) -> None:
        self.store = store
        self.exam = store.exam()
        self._png: dict[Path, bytes] = {}

    def head(self, student: str) -> str:
        r = self.store.student_record(student)
        bits = [self.exam.title, _class_of(self.store, student), _name_of(self.store, student),
                (r.school if r else None)]  # fmt: skip
        return " · ".join(b for b in bits if b)

    def _scan(self, path: Path) -> bytes | None:
        if path not in self._png:
            try:
                self._png[path] = png_scan(path)
            except OSError:
                return None
        return self._png[path]

    def sheet(self, student: str, q: str) -> Sheet:
        """판독본 (+ 채점이 끝났으면 채점본). 판독문이 없거나 이름 확인 전이면 묶음에 넣을
        안내 쪽."""
        head = self.head(student)
        r = self.store.student_record(student)
        if r is not None and r.needs_rename:
            wait = (
                "이름 확인 대기: 같은 반에 같은 이름의 시험지가 또 있음 — "
                "김다원A·김다원B처럼 바꾸면 만듦"
            )
            return Sheet(head=head, lines=[], waiting=wait)
        t = self.store.transcript(student, q)
        if t is None:
            return Sheet(head=head, lines=[], waiting="답안 없음 (판독한 글이 없음)")
        scans = [s for p in t.pages if (s := self._scan(self.store.root / p)) is not None]
        reading = Sheet(head=head, lines=sheet_lines(q, t.lines), scans=scans)
        if not t.lines:  # 빈 답안: 소문항 표식만 — 강사가 원본을 보고 그 아래에 채운다
            parts = self.exam.question(q).parts
            reading.empty_parts = [f"{q}-{p.label}" for p in parts] or [q]
            reading.reading_notes.append(
                "판독한 글이 없음 — 원본에 답이 있으면 표식 아래에 채우세요"
            )
        if t.unreadable:
            reading.reading_notes.append(
                f"입력 대기: {UNREADABLE} {t.unreadable}곳(분홍) — 원본을 보고 채워 저장한 뒤 "
                "'확인 완료'를 누르면 채점합니다"
            )
            return reading
        if not t.confirmed:
            reading.reading_notes.append(
                "판독문 확인 전 — 원본과 비교해 고칠 곳을 고쳐 저장한 뒤 '확인 완료'를 누르세요"
            )
        g = self.store.grade(student, q)
        rubric = self.store.rubric(q)
        if g is None or rubric is None:
            return reading
        notes: list[str] = []
        if not g.transcript_confirmed:
            notes.append("판독문 강사 확인 전 채점 — 판독문을 고쳐 확정하면 다시 채점합니다")
        if g.status == "teacher_confirmed":
            notes.append(f"강사 확정{f' ({g.confirmed_by})' if g.confirmed_by else ''}")
        notes += [f"! {f}" for f in g.flags]
        reading.grades = part_grades(q, g, rubric)
        reading.comment = g.comment
        reading.notes = notes
        return reading


def write_files(
    ws: WorkspacePaths, store: ExamStore, keys: list[tuple[str, str]], *, force: bool = False
) -> ExportResult:
    """판독·채점이 바뀐 (학생, 문항)의 학생 파일을 쓰고, 그 반 × 문항 묶음을 다시 만든다.
    `force`: 강사가 고친 파일도 덮어쓴다 (고친 내용은 사라진다)."""
    res = ExportResult()
    if not keys:
        return res
    sheets = _Sheets(store)
    bundles: set[tuple[str, str]] = set()
    with _lock:
        hashes = store.export_hashes()
        for student, q in dict.fromkeys(keys):
            sheet = sheets.sheet(student, q)
            bundles.add((_class_of(store, student), q))
            if sheet.waiting is not None:
                continue
            path = student_file(ws, store, student, q)
            _write(res, hashes, path, lambda p, s=sheet: build_student(p, s), force=force,
                   edited="강사가 한글에서 고친 판독본 — '확인 완료'로 읽어 들이면 "
                          "다시 씁니다")  # fmt: skip
        for klass, q in sorted(bundles):
            members = sorted(s for s in _roster(store) if _class_of(store, s) == klass)
            page = [sheets.sheet(s, q) for s in members]
            path = bundle_file(ws, store, klass, q)
            _write(res, hashes, path, lambda p, pg=page: build_bundle(p, pg), force=force,
                   edited="묶음을 한글에서 고쳐 두어 다시 만들지 않음 — 앱은 묶음을 읽지 않으니 "
                          "학생 파일에서 고치고, 이 묶음을 지우면 다시 만듭니다")  # fmt: skip
        store.save_export_hashes(hashes)
    return res


def write_all(ws: WorkspacePaths, store: ExamStore, *, force: bool = False) -> ExportResult:
    """시험 전체를 다시 쓴다 (내보내기 기능이 들어오기 전에 채점한 시험, 파일을 지운 경우)."""
    keys = [(s, q.number) for s in _roster(store) for q in store.exam().questions]
    return write_files(ws, store, keys, force=force)


def remove_student_files(ws: WorkspacePaths, store: ExamStore, student: str) -> list[str]:
    """학생 이름을 바꾸기 전에 그 학생의 옛 파일을 지운다 (강사가 고친 파일은 남기고 알린다)."""
    warnings: list[str] = []
    with _lock:
        hashes = store.export_hashes()
        for q in store.exam().questions:
            path = student_file(ws, store, student, q.number)
            if not path.is_file():
                continue
            if is_edited(store, path, hashes):
                warnings.append(f"{path.name}: 강사가 고친 파일이라 지우지 않음 — 직접 지우세요")
                continue
            try:
                path.unlink()
                hashes.pop(path.name, None)
            except OSError as e:
                warnings.append(f"{path.name}을 지우지 못함 ({e.strerror or e})")
        store.save_export_hashes(hashes)
    return warnings


def _roster(store: ExamStore) -> list[str]:
    return sorted(set(store.students()) | {r.student_id for r in store.student_records()})


def _write(
    res: ExportResult,
    hashes: dict[str, str],
    path: Path,
    build: Callable[[Path], Path],
    *,
    force: bool,
    edited: str,
) -> None:
    known = hashes.get(path.name)
    if not force and known is not None and path.is_file() and sha256_file(path) != known:
        res.edited.append(path)
        res.warnings.append(f"{path.name}: {edited}")
        return
    try:
        build(path)
        res.written.append(path)
        hashes[path.name] = sha256_file(path)
    except OSError as e:
        res.warnings.append(
            f"{path.name}을 쓰지 못함 ({e.strerror or e}) — 한글에서 닫고 다시 내보내세요"
        )
