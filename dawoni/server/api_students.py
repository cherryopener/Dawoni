"""학생 명부 API (PLAN.md §7): 엑셀·CSV 가져오기, 보기, 한 명 넣기. 전화번호는 받지 않는다."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field

from dawoni.server.api_content import save_upload, upload_dir
from dawoni.server.api_setup import State
from dawoni.server.state import AppState
from dawoni.students import Roster, RosterStudent, read_table

router = APIRouter(prefix="/api/students")


def _roster(st: AppState) -> Roster:
    return Roster(st.services().workspace)


@router.get("")
def list_students(st: AppState = State) -> dict[str, Any]:
    r = _roster(st)
    return {"students": r.all(), "classes": r.classes()}


class AddBody(BaseModel):
    name: str = Field(min_length=1, description="동명이인은 이름 뒤 알파벳 (김다원A)")
    class_name: str | None = None
    school: str | None = None


@router.post("")
def add_student(body: AddBody, st: AppState = State) -> RosterStudent:
    return _roster(st).add(body.name, body.class_name, body.school)


@router.post("/import")
def import_students(file: Annotated[UploadFile, File()], st: AppState = State) -> dict[str, Any]:
    """엑셀(xlsx)·CSV (머리 줄: 이름/성명, 반, 학교). 이름·반이 같은 학생은 학교만 고친다."""
    path = save_upload(file, upload_dir(st))
    rows, notes = read_table(path)
    res = _roster(st).import_rows(rows)
    return {
        "added": res.added,
        "updated": res.updated,
        "unchanged": res.unchanged,
        "problems": res.problems,
        "notes": notes + res.notes,
    }
