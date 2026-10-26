"""앱 설정 (PC별, 앱 데이터 폴더의 config.json).

알 수 없는 키가 있어도 실패하지 않도록 `extra="ignore"`로 읽는다. 이전 버전 설정 파일을
새 버전이 읽어도 깨지지 않게 하기 위함이다. 저장은 항상 원자적 쓰기.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dawoni.fsio import read_json, write_model_atomic
from dawoni.paths import AppPaths

log = logging.getLogger(__name__)

AgentName = Literal["agy", "claude", "codex"]
AGENT_NAMES: tuple[AgentName, ...] = ("agy", "claude", "codex")


class _Settings(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class AgentSettings(_Settings):
    enabled: bool = True
    executable: str | None = None
    """직접 지정한 실행 파일 경로. 없으면 자동 탐지."""
    model: str | None = None
    """CLI에 넘길 모델 이름. 없으면 CLI 기본값."""
    timeout_s: int = Field(default=600, ge=10)
    """한 번의 호출 전체 제한 시간."""
    idle_timeout_s: int = Field(default=300, ge=5)
    """출력 이벤트가 이 시간 동안 없으면 멈춘 것으로 보고 중단.
    추론형 모델은 '생각'하는 동안 이벤트가 없어 2분을 넘기기도 한다 (M3 실측) → 기본 5분."""


def _default_agents() -> dict[AgentName, AgentSettings]:
    return {
        "agy": AgentSettings(model="gemini-3.8-flash-medium"),
        "claude": AgentSettings(),
        "codex": AgentSettings(enabled=False),
    }


class OcrSettings(_Settings):
    """외부 OCR은 사용자 키를 넣었을 때만 켜지는 선택 기능."""

    clova_enabled: bool = False
    clova_invoke_url: str | None = None
    clova_secret: str | None = None
    mathpix_enabled: bool = False
    mathpix_app_id: str | None = None
    mathpix_app_key: str | None = None
    cross_check: bool = True
    """손글씨 판독 시 두 번째 AI로 교차 판독."""


class DocxSettings(_Settings):
    body_font: str = "맑은 고딕"
    """본문 한글/영문 폰트. Windows 기본 + Mac용 Office 동봉."""
    serif_font: str = "바탕"
    math_font: str = "Cambria Math"
    body_size_pt: float = Field(default=10.0, gt=4, lt=40)


DEFAULT_SUBJECTS: tuple[str, ...] = (
    "대수",
    "기하",
    "정수",
    "조합",
    "물리",
    "화학",
    "생물",
    "지구과학",
)


class AppConfig(_Settings):
    schema_version: int = 1
    workspace: Path | None = None
    default_agent: AgentName = "agy"
    agents: dict[AgentName, AgentSettings] = Field(default_factory=_default_agents)
    ocr: OcrSettings = Field(default_factory=OcrSettings)
    docx: DocxSettings = Field(default_factory=DocxSettings)
    subjects: list[str] = Field(default_factory=lambda: list(DEFAULT_SUBJECTS))
    max_parallel_jobs: int = Field(default=1, ge=1, le=4)
    """동시에 돌리는 AI 호출 수. 구독 한도 소모 속도를 조절한다."""

    def agent(self, name: AgentName) -> AgentSettings:
        return self.agents.get(name) or AgentSettings()


def load_config(paths: AppPaths) -> AppConfig:
    """설정을 읽는다. 파일이 없으면 기본값, 깨져 있으면 백업 후 기본값."""
    path = paths.config_file
    if not path.exists():
        return AppConfig()
    try:
        return AppConfig.model_validate(read_json(path))
    except (ValueError, ValidationError) as e:
        backup = path.with_name(path.name + ".broken")
        path.replace(backup)
        log.warning("설정 파일이 손상되어 기본값으로 시작합니다. 백업: %s (%s)", backup, e)
        return AppConfig()


def save_config(paths: AppPaths, config: AppConfig) -> None:
    write_model_atomic(paths.config_file, config)
    if sys.platform != "win32":
        # OCR API 키가 들어 있을 수 있으므로 본인만 읽을 수 있게
        paths.config_file.chmod(0o600)
