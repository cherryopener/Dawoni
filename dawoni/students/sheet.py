"""명단 파일 읽기: 표(엑셀·CSV)는 그대로, 사진(jpg·png — 출석부·명단 표를 찍거나 캡처한 것)은
AI가 읽는다 (2026-11-18 사용자: "반 명단을 jpg든 xlsx로든 줄 것"). 강사가 명단의 반을 정하면 반이
비어 있는 줄을 그 반으로 채운다 — 반 열이 있는 줄(여러 반 명단)은 그대로."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from dawoni.context import ContextEngine, TaskDef, TaskInput
from dawoni.models.base import DawoniModel
from dawoni.students.roster import read_table

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")
TABLE_SUFFIXES = (".xlsx", ".xlsm", ".csv")


class RosterRow(DawoniModel):
    name: str = Field(description="학생 이름 (띄어쓰기 없이, 동명이인 알파벳까지 — 김다원A)")
    class_name: str | None = Field(default=None, description="반 (표에 반 열·제목이 있을 때만)")
    school: str | None = Field(default=None, description="학교 (표에 학교 열이 있을 때만)")


class RosterRead(DawoniModel):
    """AI 출력: 명단 사진."""

    rows: list[RosterRow] = Field(description="학생마다 한 줄 (머리 줄·합계·빈 줄 제외)")
    uncertain: list[str] = Field(
        default_factory=lambda: list[str](), description="애매한 줄과 다른 가능성"
    )


ROSTER_IMAGE: TaskDef[RosterRead] = TaskDef(
    role="cover_reader",
    name="read_roster",
    template="read_roster.md.j2",
    output=RosterRead,
    rule_target=None,
    timeout_s=600,
)


def read_roster_file(
    engine: ContextEngine, path: Path, class_name: str | None = None
) -> tuple[list[dict[str, str | None]], list[str]]:
    """명단 파일 → (이름, 반, 학교) 줄과 알림. `class_name`: 강사가 정한 이 명단의 반."""
    klass = (class_name or "").strip() or None
    if path.suffix.lower() in IMAGE_SUFFIXES:
        read = engine.run(
            ROSTER_IMAGE,
            TaskInput(variables={"class_name": klass}, attachments=(path,)),
        ).value
        rows: list[dict[str, str | None]] = [
            {"name": r.name, "class_name": r.class_name, "school": r.school} for r in read.rows
        ]
        notes = [f"{path.name}: 사진에서 {len(rows)}명을 읽었습니다 — 학생 명부에서 확인해 주세요"]
        notes += [f"{path.name}: {u}" for u in read.uncertain]
    else:
        rows, notes = read_table(path)
    if klass:
        empty = [r for r in rows if not (r.get("class_name") or "").strip()]
        for r in empty:
            r["class_name"] = klass
        if empty:
            notes.append(f"{path.name}: 반이 적히지 않은 {len(empty)}명을 {klass} 반으로 넣음")
    return rows, notes
