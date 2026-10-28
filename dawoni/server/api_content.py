"""전역 규칙·범위 프로필 API."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel

from dawoni.context.refine import RefinedRule, refine_rule
from dawoni.context.rules import GlobalRule
from dawoni.grading.store import ScopeStore
from dawoni.models.base import utcnow
from dawoni.models.job import Job
from dawoni.models.scope import ScopeProfile
from dawoni.server.api_setup import State
from dawoni.server.jobs import JobContext
from dawoni.server.state import AppState

router = APIRouter(prefix="/api")


def save_upload(upload: UploadFile, dest_dir: Path) -> Path:
    """올린 파일을 디스크로. 파일 이름은 경로 성분을 떼고 쓴다."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = Path(upload.filename or "upload").name or "upload"
    dest = dest_dir / name
    with dest.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    return dest


def upload_dir(st: AppState) -> Path:
    d = st.paths.cache / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=d))


# ---------------------------------------------------------------- 전역 규칙


@router.get("/rules")
def list_rules(st: AppState = State) -> list[GlobalRule]:
    return st.services().rules.all()


class RefineBody(BaseModel):
    request: str


@router.post("/rules/refine")
def refine(body: RefineBody, st: AppState = State) -> Job:
    svc = st.services()

    def run(ctx: JobContext) -> dict[str, Any]:
        ctx.progress(0, 1, "요청을 규칙 문장으로 다듬는 중")
        res = refine_rule(svc.engine, body.request, svc.rules)
        ctx.progress(1, 1, "초안 완료")
        return {"request": body.request, "draft": res.value.model_dump(mode="json")}

    return st.jobs.submit("refine_rule", run, {"request": body.request})


class SaveRuleBody(BaseModel):
    request: str
    draft: RefinedRule
    by: str | None = None


@router.post("/rules")
def save_rule(body: SaveRuleBody, st: AppState = State) -> GlobalRule:
    return st.services().rules.save(body.draft.to_rule(body.request, body.by))


class RulePatch(BaseModel):
    enabled: bool | None = None
    title: str | None = None
    text: str | None = None


@router.patch("/rules/{rule_id}")
def patch_rule(rule_id: str, body: RulePatch, st: AppState = State) -> GlobalRule:
    store = st.services().rules
    rule = store.get(rule_id)
    if rule is None:
        raise KeyError(f"규칙 {rule_id} 없음")
    update = {k: v for k, v in body.model_dump().items() if v is not None}
    return store.save(rule.model_copy(update={**update, "updated_at": utcnow()}))


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: str, st: AppState = State) -> dict[str, bool]:
    return {"deleted": st.services().rules.delete(rule_id)}


# ---------------------------------------------------------------- 범위 프로필


@router.get("/scopes")
def list_scopes(st: AppState = State) -> list[dict[str, Any]]:
    return [
        {
            "id": s.id,
            "title": s.title,
            "level": s.level,
            "subject": s.subject,
            "version": s.version,
            "concepts": len(s.concepts),
            "skills": len(s.skills),
            "forbidden": [f.name for f in s.forbidden],
            "unit_ids": s.unit_ids,
            "updated_at": s.updated_at.isoformat(),
        }
        for s in ScopeStore(st.services().workspace).all()
    ]


@router.get("/scopes/{scope_id}")
def get_scope(scope_id: str, st: AppState = State) -> ScopeProfile:
    return ScopeStore(st.services().workspace).get(scope_id)


@router.post("/scopes")
def create_scope(scope: ScopeProfile, st: AppState = State) -> ScopeProfile:
    return ScopeStore(st.services().workspace).save(scope)


@router.put("/scopes/{scope_id}")
def update_scope(scope_id: str, scope: ScopeProfile, st: AppState = State) -> ScopeProfile:
    store = ScopeStore(st.services().workspace)
    old = store.get(scope_id)
    if scope.id != scope_id:
        raise ValueError("범위 ID는 바꿀 수 없습니다")
    return store.save(scope.model_copy(update={"version": old.version + 1, "updated_at": utcnow()}))


@router.post("/scopes/from-textbook")
def scope_from_textbook(
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()],
    level: Annotated[str, Form()],
    subject: Annotated[str | None, Form()] = None,
    pages: Annotated[str | None, Form()] = None,
    unit_ids: Annotated[str | None, Form(description="쉼표로 구분한 단원 ID")] = None,
    st: AppState = State,
) -> Job:
    from dawoni.cli_ingest import parse_pages
    from dawoni.ingest.workflow import ingest_textbook_pdf

    svc = st.services()
    path = save_upload(file, upload_dir(st))
    page_list = parse_pages(pages)

    def run(ctx: JobContext) -> dict[str, Any]:
        res = ingest_textbook_pdf(
            svc, path, title=title, level=level, subject=subject, pages=page_list,
            unit_ids=[u.strip() for u in (unit_ids or "").split(",") if u.strip()],
            progress=ctx.progress,
        )  # fmt: skip
        return {"scope_id": res.scope.id, "warnings": res.warnings}

    return st.jobs.submit("ingest_textbook", run, {"title": title, "file": path.name})


class FromUnitsBody(BaseModel):
    unit_ids: list[str]
    title: str
    level: str | None = None


@router.post("/scopes/from-units")
def scope_from_units(body: FromUnitsBody, st: AppState = State) -> dict[str, Any]:
    """여러 교재에 걸쳐 체크한 단원 → 범위 프로필 하나 (AI 호출 없음)."""
    from dawoni.bank.scope import compose_scope
    from dawoni.bank.units import UnitStore

    ws = st.services().workspace
    store = ScopeStore(ws)
    res = compose_scope(store.all(), UnitStore(ws).all(), body.unit_ids, title=body.title,
                        level=body.level)  # fmt: skip
    store.save(res.scope)
    return {"scope_id": res.scope.id, "used": res.used, "warnings": res.warnings}
