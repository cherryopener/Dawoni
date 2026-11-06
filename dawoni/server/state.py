"""서버 전체가 공유하는 상태: 앱 폴더·설정·작업공간 서비스·작업 실행기."""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass, field
from typing import overload

from dawoni.access import Access, load_access_config
from dawoni.config import AppConfig, load_config, save_config
from dawoni.paths import AppPaths
from dawoni.server.jobs import JobManager
from dawoni.server.lifeline import Lifeline
from dawoni.services import Services, open_services
from dawoni.sharing import machine_id
from dawoni.workspace import WorkspaceError


class NeedsSetup(Exception):
    """작업공간이 아직 정해지지 않음 → 화면은 연결 마법사로."""


class NotLoggedIn(Exception):
    """로그인이 켜진 배포본에서 아직 로그인하지 않음 (기록에 남길 강사 이름이 없음)."""


@dataclass
class AppState:
    paths: AppPaths
    token: str
    jobs: JobManager
    machine: str = ""
    """이 PC의 ID (공유 폴더의 '사용 중' 표식 이름)."""
    access: Access | None = None
    """정해진 사람만 쓰게 하기 (Google 로그인 + 허용 명단). 설정 파일이 없으면 검사하지 않음."""
    lifeline: Lifeline = field(default_factory=Lifeline)
    """화면 창이 살아 있는지 (닫히면 `dawoni ui`가 서버를 끝낸다)."""
    _services: Services | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def create(cls, paths: AppPaths, token: str, *, access: Access | None = None) -> AppState:
        paths.ensure()
        cfg = load_config(paths)
        st = cls(
            paths=paths, token=token, jobs=JobManager(cfg.max_parallel_jobs),
            machine=machine_id(paths),
        )  # fmt: skip
        if access is None:
            # 앱 데이터 폴더를 통째로 다른 PC에 복사해도 로그인이 따라가지 않게 PC 이름도 묶는다
            access = Access(load_access_config(), paths, f"{st.machine}@{socket.gethostname()}")
            threading.Thread(target=access.check_saved, daemon=True).start()
        st.access = access
        return st

    @property
    def teacher_locked(self) -> bool:
        """로그인이 켜진 배포본: 강사 이름은 허용 명단에서 온다 (화면에서 고칠 수 없음)."""
        return self.access is not None and self.access.required

    @overload
    def who(self, by: str) -> str: ...
    @overload
    def who(self, by: str | None) -> str | None: ...
    def who(self, by: str | None) -> str | None:
        """기록에 남길 강사 이름.

        로그인이 켜져 있으면 화면이 보낸 `by`를 믿지 않고 허용 명단의 이름을 쓴다."""
        if not self.teacher_locked:
            return by
        assert self.access is not None
        name = self.access.teacher
        if (
            name is None
        ):  # 로그인 전 요청은 LocalOnly가 막지만, 혹시 지나와도 다른 이름을 쓰지 않는다
            raise NotLoggedIn("로그인이 필요합니다")
        return name

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
