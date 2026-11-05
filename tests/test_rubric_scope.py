"""M2 범위 검사: 해설지 개념 목록 + 수준 프리셋 금지 목록 (PLAN.md §4-2).

완료 기준: 미적분·대학 수준 기준이 섞인 합성 사례를 **모두** 걸러낸다. 같은 수준에서 정당한
기준(물리의 벡터·v'·삼각비, 해설지가 직접 쓴 기법)은 걸리지 않아야 한다.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import MakeFake
from tests_support import fake_prompts

from dawoni.agents import AgentClient
from dawoni.config import AppConfig
from dawoni.context import ContextEngine
from dawoni.grading import checks
from dawoni.grading.pipeline import expand_key_rubric
from dawoni.levels import parse_level, preset_scope
from dawoni.models.answer_key import AnswerKey
from dawoni.models.grading import ExamQuestion, GradeOutput, KeyRubricDraft, RubricDraft
from dawoni.models.scope import ScopeProfile
from dawoni.paths import AppPaths

H1_PHYS = preset_scope(parse_level("고1 물리"))
M3_MATH = preset_scope(parse_level("중3 수학"))
H1_MATH = preset_scope(parse_level("고1 수학"))


def _question(
    text: str = "높이 h에서 가만히 놓은 물체가 바닥에 닿는 순간의 속력을 구하시오.",
) -> ExamQuestion:
    return ExamQuestion.model_validate(
        {
            "number": "1",
            "points": 4,
            "item": {
                "item_type": "essay",
                "subject": "물리",
                "stem": [{"type": "paragraph", "runs": [{"t": "text", "v": text}]}],
            },
        }
    )


KEY: dict[str, Any] = {
    "question_number": "1",
    "source": "합성",
    "parts": [
        {
            "label": "",
            "answer": "\\(v=\\sqrt{2gh}\\)",
            "answer_expr": "sqrt(2*g*h)",
            "solution": "역학적 에너지 보존에 의해 \\(mgh=\\frac{1}{2}mv^2\\)",
            "credit_policy": "listed",
            "credits": [{"what": "식", "points": 2}, {"what": "답", "points": 2}],
            "concepts": ["역학적 에너지 보존", "운동 에너지"],
        }
    ],
}

CLEAN: dict[str, Any] = {
    "items": [
        {"id": "R1", "points": 2, "key_credit": 1, "criterion": "역학적 에너지 보존 식을 세움",
         "evidence_hint": "mgh=\\frac12 mv^2", "concepts": ["역학적 에너지 보존 법칙"]},
        {"id": "R2", "points": 2, "key_credit": 2, "criterion": "v=√(2gh)",
         "evidence_hint": "v=\\sqrt{2gh}", "concepts": []},
    ],
    "expansions": [
        {"part": "", "description": "위치 에너지가 모두 운동 에너지로 바뀌므로 \\(v=\\sqrt{2gh}\\)",
         "item_ids": ["R1", "R2"], "concepts": ["운동에너지"]},
    ],
}  # fmt: skip


def _check(
    draft: dict[str, Any],
    scope: ScopeProfile = H1_PHYS,
    key: dict[str, Any] | None = KEY,
    q: ExamQuestion | None = None,
) -> list[str]:
    d = KeyRubricDraft.model_validate(draft).to_rubric_draft()
    k = AnswerKey.model_validate(key) if key else None
    return checks.check_rubric_scope(d, q or _question(), k, scope)


def _with(path: str, value: Any, base: dict[str, Any] = CLEAN) -> dict[str, Any]:
    d = copy.deepcopy(base)
    where, idx, field = path.split(".")
    d[where][int(idx)][field] = value
    return d


def test_clean_rubric_passes() -> None:
    assert _check(CLEAN) == []
    assert checks.check_rubric(
        KeyRubricDraft.model_validate(CLEAN).to_rubric_draft(), _question(), None,
        AnswerKey.model_validate(KEY),
    ) == []  # fmt: skip


# 미적분·대학 수준이 섞인 합성 사례: 기준, 찾을 식, 부분 점수, 넓힌 표현, 개념 표시 어디에 있든
CALCULUS_AND_COLLEGE: list[tuple[str, dict[str, Any]]] = [
    ("미분 낱말", _with("items.0.criterion", "속도를 시간에 대해 미분해 가속도를 구함")),
    ("도함수", _with("items.0.criterion", "위치의 도함수로 속도를 구함")),
    ("dv/dt 식", _with("items.0.evidence_hint", "a=\\frac{dv}{dt}")),
    ("dy/dx 글", _with("items.1.evidence_hint", "dv/dt = g")),
    ("f'(t)", _with("items.0.evidence_hint", "f'(t)=gt")),
    ("점 표기", _with("items.0.evidence_hint", "\\dot{x}=v")),
    ("적분 낱말", _with("items.0.criterion", "속도를 적분해 이동 거리를 구함")),
    ("\\int", _with("items.1.evidence_hint", "\\int_0^t g\\,dt")),
    ("∫ 기호", _with("items.1.criterion", "∫g dt = gt")),
    ("극한", _with("items.0.criterion", "Δt→0 극한으로 순간 속도")),
    (
        "\\lim",
        _with("items.0.evidence_hint", "\\lim_{\\Delta t\\to 0}\\frac{\\Delta x}{\\Delta t}"),
    ),
    ("라그랑지안", _with("items.0.criterion", "라그랑지안 L=T-V로 운동 방정식을 세움")),
    ("\\mathcal{L}", _with("items.0.evidence_hint", "\\mathcal{L}=T-V")),
    ("편미분", _with("items.0.evidence_hint", "\\frac{\\partial L}{\\partial q}")),
    ("테일러", _with("items.0.criterion", "테일러 전개로 근사")),
    ("로피탈", _with("items.0.criterion", "로피탈의 정리로 극값")),
    ("벡터 외적", _with("items.0.evidence_hint", "\\vec{\\tau}=\\vec{r}\\times\\vec{F}")),
    ("외적 낱말", _with("items.0.criterion", "토크를 외적으로 구함")),
    ("넓힌 표현에 미분", _with("expansions.0.description", "에너지를 시간에 대해 미분하면 0")),
    (
        "넓힌 표현에 적분",
        _with("expansions.0.description", "\\(\\int F\\,dx\\)가 운동 에너지 변화"),
    ),
    ("개념 표시에 미분", _with("items.1.concepts", ["미분"])),
    ("해설지 밖 개념", _with("items.0.concepts", ["각운동량 보존"])),
    ("넓힌 표현의 밖 개념", _with("expansions.0.concepts", ["라그랑주 역학"])),
    ("결론이 다른 넓힌 표현", _with("expansions.0.description", "그러므로 \\(v=2\\sqrt{gh}\\)")),
]


@pytest.mark.parametrize(
    ("name", "draft"), CALCULUS_AND_COLLEGE, ids=[n for n, _ in CALCULUS_AND_COLLEGE]
)
def test_out_of_level_criteria_are_all_caught(name: str, draft: dict[str, Any]) -> None:
    assert _check(draft), f"{name}: 걸러내지 못함"


def test_partial_and_candidate_texts_are_checked() -> None:
    d = RubricDraft.model_validate(
        {**KeyRubricDraft.model_validate(CLEAN).to_rubric_draft().model_dump(),
         "partial": [{"item_id": "R1", "condition": "도함수로 구하면 1점", "points": 1}]}
    )  # fmt: skip
    assert checks.check_rubric_scope(d, _question(), AnswerKey.model_validate(KEY), H1_PHYS)
    d = RubricDraft.model_validate(
        {**d.model_dump(), "partial": [],
         "candidates": [{"id": "A1", "part": "", "answer": "v=gt", "argument": "\\int g\\,dt"}]}
    )  # fmt: skip
    assert any(
        "정답 후보 A1" in p for p in checks.check_rubric_scope(d, _question(), None, H1_PHYS)
    )


@pytest.mark.parametrize(
    ("scope", "criterion"),
    [
        (M3_MATH, "판별식 D=b^2-4ac<0이므로 실근이 없음"),
        (M3_MATH, "\\(\\tan x\\)의 값을 구함 (삼각함수)"),
        (H1_MATH, "\\(\\log_2 8 = 3\\)을 씀"),
        (H1_MATH, "\\(\\lim_{x\\to1}\\)을 계산"),
    ],
)
def test_math_presets(scope: ScopeProfile, criterion: str) -> None:
    assert _check(_with("items.0.criterion", criterion), scope=scope, key=None)


@pytest.mark.parametrize(
    "criterion",
    [
        "운동 방정식 \\(\\vec{F}=m\\vec{a}\\)를 세움",  # 고1 물리에서 벡터는 범위 안 (외적만 금지)
        "충돌 후 속도 \\(v'=2\\,\\mathrm{m/s}\\)",  # v'는 미분이 아님
        "빗면 성분 \\(mg\\sin\\theta\\)",  # 과학은 삼각함수를 막지 않음
        "v-t 그래프의 기울기로 가속도, 넓이로 이동 거리",
        "프로그램처럼 순서대로 계산",  # '로그'가 들어 있어도 로그가 아님
    ],
)
def test_in_level_criteria_are_not_flagged(criterion: str) -> None:
    assert _check(_with("items.0.criterion", criterion), key=None) == []


def test_key_itself_sets_the_scope() -> None:
    """해설지가 그 기법을 쓰면 프리셋이 금지해도 이 시험의 범위 (강사가 정함)."""
    key = copy.deepcopy(KEY)
    key["parts"][0]["solution"] = "속도를 미분하면 가속도 \\(a=\\frac{dv}{dt}=g\\)"
    draft = _with("items.0.evidence_hint", "a=\\frac{dv}{dt}")
    assert _check(draft, key=key) == []
    assert _check(draft) != []


def test_no_concept_list_skips_concept_check() -> None:
    key = copy.deepcopy(KEY)
    key["parts"][0]["concepts"] = []
    assert _check(_with("items.0.concepts", ["각운동량 보존"]), key=key) == []


@pytest.mark.parametrize(
    ("name", "allowed", "ok"),
    [
        ("옴 법칙", ["옴의 법칙"], True),
        ("에너지 보존", ["역학적 에너지 보존"], True),
        ("운동량 보존", ["역학적 에너지 보존"], False),
        ("직렬연결 전압분배", ["직렬 연결 전압 분배"], True),
        ("힘", ["힘의 평형"], False),  # 한 글자는 줄임으로 보지 않음
    ],
)
def test_concept_matching(name: str, allowed: list[str], ok: bool) -> None:
    assert checks.concept_in(name, allowed) is ok


def test_deduction_for_unrequired_method() -> None:
    rubric = KeyRubricDraft.model_validate(CLEAN).to_rubric_draft()
    assert (
        checks.unrequired_method("운동량 보존 법칙을 쓰지 않아 감점", rubric) == "운동량 보존 법칙"
    )
    assert checks.unrequired_method("역학적 에너지 보존을 쓰지 않음", rubric) is None
    assert checks.unrequired_method("단위를 쓰지 않음", rubric) is None
    assert checks.unrequired_method("계산 실수", rubric) is None


def test_check_grade_flags_unrequired_method() -> None:
    from dawoni.models.grading import Rubric, Transcript, TranscriptLine

    rubric = Rubric(
        **KeyRubricDraft.model_validate(CLEAN).to_rubric_draft().model_dump(), question_number="1"
    )
    lines = [TranscriptLine(text="mgh=1/2mv^2"), TranscriptLine(text="v=√(2gh)")]
    t = Transcript(student_id="A", question_number="1", lines=lines)
    out = GradeOutput.model_validate(
        {"judgements": [
            {"rubric_item_id": "R1", "awarded": 2, "evidence_quote": "mgh=1/2mv^2", "reason": "ok",
             "confidence": 0.9},
            {"rubric_item_id": "R2", "awarded": 1, "evidence_quote": "v=√(2gh)", "reason": "",
             "deduction_reason": "뉴턴 운동 법칙을 쓰지 않음", "confidence": 0.9},
        ], "comment": ""}
    )  # fmt: skip
    problems = checks.check_grade(out, rubric, t, H1_PHYS)
    assert any("루브릭에 없는 요구" in p for p in problems)


# ---------------------------------------------------------------- 해설지 넓히기 호출


@pytest.fixture
def engine_with(
    app_paths: AppPaths, make_fake: MakeFake
) -> Callable[[list[dict[str, Any]]], tuple[ContextEngine, Path]]:
    def make(responses: list[dict[str, Any]]) -> tuple[ContextEngine, Path]:
        exe = make_fake(
            "script", script=[{"response": json.dumps(r, ensure_ascii=False)} for r in responses]
        )
        cfg = AppConfig()
        cfg.agents["agy"].executable = str(exe)
        cfg.agents["claude"].enabled = False
        cfg.agents["codex"].enabled = False
        return ContextEngine(AgentClient(cfg, app_paths)), exe

    return make


def test_expand_repairs_out_of_level_criterion(
    engine_with: Callable[[list[dict[str, Any]]], tuple[ContextEngine, Path]],
) -> None:
    bad = _with("items.0.evidence_hint", "a=\\frac{dv}{dt}")
    engine, exe = engine_with([bad, CLEAN])
    r = expand_key_rubric(engine, _question(), AnswerKey.model_validate(KEY), H1_PHYS)
    assert r.items[0].concepts == ["역학적 에너지 보존 법칙"]
    assert r.accepted_alternatives[0].concepts == ["운동에너지"]
    first, second = fake_prompts(exe)
    assert "개념: 역학적 에너지 보존, 운동 에너지" in first
    assert "해설지 개념 목록이 범위다" in first
    assert "Expand wording, not concepts." in first
    assert "쓰지 않는 기법(미분)" in second


def test_expand_clean_is_one_call(
    engine_with: Callable[[list[dict[str, Any]]], tuple[ContextEngine, Path]],
) -> None:
    engine, exe = engine_with([CLEAN])
    expand_key_rubric(engine, _question(), AnswerKey.model_validate(KEY), H1_PHYS)
    assert len(fake_prompts(exe)) == 1
