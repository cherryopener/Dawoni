"""Claude Code CLI (`claude`) 어댑터.

격리 옵션 (claude 2.1.310 `--help` 기준):
- `-p` + stdin 텍스트, `--output-format stream-json --verbose`
- `--restricted`: 명령 실행 도구 제거, 사용자/프로젝트 설정 무시, 파일 도구를 작업 폴더로 제한
- `--safe-mode`: CLAUDE.md·스킬·플러그인·훅·MCP 등 사용자 지정 요소 끔
- `--tools Read,WebSearch,WebFetch`, `--permission-mode dontAsk`: 읽기·인터넷 검색만 허용,
  그 밖의 권한 요청은 묻지 않고 거부
- `--no-session-persistence`: 작업 1건 = 새 세션, 기록을 남기지 않음

`--json-schema`는 파일이 아니라 JSON 문자열을 받는다. Windows 명령행 길이 한계(약 32K)를
넘지 않도록 일정 크기 이상이면 넘기지 않고, 컨텍스트 엔진(M2)이 프롬프트에 스키마를 넣고
사후 검증한다 (`schema_enforced=False`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dawoni.agents.base import (
    RATE_LIMIT_WARNING,
    AgentError,
    AgentErrorCode,
    AgentUsage,
    FinalOutput,
    Invocation,
    ParsedLine,
    as_dict,
    as_int,
    as_list,
    classify_failure,
    json_object,
    reported_model,
)
from dawoni.config import AgentName

MAX_INLINE_SCHEMA_CHARS = 12_000
MAX_CMD_LINE_CHARS = 7_500
""".cmd·.bat를 거치면 cmd.exe 한계 8,191자 (보통은 `detect.unwrap_cmd_shim`이 진짜 exe로 피한다)."""

TOOLS = "Read,WebSearch,WebFetch"
"""읽기 + 인터넷 검색 (기출·올림피아드 원문 참고). `--restricted`는 WebFetch를 `--tools`에
적어야 남긴다."""


class ClaudeAdapter:
    name: AgentName = "claude"

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
        effort: str | None = None,
    ) -> Invocation:
        argv = [
            str(exe),
            "-p",
            "--input-format",
            "text",
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
            "--restricted",
            "--safe-mode",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--tools",
            TOOLS,
            "--allowedTools",
            TOOLS,
            "--permission-mode",
            "dontAsk",
        ]
        if model:
            argv += ["--model", model]
        if effort:
            argv += ["--effort", effort]
        enforced = False
        if schema is not None:
            inline = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
            via_cmd = exe.suffix.lower() in (".cmd", ".bat")
            room = MAX_CMD_LINE_CHARS - sum(len(a) + 3 for a in argv) - 20
            if len(inline) <= (
                min(room, MAX_INLINE_SCHEMA_CHARS) if via_cmd else MAX_INLINE_SCHEMA_CHARS
            ):
                argv += ["--json-schema", inline]
                enforced = True
        return Invocation(argv=argv, stdin=prompt.encode("utf-8"), schema_enforced=enforced)

    def parse_line(self, line: str) -> ParsedLine:
        obj = json_object(line)
        if obj is None:
            return ParsedLine(note=line.strip())
        kind = obj.get("type")
        if kind == "system" and obj.get("subtype") == "init":
            m = obj.get("model")
            model = m if isinstance(m, str) else None
            return ParsedLine(progress=f"모델 {model or '?'} 준비", model=model)
        if kind == "assistant":
            content = as_list(as_dict(obj.get("message")).get("content"))
            texts: list[str] = []
            progress = "응답 작성 중"
            for block in map(as_dict, content):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    texts.append(str(block["text"]))
                elif block.get("type") == "tool_use":
                    progress = f"도구 사용: {block.get('name', '?')}"
            return ParsedLine(progress=progress, text_delta="".join(texts))
        if kind == "result":
            return ParsedLine(final=obj)
        if kind == "rate_limit_event":
            status = as_dict(obj.get("rate_limit_info")).get("status")
            if status not in (None, "allowed"):  # allowed_warning, rejected …
                return ParsedLine(progress=RATE_LIMIT_WARNING, rate_limited=True)
        return ParsedLine()

    def finish(
        self,
        lines: list[ParsedLine],
        returncode: int,
        stderr_tail: str,
        *,
        schema_requested: bool,
    ) -> FinalOutput:
        notes = "\n".join(p.note for p in lines if p.note)
        finals = [p.final for p in lines if p.final is not None]
        if not finals:
            detail = "\n".join(x for x in (notes, stderr_tail) if x) or f"exit {returncode}"
            raise AgentError(classify_failure(detail), detail)
        res = finals[-1]
        result_text = res.get("result")
        text = result_text if isinstance(result_text, str) else ""
        if res.get("is_error") or res.get("subtype") != "success":
            detail = text or str(res.get("subtype")) or stderr_tail
            raise AgentError(classify_failure(detail), detail)

        denials = [
            str(as_dict(d).get("tool_name", "?")) for d in as_list(res.get("permission_denials"))
        ]
        warnings = [f"차단된 작업: {', '.join(denials)}"] if denials else []
        if any(p.rate_limited for p in lines):
            warnings.append(RATE_LIMIT_WARNING)
        structured = res.get("structured_output") if schema_requested else None
        if not text.strip() and structured is None:
            if denials:
                raise AgentError(AgentErrorCode.PERMISSION_DENIED, ", ".join(denials))
            raise AgentError(AgentErrorCode.BAD_OUTPUT, "빈 응답")

        u = as_dict(res.get("usage"))
        inp = as_int(u.get("input_tokens"))
        out = as_int(u.get("output_tokens"))
        cached = (as_int(u.get("cache_read_input_tokens")) or 0) + (
            as_int(u.get("cache_creation_input_tokens")) or 0
        )
        total = (inp or 0) + (out or 0) + cached if inp is not None or out is not None else None
        return FinalOutput(
            text=text.strip(),
            structured=structured,
            usage=AgentUsage(input_tokens=inp, output_tokens=out, total_tokens=total),
            model=reported_model(lines),
            warnings=warnings,
        )
