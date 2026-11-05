"""정해진 사람만 쓰게 하기 — Google·허용 명단은 가짜 HTTP로 확인한다."""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dawoai.access import (
    TOKEN_URL,
    USERINFO_URL,
    Access,
    AccessConfig,
    AccessDenied,
    load_access_config,
    parse_allowlist,
)
from dawoai.fsio import read_json, write_json_atomic
from dawoai.paths import AppPaths
from dawoai.server.app import create_app

LIST_URL = "https://example.test/allow.txt"
CFG = AccessConfig(
    google_client_id="cid",
    google_client_secret="sec",
    allowlist_url=LIST_URL,
    allowed_emails=["dev@x.com"],
)


class FakeGoogle:
    def __init__(self) -> None:
        self.allowlist = "teacher@x.com, 김선생  # 물리\n"
        self.email = "teacher@x.com"
        self.online = True
        self.refresh_ok = True
        self.scope = "openid https://www.googleapis.com/auth/drive.file email"
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def __call__(
        self, method: str, url: str, form: dict[str, str] | None, headers: dict[str, str]
    ) -> tuple[int, bytes]:
        self.calls.append((url, form))
        if not self.online:
            raise OSError("network down")
        if url == LIST_URL:
            return 200, self.allowlist.encode()
        if url == TOKEN_URL:
            assert form is not None
            if form["grant_type"] == "refresh_token" and not self.refresh_ok:
                return 400, b'{"error": "invalid_grant"}'
            tok = {"access_token": "at", "refresh_token": "rt", "scope": self.scope}
            return 200, json.dumps(tok).encode()
        if url == USERINFO_URL:
            return 200, json.dumps({"email": self.email, "email_verified": True}).encode()
        raise AssertionError(url)


def _access(app_paths: AppPaths, g: FakeGoogle, machine: str = "pc1") -> Access:
    return Access(CFG, app_paths, machine, http=g)


def _login(a: Access) -> str:
    url = a.start_login("http://127.0.0.1:9/auth/callback")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"] and q["scope"] == [
        "openid email https://www.googleapis.com/auth/drive.file"
    ]
    return a.finish_login("code", q["state"][0])


def test_parse_allowlist() -> None:
    assert parse_allowlist("A@x.com, 김다원\n# 주석\n b@x.com # 메모\nc@x.com\t이 원\n") == {
        "a@x.com": "김다원",
        "b@x.com": "",
        "c@x.com": "이 원",
    }
    assert parse_allowlist('["C@x.com, 박"]') == {"c@x.com": "박"}
    assert parse_allowlist('{"emails": [{"email": "D@x.com", "name": "최"}]}') == {"d@x.com": "최"}


def test_login_then_relaunch(app_paths: AppPaths) -> None:
    g = FakeGoogle()
    a = _access(app_paths, g)
    assert a.check_saved() == "needs_login" and not a.ok
    assert _login(a) == "teacher@x.com" and a.ok and a.name == "김선생"
    token_form = next(f for u, f in g.calls if u == TOKEN_URL)
    assert token_form is not None and token_form["code_verifier"]  # PKCE

    # 다시 켜면 브라우저 없이 Google에 확인만
    b = _access(app_paths, g)
    assert b.check_saved() == "ok" and b.email == "teacher@x.com"

    # 명단에서 빼면 다음 실행부터 막힘
    g.allowlist = "other@x.com"
    c = _access(app_paths, g)
    assert c.check_saved() == "blocked" and "명단에 없습니다" in (c.message or "")


def test_drive_permission_unchecked(app_paths: AppPaths) -> None:
    """Google 권한 화면에서 Drive 칸을 빼고 넘어가도 로그인은 되고, 시트는 못 쓴다고 알린다."""
    g = FakeGoogle()
    a = _access(app_paths, g)
    _login(a)
    assert a.ok and a.status()["sheets"] is True
    g.scope = "openid email"
    _login(a)
    assert a.ok and a.status()["sheets"] is False
    b = _access(app_paths, g)  # 다시 켜도 기억
    b.check_saved()
    assert b.status()["sheets"] is False


def test_not_allowed_login_is_refused(app_paths: AppPaths) -> None:
    g = FakeGoogle()
    g.email = "stranger@x.com"
    a = _access(app_paths, g)
    with pytest.raises(AccessDenied):
        _login(a)
    assert a.state == "blocked" and not a.session_file.exists()


def test_fixed_email_allowed_without_list(app_paths: AppPaths) -> None:
    g = FakeGoogle()
    g.email, g.allowlist = "dev@x.com", ""
    assert _login(_access(app_paths, g)) == "dev@x.com"


def test_state_must_match(app_paths: AppPaths) -> None:
    a = _access(app_paths, FakeGoogle())
    a.start_login("http://127.0.0.1:9/auth/callback")
    with pytest.raises(AccessDenied):
        a.finish_login("code", "forged")


def test_offline_grace(app_paths: AppPaths) -> None:
    g = FakeGoogle()
    _login(_access(app_paths, g))
    g.online = False
    a = _access(app_paths, g)
    assert a.check_saved() == "ok" and a.offline and a.name == "김선생"

    s = read_json(a.session_file)
    s["checked_at"] = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    write_json_atomic(a.session_file, s)
    assert _access(app_paths, g).check_saved() == "needs_login"


def test_edited_or_copied_session_is_not_trusted(app_paths: AppPaths) -> None:
    g = FakeGoogle()
    a = _access(app_paths, g)
    _login(a)
    # 다른 PC로 복사
    assert _access(app_paths, g, machine="pc2").check_saved() == "needs_login"
    # 이메일만 고쳐도 Google이 돌려주는 이메일과 달라 통과 못 함
    s: dict[str, Any] = read_json(a.session_file)
    s["email"] = "dev@x.com"
    write_json_atomic(a.session_file, s)
    assert _access(app_paths, g).check_saved() == "needs_login"
    # 토큰이 취소되면 다시 로그인
    g.refresh_ok = False
    assert _access(app_paths, g).check_saved() == "needs_login"


def test_no_config_means_open(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    assert load_access_config() is None  # conftest가 없는 파일을 가리킴
    f = tmp_path / "a.json"
    f.write_text(json.dumps({"google_client_id": "x"}), encoding="utf-8")
    monkeypatch.setenv("DAWOAI_ACCESS_FILE", str(f))
    cfg = load_access_config()
    assert cfg is not None and cfg.google_client_id == "x"


def test_server_gate(app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    g = FakeGoogle()
    a = _access(app_paths, g)
    a.check_saved()
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    c = TestClient(
        create_app(app_paths, "t", web_dir=app_paths.root / "x", access=a),
        base_url="http://127.0.0.1",
    )
    c.get("/login?t=t")
    r = c.get("/api/status")
    assert r.status_code == 401 and r.json()["code"] == "needs_login"
    assert c.get("/api/auth/status").json()["state"] == "needs_login"

    url: str = c.post("/api/auth/login").json()["url"]
    assert opened == [url] and "redirect_uri=http%3A%2F%2F127.0.0.1%2Fauth%2Fcallback" in url
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    # 기본 브라우저에서 돌아오는 요청에는 쿠키가 없다
    page = TestClient(c.app, base_url="http://127.0.0.1").get(
        f"/auth/callback?code=c&state={state}"
    )
    assert page.status_code == 200 and "teacher@x.com" in page.text
    assert c.get("/api/status").status_code == 200

    assert c.post("/api/auth/logout").json()["state"] == "needs_login"
    assert c.get("/api/status").status_code == 401


def _logged_in_client(app_paths: AppPaths, g: FakeGoogle, tmp_path: Any) -> TestClient:
    a = _access(app_paths, g)
    _login(a)
    c = TestClient(
        create_app(app_paths, "t", web_dir=app_paths.root / "x", access=a),
        base_url="http://127.0.0.1",
    )
    c.get("/login?t=t")
    assert c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True}).is_success
    return c


RULE = {"title": "단위", "text": "단위가 없으면 1점 감점", "applies_to": ["all"]}


def test_teacher_name_comes_from_allowlist(app_paths: AppPaths, tmp_path: Any) -> None:
    """배포본: 기록의 강사 이름은 명단에서 — 화면이 보낸 `by`·설정의 이름은 쓰지 않는다."""
    g = FakeGoogle()
    c = _logged_in_client(app_paths, g, tmp_path)
    assert c.patch("/api/config", json={"teacher": "가짜"}).is_success
    st = c.get("/api/status").json()
    assert st["teacher"] == "김선생" and st["teacher_locked"] is True
    assert c.get("/api/config").json()["teacher"] is None, "잠긴 동안 설정에 이름을 저장하지 않음"

    saved = c.post("/api/rules", json={"request": "r", "draft": RULE, "by": "가짜"}).json()
    assert saved["created_by"] == "김선생"
    # 아무 이름도 안 보내도 명단 이름
    nobody = c.post("/api/rules", json={"request": "r", "draft": RULE}).json()
    assert nobody["created_by"] == "김선생"


def test_teacher_name_falls_back_to_email(app_paths: AppPaths, tmp_path: Any) -> None:
    g = FakeGoogle()
    g.allowlist = "teacher@x.com\n"  # 이름 없는 줄
    c = _logged_in_client(app_paths, g, tmp_path)
    assert c.get("/api/status").json()["teacher"] == "teacher@x.com"
    saved = c.post("/api/rules", json={"request": "r", "draft": RULE, "by": "가짜"}).json()
    assert saved["created_by"] == "teacher@x.com"


def test_without_access_by_is_kept(app_paths: AppPaths, tmp_path: Any) -> None:
    """로그인이 꺼진 개발·직접 실행은 예전처럼 화면이 보낸 이름."""
    a = Access(None, app_paths, "pc1")
    c = TestClient(
        create_app(app_paths, "t", web_dir=app_paths.root / "x", access=a),
        base_url="http://127.0.0.1",
    )
    c.get("/login?t=t")
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    c.patch("/api/config", json={"teacher": "이선생"})
    st = c.get("/api/status").json()
    assert st["teacher"] == "이선생" and st["teacher_locked"] is False
    saved = c.post("/api/rules", json={"request": "r", "draft": RULE, "by": "이선생"}).json()
    assert saved["created_by"] == "이선생"


def test_every_recorded_name_goes_through_who() -> None:
    """서버가 `body.by`·`body.teacher`를 그대로 기록에 쓰는 곳이 새로 생기지 않게."""
    import re
    from pathlib import Path

    server = Path(__file__).resolve().parents[1] / "dawoai" / "server"
    raw = re.compile(r"(?<!who\()\bbody\.(by|teacher)\b")
    bad = [
        f"{p.name}:{i}"
        for p in server.glob("*.py")
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if raw.search(line)
    ]
    assert bad == []


def test_bundle_ignores_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """배포 묶음에서는 환경 변수로 로그인 설정을 바꿔(꺼) 버릴 수 없다."""
    import dawoai.access as access_mod

    f = tmp_path / "a.json"
    f.write_text(json.dumps({"google_client_id": "x"}), encoding="utf-8")
    monkeypatch.setenv("DAWOAI_ACCESS_FILE", str(f))
    assert load_access_config() is not None
    marker = tmp_path / "bundled.json"
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(access_mod, "BUNDLE_FILE", marker)
    monkeypatch.setattr(access_mod, "ACCESS_FILE", tmp_path / "none.json")
    assert access_mod.is_bundled()
    assert load_access_config() is None, "환경 변수를 무시하고 묶음 설정만"


def test_main_without_args_opens_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    import dawoai.__main__ as entry
    import dawoai.cli

    seen: list[list[str]] = []

    def fake_main(argv: list[str]) -> int:
        seen.append(list(argv))
        return 0

    monkeypatch.setattr(dawoai.cli, "main", fake_main)
    monkeypatch.setattr("sys.argv", ["dawoai"])
    assert entry.run() == 0
    monkeypatch.setattr("sys.argv", ["dawoai", "version"])
    entry.run()
    assert seen == [["ui"], ["version"]]
