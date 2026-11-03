"""로그인 API — 허용된 Google 계정만 다워이를 쓴다 (dawoai/access.py).

Google 로그인은 앱 창이 아니라 기본 브라우저에서 한다 (앱 창 안의 로그인은 Google이 막음).
돌아오는 주소 `/auth/callback`은 기본 브라우저가 부르므로 `/api` 쿠키가 없다 —
`state`·PKCE 값으로 이 앱이 시작한 로그인인지 확인한다.
"""

from __future__ import annotations

import html
import webbrowser
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from dawoai.access import Access, AccessDenied
from dawoai.server.api_setup import State
from dawoai.server.state import AppState

router = APIRouter()


def _access(st: AppState) -> Access:
    assert st.access is not None
    return st.access


@router.get("/api/auth/status")
def auth_status(st: AppState = State) -> dict[str, Any]:
    return _access(st).status()


@router.post("/api/auth/login")
def auth_login(request: Request, st: AppState = State) -> dict[str, Any]:
    """Google 로그인 주소를 만들어 기본 브라우저로 연다 (안 열리면 화면에 주소를 보인다)."""
    a = _access(st)
    if not a.required:
        return {"url": None}
    base = str(request.base_url).rstrip("/")
    url = a.start_login(f"{base}/auth/callback")
    webbrowser.open(url)
    return {"url": url}


@router.post("/api/auth/logout")
def auth_logout(st: AppState = State) -> dict[str, Any]:
    a = _access(st)
    a.logout()
    return a.status()


_PAGE = """<!doctype html><meta charset="utf-8"><title>다워이 로그인</title>
<body style="font-family:sans-serif;padding:48px;line-height:1.6">
<h2>{title}</h2><p>{body}</p></body>"""


@router.get("/auth/callback")
def auth_callback(
    st: AppState = State, code: str = "", state: str = "", error: str = ""
) -> HTMLResponse:
    a = _access(st)
    if error or not code:
        msg = "Google 로그인을 취소했거나 실패했습니다." + (f" ({error})" if error else "")
        return HTMLResponse(_PAGE.format(title="로그인하지 않았습니다", body=html.escape(msg)), 400)
    try:
        email = a.finish_login(code, state)
    except AccessDenied as e:
        body = html.escape(str(e)) + "<br>허가가 필요하면 개발자에게 이 이메일을 알려 주세요."
        return HTMLResponse(_PAGE.format(title="사용할 수 없습니다", body=body), 403)
    body = f"{html.escape(email)} — 이 창을 닫고 다워이 창으로 돌아가세요."
    if a.drive is False:
        body += (
            "<br><br><b>Google Drive 파일 권한을 허용하지 않았습니다.</b> 채점 결과를 구글 시트로"
            " 내보내려면 다워이에서 로그아웃 후 다시 로그인하고, 권한 화면에서 'Google Drive"
            " 파일' 칸에 체크하세요."
        )
    return HTMLResponse(_PAGE.format(title="로그인했습니다", body=body))
