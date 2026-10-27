"""서버 전체가 공유하는 상태: 앱 폴더·설정·작업공간 서비스·작업 실행기."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from dawoni.config import AppConfig, load_config, save_config
from dawoni.lecture.workflow import Loader
from dawoni.lecture.youtube import load_lecture
from dawoni.paths import AppPaths
from dawoni.server.jobs import JobManager
from dawoni.services import Services, open_services
from dawoni.sharing import machine_id
from dawoni.workspace import WorkspaceError


class NeedsSetup(Exception):
    """작업공간이 아직 정해지지 않음 → 화면은 연결 마법사로."""


@dataclass
class AppState:
    paths: AppPaths
    token: str
    jobs: JobManager
    lecture_loader: Loader = field(default=load_lecture)
    """유튜브 자막 받기 (시험에서는 가짜로 바꾼다)."""
    machine: str = ""
    """이 PC의 ID (공유 폴더의 '사용 중' 표식 이름)."""
    _services: Services | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def create(cls, paths: AppPaths, token: str) -> AppState:
        paths.ensure()
        cfg = load_config(paths)
        return cls(
            paths=paths, token=token, jobs=JobManager(cfg.max_parallel_jobs),
            machine=machine_id(paths),
        )  # fmt: skip

    def config(self) -> AppConfig:
        return load_config(self.paths)

    def save_config(self, cfg: AppConfig) -> None:
        save_config(self.paths, cfg)
        self.reset()

    def reset(self) -> None:
        """설정이 바뀌면 다음 요청에서 서비스를 새로 조립한다 (진행 중 작업은 원래 것을 계속 씀)."""
        with self._lock:
            self._services = None

    def services(self) -> Services:
        with self._lock:
            if self._services is None:
                cfg = load_config(self.paths)
                if cfg.workspace is None:
                    raise NeedsSetup("작업공간을 먼저 정하세요.")
                try:
                    self._services = open_services(cfg.workspace, paths=self.paths)
                except WorkspaceError as e:
                    raise NeedsSetup(str(e)) from e
            return self._services
