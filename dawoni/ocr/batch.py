# pyright: basic, reportMissingTypeStubs=false
"""여러 학생 답안지를 한 파일로: 스캔 PDF → 학생별 묶음 → 표지(이름·반·학교) → 판독.

학원에서는 채점한 답안지를 한꺼번에 스캔한다 (실측: 3쪽 × 24명 = 72쪽, 가로로 돌아간 쪽).
- 쪽 수로 학생별 묶음을 나눈다 (`per_student`, 첫 쪽은 표지).
- 표지는 AI가 읽는다: 돌려 세울 각도, 이름(동명이인 알파벳까지), 반, 학교, 강사가 적은 문항별 점수.
  반 목록을 주면 그 중에서만 고른다 (손글씨 "1TSS" → TSS1).
- 같은 각도로 묶음의 모든 쪽을 세워 답안 쪽만 판독한다 (`transcribe_student`).
- 학생마다 따로 저장하므로 중간에 멈춰도 다시 하면 끝난 학생은 건너뛴다.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pymupdf
from PIL import Image
from pydantic import Field

from dawoni.context import ContextEngine, TaskDef, TaskInput
from dawoni.models.base import DawoniModel
from dawoni.models.grading import Exam, StudentRecord
from dawoni.ocr.pages import PDF_DPI, PageError

Progress = Callable[[int, int, str], None]


def _noop(done: int, total: int, message: str) -> None:
    pass


class CoverScore(DawoniModel):
    question: str = Field(description="문항 번호 (표지 점수 칸의 '1번' → \"1\")")
    points: float = Field(ge=0)


class CoverRead(DawoniModel):
    """AI 출력: 답안지 표지."""

    # 숫자 enum은 Gemini가 거부한다 (실측: "enum[0]: cannot be empty") → 문자열
    rotation: Literal["0", "90", "180", "270"] = Field(
        description="이미지를 똑바로 세우려면 시계 방향으로 몇 도 돌려야 하는가 (이미 바르면 0)"
    )
    name: str | None = Field(
        description="성명 칸 (동명이인 구분 알파벳까지, 예: 김태희A). 없으면 null"
    )
    class_name: str | None = Field(description="반명 칸. 없으면 null")
    school: str | None = Field(description="학교 칸. 비어 있으면 null")
    teacher_scores: list[CoverScore] = Field(
        default_factory=lambda: list[CoverScore](),
        description="표지의 문항별 점수 칸에 강사가 (빨간 펜으로) 적은 점수. 없으면 빈 목록",
    )
    uncertain: list[str] = Field(
        default_factory=lambda: list[str](), description="읽기 애매한 글자와 다른 가능성"
    )


COVER: TaskDef[CoverRead] = TaskDef(
    role="cover_reader",
    name="read_cover",
    template="read_cover.md.j2",
    output=CoverRead,
    rule_target="transcribe",
    timeout_s=300,
)

_SPACES = re.compile(r"\s+")


def student_id_of(name: str | None, set_no: int) -> str:
    """이름을 학생 ID로 (공백 제거). 못 읽으면 '미확인07'."""
    s = _SPACES.sub("", name or "")
    return s or f"미확인{set_no:02d}"


def check_cover(c: CoverRead, exam: Exam, classes: list[str] | None) -> list[str]:
    problems: list[str] = []
    numbers = {q.number for q in exam.questions}
    bad = sorted({s.question for s in c.teacher_scores} - numbers)
    if bad:
        problems.append(f"teacher_scores의 문항 {bad}는 이 시험에 없다. {sorted(numbers)} 중에서")
    if classes and c.class_name is not None and c.class_name not in classes:
        problems.append(
            f"class_name '{c.class_name}'는 반 목록 {classes}에 없다. 손글씨가 다르게 보여도 "
            "목록 중 가장 가까운 것을 고르고 uncertain에 적은 그대로를 남길 것 (해당 없으면 null)"
        )
    return problems


def page_count(pdf: Path) -> int:
    try:
        with pymupdf.open(pdf) as doc:
            return int(doc.page_count)
    except (RuntimeError, ValueError) as e:
        raise PageError(f"PDF를 열 수 없습니다: {pdf.name} ({e})") from e


def render_set(pdf: Path, pages: list[int], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    with pymupdf.open(pdf) as doc:
        for p in pages:
            out = out_dir / f"page_{p:03d}.png"
            doc.load_page(p - 1).get_pixmap(dpi=PDF_DPI).save(str(out))
            paths.append(out)
    return paths


def rotate(paths: list[Path], degrees: int) -> None:
    """시계 방향으로 `degrees`도 (제자리에 저장)."""
    if degrees % 360 == 0:
        return
    for p in paths:
        with Image.open(p) as im:
            im.rotate(-degrees, expand=True).save(p)


def read_cover(
    engine: ContextEngine, exam: Exam, cover: Path, classes: list[str] | None
) -> CoverRead:
    def check(c: CoverRead) -> list[str]:
        return check_cover(c, exam, classes)

    return engine.run(
        dataclasses.replace(COVER, validator=check),
        TaskInput(
            variables={"classes": classes or []},
            material="이 시험의 문항 번호: " + ", ".join(q.number for q in exam.questions),
            attachments=(cover,),
        ),
    ).value


@dataclass
class SetPlan:
    number: int
    """1부터 (파일 안에서 몇 번째 학생 묶음)."""
    pages: list[int]


def plan_sets(total: int, per_student: int, only: list[int] | None = None) -> list[SetPlan]:
    if per_student < 1:
        raise PageError("학생 한 명의 쪽 수는 1 이상이어야 합니다.")
    if total % per_student:
        raise PageError(
            f"전체 {total}쪽이 학생 한 명당 {per_student}쪽으로 나누어떨어지지 않습니다 "
            "— 빠진 쪽이나 끼어든 쪽이 있는지 확인하세요."
        )
    sets = [
        SetPlan(k + 1, list(range(k * per_student + 1, (k + 1) * per_student + 1)))
        for k in range(total // per_student)
    ]
    return [s for s in sets if not only or s.number in only]


@dataclass
class BatchSet:
    plan: SetPlan
    student_id: str
    record: StudentRecord
    images: list[Path]
    """세운 쪽 이미지 (표지 포함)."""


@dataclass
class BatchPlan:
    sets: list[BatchSet] = field(default_factory=lambda: list[BatchSet]())
    warnings: list[str] = field(default_factory=lambda: list[str]())
