"""앱 설정 (PC별, 앱 데이터 폴더의 config.json).

알 수 없는 키가 있어도 실패하지 않도록 `extra="ignore"`로 읽는다. 이전 버전 설정 파일을
새 버전이 읽어도 깨지지 않게 하기 위함이다. 저장은 항상 원자적 쓰기.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

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
    idle_timeout_s: int = Field(default=480, ge=5)
    """출력 이벤트가 이 시간 동안 없으면 멈춘 것으로 보고 중단.
    추론형 모델은 '생각'하는 동안 이벤트가 없다: M3 실측 2분 → 5분으로 했으나, 실제 시험지
    (그림 많은 쪽 구조화·해설)에서 agy가 출력 없이 4~5분 생각하다 끊겼다 → 기본 8분.
    호출 전체 제한(`timeout_s`, 작업별 10~15분)은 그대로라 진짜 멈춤도 그 안에서 끝난다."""


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


DEFAULT_SCANNER_URL = "smb://192.168.0.100/share/folder"
"""학원 복합기의 스캔본 공유 폴더 (고급박스)."""
DEFAULT_SCANNER_USER = "Administrator"


class ScannerSettings(_Settings):
    """복합기 스캔 폴더 (공유 폴더, SMB).

    비밀번호는 저장하지 않는다 — 처음 열 때 운영체제(Finder·탐색기)가 묻고 키체인·자격 증명
    관리자에 암호화해 저장한다. 이 설정 파일은 평문이라 비밀번호를 두지 않는다.
    """

    smb_url: str | None = Field(
        default=DEFAULT_SCANNER_URL,
        description="예: smb://192.168.0.100/share/folder (복합기 고급박스 공유 폴더)",
    )
    username: str | None = DEFAULT_SCANNER_USER

    @field_validator("smb_url")
    @classmethod
    def _smb(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip().replace("\\", "/")
        if v.startswith("//"):
            v = "smb:" + v
        if not v.lower().startswith("smb://"):
            raise ValueError("smb://주소 형식이어야 합니다 (예: smb://192.168.0.100)")
        return v.rstrip("/")


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
    scanner: ScannerSettings = Field(default_factory=ScannerSettings)
    subjects: list[str] = Field(default_factory=lambda: list(DEFAULT_SUBJECTS))
    max_parallel_jobs: int = Field(default=1, ge=1, le=4)
    """동시에 돌리는 AI 호출 수. 구독 한도 소모 속도를 조절한다."""
    parallel_questions: int = Field(default=3, ge=1, le=6)
    """한 작업 안에서 동시에 처리하는 문항 수 (해설·루브릭·문제은행 풀이). 1이면 차례로."""

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
