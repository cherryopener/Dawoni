"""AI 작업 정의.

작업 하나 = 템플릿 + 출력 모델 + (선택) 작업별 검증기. 기능 코드는 작업을 정의하고
`ContextEngine.run()`에 넘기기만 한다. "풀어줘", "채점해줘" 같은 즉석 프롬프트는 없다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from dawoni.roles import RoleName

RuleTarget = Literal[
    "solve",  # 해설
    "rubric",  # 루브릭
    "transcribe",  # 손글씨 판독
    "grade",  # 채점
    "feedback",  # 첨삭
    "ingest",  # 시험지·해설지 읽기
]
"""전역 규칙을 적용할 수 있는 작업 종류 (강사에게 보이는 분류)."""

RULE_TARGET_LABELS: dict[RuleTarget, str] = {
    "solve": "해설",
    "rubric": "루브릭",
    "transcribe": "손글씨 판독",
    "grade": "채점",
    "feedback": "첨삭",
    "ingest": "시험지 읽기",
}


@dataclass(frozen=True)
class TaskDef[T: BaseModel]:
    name: str
    """로그·격리 폴더 이름 (예: grade_item)."""
    template: str
    """context/templates/ 안의 과업 템플릿 파일 이름."""
    output: type[T]
    rule_target: RuleTarget | tuple[RuleTarget, ...] | None
    """적용할 전역 규칙의 작업 종류 (여러 개 가능: 채점은 채점+첨삭).
    None이면 전역 규칙을 넣지 않는다 (규칙 정제 같은 내부 작업)."""
    role: RoleName
    """맡는 AI 역할 (dawoni/roles): 역할 지시서·모델 등급·수리 횟수·시간·예산."""
    validator: Callable[[T], list[str]] | None = None
    """모델 검증을 통과한 결과에 대한 작업별 검사(범위·인용·합계 등).
    문제 목록(한국어)을 돌려준다. 빈 목록 = 통과. 문제가 있으면 수리 요청에 그대로 쓰인다."""
    timeout_s: float | None = None
    """None이면 역할의 제한 시간."""
    max_repairs: int | None = None
    """None이면 역할의 수리 횟수."""
    fix: Callable[[T], tuple[T, list[str]]] | None = None
    """코드 선수리 (PLAN.md §2-5): 스키마를 통과한 결과의 고칠 수 있는 형식 오류(항목 ID 대소문자,
    인용 따옴표, 소수 끝자리 등)를 검증 전에 코드가 고친다. (고친 결과, 고친 내용 설명)을 돌려주고
    설명은 호출 기록에 남는다. AI에게 다시 묻지 않으려는 것이라 판단(점수·근거)은 바꾸지 않는다."""
    partial: PartialRepair[T] | None = None
    """부분 재요청: 검증 오류가 일부 조각(채점 항목 등)에만 있으면 그 조각만 다시 묻고 합친다."""


@dataclass(frozen=True)
class PartialRepair[T: BaseModel]:
    select: Callable[[T, list[str]], list[str] | None]
    """(받은 결과, 검증 오류) → 다시 물을 조각 키. None이면 전체를 다시 묻는다."""
    merge: Callable[[T, T, list[str]], T]
    """(앞 결과, 조각만 담은 새 응답, 키) → 합친 결과 (합친 것을 다시 검증한다)."""
    noun: str = "항목"
    """수리 요청에 쓸 조각 이름."""
