"""agy 출력 규약을 흉내 내는 가짜 CLI (테스트 전용).

동작은 환경 변수 FAKE_AGENT_MODE로 정한다:
  ok        stdin 메시지를 받아 JSON 응답 (스키마가 있으면 structured_output 포함)
  echo_len  받은 프롬프트 길이·첫 글자·첨부 파일 목록을 응답
  slow      0.2초 간격으로 진행 이벤트 10개 후 응답 (heartbeat가 끊지 않아야 함)
  stall     시작 이벤트 후 출력 없이 대기 (heartbeat가 끊어야 함)
  quota     한도 초과 오류 결과
  auth      인증 오류 결과
  denied    응답 없이 denied_actions만 남기고 종료
  garbage   JSON이 아닌 출력 후 비정상 종료
  crash     stderr에 메시지 후 exit 3
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def emit(obj: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def result(status: str = "SUCCESS", response: str = "", **extra: object) -> None:
    emit(
        {
            "event": "result",
            "result": {
                "conversation_id": "fake",
                "status": status,
                "response": response,
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                **extra,
            },
        }
    )


def main() -> int:
    mode = os.environ.get("FAKE_AGENT_MODE", "ok")
    args = sys.argv[1:]
    schema = args[args.index("--json-schema") + 1] if "--json-schema" in args else None
    line = sys.stdin.readline()
    msg = json.loads(line)
    assert msg["event"] == "user", msg
    content: str = msg["message"]["content"]

    emit({"event": "init", "init": {"model": "fake-model", "cwd": str(Path.cwd())}})

    if mode == "garbage":
        sys.stdout.write("이것은 JSON이 아님\n")
        return 1
    if mode == "crash":
        sys.stderr.write("fatal: something broke\n")
        return 3
    if mode == "stall":
        time.sleep(60)
        return 0
    if mode == "quota":
        result("ERROR", error="RESOURCE_EXHAUSTED: quota exceeded for model")
        return 0
    if mode == "auth":
        result("ERROR", error="Print mode: not authenticated")
        return 0
    if mode == "denied":
        sys.stdout.write("jetski: no output produced — a tool required permission\n")
        result("SUCCESS", "", denied_actions=[{"action": "write_file"}])
        return 0
    if mode == "slow":
        for i in range(10):
            time.sleep(0.2)
            emit({"event": "step_update", "step_update": {"step_type": "agent_response",
                  "state": "ACTIVE", "text_delta": f"{i}"}})  # fmt: skip
        result("SUCCESS", '{"done": true}')
        return 0
    if mode == "echo_len":
        inputs = sorted(p.name for p in Path("inputs").glob("*")) if Path("inputs").is_dir() else []
        body = {"length": len(content), "first": content[:1], "inputs": inputs}
        result("SUCCESS", json.dumps(body, ensure_ascii=False))
        return 0

    answer = {"ok": True, "echo": content[:10]}
    emit(
        {
            "event": "step_update",
            "step_update": {"step_type": "tool", "state": "ACTIVE", "tool_name": "read_file"},
        }
    )
    extra: dict[str, object] = {"structured_output": answer} if schema else {}
    result("SUCCESS", json.dumps(answer, ensure_ascii=False), **extra)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
