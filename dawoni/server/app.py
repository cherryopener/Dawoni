"""FastAPI 앱 조립: API + 빌드된 화면(`dawoni/web/`) + 로컬 보호 + 오류 → 한국어 안내."""

from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from dawoni.export.docx.builder import ExportError
from dawoni.export.fill import FillError
from dawoni.grading.pipeline import PipelineError
from dawoni.ingest.workflow import IngestRefused
from dawoni.ocr.pages import PageError
from dawoni.paths import AppPaths
from dawoni.server import api_calibration, api_content, api_exams, api_generate, api_setup
from dawoni.server.files import PathRefused
from dawoni.server.security import COOKIE, LocalOnly
from dawoni.server.state import AppState, NeedsSetup
from dawoni.workspace import WorkspaceError

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

_NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>다워니</title>
<body style="font-family:sans-serif;padding:40px">
<h2>화면 파일이 아직 없습니다</h2>
<p><code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code> 후 다시 여세요.</p>
<p>API는 동작 중입니다.</p></body>"""


def create_app(paths: AppPaths, token: str, *, web_dir: Path | None = None) -> FastAPI:
    state = AppState.create(paths, token)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        yield
        state.jobs.shutdown()

    app = FastAPI(
        title="Dawoni", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.dawoni = state
    app.add_middleware(LocalOnly, token=token)

    def err(status: int, code: str, e: BaseException) -> JSONResponse:
        return JSONResponse({"detail": str(e).strip("'\""), "code": code}, status_code=status)

    @app.exception_handler(NeedsSetup)
    async def _needs_setup(_: Request, e: NeedsSetup) -> Response:  # pyright: ignore[reportUnusedFunction]
        return err(409, "needs_setup", e)

    @app.exception_handler(KeyError)
    async def _not_found(_: Request, e: KeyError) -> Response:  # pyright: ignore[reportUnusedFunction]
        return err(404, "not_found", e)

    bad_request = (
        PipelineError,
        WorkspaceError,
        IngestRefused,
        ExportError,
        FillError,
        PageError,
        PathRefused,
        ValidationError,
        ValueError,
    )
    for exc in bad_request:

        async def _bad(_: Request, e: Exception) -> Response:
            if isinstance(e, ValidationError):
                msgs = [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:5]]
                return JSONResponse({"detail": "; ".join(msgs), "code": "invalid"}, 400)
            return err(400, "invalid", e)

        app.add_exception_handler(exc, _bad)

    app.include_router(api_setup.router)
    app.include_router(api_content.router)
    app.include_router(api_exams.router)
    app.include_router(api_calibration.router)
    app.include_router(api_generate.router)

    @app.get("/login")
    def login(t: str = "") -> Response:  # pyright: ignore[reportUnusedFunction]
        if not hmac.compare_digest(t, token):
            return HTMLResponse("주소가 올바르지 않습니다. 앱을 다시 열어 주세요.", 403)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict")
        return resp

    web = web_dir or WEB_DIR
    index = web / "index.html"
    if (web / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

    @app.get("/")
    def root() -> Response:  # pyright: ignore[reportUnusedFunction]
        if not index.is_file():
            return HTMLResponse(_NOT_BUILT)
        return HTMLResponse(
            index.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"}
        )

    return app
