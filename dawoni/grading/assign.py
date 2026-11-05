"""문항 나눠 맡기 (PLAN.md §8) — 강사 1명이 문항 1개씩 맡는다.

- 맡기: `exams/<시험>/assign/<문항>.json`에 강사 이름이 적힌다. 학생 × 문항 = 파일 1개라
  같은 학생의 다른 문항을 여러 강사가 동시에 채점해도 한 파일을 둘이 고치지 않는다.
- 다른 강사가 맡은 문항은 채점이 잠긴다(`grade_exam(by=…)`이 건너뜀). "가져오기"(`take=True`)로
  넘겨받으면 기록이 남는다.
- 진행판(`board`): 문항마다 맡은 사람, 채점 완료·검토 필요·입력 대기·미채점 학생 수.

동기화 폴더에서 두 PC가 동시에 맡으면 나중에 저장한 쪽이 남는다(충돌 사본은 `sharing`이 찾는다).
잠금은 실수 방지이지 보안이 아니다 — 이름만 맞추면 누구든 풀 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from dawoni.fsio import write_model_atomic
from dawoni.grading.store import ExamStore, safe_name
from dawoni.models.base import DawoniModel, utcnow


class AssignError(ValueError):
    """다른 강사가 맡은 문항 (가져오기로 넘겨받을 수 있음)."""


class AssignEvent(DawoniModel):
    action: Literal["assign", "take", "release"]
    teacher: str
    at: datetime = Field(default_factory=utcnow)
    previous: str | None = Field(default=None, description="가져오기 전에 맡았던 강사")


class Assignment(DawoniModel):
    question_number: str
    teacher: str | None = Field(default=None, description="맡은 강사 (None = 아무도 안 맡음)")
    since: datetime | None = None
    history: list[AssignEvent] = Field(default_factory=lambda: list[AssignEvent]())


def _path(store: ExamStore, q: str) -> Path:
    return store.root / "assign" / f"{safe_name(q)}.json"


def get(store: ExamStore, q: str) -> Assignment:
    p = _path(store, q)
    if not p.is_file():
        return Assignment(question_number=q)
    return Assignment.model_validate_json(p.read_text(encoding="utf-8"))


def all_assignments(store: ExamStore) -> dict[str, Assignment]:
    return {q.number: get(store, q.number) for q in store.exam().questions}


def assign(store: ExamStore, q: str, teacher: str, *, take: bool = False) -> Assignment:
    """문항을 맡는다. 다른 강사가 맡았으면 `take=True`(가져오기)일 때만 넘겨받는다."""
    store.exam().question(q)  # 없는 문항이면 KeyError
    teacher = teacher.strip()
    if not teacher:
        raise AssignError("맡을 강사 이름이 비어 있습니다.")
    a = get(store, q)
    if a.teacher == teacher:
        return a
    if a.teacher and not take:
        raise AssignError(
            f"문항 {q}은(는) {a.teacher} 선생님이 맡았습니다 — 넘겨받으려면 가져오기를 하세요."
        )
    event = AssignEvent(action="take" if a.teacher else "assign", teacher=teacher,
                        previous=a.teacher)  # fmt: skip
    a = a.model_copy(update={"teacher": teacher, "since": event.at, "history": [*a.history, event]})
    write_model_atomic(_path(store, q), a)
    return a


def release(store: ExamStore, q: str, teacher: str) -> Assignment:
    """맡은 문항을 놓는다 (맡은 강사만)."""
    a = get(store, q)
    if a.teacher is None:
        return a
    if a.teacher != teacher:
        raise AssignError(f"문항 {q}은(는) {a.teacher} 선생님이 맡았습니다.")
    a = a.model_copy(
        update={
            "teacher": None,
            "since": None,
            "history": [*a.history, AssignEvent(action="release", teacher=teacher)],
        }
    )
    write_model_atomic(_path(store, q), a)
    return a


def locked_for(store: ExamStore, q: str, teacher: str | None) -> str | None:
    """이 강사가 채점하면 안 되는 문항이면 맡은 강사 이름. 이름 없이 부르면(혼자 쓰는 PC) 잠그지
    않는다."""
    if not teacher:
        return None
    owner = get(store, q).teacher
    return owner if owner and owner != teacher else None


@dataclass
class QuestionProgress:
    question: str
    teacher: str | None
    students: int
    """판독문이 있는 학생 수."""
    graded: int
    """채점이 끝나 검토할 것이 없는 학생 (자동 + 강사 확정)."""
    needs_review: int
    waiting_input: int
    """`[판독불가]`가 남아 강사 입력을 기다림."""
    ungraded: int
    """판독은 있는데 아직 채점 전 (루브릭이 바뀌어 다시 채점할 것 포함)."""


def board(store: ExamStore) -> list[QuestionProgress]:
    """진행판: 문항마다 맡은 강사와 학생 수 (코드만, 파일을 읽어 센다)."""
    out: list[QuestionProgress] = []
    students = store.students()
    for q in store.exam().questions:
        rubric = store.rubric(q.number)
        n = graded = review = waiting = ungraded = 0
        for s in students:
            t = store.transcript(s, q.number)
            if t is None:
                continue
            n += 1
            g = store.grade(s, q.number)
            if t.unreadable and g is None:
                waiting += 1
            elif g is None or (
                rubric is not None
                and g.rubric_version != rubric.version
                and g.status != "teacher_confirmed"
            ):
                ungraded += 1
            elif g.status == "needs_review":
                review += 1
            else:
                graded += 1
        out.append(
            QuestionProgress(
                q.number, get(store, q.number).teacher, n, graded, review, waiting, ungraded
            )
        )
    return out
