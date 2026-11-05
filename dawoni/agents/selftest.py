"""연결 테스트: 동봉 이미지 1장을 읽고 구조화된 JSON으로 답하게 한다.

확인하는 것: CLI 실행, stdin 전달, 권한 승인 없이 이미지 읽기, 한글·수식 인식, JSON 스키마 응답.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dawoni.agents.base import AgentRequest, AgentResult, as_dict, as_list

SELFTEST_IMAGE = Path(__file__).resolve().parent.parent / "assets" / "selftest.png"

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["instruction", "equation_latex", "solution_x", "legs", "hypotenuse"],
    "properties": {
        "instruction": {"type": "string", "description": "그림 맨 위의 한국어 지시문"},
        "equation_latex": {"type": "string", "description": "방정식 (LaTeX, $ 없이)"},
        "solution_x": {"type": "number"},
        "legs": {"type": "array", "items": {"type": "number"}, "description": "두 직각변"},
        "hypotenuse": {"type": "number", "description": "빗변 길이(계산)"},
    },
}

PROMPT = """너는 Dawoni의 연결 테스트를 수행한다.
첨부된 이미지 한 장을 읽기 도구로 열어 보고 아래 항목을 JSON 객체 하나로만 답하라.
파일을 만들거나 고치지 말고, 명령을 실행하지 말 것. 설명 문장 없이 JSON만 출력.

- instruction: 이미지 맨 위 한국어 지시문을 그대로
- equation_latex: 이미지의 방정식을 LaTeX로 ($ 기호 없이)
- solution_x: 그 방정식의 해
- legs: 직각삼각형의 두 직각변 길이
- hypotenuse: 피타고라스 정리로 계산한 빗변 길이
"""


def selftest_request() -> AgentRequest:
    return AgentRequest(
        prompt=PROMPT,
        attachments=(SELFTEST_IMAGE,),
        json_schema=SCHEMA,
        task="selftest",
        timeout_s=240,
    )


def _extract_json(text: str) -> Any | None:
    """응답 텍스트에서 JSON 객체를 뽑는다 (스키마 강제를 못 쓴 경우 대비, M2에서 일반화)."""
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s.split("\n", 1)[1] if "\n" in s else s
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return None


@dataclass
class SelftestReport:
    passed: bool
    answer: dict[str, Any]
    problems: list[str] = field(default_factory=lambda: list[str]())


def evaluate(result: AgentResult) -> SelftestReport:
    data = as_dict(
        result.structured if result.structured is not None else _extract_json(result.text)
    )
    problems: list[str] = []
    if not data:
        return SelftestReport(False, {}, ["JSON 응답을 찾지 못함"])

    instruction = str(data.get("instruction", ""))
    if "방정식" not in instruction:
        problems.append(f"한글 지시문 인식 실패: {instruction!r}")
    eq = str(data.get("equation_latex", "")).replace(" ", "")
    if "2x+5=11" not in eq or "$" in eq:
        problems.append(f"방정식 인식 실패: {eq!r}")

    def num(v: Any) -> float | None:
        return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None

    if num(data.get("solution_x")) != 3:
        problems.append(f"해 오답: {data.get('solution_x')!r}")
    legs = sorted(x for x in (num(v) for v in as_list(data.get("legs"))) if x is not None)
    if legs != [3, 4]:
        problems.append(f"직각변 인식 실패: {data.get('legs')!r}")
    if num(data.get("hypotenuse")) != 5:
        problems.append(f"빗변 오답: {data.get('hypotenuse')!r}")
    return SelftestReport(not problems, data, problems)
