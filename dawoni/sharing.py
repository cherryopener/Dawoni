"""작업공간을 혼자 쓰는가, 공유 폴더(Google Drive·OneDrive·Dropbox·iCloud 등)로 함께 쓰는가.

공유 폴더는 동기화 프로그램이 파일을 옮길 뿐이라, 앱이 할 일은 셋이다:
1. PC마다 다른 것(설정·로그·캐시·색인)은 작업공간 밖 앱 데이터 폴더에 둔다 (paths.py).
2. 같은 시험을 다른 강사가 열어 두었으면 알린다 — 시험 폴더의 `.inuse/<PC>.json` 표식.
3. 두 PC가 같은 파일을 동시에 고쳐 동기화 프로그램이 만든 **충돌 사본**을 찾아 알린다.
"""

from __future__ import annotations

import re
import socket
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import Field

from dawoni.fsio import read_json, write_model_atomic
from dawoni.models.base import DawoniModel, utcnow
from dawoni.paths import AppPaths, WorkspacePaths

FRESH = timedelta(minutes=3)
"""표식이 이보다 오래되면 (창을 닫았거나 PC가 꺼짐) 쓰는 중으로 보지 않는다."""

_PROVIDERS = (
    (re.compile(r"google ?drive|googledrive|내 드라이브|my drive", re.I), "Google Drive"),
    (re.compile(r"onedrive", re.I), "OneDrive"),
    (re.compile(r"dropbox", re.I), "Dropbox"),
    (re.compile(r"mobile documents|icloud|com~apple~clouddocs", re.I), "iCloud Drive"),
    (re.compile(r"/library/cloudstorage/", re.I), "클라우드 폴더"),
)


def cloud_provider(path: Path) -> str | None:
    """경로로 짐작한 동기화 서비스 이름 (모르면 None — NAS·공유 드라이브는 알 수 없음)."""
    s = path.expanduser().as_posix() + "/"
    for pat, name in _PROVIDERS:
        if pat.search(s):
            return name
    return None


# ---------------------------------------------------------------- PC·사용 중 표식


def machine_id(paths: AppPaths) -> str:
    """이 PC(앱 데이터 폴더)의 고정 ID."""
    p = paths.root / "machine-id"
    if p.is_file() and (v := p.read_text(encoding="utf-8").strip()):
        return v
    v = uuid.uuid4().hex[:12]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(v, encoding="utf-8")
    return v


class Presence(DawoniModel):
    machine: str
    teacher: str | None = None
    host: str = Field(default_factory=socket.gethostname)
    at: datetime = Field(default_factory=utcnow)


def _inuse_dir(ws: WorkspacePaths, exam_id: str) -> Path:
    return ws.exams / exam_id / ".inuse"


def mark_in_use(ws: WorkspacePaths, exam_id: str, machine: str, teacher: str | None) -> None:
    write_model_atomic(
        _inuse_dir(ws, exam_id) / f"{machine}.json", Presence(machine=machine, teacher=teacher)
    )


def release(ws: WorkspacePaths, exam_id: str, machine: str) -> None:
    (_inuse_dir(ws, exam_id) / f"{machine}.json").unlink(missing_ok=True)


def others_using(
    ws: WorkspacePaths, exam_id: str, machine: str, *, now: datetime | None = None
) -> list[Presence]:
    d = _inuse_dir(ws, exam_id)
    if not d.is_dir():
        return []
    now = now or utcnow()
    out: list[Presence] = []
    for p in d.glob("*.json"):
        try:
            x = Presence.model_validate(read_json(p))
        except ValueError:
            continue  # 동기화 중 반쯤 받은 파일 등
        if x.machine != machine and now - x.at <= FRESH:
            out.append(x)
    return sorted(out, key=lambda x: x.at, reverse=True)


# ---------------------------------------------------------------- 충돌 사본

_COPY = re.compile(
    r"^(?P<base>.+?)"
    # Drive "(1)", Dropbox "(… conflicted copy …)"
    r"(?: ?\((?:\d+|[^)]*(?:conflict|충돌|복사본|copy)[^)]*)\)"
    r"| \d+"  # iCloud "이름 2"
    r"|-(?:DESKTOP|LAPTOP|MACBOOK|PC)[-\w]*"  # OneDrive "이름-DESKTOP-AB12"
    r")$",
    re.I,
)
_SKIP_DIRS = {".inuse", "_render", ".git"}


class ConflictCopy(DawoniModel):
    path: str = Field(description="작업공간 기준 상대 경로")
    original: str


def conflict_copies(ws: WorkspacePaths, *, limit: int = 200) -> list[ConflictCopy]:
    """동기화 프로그램이 만든 충돌 사본. 원본 파일이 같은 폴더에 있을 때만 사본으로 본다."""
    out: list[ConflictCopy] = []
    for p in sorted(ws.root.rglob("*")):
        if not p.is_file() or _SKIP_DIRS & set(p.relative_to(ws.root).parts):
            continue
        m = _COPY.match(p.stem)
        if not m:
            continue
        orig = p.with_name(m["base"].rstrip() + p.suffix)
        if orig != p and orig.is_file():
            rel = p.relative_to(ws.root).as_posix()
            out.append(ConflictCopy(path=rel, original=orig.relative_to(ws.root).as_posix()))
            if len(out) >= limit:
                break
    return out
