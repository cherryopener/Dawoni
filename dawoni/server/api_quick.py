"""한 번에 채점 API (2026-11-18): 파일을 올리고 버튼 하나 — `dawoni.autopilot`.

POST /api/quick                    시험지(해설 포함) + 답안지 PDF들 (+ 명단) → 작업 "autopilot"
POST /api/quick (exam_id=…)        이미 있는 시험에 답안지 더 넣기
GET  /api/exams/{id}/autopilot     진행·결과·물을 것 (결과 화면)
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, UploadFile

from dawoni import autopilot
from dawoni.grading.pipeline import PipelineError
from dawoni.grading.store import ExamStore, safe_name
from dawoni.models.job import Job
from dawoni.server.api_content import save_upload, upload_dir
from dawoni.server.api_setup import State
from dawoni.server.jobs import JobContext
from dawoni.server.state import AppState

router = APIRouter(prefix="/api")


def _keep(src: Path, inbox: Path) -> Path:
    """답안지 원본을 작업공간 '스캔본' 폴더에 남긴다 (이름이 겹치면 뒤에 번호)."""
    inbox.mkdir(parents=True, exist_ok=True)
    dest = inbox / safe_name(src.name)
    k = 2
    while dest.exists():
        dest = inbox / safe_name(f"{src.stem} ({k}){src.suffix}")
        k += 1
    shutil.copyfile(src, dest)
    return dest


@router.post("/quick")
def quick(
    answers: Annotated[list[UploadFile], File()],
    exam: Annotated[UploadFile | None, File()] = None,
    roster: Annotated[UploadFile | None, File()] = None,
    exam_id: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
    level: Annotated[str | None, Form()] = None,
    by: Annotated[str | None, Form()] = None,
    st: AppState = State,
) -> Job:
    """시험지(해설 포함)와 학생 답안지 PDF → 시험 읽기 → 루브릭 자동 승인 → 판독 → 채점 →
    점수표·채점 답안지 PDF. `exam_id`를 주면 그 시험에 답안지를 더 넣는다. 명단(엑셀·CSV)은 학생
    명부에 더한다. 시험 ID는 작업 매개변수 `exam`으로 알린다 (시험을 만들면 바로)."""
    from dawoni.cli_ingest import resolve_scope
    from dawoni.ingest.workflow import existing_exam, ingest_exam
    from dawoni.students import Roster, read_table

    svc = st.services()
    if exam_id is None and exam is None:
        raise PipelineError("시험지 파일을 넣어 주세요.")
    pdfs = [a for a in answers if (a.filename or "").lower().endswith(".pdf")]
    if not pdfs:
        raise PipelineError("학생 답안지 PDF를 넣어 주세요.")
    tmp = upload_dir(st)
    answer_paths = [_keep(save_upload(a, tmp), svc.workspace.scan_inbox) for a in pdfs]
    exam_path = save_upload(exam, tmp) if exam is not None else None
    roster_path = save_upload(roster, tmp) if roster is not None else None
    scope_id = resolve_scope(svc, None, level or "물리") if exam_path is not None else None
    who = st.who(by)
    if exam_id is not None:
        ExamStore(svc.workspace, exam_id).exam()  # 없는 시험이면 작업을 띄우기 전에 알린다

    def run(ctx: JobContext) -> dict[str, Any]:
        eid = exam_id
        notes: list[str] = []
        if roster_path is not None:
            ctx.progress(0, 1, "학생 명단 넣는 중")
            rows, read_notes = read_table(roster_path)
            res = Roster(svc.workspace).import_rows(rows)
            notes += read_notes + res.problems
        if eid is None:
            assert exam_path is not None and scope_id is not None
            if (old := existing_exam(svc, exam_path)) is not None:
                eid = old.exam_id
                assert eid is not None
                notes.append("같은 시험지로 만든 시험을 그대로 씁니다")
            else:
                ctx.progress(0, 1, "시험지·해설 읽는 중")
                name = title or exam_path.stem
                made = ingest_exam(svc, exam_path, title=name, scope_id=scope_id,
                                   progress=ctx.progress)  # fmt: skip
                eid = made.exam.id
                notes += made.warnings
        ctx.tag("exam", eid)
        store = ExamStore(svc.workspace, eid)
        s = autopilot.run(svc, store, answer_paths, by=who, progress=ctx.progress)
        if notes:
            s.warnings = notes + s.warnings
            autopilot.save_state(store, s)
        return {"exam_id": eid, "students": s.students, "graded": s.graded,
                "review": s.review, "asks": len(s.asks), "files": s.files}  # fmt: skip

    return st.jobs.submit("autopilot", run, {"exam": exam_id, "title": title or ""})


@router.get("/exams/{exam_id}/autopilot")
def autopilot_state(exam_id: str, st: AppState = State) -> dict[str, Any] | None:
    """한 번에 채점의 진행·결과. 강사가 이름·판독문을 고쳤으면 물을 것을 다시 센다."""
    from dataclasses import asdict

    store = ExamStore(st.services().workspace, exam_id)
    s = autopilot.refresh(store)
    return asdict(s) if s is not None else None
