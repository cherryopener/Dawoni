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

from dawoai.fsio import read_json, write_model_atomic
from dawoai.paths import AppPaths

log = logging.getLogger(__name__)

AgentName = Literal["agy", "claude", "codex", "local"]
AGENT_NAMES: tuple[AgentName, ...] = ("agy", "claude", "codex", "local")
"""`local`: CLI가 아니라 Ollama·LM Studio 같은 로컬 LLM 서버 (OpenAI 호환 주소, agents/local.py)."""

TierName = Literal["fast", "standard", "strong"]
"""모델 등급. 역할(dawoai/roles)마다 등급이 정해져 있고, 등급 → AI별 모델은 설정에서 고친다."""
TIER_NAMES: tuple[TierName, ...] = ("fast", "standard", "strong")
Effort = Literal["low", "medium", "high", "xhigh", "max"]


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
    base_url: str | None = None
    """로컬 LLM만: OpenAI 호환 주소. 없으면 Ollama 기본(http://localhost:11434/v1)."""
    api_key: str | None = None
    """로컬 LLM만: 서버가 키를 요구할 때 (보통 없음)."""


class TierModel(_Settings):
    """한 등급에서 이 AI가 쓸 모델과 생각 깊이. 둘 다 없으면 그 AI 설정의 `model`을 쓴다."""

    model: str | None = None
    effort: Effort | None = None
    """CLI의 추론 깊이 옵션 (claude `--effort`, codex `model_reasoning_effort`).
    agy는 모델 이름에 깊이가 들어 있다 (gemini-3.8-flash-low 등)."""


def _default_tiers() -> dict[AgentName, dict[TierName, TierModel]]:
    """등급별 기본 모델 — AI마다 비슷한 수준으로 맞춘다.

    agy: 2026-11-04 실측(README "역할별 모델 실측")에서 flash-medium과 argon-low가 정확도가 같았다.
    argon은 flash와 구독 한도가 따로라, 대부분의 호출은 flash-medium에 두고 strong(검증·보정)만
    argon-low로 보내 두 한도를 나눠 쓴다 (사용자 결정, 2026-11-04). flash-low는 표지 회전·시험지
    읽기에서 틀려 쓰지 않는다. 강사가 설정에서 바꿀 수 있다.
    codex는 모델 이름을 모르므로 깊이만 정하고 모델은 codex 설정을 따른다."""
    return {
        "agy": {
            "fast": TierModel(model="gemini-3.8-flash-medium"),
            "standard": TierModel(model="gemini-3.8-flash-medium"),
            "strong": TierModel(model="gemini-4-argon-low"),
        },
        "claude": {
            "fast": TierModel(model="claude-haiku-5-5"),
            "standard": TierModel(model="claude-sonnet-5-5", effort="medium"),
            "strong": TierModel(model="claude-sonnet-5-5", effort="high"),
        },
        "codex": {
            "fast": TierModel(effort="low"),
            "standard": TierModel(effort="medium"),
            "strong": TierModel(effort="high"),
        },
    }


def _default_agents() -> dict[AgentName, AgentSettings]:
    return {
        "agy": AgentSettings(model="gemini-3.8-flash-medium"),
        "claude": AgentSettings(),
        "codex": AgentSettings(enabled=False),
        # 로컬 모델은 생각이 느리고 PC 사양에 따라 더 걸린다
        "local": AgentSettings(enabled=False, timeout_s=1200, idle_timeout_s=600),
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


class AppConfig(_Settings):
    schema_version: int = 1
    workspace: Path | None = None
    default_agent: AgentName = "agy"
    teacher: str | None = None
    """이 PC를 쓰는 강사 이름 — 문항 맡기·확정·승인 기록에 남는다 (처음 실행 마법사에서 묻는다)."""
    coordinator_agent: AgentName | None = None
    """총괄 AI를 맡을 AI (강사마다 구독이 달라 강사가 고른다, PLAN.md §15-6). None = 기본 AI."""
    agents: dict[AgentName, AgentSettings] = Field(default_factory=_default_agents)
    tiers: dict[AgentName, dict[TierName, TierModel]] = Field(default_factory=_default_tiers)
    """등급 → AI별 모델. 없는 칸은 그 AI 설정의 `model`을 쓴다 (모델 이름을 짐작하지 않는다)."""
    role_tiers: dict[str, TierName] = Field(default_factory=lambda: dict[str, TierName]())
    """역할별 등급 바꾸기 (예: {"grader": "strong"}). 없으면 역할 기본 등급."""
    auto_fallback: bool = False
    """AI가 한도 초과·로그인 필요·미설치일 때 다음 AI로 저절로 넘어갈까.
    기본은 멈추고 묻는다: 2026-10-30 agy 한도가 끝나자 호출 103건(약 340만 토큰)이 조용히
    claude로 넘어가 claude 주간 한도까지 다 썼다."""
    ocr: OcrSettings = Field(default_factory=OcrSettings)
    scanner: ScannerSettings = Field(default_factory=ScannerSettings)
    max_parallel_jobs: int = Field(default=1, ge=1, le=4)
    """동시에 돌리는 AI 호출 수. 구독 한도 소모 속도를 조절한다."""
    parallel_questions: int = Field(default=3, ge=1, le=6)
    """한 작업 안에서 동시에 부르는 AI 수 (해설·루브릭 문항, PDF 쪽). 1이면 차례로."""
    parallel_students: int = Field(default=4, ge=1, le=6)
    """답안지 묶음 판독에서 동시에 읽는 학생 수 (표지·손글씨). M3 실측(24명, agy flash-medium)에서
    4명씩 한도 오류 없이 돌았다 (M4에서 기본값으로)."""
    parallel_grading: int = Field(default=4, ge=1, le=6)
    """채점에서 동시에 부르는 AI 수 (학생 × 문항). 한도 신호를 보면 저절로 줄인다."""
    second_opinion: Literal["off", "low", "all"] = "low"
    """재채점 (PLAN.md §2-4 4번): low = 확신도 낮음·수리가 있었던 채점만 새 세션으로 한 번 더,
    all = 모두, off = 안 함. 두 번째 채점이 다르면 '검토 필요'."""
    second_agent: AgentName | None = None
    """재채점에 쓸 AI (None = 첫 채점과 같은 AI, 새 세션)."""
    second_model: str | None = None

    @field_validator("agents", mode="after")
    @classmethod
    def _all_agents(cls, v: dict[AgentName, AgentSettings]) -> dict[AgentName, AgentSettings]:
        """예전 설정 파일에 없는 AI는 기본값으로 — 일부만 고쳐 저장해도 저절로 켜지지 않게."""
        return {**_default_agents(), **v}

    def agent(self, name: AgentName) -> AgentSettings:
        """예전 설정 파일에 없는 AI(로컬 LLM 등)는 기본값 — 저절로 켜지지 않게."""
        return self.agents.get(name) or _default_agents()[name]

    def tier_model(self, name: AgentName, tier: TierName | None) -> TierModel:
        """이 AI가 이 등급에서 쓸 모델·깊이. 정해지지 않은 칸은 AI 설정의 모델로 채운다."""
        t = self.tiers.get(name, {}).get(tier) if tier else None
        model = (t.model if t else None) or self.agent(name).model
        return TierModel(model=model, effort=t.effort if t else None)


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
