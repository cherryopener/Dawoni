"""정해진 사람만 쓰게 하기 — Google 로그인 + 허용 명단.

설정 파일 `dawoai/assets/access.json` (git에 올리지 않는다 — 저장소가 공개라서. 배포 빌드는
GitHub 비밀값 `DAWOAI_ACCESS_JSON`으로 만든다)::

    {
      "google_client_id": "….apps.googleusercontent.com",
      "google_client_secret": "…",
      "allowlist_url": "https://gist.githubusercontent.com/…/raw/allowlist.txt",
      "allowed_emails": ["개발자@gmail.com, 김다원"],
      "offline_days": 7
    }

파일이 없으면 검사하지 않는다 (개발 중·직접 실행).

흐름:
1. 처음: 기본 브라우저로 Google 로그인 → `http://127.0.0.1:<port>/auth/callback`으로 돌아온다
   (앱 창 안에서는 Google이 로그인을 막으므로 바깥 브라우저). 받은 refresh token과 이메일을
   앱 데이터 폴더 `auth.json`에 둔다 (이 PC 전용 — 다른 PC로 복사하면 다시 로그인).
2. 켤 때마다: refresh token으로 Google에 이메일을 다시 확인하고 허용 명단을 새로 받는다 →
   명단에서 빼면 다음 실행부터 못 쓴다. `auth.json`의 이메일만 고쳐서는 통과할 수 없다.
3. 인터넷이 안 되면 마지막 확인 뒤 `offline_days`일까지는 받아 둔 명단으로 허용한다.

허용 명단(`allowlist_url`)은 한 줄에 `이메일, 강사 이름` 하나(# 뒤는 주석, 이름은 빼도 됨)
또는 JSON 목록. 강사 이름은 루브릭 승인·판독 확인·점수 확인 기록에 남는다. 개발자가 그
파일만 고치면 다시 빌드하지 않고 사람을 더하거나 뺄 수 있다. `allowed_emails`는 명단을 못
받아도 늘 허용 (개발자 본인, 같은 형식).

한계: 실행 파일이 사용자 손에 있으므로, 작정하고 프로그램을 뜯어 고치면 이 검사를 건너뛸 수
있다. 막는 것은 "파일을 복사해 남에게 주면 그냥 돌아가는 것"이다.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dawoai.agents.base import as_dict, as_list
from dawoai.fsio import read_json, write_json_atomic
from dawoai.paths import AppPaths

log = logging.getLogger(__name__)

ACCESS_FILE = Path(__file__).resolve().parent / "assets" / "access.json"
BUNDLE_FILE = Path(__file__).resolve().parent / "assets" / "bundled.json"
"""배포 묶음(packaging/build_bundle.py)이 넣는 표식 — 버전·커밋·만든 때."""
ENV_ACCESS_FILE = "DAWOAI_ACCESS_FILE"
"""시험·개발용으로 설정 파일 위치를 바꾼다 (배포본에서는 무시)."""


def is_bundled() -> bool:
    return bool(getattr(sys, "frozen", False)) or BUNDLE_FILE.is_file()


AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
SCOPES = f"openid email {DRIVE_SCOPE}"
"""이메일 + 이 앱이 만든 Drive 파일만 (채점 결과 구글 시트). 둘 다 민감하지 않은 범위라 Google 앱
심사가 필요 없다. 강사의 다른 Drive 파일은 읽을 수 없다."""

AccessState = Literal["off", "checking", "ok", "needs_login", "blocked"]


class AccessConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    google_client_id: str
    google_client_secret: str = ""
    allowlist_url: str | None = None
    allowed_emails: list[str] = Field(default_factory=lambda: list[str]())
    offline_days: int = Field(default=7, ge=0, le=60)


def load_access_config() -> AccessConfig | None:
    # 배포본에서는 환경 변수로 바꿀 수 없다 — 바꿀 수 있으면 그것만으로 검사를 끌 수 있다
    override = None if is_bundled() else os.environ.get(ENV_ACCESS_FILE)
    path = Path(override or ACCESS_FILE)
    if not path.is_file():
        return None
    cfg = AccessConfig.model_validate(read_json(path))
    return cfg if cfg.google_client_id.strip() else None


# ---------------------------------------------------------------- HTTP (시험에서 바꿔 끼운다)

Http = Callable[[str, str, dict[str, str] | None, dict[str, str]], tuple[int, bytes]]
"""(메서드, 주소, 폼 데이터, 헤더) → (상태 코드, 본문). 연결 실패는 OSError."""


def _http(
    method: str, url: str, form: dict[str, str] | None, headers: dict[str, str]
) -> tuple[int, bytes]:
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class AccessDenied(Exception):
    pass


class Offline(Exception):
    """Google·명단 서버에 닿지 않음 (인터넷 끊김)."""


def _entry(line: str) -> tuple[str, str] | None:
    """`이메일, 이름` / `이메일 이름` / `이메일` → (소문자 이메일, 이름)."""
    line = line.split("#", 1)[0].strip()
    if not line:
        return None
    email, _, name = line.replace(",", " ", 1).replace("\t", " ").partition(" ")
    return email.strip().lower(), name.strip().strip(",").strip()


def parse_allowlist(text: str) -> dict[str, str]:
    """허용 명단 → {이메일: 강사 이름(없으면 빈 칸)}."""
    s = text.strip()
    out: dict[str, str] = {}
    if s.startswith(("[", "{")):
        data: Any = json.loads(s)
        items = as_list(as_dict(data).get("emails")) if isinstance(data, dict) else as_list(data)
        for x in items:
            if isinstance(x, dict):
                d = as_dict(x)
                email = str(d.get("email") or "").strip().lower()
                if email:
                    out[email] = str(d.get("name") or "").strip()
            elif (e := _entry(str(x))) is not None:
                out[e[0]] = e[1]
        return out
    for line in s.splitlines():
        if (e := _entry(line)) is not None:
            out[e[0]] = e[1]
    return out


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class _Pending:
    verifier: str
    redirect_uri: str


def _drive_granted(scope: object) -> bool | None:
    return None if not isinstance(scope, str) else DRIVE_SCOPE in scope.split()


class Access:
    """이 PC에서 지금 다워이를 써도 되는가. 서버(화면)와 명령줄이 함께 쓴다."""

    def __init__(
        self,
        config: AccessConfig | None,
        paths: AppPaths,
        machine: str,
        *,
        http: Http = _http,
    ) -> None:
        self.config = config
        self.paths = paths
        self.machine = machine
        self.http = http
        self.state: AccessState = "off" if config is None else "checking"
        self.email: str | None = None
        self.name: str | None = None
        """허용 명단에 적힌 강사 이름."""
        self.message: str | None = None
        self.offline = False
        self.drive: bool | None = None
        """로그인 때 Drive 파일(구글 시트) 권한까지 허용했는가. Google 권한 화면에서 이 칸은
        강사가 체크를 빼고 넘어갈 수 있다. 모르면 None (예전 로그인)."""
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()

    @property
    def required(self) -> bool:
        return self.config is not None

    @property
    def ok(self) -> bool:
        return self.state in ("off", "ok")

    @property
    def teacher(self) -> str | None:
        """기록에 남길 강사 이름: 명단의 이름, 명단에 이름이 비었으면 이메일. 로그인 전이면 None."""
        if self.config is None or self.state != "ok":
            return None
        return self.name or self.email

    @property
    def session_file(self) -> Path:
        return self.paths.root / "auth.json"

    # ------------------------------------------------------------ 저장된 로그인

    def _read_session(self) -> dict[str, Any] | None:
        try:
            data = read_json(self.session_file)
        except (OSError, ValueError):
            return None
        session = as_dict(data)
        if not session or session.get("machine") != self.machine:
            return None  # 다른 PC에서 복사해 온 로그인은 쓰지 않는다
        return session

    def _write_session(self, data: dict[str, Any]) -> None:
        write_json_atomic(self.session_file, {**data, "machine": self.machine})
        if sys.platform != "win32":
            self.session_file.chmod(0o600)

    def _call(
        self,
        method: str,
        url: str,
        form: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        try:
            return self.http(method, url, form, headers or {})
        except OSError as e:  # 연결 실패·시간 초과 (URLError도 OSError)
            raise Offline(str(e)) from e

    def _json(self, status: int, body: bytes) -> dict[str, Any]:
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        return data if isinstance(data, dict) else {}  # pyright: ignore[reportUnknownVariableType]

    def _allowlist(self) -> dict[str, str]:
        """명단을 새로 받는다 ({이메일: 이름}). 받을 수 없으면 Offline."""
        assert self.config is not None
        fixed = parse_allowlist("\n".join(self.config.allowed_emails))
        if not self.config.allowlist_url:
            return fixed
        status, body = self._call("GET", self.config.allowlist_url)
        if status != 200:
            raise Offline(f"허용 명단을 받지 못함 (HTTP {status})")
        try:
            listed = parse_allowlist(body.decode("utf-8"))
            # 명단에 이름이 없으면 고정 명단의 이름을 쓴다
            return {**fixed, **{e: n or fixed.get(e, "") for e, n in listed.items()}}
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise Offline(f"허용 명단 형식 오류: {e}") from e

    def _email_from_token(self, access_token: str) -> str:
        status, body = self._call(
            "GET", USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        info = self._json(status, body)
        email = str(info.get("email") or "").strip().lower()
        if status != 200 or not email:
            raise AccessDenied("Google 계정 정보를 받지 못했습니다. 다시 로그인하세요.")
        if info.get("email_verified") is False:
            raise AccessDenied(f"{email}은(는) Google에서 확인되지 않은 이메일입니다.")
        return email

    def _allow(self, email: str, allowed: dict[str, str]) -> None:
        if email not in allowed:
            self._set("blocked", email, f"{email} 계정은 다워이 사용 허가 명단에 없습니다.")
            raise AccessDenied(self.message or "")
        self._set("ok", email, None)
        self.name = allowed[email] or None
        if self.name is None:
            log.warning(
                "허용 명단에 %s의 이름이 없어 이메일로 기록합니다 — 명단에 이름을 적어 주세요",
                email,
            )

    def _set(self, state: AccessState, email: str | None, message: str | None) -> None:
        self.state, self.email, self.message = state, email, message
        self.name = None

    def check_saved(self) -> AccessState:
        """켤 때: 저장된 로그인을 Google·허용 명단으로 다시 확인한다."""
        if self.config is None:
            return self.state
        with self._lock:
            try:
                self._check_saved()
            except AccessDenied:
                pass
            except Exception as e:  # 확인 중 뜻밖의 오류도 '못 씀'으로 — 열어 두지 않는다
                log.exception("로그인 확인 실패")
                self._set("needs_login", None, f"로그인 확인 중 오류: {e}")
        return self.state

    def _check_saved(self) -> None:
        assert self.config is not None
        self.offline = False
        s = self._read_session()
        if s is None or not s.get("refresh_token") or not s.get("email"):
            self._set("needs_login", None, None)
            return
        email = str(s["email"])
        self.drive = _drive_granted(s.get("scope"))
        try:
            status, body = self._call(
                "POST",
                TOKEN_URL,
                {
                    "client_id": self.config.google_client_id,
                    "client_secret": self.config.google_client_secret,
                    "refresh_token": str(s["refresh_token"]),
                    "grant_type": "refresh_token",
                },
            )
            tok = self._json(status, body)
            if status != 200 or not tok.get("access_token"):
                self._set("needs_login", None, "로그인이 만료되었습니다. 다시 로그인하세요.")
                return
            if self._email_from_token(str(tok["access_token"])) != email:
                self._set("needs_login", None, "저장된 로그인이 맞지 않습니다. 다시 로그인하세요.")
                return
            allowed = self._allowlist()
        except Offline as e:
            self._offline(s, email, str(e))
            return
        self._write_session({**s, "checked_at": _now().isoformat(), "allowed": allowed})
        self._allow(email, allowed)

    def _offline(self, s: dict[str, Any], email: str, why: str) -> None:
        assert self.config is not None
        try:
            checked = datetime.fromisoformat(str(s.get("checked_at")))
        except ValueError:
            checked = None
        raw = s.get("allowed")
        cached = (
            {str(k): str(v) for k, v in as_dict(raw).items()}
            if isinstance(raw, dict)
            else {str(x): "" for x in as_list(raw)}
        )
        limit = timedelta(days=self.config.offline_days)
        if checked is not None and _now() - checked <= limit and email in cached:
            self.offline = True
            self._set("ok", email, f"인터넷에 연결되지 않아 지난 확인으로 허용합니다 ({why})")
            self.name = cached[email] or None
            return
        self._set(
            "needs_login",
            None,
            f"로그인을 확인하려면 인터넷 연결이 필요합니다 "
            f"(마지막 확인 뒤 {self.config.offline_days}일 지남): {why}",
        )

    # ------------------------------------------------------------ 새로 로그인

    def start_login(self, redirect_uri: str) -> str:
        """Google 로그인 주소. PKCE로 가로챈 코드를 쓸 수 없게 한다."""
        assert self.config is not None
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        state = secrets.token_urlsafe(24)
        with self._lock:
            self._pending = {state: _Pending(verifier, redirect_uri)}  # 마지막 시도만 유효
        q = {
            "client_id": self.config.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent select_account",
        }
        return f"{AUTH_URL}?{urllib.parse.urlencode(q)}"

    def finish_login(self, code: str, state: str) -> str:
        """Google에서 돌아온 코드로 로그인을 끝낸다. 허용된 이메일. 아니면 AccessDenied."""
        assert self.config is not None
        with self._lock:
            pending = self._pending.pop(state, None)
            if pending is None:
                raise AccessDenied("로그인 요청이 맞지 않습니다. 다워이 창에서 다시 시도하세요.")
            try:
                status, body = self._call(
                    "POST",
                    TOKEN_URL,
                    {
                        "client_id": self.config.google_client_id,
                        "client_secret": self.config.google_client_secret,
                        "code": code,
                        "code_verifier": pending.verifier,
                        "redirect_uri": pending.redirect_uri,
                        "grant_type": "authorization_code",
                    },
                )
                tok = self._json(status, body)
                if status != 200 or not tok.get("access_token"):
                    raise AccessDenied(f"Google 로그인 실패: {tok.get('error') or status}")
                email = self._email_from_token(str(tok["access_token"]))
                allowed = self._allowlist()
            except Offline as e:
                self._set("needs_login", None, f"인터넷 연결을 확인하세요: {e}")
                raise AccessDenied(self.message or "") from e
            self._allow(email, allowed)
            self.drive = _drive_granted(tok.get("scope"))
            self._write_session(
                {
                    "email": email,
                    "scope": tok.get("scope"),
                    "refresh_token": tok.get("refresh_token"),
                    "checked_at": _now().isoformat(),
                    "allowed": allowed,
                }
            )
            return email

    def access_token(self) -> str:
        """구글 시트 등에 쓸 짧은 토큰 (저장된 로그인으로 새로 받는다)."""
        if self.config is None:
            raise AccessDenied(
                "Google 연결 설정이 없습니다 — 로그인 설정(access.json)이 있는 배포 앱에서 쓰세요"
            )
        s = self._read_session()
        if s is None or not s.get("refresh_token"):
            raise AccessDenied("Google 로그인이 필요합니다")
        try:
            status, body = self._call(
                "POST",
                TOKEN_URL,
                {
                    "client_id": self.config.google_client_id,
                    "client_secret": self.config.google_client_secret,
                    "refresh_token": str(s["refresh_token"]),
                    "grant_type": "refresh_token",
                },
            )
        except Offline as e:
            raise AccessDenied(f"인터넷 연결을 확인하세요: {e}") from e
        tok = self._json(status, body)
        if status != 200 or not tok.get("access_token"):
            raise AccessDenied("Google 로그인이 만료되었습니다 — 로그아웃 후 다시 로그인하세요")
        return str(tok["access_token"])

    def logout(self) -> None:
        with self._lock:
            with contextlib.suppress(FileNotFoundError):
                self.session_file.unlink()
            if self.config is not None:
                self._set("needs_login", None, None)

    def status(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "state": self.state,
            "email": self.email,
            "name": self.name,
            "message": self.message,
            "offline": self.offline,
            "sheets": self.drive,
        }
