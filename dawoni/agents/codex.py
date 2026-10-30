"""OpenAI Codex CLI (`codex`) 어댑터.

주의: 이 PC에 codex가 설치되어 있지 않아 **실측하지 못했다**. 공개 문서 기준으로 작성했고,
설치 후 `dawoni agent test --cli codex`로 확인해야 한다.

- `codex exec --json -`: 프롬프트를 stdin으로 읽고 JSONL 이벤트를 출력
- `--sandbox read-only`, `--skip-git-repo-check`, `-C <작업 폴더>`
- 이미지는 `-i <파일>`로 직접 첨부
- `--output-schema <파일>`: 마지막 응답을 스키마로 강제
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dawoni.agents.base import (
    AgentError,
    AgentErrorCode,
    AgentUsage,
    FinalOutput,
    Invocation,
    ParsedLine,
    as_dict,
    as_int,
    classify_failure,
    json_object,
)
from dawoni.config import AgentName

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


class CodexAdapter:
    name: AgentName = "codex"

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
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "-C",
            str(workdir),
        ]
        if model:
            argv += ["-m", model]
        if effort:
            argv += ["-c", f'model_reasoning_effort="{effort}"']
        if schema_path is not None:
            argv += ["--output-schema", str(schema_path)]
        for rel in attachments:
            if rel.suffix.lower() in _IMAGE_SUFFIXES:
                argv += ["-i", str(workdir / rel)]
        argv.append("-")
        return Invocation(
            argv=argv, stdin=prompt.encode("utf-8"), schema_enforced=schema_path is not None
        )

    def parse_line(self, line: str) -> ParsedLine:
        obj = json_object(line)
        if obj is None:
            return ParsedLine(note=line.strip())
        kind = obj.get("type")
        if kind == "item.completed":
            item = as_dict(obj.get("item"))
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                return ParsedLine(progress="응답 작성 중", text_delta=str(item["text"]))
            return ParsedLine(progress=f"단계 완료: {item.get('type', '?')}")
        if kind in ("turn.completed", "turn.failed", "error"):
            return ParsedLine(final=obj)
        return ParsedLine()

    def finish(
        self,
        lines: list[ParsedLine],
        returncode: int,
        stderr_tail: str,
        *,
        schema_requested: bool,
    ) -> FinalOutput:
        finals = [p.final for p in lines if p.final is not None]
        errors = [f for f in finals if f.get("type") in ("turn.failed", "error")]
        if errors or returncode != 0:
            e = errors[-1] if errors else {}
            msg = e.get("message") or as_dict(e.get("error")).get("message") or stderr_tail
            detail = str(msg or f"exit {returncode}")
            raise AgentError(classify_failure(detail), detail)
        messages = [p.text_delta for p in lines if p.text_delta]
        if not messages:
            raise AgentError(AgentErrorCode.BAD_OUTPUT, "응답 메시지 없음")
        text = messages[-1].strip()
        structured: Any | None = None
        if schema_requested:
            try:
                structured = json.loads(text)
            except json.JSONDecodeError:
                structured = None
        u = as_dict(next((f.get("usage") for f in reversed(finals) if f.get("usage")), None))
        inp, out = as_int(u.get("input_tokens")), as_int(u.get("output_tokens"))
        total = (inp or 0) + (out or 0) if inp is not None or out is not None else None
        return FinalOutput(
            text=text,
            structured=structured,
            usage=AgentUsage(input_tokens=inp, output_tokens=out, total_tokens=total),
            model=None,
            warnings=[],
        )
