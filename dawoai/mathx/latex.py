"""AI가 쓴 LaTeX의 짜임새 검사 (변환기 없이, 코드만).

다워니는 식마다 pandoc으로 Word 수식(OMML)을 만들어 보고 실패를 수리 요청했다. 다워이는 DOCX를
쓰지 않으므로 pandoc을 싣지 않는다. 출력 시점에 식이 깨져 나오지 않도록, 저장 전에 괄호·환경 짝처럼
어느 변환기로도 실패할 것만 잡는다. 한글 수식 변환기(PLAN.md §5-3)가 생기면 그 실패도 여기에 더한다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_ENV = re.compile(r"\\(begin|end)\s*\{([^{}]*)\}")
_LEFT_RIGHT = re.compile(r"\\(left|right)(?![A-Za-z])")


def _braces(s: str) -> str | None:
    depth = 0
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\":  # \{ \} \\ 는 글자
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return "닫는 중괄호 }가 남음"
        i += 1
    return "중괄호 {가 닫히지 않음" if depth else None


def _envs(s: str) -> str | None:
    stack: list[str] = []
    for kind, name in _ENV.findall(s):
        if kind == "begin":
            stack.append(name)
        elif not stack or stack.pop() != name:
            return f"\\end{{{name}}}의 짝이 맞지 않음"
    return f"\\begin{{{stack[-1]}}}이 닫히지 않음" if stack else None


def _left_right(s: str) -> str | None:
    depth = 0
    for kind in _LEFT_RIGHT.findall(s):
        depth += 1 if kind == "left" else -1
        if depth < 0:
            return "\\right의 짝 \\left가 없음"
    return "\\left의 짝 \\right가 없음" if depth else None


def latex_error(latex: str) -> str | None:
    """깨진 곳 한 줄, 문제가 없으면 None."""
    s = latex.strip()
    if not s:
        return "빈 식"
    if "$" in s.replace("\\$", ""):
        return "식 안에 $가 있음 (식 구분 기호는 빼고 쓸 것)"
    return _braces(s) or _envs(s) or _left_right(s)


def latex_problems(latexes: Iterable[str]) -> list[str]:
    """깨진 식마다 한 줄 (AI 수리 요청·출력 전 검사용)."""
    out: list[str] = []
    for latex in dict.fromkeys(latexes):
        if err := latex_error(latex):
            out.append(f"수식 `{latex}`의 LaTeX가 깨짐 ({err}). 표준 LaTeX로 고칠 것")
    return out
