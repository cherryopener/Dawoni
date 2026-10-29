"""AI CLI 브리지의 공통 타입.

어댑터는 CLI마다 다른 부분(명령줄, stdin 형식, 출력 해석, 오류 분류)만 맡고,
프로세스 실행·타임아웃·격리 폴더는 runner가 공통으로 처리한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast

from dawoni.config import AgentName


class AgentErrorCode(StrEnum):
    NOT_INSTALLED = "agent_not_installed"
    NOT_LOGGED_IN = "agent_not_logged_in"
    QUOTA = "agent_quota"
    TIMEOUT = "agent_timeout"
    STALLED = "agent_stalled"
    PERMISSION_DENIED = "agent_permission_denied"
    PROCESS_FAILED = "agent_process_failed"
    BAD_OUTPUT = "agent_bad_output"
    CANCELLED = "agent_cancelled"


_USER_MESSAGES: dict[AgentErrorCode, str] = {
    AgentErrorCode.NOT_INSTALLED: "AI 프로그램(CLI)이 설치되어 있지 않습니다.",
    AgentErrorCode.NOT_LOGGED_IN: (
        "AI 계정 로그인이 필요합니다. 연결 마법사에서 다시 로그인해 주세요."
    ),
    AgentErrorCode.QUOTA: (
        "AI 사용량 한도에 도달했습니다. 잠시 후 다시 시도하거나 다른 AI로 전환하세요."
    ),
    AgentErrorCode.TIMEOUT: "AI 응답이 제한 시간 안에 끝나지 않았습니다.",
    AgentErrorCode.STALLED: "AI가 한동안 아무 응답이 없어 중단했습니다.",
    AgentErrorCode.PERMISSION_DENIED: (
        "AI가 허용되지 않은 작업(파일 쓰기·명령 실행)을 시도해 차단되었습니다."
    ),
    AgentErrorCode.PROCESS_FAILED: "AI 프로그램이 오류로 종료되었습니다.",
    AgentErrorCode.BAD_OUTPUT: "AI 응답을 해석할 수 없습니다.",
    AgentErrorCode.CANCELLED: "작업이 취소되었습니다.",
}

# 다른 AI로 바꿔 시도하면 해결될 수 있는 오류
SWITCHABLE_ERRORS = frozenset(
    {AgentErrorCode.NOT_INSTALLED, AgentErrorCode.NOT_LOGGED_IN, AgentErrorCode.QUOTA}
)


class AgentError(Exception):
    def __init__(
        self,
        code: AgentErrorCode,
        detail: str = "",
        *,
        agent: str | None = None,
        sandbox: Path | None = None,
    ) -> None:
        self.code = code
        self.detail = detail.strip()
        self.agent = agent
        self.sandbox = sandbox
        super().__init__(f"[{code}] {self.user_message}" + (f" — {self.detail}" if detail else ""))

    @property
    def user_message(self) -> str:
        return _USER_MESSAGES[self.code]


@dataclass(frozen=True)
class AgentRequest:
    prompt: str
    attachments: tuple[Path, ...] = ()
    json_schema: dict[str, Any] | None = None
    task: str = "adhoc"
    """작업 이름 (격리 폴더 이름·로그용)."""
    timeout_s: float | None = None
    idle_timeout_s: float | None = None
    """None이면 설정값 사용."""


@dataclass
class AgentUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass
class AgentResult:
    agent: AgentName
    model: str | None
    text: str
    """최종 응답 텍스트."""
    structured: Any | None
    """CLI가 스키마 강제로 돌려준 구조화 결과 (지원·사용한 경우)."""
    schema_enforced: bool
    usage: AgentUsage
    duration_s: float
    sandbox: Path
    warnings: list[str] = field(default_factory=lambda: list[str]())


@dataclass
class ParsedLine:
    """출력 한 줄의 해석 결과."""

    progress: str | None = None
    """진행 상황 메시지 (UI 표시용)."""
    text_delta: str = ""
    final: dict[str, Any] | None = None
    """최종 결과 이벤트 원본."""
    note: str | None = None
    """JSON이 아닌 줄 등 참고 메시지."""
    model: str | None = None
    """CLI가 실제로 쓴 모델 (시작 이벤트에서)."""


def reported_model(lines: list[ParsedLine]) -> str | None:
    return next((p.model for p in lines if p.model), None)


@dataclass
class FinalOutput:
    text: str
    structured: Any | None
    usage: AgentUsage
    model: str | None
    warnings: list[str]


@dataclass(frozen=True)
class Invocation:
    argv: list[str]
    stdin: bytes
    schema_enforced: bool


class AgentAdapter(Protocol):
    name: AgentName

    def build(
        self,
        exe: Path,
        prompt: str,
        *,
        model: str | None,
        schema_path: Path | None,
        schema: dict[str, Any] | None,
        attachments: list[Path],
        timeout_s: float,
        workdir: Path,
    ) -> Invocation: ...

    def parse_line(self, line: str) -> ParsedLine: ...

    def finish(
        self,
        lines: list[ParsedLine],
        returncode: int,
        stderr_tail: str,
        *,
        schema_requested: bool,
    ) -> FinalOutput:
        """프로세스 종료 후 결과 확정. 실패면 AgentError를 던진다."""
        ...


# ---------------------------------------------------------------- 공통 도우미


def json_object(line: str) -> dict[str, Any] | None:
    """한 줄을 JSON 객체로. 객체가 아니면 None."""
    s = line.strip()
    if not s.startswith("{"):
        return None
    try:
        v = json.loads(s)
    except json.JSONDecodeError:
        return None
    return cast(dict[str, Any], v) if isinstance(v, dict) else None


def as_dict(v: Any) -> dict[str, Any]:
    return cast(dict[str, Any], v) if isinstance(v, dict) else {}


def as_list(v: Any) -> list[Any]:
    return cast(list[Any], v) if isinstance(v, list) else []


def as_int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


_QUOTA_HINTS = (
    "quota",
    "rate limit",
    "rate_limit",
    "resource_exhausted",
    "resource exhausted",
    "429",
    "usage limit",
    "too many requests",
    "limit reached",
)
_AUTH_HINTS = (
    "not authenticated",
    "unauthenticated",
    "please run /login",
    "not logged in",
    "login required",
    "invalid api key",
    "401",
    "oauth token",
    "authentication",
)


_STALL_HINTS = ("stream was interrupted", "connection reset", "stream error")
"""실측(agy): 응답 도중 끊김 — 같은 요청을 새 세션으로 다시 하면 대개 된다."""


def classify_failure(message: str) -> AgentErrorCode:
    m = message.lower()
    if any(h in m for h in _QUOTA_HINTS):
        return AgentErrorCode.QUOTA
    if any(h in m for h in _AUTH_HINTS):
        return AgentErrorCode.NOT_LOGGED_IN
    if "timed out" in m or "timeout" in m:
        return AgentErrorCode.TIMEOUT
    if any(h in m for h in _STALL_HINTS):
        return AgentErrorCode.STALLED  # 일시적 — 엔진이 새 세션으로 한 번 더 한다
    return AgentErrorCode.PROCESS_FAILED


def attachment_note(rel_paths: list[str]) -> str:
    """프롬프트 끝에 붙이는 첨부 파일 안내."""
    if not rel_paths:
        return ""
    lines = "\n".join(f"- {p}" for p in rel_paths)
    return "\n\n[첨부 파일 — 현재 작업 폴더 기준 경로. 읽기 도구로 열어 내용을 확인할 것]\n" + lines
