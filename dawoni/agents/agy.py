"""Antigravity CLI (`agy`) 어댑터.

2026-10-25 agy 1.4.7 실측 결과:
- 일반 텍스트 stdin은 무시된다. `--input-format stream-json --output-format stream-json`으로
  `{"event":"user","message":{"content":"…"}}` 한 줄을 넣어야 하며, 이때 `-p`는 쓰지 않는다.
- 출력: `init` → `step_update`(text_delta, tool 사용) … → `result`
  (`status`, `response`, `structured_output`, `usage`, `denied_actions`).
- 작업 폴더 안 파일은 `read_file` 도구로 승인 없이 읽는다 (이미지 포함).
- 비대화 모드는 파일 쓰기·변경 명령을 자동 거부하고 `denied_actions`에 남긴다(멈추지 않음).
  `--sandbox`는 명령 실행을 추가로 제한한다. `--dangerously-skip-permissions`는 쓰지 않는다.
- `--mode plan`은 계획 문서를 따로 쓰며 토큰을 약 4배 써서 쓰지 않는다.
- `--json-schema <파일>`을 주면 `result.structured_output`에 검증된 객체가 온다.
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
    as_list,
    classify_failure,
    json_object,
    reported_model,
)
from dawoni.config import AgentName


class AgyAdapter:
    name: AgentName = "agy"

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
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--sandbox",
            "--disable-slash-commands",
            "--print-timeout",
            f"{int(timeout_s)}s",
        ]
        if model:
            argv += ["--model", model]
        if effort:
            argv += ["--effort", effort]
        if schema_path is not None:
            argv += ["--json-schema", str(schema_path)]
        msg = {"event": "user", "message": {"content": prompt}}
        stdin = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        return Invocation(argv=argv, stdin=stdin, schema_enforced=schema_path is not None)

    def parse_line(self, line: str) -> ParsedLine:
        obj = json_object(line)
        if obj is None:
            return ParsedLine(note=line.strip())
        event = obj.get("event")
        if event == "init":
            m = as_dict(obj.get("init")).get("model")
            model = m if isinstance(m, str) else None
            return ParsedLine(progress=f"모델 {model or '?'} 준비", model=model)
        if event == "step_update":
            step = as_dict(obj.get("step_update"))
            delta = step.get("text_delta")
            text = delta if isinstance(delta, str) else ""
            progress: str | None = None
            if step.get("step_type") == "tool" and step.get("state") == "ACTIVE":
                progress = f"도구 사용: {step.get('tool_name', '?')}"
            elif step.get("step_type") == "agent_response" and step.get("state") == "ACTIVE":
                progress = "응답 작성 중"
            # 도구 출력 등 응답 본문이 아닌 텍스트는 버린다
            if step.get("step_type") != "agent_response":
                text = ""
            return ParsedLine(progress=progress, text_delta=text)
        if event == "result":
            return ParsedLine(final=as_dict(obj.get("result")))
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
        status = res.get("status")
        error = res.get("error")
        if status != "SUCCESS":
            detail = str(error or notes or stderr_tail or status)
            raise AgentError(classify_failure(detail), detail)

        denied = [str(as_dict(d).get("action", "?")) for d in as_list(res.get("denied_actions"))]
        warnings = [f"차단된 작업: {', '.join(denied)}"] if denied else []
        response = res.get("response")
        text = response if isinstance(response, str) else ""
        if not text.strip():
            # 응답 본문 없이 끝난 경우: 대개 허용되지 않은 도구를 쓰려다 거부됨
            if denied:
                raise AgentError(AgentErrorCode.PERMISSION_DENIED, ", ".join(denied))
            text = "".join(p.text_delta for p in lines)
        structured = res.get("structured_output") if schema_requested else None

        usage_d = as_dict(res.get("usage"))
        usage = AgentUsage(
            input_tokens=as_int(usage_d.get("input_tokens")),
            output_tokens=as_int(usage_d.get("output_tokens")),
            total_tokens=as_int(usage_d.get("total_tokens")),
        )
        return FinalOutput(
            text=text.strip(),
            structured=structured,
            usage=usage,
            model=reported_model(lines),
            warnings=warnings,
        )
