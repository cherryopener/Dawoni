"""채점 자료 저장 (작업공간, 항목 1개 = 파일 1개).

scopes/<범위ID>.json
exams/<시험ID>/exam.json
              /keys/<문항>.json            (강사 해설지)
              /solutions/<문항>.json
              /rubrics/<문항>.json
              /transcripts/<학생>/<문항>.json
              /grades/<학생>/<문항>.json
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from dawoni.fsio import read_json, write_model_atomic
from dawoni.models.answer_key import AnswerKey
from dawoni.models.grading import Exam, GradeResult, Rubric, ScopedSolution, Transcript
from dawoni.models.scope import ScopeProfile
from dawoni.paths import WorkspacePaths

_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')


def safe_name(s: str) -> str:
    """파일 이름에 쓸 수 있게 (한글은 그대로, Windows 금지 문자만 바꿈)."""
    out = _UNSAFE.sub("_", s.strip()).strip("._")
    if not out:
        raise ValueError(f"파일 이름으로 쓸 수 없음: {s!r}")
    return out


def _load[T: BaseModel](path: Path, model: type[T]) -> T | None:
    return model.model_validate(read_json(path)) if path.exists() else None


class ScopeStore:
    def __init__(self, ws: WorkspacePaths) -> None:
        self.dir = ws.scopes

    def save(self, scope: ScopeProfile) -> ScopeProfile:
        write_model_atomic(self.dir / f"{scope.id}.json", scope)
        return scope

    def get(self, scope_id: str) -> ScopeProfile:
        s = _load(self.dir / f"{scope_id}.json", ScopeProfile)
        if s is None:
            raise KeyError(f"범위 프로필 {scope_id} 없음")
        return s

    def all(self) -> list[ScopeProfile]:
        if not self.dir.is_dir():
            return []
        return [ScopeProfile.model_validate(read_json(p)) for p in sorted(self.dir.glob("*.json"))]


class ExamStore:
    def __init__(self, ws: WorkspacePaths, exam_id: str) -> None:
        self.root = ws.exams / exam_id
        self.exam_id = exam_id

    @classmethod
    def create(cls, ws: WorkspacePaths, exam: Exam) -> ExamStore:
        store = cls(ws, exam.id)
        write_model_atomic(store.root / "exam.json", exam)
        return store

    @staticmethod
    def list_ids(ws: WorkspacePaths) -> list[str]:
        if not ws.exams.is_dir():
            return []
        return sorted(p.parent.name for p in ws.exams.glob("*/exam.json"))

    def exam(self) -> Exam:
        e = _load(self.root / "exam.json", Exam)
        if e is None:
            raise KeyError(f"시험 {self.exam_id} 없음")
        return e

    def save_exam(self, exam: Exam) -> None:
        """문항 내용 수정 (그림 스펙·모드 등). 시험 ID는 바꿀 수 없다."""
        if exam.id != self.exam_id:
            raise ValueError("시험 ID가 다릅니다")
        write_model_atomic(self.root / "exam.json", exam)

    def _q(self, kind: str, q: str) -> Path:
        return self.root / kind / f"{safe_name(q)}.json"

    def _sq(self, kind: str, student: str, q: str) -> Path:
        return self.root / kind / safe_name(student) / f"{safe_name(q)}.json"

    def save_key(self, k: AnswerKey) -> None:
        write_model_atomic(self._q("keys", k.question_number), k)

    def answer_key(self, q: str) -> AnswerKey | None:
        return _load(self._q("keys", q), AnswerKey)

    def save_solution(self, s: ScopedSolution) -> None:
        write_model_atomic(self._q("solutions", s.question_number), s)

    def solution(self, q: str) -> ScopedSolution | None:
        return _load(self._q("solutions", q), ScopedSolution)

    def save_rubric(self, r: Rubric) -> None:
        write_model_atomic(self._q("rubrics", r.question_number), r)

    def rubric(self, q: str) -> Rubric | None:
        return _load(self._q("rubrics", q), Rubric)

    def scan_dir(self, student: str) -> Path:
        return self.root / "scans" / safe_name(student)

    def save_transcript(self, t: Transcript) -> None:
        write_model_atomic(self._sq("transcripts", t.student_id, t.question_number), t)

    def transcript(self, student: str, q: str) -> Transcript | None:
        return _load(self._sq("transcripts", student, q), Transcript)

    def students(self) -> list[str]:
        d = self.root / "transcripts"
        if not d.is_dir():
            return []
        ids: set[str] = set()
        for p in d.glob("*/*.json"):
            ids.add(Transcript.model_validate(read_json(p)).student_id)
        return sorted(ids)

    def save_grade(self, g: GradeResult) -> None:
        write_model_atomic(self._sq("grades", g.student_id, g.question_number), g)

    def grade(self, student: str, q: str) -> GradeResult | None:
        return _load(self._sq("grades", student, q), GradeResult)
