"""앱 데이터 폴더와 작업공간 폴더 구조.

- 앱 데이터(설정·로그·로컬 색인·캐시): PC마다 따로. 팀 공유 대상이 아님.
- 작업공간(원본·문항 DB·출력물): 로컬 폴더 또는 Google Drive 동기화 폴더. 팀 공유 대상.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "Dawoni"
ENV_HOME = "DAWONI_HOME"  # 테스트·휴대용 실행 시 앱 데이터 폴더를 덮어쓴다


def app_data_dir() -> Path:
    if override := os.environ.get(ENV_HOME):
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / APP_NAME
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / APP_NAME.lower()


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def index_db(self) -> Path:
        """작업공간 색인(SQLite). Drive 위에 두면 동기화 충돌로 깨지므로 로컬에 둔다."""
        return self.root / "index.sqlite3"

    @property
    def sandboxes(self) -> Path:
        """AI CLI 호출마다 만드는 격리 작업 폴더의 상위 폴더."""
        return self.root / "sandboxes"

    def ensure(self) -> None:
        for p in (self.root, self.logs, self.cache, self.sandboxes):
            p.mkdir(parents=True, exist_ok=True)

    @classmethod
    def default(cls) -> AppPaths:
        return cls(app_data_dir())


WORKSPACE_MARKER = "dawoni-workspace.json"


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path

    @property
    def marker(self) -> Path:
        return self.root / WORKSPACE_MARKER

    @property
    def sources(self) -> Path:
        return self.root / "sources"

    @property
    def bank(self) -> Path:
        return self.root / "bank"

    @property
    def units(self) -> Path:
        """과목별 공통 단원표 (문제은행 분류 기준)."""
        return self.root / "units"

    @property
    def concepts(self) -> Path:
        return self.root / "concepts"

    @property
    def rubrics(self) -> Path:
        return self.root / "rubrics"

    @property
    def scopes(self) -> Path:
        """범위 프로필 (허용 개념·기법)."""
        return self.root / "scopes"

    @property
    def rules(self) -> Path:
        """강사 전역 규칙 (ON/OFF)."""
        return self.root / "rules"

    @property
    def exams(self) -> Path:
        return self.root / "exams"

    @property
    def students(self) -> Path:
        return self.root / "students"

    @property
    def lectures(self) -> Path:
        return self.root / "lectures"

    @property
    def templates(self) -> Path:
        return self.root / "templates"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def scan_inbox(self) -> Path:
        """복합기에서 받은 답안지 스캔본 (PDF)을 강사가 옮겨 두는 곳."""
        return self.root / "스캔본"

    def all_dirs(self) -> tuple[Path, ...]:
        return (
            self.scan_inbox,
            self.sources,
            self.bank,
            self.units,
            self.concepts,
            self.rubrics,
            self.scopes,
            self.rules,
            self.exams,
            self.students,
            self.lectures,
            self.templates,
            self.exports,
        )

    def item_dir(self, item_id: str) -> Path:
        """문항 폴더. 과목·단원은 바뀔 수 있으므로 경로에 넣지 않고 ID만 쓴다(이동 시 충돌 방지)."""
        return self.bank / item_id
