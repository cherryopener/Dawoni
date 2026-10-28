"""문항 제작 (M9, PLAN §9): 동형문제(수치형·미지수형·조건 변형), 신규 A(같은 범위·다른 기법),
신규 B(확장 개념 + 제시문).

제작물은 항상 **문항 + 범위 해설 + 루브릭 초안** 세트이고, 초안으로 저장해 강사가 승인한다.
AI 출력 모델(`*Template`, `*Draft`)과 저장 모델(`GeneratedSet`)을 나눈다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from dawoni.models.base import DawoniModel, new_id, utcnow
from dawoni.models.grading import ExamPart, RubricDraft, ScopedSolution, SolutionDraft
from dawoni.models.item import ItemContent
from dawoni.models.scope import ExtensionConcept

VariantKind = Literal["numeric", "symbolic", "condition", "novel_a", "novel_b"]
"""기준 문항이 있는 제작."""
GenKind = Literal["numeric", "symbolic", "condition", "novel_a", "novel_b", "new"]
GEN_KIND_LABELS: dict[GenKind, str] = {
    "numeric": "동형 · 수치형",
    "symbolic": "동형 · 미지수형",
    "condition": "동형 · 조건 변형",
    "novel_a": "신규 A · 다른 기법",
    "novel_b": "신규 B · 확장 개념",
    "new": "새 문제 · 강사 아이디어",
}

AnswerFormat = Literal["choice5", "bogi", "short", "essay"]
ANSWER_FORMAT_LABELS: dict[AnswerFormat, str] = {
    "choice5": "객관식 (5지선다)",
    "bogi": "ㄱㄴㄷ 합답형",
    "short": "단답형",
    "essay": "서술형",
}

# ---------------------------------------------------------------- AI 출력


class NumericTemplate(DawoniModel):
    """수치형: 숫자를 변수로 바꾼 문항. 값은 코드가 고른다."""

    item: ItemContent = Field(
        description="수치를 `\\var{변수}`로 바꾼 문항. variables에 원래 값(value)과 바꿀 수 있는 "
        "범위(min·max·step) 또는 후보(choices), constraints에 성립 조건"
    )
    answer_expr: str = Field(description="정답을 변수로 계산하는 sympy 식 (예: (a+b)/2)")
    intermediate_exprs: list[str] = Field(
        default_factory=lambda: list[str](),
        description="풀이 중간에 나오는 값의 sympy 식 (이것도 깔끔해야 함)",
    )
    note: str = Field(description="무엇을 바꿀 수 있게 했는지 한두 문장")


class SymbolicTemplate(DawoniModel):
    """미지수형: 수치 대신 문자로 조건을 주고 답도 문자식."""

    item: ItemContent = Field(
        description="수치를 문자(a, k, m …)로 바꾼 문항. `\\var{}`는 쓰지 않음"
    )
    answer_expr: str = Field(description="정답 문자식 (sympy, 예: m*g*sin(theta))")
    assumptions: list[str] = Field(
        default_factory=lambda: list[str](),
        description="문자에 필요한 조건 (예: a > 0). 본문에도 반드시 적을 것",
    )
    note: str


class RewriteDraft(DawoniModel):
    """조건 변형: 문제 조건만 바꿔 다른 답이 나오게. 그림 선지는 그대로 둔다."""

    item: ItemContent
    expected_answer: str = Field(description="새 정답. 선택형이면 선지 번호(예: 3), 아니면 LaTeX")
    expected_expr: str | None = Field(default=None, description="값·식이면 sympy 식")
    change_note: str = Field(description="원래 문항에서 무엇을 바꿨는지")


class NovelDraft(DawoniModel):
    """신규 A·B: 문항과 범위 해설을 함께."""

    item: ItemContent
    solution: SolutionDraft
    differences: str = Field(description="기준 문항과 무엇이 다른지 (풀이 기법·구조)")
    definition_quotes: dict[str, str] = Field(
        default_factory=lambda: dict[str, str](),
        description="신규 B: 해설이 쓰는 확장 개념 ID → 그 정의가 적힌 제시문 문장을 그대로 인용",
    )


class BogiStatement(DawoniModel):
    label: str = Field(description="ㄱ, ㄴ, ㄷ …")
    true: bool = Field(description="이 진술이 옳은가")
    reason: str = Field(description="옳고 그른 이유 (한두 문장)")


class NewProblemDraft(DawoniModel):
    """새 문제: 강사 아이디어 → 문항 + 모범답안."""

    item: ItemContent = Field(
        description="문항. 정답(answer)은 비워 두어도 된다 (아래 필드로 받음)"
    )
    parts: list[ExamPart] = Field(
        default_factory=lambda: list[ExamPart](),
        description="서술형 소문항 (1)(2)…의 표시·배점·유형. 소문항이 없으면 빈 목록",
    )
    statements: list[BogiStatement] = Field(
        default_factory=lambda: list[BogiStatement](),
        description="ㄱㄴㄷ 합답형: <보기> 진술마다 참·거짓. 다른 형태면 빈 목록",
    )
    answer_label: int | None = Field(
        default=None, ge=1, le=5, description="객관식·합답형 정답 선지 번호 (1~5)"
    )
    answer_latex: str | None = Field(default=None, description="단답형 정답 (LaTeX, $ 없이)")
    answer_expr: str | None = Field(default=None, description="단답형 정답의 sympy 식 (값·식이면)")
    solution: SolutionDraft = Field(description="모범답안 (풀이 단계마다 쓰는 개념 ID)")
    design_note: str = Field(description="강사 아이디어를 문항 어디에 어떻게 썼는지 두세 문장")
    definition_quotes: dict[str, str] = Field(
        default_factory=lambda: dict[str, str](),
        description="제시문을 쓰면: 확장 개념 ID → 그 정의가 적힌 제시문 문장을 그대로 인용",
    )


class ExtensionProposals(DawoniModel):
    candidates: list[ExtensionConcept] = Field(min_length=1, max_length=5)
    rationale: list[str] = Field(
        default_factory=lambda: list[str](), description="후보마다 왜 이 단원에 맞는지 (같은 순서)"
    )


# ---------------------------------------------------------------- 저장


class SourceRef(DawoniModel):
    """기준 문항: 시험의 문항 또는 문제은행 문항."""

    exam_id: str | None = None
    question_number: str
    bank_item_id: str | None = None


GenStatus = Literal["draft", "approved", "rejected"]


class GeneratedSet(DawoniModel):
    id: str = Field(default_factory=lambda: new_id("gen"))
    kind: GenKind
    source: SourceRef
    scope_id: str
    points: float = Field(gt=0)
    item: ItemContent
    solution: ScopedSolution
    rubric: RubricDraft | None = None
    status: GenStatus = "draft"
    checks: list[str] = Field(default_factory=lambda: list[str](), description="코드 검사 결과")
    warnings: list[str] = Field(default_factory=lambda: list[str]())
    note: str | None = Field(default=None, description="무엇을 바꿨는지·기준 문항과 다른 점")
    values: dict[str, float] | None = Field(default=None, description="수치형: 고른 값")
    extension: ExtensionConcept | None = None
    skill_ids: list[str] = Field(default_factory=lambda: list[str]())
    created_at: datetime = Field(default_factory=utcnow)
    reviewed_by: str | None = None
    bank_item_id: str | None = Field(default=None, description="승인해 문제은행에 넣은 문항 ID")
    parts: list[ExamPart] = Field(default_factory=lambda: list[ExamPart]())
    unit_ids: list[str] = Field(
        default_factory=lambda: list[str](), description="새 문제: 강사가 고른 단원"
    )
    idea: str | None = Field(default=None, description="새 문제: 강사가 적은 아이디어")
    answer_format: AnswerFormat | None = None
