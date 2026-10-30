"""AI 역할: 작업(TaskDef)마다 어느 역할이 맡는지, 역할마다 등급·수리 횟수·시간·예산.

오케스트레이터는 결정적인 파이썬 코드(`ContextEngine` + 작업 흐름)다. 역할은 AI에게
"너는 무엇을 하는 일꾼인가"만 짧게 알려 준다 — 역할 지시서(`<역할>.md`, 영어)는 계약
다음, 과업 앞에 들어간다. 강사에게 보이는 출력은 계약대로 한국어.

등급(fast/standard/strong) → AI별 모델은 `AppConfig.tiers`에서, 역할별 등급 바꾸기는
`AppConfig.role_tiers`에서 강사가 고친다. 예산은 프롬프트 글자 수 추정치(이미지 제외)로,
README의 "역할과 모델 등급"에 실측과 함께 적었다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

from dawoni.config import TierName

ROLE_DIR = Path(__file__).resolve().parent
_VERSION = re.compile(r"<!--\s*version:\s*([\w.\-]+)\s*-->")

RoleName = Literal[
    "page_reader",
    "key_reader",
    "handwriting_reader",
    "cover_reader",
    "concept_extractor",
    "rubric_builder",
    "grader",
    "solver_verifier",
    "problem_maker",
    "figure_maker",
    "lecture_summarizer",
    "light_helper",
]


@dataclass(frozen=True)
class Role:
    name: RoleName
    title: str
    """강사에게 보이는 이름."""
    tier: TierName
    max_repairs: int
    """검증 실패 시 수리 요청 최대 횟수 (엔진 설정보다 크면 엔진 설정)."""
    timeout_s: int
    """작업에 따로 정한 시간이 없을 때 호출 하나의 제한 시간."""
    budget_tokens: int
    """프롬프트 추정 토큰 상한. 넘으면 줄일 수 있는 레이어부터 줄이고, 그래도 넘으면 오류."""
    tasks: tuple[str, ...]
    """이 역할이 맡는 작업 이름 (문서·검사용)."""
    image_max_px: int | None = None
    """첨부 그림의 긴 변 상한. claude는 어차피 1568px로 줄여 보므로 1568은 claude에 손실이
    없고 gemini 타일 수(토큰)를 줄인다. 손글씨는 작은 계수·첨자를 잘못 읽은 사례가 있어 그대로."""

    @property
    def file(self) -> str:
        return f"{self.name}.md"


ROLES: dict[RoleName, Role] = {
    r.name: r
    for r in (
        Role("page_reader", "쪽 판독", "standard", 2, 900, 40_000,
             ("extract_questions",), image_max_px=1568),
        Role("key_reader", "해설지 판독", "standard", 2, 900, 40_000,
             ("extract_answer_key",), image_max_px=1568),
        Role("handwriting_reader", "손글씨 판독", "standard", 2, 900, 20_000,
             ("transcribe_answer",)),
        Role("cover_reader", "표지 판독", "fast", 1, 300, 8_000,
             ("read_cover",), image_max_px=1568),
        Role("concept_extractor", "개념 추출", "standard", 2, 900, 50_000,
             ("extract_concepts",)),
        Role("rubric_builder", "루브릭", "standard", 2, 600, 30_000,
             ("expand_key_rubric", "derive_rubric", "audit_rubric", "suggest_rubric")),
        Role("grader", "채점", "standard", 2, 600, 24_000,
             ("grade_answer",)),
        Role("solver_verifier", "풀이·검산", "strong", 2, 600, 16_000,
             ("solve_question", "check_answer")),
        Role("problem_maker", "문제 만들기", "strong", 2, 900, 24_000,
             ("gen_numeric", "gen_symbolic", "gen_condition", "gen_novel_a", "gen_novel_b",
              "propose_extensions", "draft_extension", "gen_new")),
        Role("figure_maker", "그림", "standard", 2, 600, 12_000,
             ("figure_spec", "figure_compare"), image_max_px=1568),
        Role("lecture_summarizer", "강의 요약", "fast", 2, 600, 40_000,
             ("lecture_chunk", "lecture_summary")),
        Role("light_helper", "가벼운 도우미", "fast", 1, 600, 24_000,
             ("match_candidates", "refine_rule")),
    )
}  # fmt: skip


@cache
def instructions(name: RoleName) -> str:
    """역할 지시서 본문 (첫 줄 version 주석 제외)."""
    text = (ROLE_DIR / ROLES[name].file).read_text(encoding="utf-8")
    return _VERSION.sub("", text, count=1).strip()


@cache
def version(name: RoleName) -> str:
    head = (ROLE_DIR / ROLES[name].file).read_text(encoding="utf-8").split("\n", 1)[0]
    m = _VERSION.search(head)
    if not m:
        raise ValueError(f"역할 지시서 첫 줄에 version이 없음: {ROLES[name].file}")
    return m.group(1)


def role_for_task(task: str) -> Role | None:
    return next((r for r in ROLES.values() if task in r.tasks), None)
