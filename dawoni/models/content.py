"""문항 본문을 이루는 런(run)과 블록(block).

수식은 `$…$` 구분자를 쓰지 않고 `MathRun`으로 분리해 저장한다. AIHelper에서 반복되던
`$ x $` 공백 문제·조사 붙음 문제가 구조적으로 생기지 않는다.

런의 텍스트와 수식에는 변수 자리표시 `\\var{식}`을 쓸 수 있다. 예: `\\var{m}`, `\\var{2*m}`.
동형문제(L1)에서 변수 값을 바꾸면 이 자리가 다시 계산된다.
`{{m}}` 같은 이중 중괄호는 LaTeX(`\\frac{1}{{{a}}}`, `x^{{2}}`)와 충돌하므로 쓰지 않는다.

AI 출력 토큰을 아끼기 위해 런은 짧은 키(`t`, `v`)를, 블록은 `type`을 판별자로 쓴다.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import Field, field_validator

from dawoni.models.base import DawoniModel

_DOUBLED_BEGIN = re.compile(r"\\\\begin\{")
_DOUBLED_CMD = re.compile(r"\\\\(?=[A-Za-z])")


def fix_overescaped(latex: str) -> str:
    """AI가 JSON 이스케이프를 한 번 더 해 `\\\\text{…}`처럼 쓴 식 → `\\text{…}` (M7 실측: gemini).

    - `\\\\begin{`이 있으면 식 전체가 두 겹 → 모든 `\\\\`를 `\\`로
    - 환경(`\\begin`)이 없으면 줄바꿈 `\\\\`가 의미 없으므로, 글자 앞의 `\\\\`만 `\\`로
    - 환경이 있으면(행렬·정렬의 줄바꿈 `\\\\`) 그대로 둔다
    """
    if _DOUBLED_BEGIN.search(latex):
        return latex.replace("\\\\", "\\")
    if "\\begin{" in latex:
        return latex
    return _DOUBLED_CMD.sub(r"\\", latex)


class TextRun(DawoniModel):
    t: Literal["text"]
    v: str
    bold: bool = False
    underline: bool = False
    """수능 발문의 '옳은 것', '않은 것' 밑줄 등."""


class MathRun(DawoniModel):
    t: Literal["math"]
    v: str = Field(description="LaTeX 수식. `$` 구분자 없이 내용만.")

    @field_validator("v")
    @classmethod
    def _strip_delimiters(cls, v: str) -> str:
        s = v.strip()
        # AI가 습관적으로 붙이는 구분자를 벗겨 낸다: $..$, $$..$$, \(..\), \[..\]
        for left, right in (("$$", "$$"), ("$", "$"), ("\\(", "\\)"), ("\\[", "\\]")):
            if s.startswith(left) and s.endswith(right) and len(s) >= len(left) + len(right):
                s = s[len(left) : len(s) - len(right)].strip()
                break
        if not s:
            raise ValueError("빈 수식")
        return fix_overescaped(s)


Run = Annotated[TextRun | MathRun, Field(discriminator="t")]


def text(v: str, *, bold: bool = False, underline: bool = False) -> TextRun:
    return TextRun(t="text", v=v, bold=bold, underline=underline)


def math(v: str) -> MathRun:
    return MathRun(t="math", v=v)


class ParagraphBlock(DawoniModel):
    type: Literal["paragraph"]
    runs: list[Run] = Field(min_length=1)
    align: Literal["left", "center", "right"] = "left"


class DisplayMathBlock(DawoniModel):
    """가운데 정렬 별행 수식."""

    type: Literal["display_math"]
    latex: str

    @field_validator("latex")
    @classmethod
    def _strip(cls, v: str) -> str:
        return MathRun(t="math", v=v).v


class FigureBlock(DawoniModel):
    """본문 안에서 그림이 놓이는 자리."""

    type: Literal["figure"]
    figure_id: str
    width: float | None = Field(default=None, gt=0, le=1, description="단 너비 대비 비율")


class LabeledItem(DawoniModel):
    label: str = Field(min_length=1, description="ㄱ, ㄴ, (가), (나) 등")
    runs: list[Run] = Field(min_length=1)


class BogiBlock(DawoniModel):
    """<보기> 상자 (ㄱ, ㄴ, ㄷ 진술). 렌더러는 판별하지 않고 이 구조를 그대로 그린다."""

    type: Literal["bogi"]
    title: str = "보기"
    items: list[LabeledItem] = Field(min_length=1)


class ConditionsBlock(DawoniModel):
    """(가), (나) 조건 목록 (수학 문항에서 흔함)."""

    type: Literal["conditions"]
    boxed: bool = True
    items: list[LabeledItem] = Field(min_length=1)


class BoxBlock(DawoniModel):
    """테두리 상자 안의 지문·자료."""

    type: Literal["box"]
    title: str | None = None
    paragraphs: list[list[Run]] = Field(min_length=1)


class TableBlock(DawoniModel):
    """실험 결과표 등. 셀은 런 목록."""

    type: Literal["table"]
    rows: list[list[list[Run]]] = Field(min_length=1)
    header_rows: int = Field(default=0, ge=0)


Block = Annotated[
    ParagraphBlock
    | DisplayMathBlock
    | FigureBlock
    | BogiBlock
    | ConditionsBlock
    | BoxBlock
    | TableBlock,
    Field(discriminator="type"),
]


def paragraph(*runs: TextRun | MathRun) -> ParagraphBlock:
    return ParagraphBlock(type="paragraph", runs=list(runs))


_PLACEHOLDER = re.compile(r"\\var\{([^{}]*)\}")


def placeholders_in(s: str) -> list[str]:
    """문자열 안의 `\\var{식}` 내용 목록."""
    return [m.strip() for m in _PLACEHOLDER.findall(s)]


def iter_runs(blocks: list[Block]) -> list[TextRun | MathRun]:
    """블록 목록 안의 모든 런을 순서대로 모은다."""
    out: list[TextRun | MathRun] = []
    for b in blocks:
        match b:
            case ParagraphBlock():
                out.extend(b.runs)
            case BogiBlock() | ConditionsBlock():
                for it in b.items:
                    out.extend(it.runs)
            case BoxBlock():
                for p in b.paragraphs:
                    out.extend(p)
            case TableBlock():
                for row in b.rows:
                    for cell in row:
                        out.extend(cell)
            case DisplayMathBlock():
                out.append(math(b.latex))
            case FigureBlock():
                pass
    return out


def figure_ids_in(blocks: list[Block]) -> list[str]:
    return [b.figure_id for b in blocks if isinstance(b, FigureBlock)]


_HANGUL = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
_INLINE_MATH = re.compile(r"\\\((.+?)\\\)|(?<![\\$])\$(?!\$)(.+?)(?<!\\)\$", re.DOTALL)


def runs_from_text(s: str) -> list[TextRun | MathRun]:
    """AI가 쓴 문장 안의 `\\( … \\)`·`$ … $`를 수식 런으로 나눈다 (해설 설명, 판독 줄 등)."""
    out: list[TextRun | MathRun] = []
    pos = 0
    for m in _INLINE_MATH.finditer(s):
        latex = (m.group(1) or m.group(2) or "").strip()
        if not latex or (m.group(2) is not None and _HANGUL.search(latex)):
            continue  # `$5와 $3` 같은 금액 표기는 수식이 아님
        if m.start() > pos:
            out.append(text(s[pos : m.start()]))
        out.append(math(latex))
        pos = m.end()
    if pos < len(s):
        out.append(text(s[pos:]))
    return out
