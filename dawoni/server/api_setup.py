"""연결 마법사·설정·작업·파일 API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from dawoni import __version__
from dawoni.config import AGENT_NAMES, AgentName, AppConfig
from dawoni.models.job import Job
from dawoni.models.source import WorkspaceMode
from dawoni.ocr.pages import PageError
from dawoni.server import files
from dawoni.server.jobs import JobContext
from dawoni.server.state import AppState, NeedsSetup
from dawoni.sharing import cloud_provider, conflict_copies
from dawoni.workspace import create_workspace, open_workspace, set_mode, set_subjects

router = APIRouter(prefix="/api")

SECRET_FIELDS = ("clova_secret", "mathpix_app_key")
MASK = "••••"


def get_state(request: Request) -> AppState:
    return request.app.state.dawoni


State = Depends(get_state)


# ---------------------------------------------------------------- 상태


@router.get("/status")
def status(st: AppState = State) -> dict[str, Any]:
    cfg = st.config()
    ws_ok = True
    ws_error: str | None = None
    mode: WorkspaceMode | None = None
    subjects: list[str] = []
    try:
        meta = open_workspace(st.services().workspace.root)[1]
        mode, subjects = meta.mode, meta.subjects
    except NeedsSetup as e:
        ws_ok, ws_error = False, str(e)
    return {
        "version": __version__,
        "workspace": str(cfg.workspace) if cfg.workspace else None,
        "workspace_ok": ws_ok,
        "workspace_error": ws_error,
        "workspace_mode": mode,
        "workspace_subjects": subjects,
        "machine": st.machine,
        "default_agent": cfg.default_agent,
        "agents": {n: cfg.agent(n).model_dump(mode="json") for n in AGENT_NAMES},
    }


# ---------------------------------------------------------------- AI 연결


@router.get("/agents/detect")
def detect_agents(st: AppState = State) -> list[dict[str, Any]]:
    from dawoni.agents.detect import detect

    cfg = st.config()
    out: list[dict[str, Any]] = []
    for name in AGENT_NAMES:
        d = detect(name, cfg.agent(name).executable)
        out.append(
            {
                "name": name,
                "installed": d.installed,
                "path": str(d.path) if d.path else None,
                "version": d.version,
                "logged_in": d.logged_in,
                "notes": d.notes,
                "enabled": cfg.agent(name).enabled,
                "model": cfg.agent(name).model,
            }
        )
    return out


class AgentTestBody(BaseModel):
    model: str | None = None


@router.post("/agents/{name}/test")
def test_agent(name: AgentName, body: AgentTestBody, st: AppState = State) -> Job:
    from dawoni.agents import AgentClient
    from dawoni.agents.selftest import evaluate, selftest_request

    def run(ctx: JobContext) -> dict[str, Any]:
        ctx.progress(0, 1, f"{name}에 시험 문제(그림 1장) 보내는 중")
        client = AgentClient(st.config(), st.paths)
        res = client.run(
            selftest_request(), agent=name, model=body.model, fallback=False, on_progress=ctx.note
        )
        rep = evaluate(res)
        ctx.progress(1, 1, "통과" if rep.passed else "응답 확인 필요")
        return {
            "passed": rep.passed,
            "problems": rep.problems,
            "answer": rep.answer,
            "agent": res.agent,
            "model": res.model,
            "duration_s": round(res.duration_s, 1),
            "tokens": res.usage.total_tokens,
            "warnings": res.warnings,
        }

    return st.jobs.submit("agent_test", run, {"agent": name, "model": body.model})


# ---------------------------------------------------------------- 설정


def masked_config(cfg: AppConfig) -> dict[str, Any]:
    data = cfg.model_dump(mode="json")
    for k in SECRET_FIELDS:
        if data["ocr"].get(k):
            data["ocr"][k] = MASK
    return data


@router.get("/config")
def get_config(st: AppState = State) -> dict[str, Any]:
    return masked_config(st.config())


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)  # pyright: ignore[reportUnknownArgumentType]
        elif v == MASK:
            continue  # 가려진 비밀값은 그대로 둔다
        else:
            out[k] = v
    return out


@router.patch("/config")
def patch_config(patch: dict[str, Any], st: AppState = State) -> dict[str, Any]:
    patch.pop("workspace", None)  # 작업공간은 /workspace로만 바꾼다
    cfg = AppConfig.model_validate(_merge(st.config().model_dump(mode="json"), patch))
    st.save_config(cfg)
    return masked_config(cfg)


class WorkspaceBody(BaseModel):
    path: str
    create: bool = False
    name: str | None = None
    mode: WorkspaceMode = "personal"
    subjects: list[str] = Field(
        default_factory=lambda: list[str](), description="과목 팀 (비면 전체)"
    )


@router.post("/workspace")
def set_workspace(body: WorkspaceBody, st: AppState = State) -> dict[str, Any]:
    root = Path(body.path).expanduser()
    if body.create:
        create_workspace(root, body.name, body.mode, body.subjects)
    else:
        open_workspace(root)
    cfg = st.config()
    cfg.workspace = root
    st.save_config(cfg)
    return status(st)


@router.get("/workspace/sharing")
def sharing(path: str | None = None, st: AppState = State) -> dict[str, Any]:
    """`path`만 주면 그 폴더가 클라우드 동기화 폴더인지만 (연결 전 안내용)."""
    if path is not None:
        return {"provider": cloud_provider(Path(path))}
    ws = st.services().workspace
    _, meta = open_workspace(ws.root)
    return {
        "mode": meta.mode,
        "subjects": meta.subjects,
        "provider": cloud_provider(ws.root),
        "conflicts": [c.model_dump() for c in conflict_copies(ws)],
    }


class ModeBody(BaseModel):
    mode: WorkspaceMode


@router.put("/workspace/mode")
def put_mode(body: ModeBody, st: AppState = State) -> dict[str, Any]:
    set_mode(st.services().workspace.root, body.mode)
    return sharing(None, st)


class SubjectsBody(BaseModel):
    subjects: list[str]


@router.put("/workspace/subjects")
def put_subjects(body: SubjectsBody, st: AppState = State) -> dict[str, Any]:
    set_subjects(st.services().workspace.root, body.subjects)
    return sharing(None, st)


@router.post("/system/choose-folder")
def choose_folder() -> dict[str, str | None]:
    p = files.choose_folder()
    return {"path": str(p) if p else None}


# ---------------------------------------------------------------- 작업


@router.get("/jobs")
def list_jobs(st: AppState = State) -> list[Job]:
    return st.jobs.list()[:50]


@router.get("/jobs/{job_id}")
def get_job(job_id: str, st: AppState = State) -> Job:
    return st.jobs.get(job_id)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, st: AppState = State) -> Job:
    return st.jobs.cancel(job_id)


# ---------------------------------------------------------------- 파일 (작업공간 안만)


class PathBody(BaseModel):
    path: str


@router.get("/exports")
def list_exports(st: AppState = State) -> list[dict[str, Any]]:
    ws = st.services().workspace
    items = sorted(ws.exports.glob("*.docx"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "path": p.relative_to(ws.root).as_posix(),
            "name": p.name,
            "size": p.stat().st_size,
            "modified": p.stat().st_mtime,
        }
        for p in items
    ]


@router.post("/files/open")
def open_file(body: PathBody, st: AppState = State) -> dict[str, bool]:
    files.open_file(files.inside(st.services().workspace.root, body.path))
    return {"ok": True}


@router.post("/files/reveal")
def reveal_file(body: PathBody, st: AppState = State) -> dict[str, bool]:
    files.reveal(files.inside(st.services().workspace.root, body.path))
    return {"ok": True}


@router.post("/scanner/open")
def scanner_open(st: AppState = State) -> dict[str, Any]:
    """복합기 공유 폴더와 작업공간 '스캔본' 폴더를 함께 연다."""
    from dawoni.scanner import open_scan_folders

    try:
        inbox: Path | None = st.services().workspace.scan_inbox
    except NeedsSetup:
        inbox = None  # 작업공간 없이도 복합기 연결은 시험해 볼 수 있게
    opened = open_scan_folders(st.config().scanner, inbox)
    notes = list(opened.notes)
    if inbox is None:
        notes.append("작업공간을 아직 정하지 않아 복합기 폴더만 열었습니다.")
    return {"scanner": opened.scanner, "inbox": str(inbox) if inbox else None, "notes": notes}


@router.get("/scanner/inbox")
def scanner_inbox(st: AppState = State) -> list[dict[str, Any]]:
    """작업공간 '스캔본' 폴더의 PDF (새것 먼저) — 여러 학생 답안지 판독에 고른다."""
    from dawoni.ocr.batch import page_count
    from dawoni.scanner import inbox_pdfs

    root = st.services().workspace.root
    out: list[dict[str, Any]] = []
    for p in inbox_pdfs(st.services().workspace.scan_inbox):
        try:
            pages: int | None = page_count(p)
        except PageError:
            pages = None
        out.append(
            {"path": p.relative_to(root).as_posix(), "name": p.name, "pages": pages,
             "size": p.stat().st_size, "modified": p.stat().st_mtime}
        )  # fmt: skip
    return out


@router.post("/files/save-as")
def save_as(body: PathBody, st: AppState = State) -> dict[str, str | None]:
    dest = files.save_as(files.inside(st.services().workspace.root, body.path))
    return {"saved_to": str(dest) if dest else None}
