"""로컬 서버 보호.

다른 웹사이트가 브라우저를 통해 이 서버를 부르는 것(CSRF)과 DNS 리바인딩을 막는다.
- Host 헤더는 127.0.0.1 / localhost 만
- `/api`는 실행할 때마다 새로 만드는 토큰이 필요: 첫 화면 주소(`/login?t=…`)로 받은 토큰을
  `SameSite=Strict` 쿠키로 저장 → 다른 사이트에서 보낸 요청에는 쿠키가 실리지 않는다.
"""

from __future__ import annotations

import hmac
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

COOKIE = "dawoni_token"
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})


def new_token() -> str:
    return secrets.token_urlsafe(24)


class LocalOnly(BaseHTTPMiddleware):
    def __init__(self, app: object, token: str) -> None:
        super().__init__(app)  # pyright: ignore[reportArgumentType]
        self.token = token

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "허용되지 않은 주소"}, status_code=403)
        if request.url.path.startswith("/api/"):
            got = request.cookies.get(COOKIE, "")
            if not hmac.compare_digest(got, self.token):
                return JSONResponse(
                    {"detail": "세션이 없습니다. 앱을 다시 열어 주세요."}, status_code=401
                )
        return await call_next(request)
