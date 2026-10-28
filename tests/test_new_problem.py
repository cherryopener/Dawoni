"""새 문제 만들기: 강사 아이디어 + 답 형태 + (제시문 개념) → 문항·모범답안 → 독립 풀이 → 문제은행."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import MakeFake
from test_generate import M, T
from test_grading import FIX as GFIX
from test_grading import RUBRIC_OK
from tests_support import fake_prompts

from dawoni.bank.make import approve_to_bank, make_new_problem
from dawoni.bank.store import BankStore
from dawoni.config import AppConfig, save_config
from dawoni.fsio import read_json
from dawoni.generate.new import NewRequest, check_new
from dawoni.grading.store import ScopeStore
from dawoni.models.generation import NewProblemDraft
from dawoni.models.item import ChoiceAnswer
from dawoni.models.scope import ExtensionConcept, ScopeProfile
from dawoni.paths import AppPaths
from dawoni.services import Services, open_services
from dawoni.workspace import create_workspace

EXT = ExtensionConcept(
    id="X1", name="꼭짓점 이동", definition="이차함수를 x축 방향으로 p만큼 옮기면 꼭짓점도 p만큼 옮겨진다",
    assumptions=["모양은 바뀌지 않는다"], expected_inference="옮긴 함수의 최솟값은 그대로",
)  # fmt: skip
PASSAGE = "이차함수를 x축 방향으로 p만큼 옮기면 꼭짓점도 p만큼 옮겨진다."


def _bogi(
    answer_label: int = 4, truth: tuple[bool, bool, bool] = (True, False, True)
) -> dict[str, Any]:
    combos = ["ㄱ", "ㄴ", "ㄱ, ㄴ", "ㄱ, ㄷ", "ㄱ, ㄴ, ㄷ"]
    return {
        "item": {
            "item_type": "multiple_choice",
            "stem": [
                {"type": "box", "title": "제시문", "paragraphs": [[T(PASSAGE)]]},
                {"type": "paragraph", "runs": [T("이차함수 "), M("y=x^2-4x+7"), T("에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은?")]},
                {"type": "bogi", "items": [
                    {"label": "ㄱ", "runs": [T("최솟값은 3이다.")]},
                    {"label": "ㄴ", "runs": [T("꼭짓점의 x좌표는 -2이다.")]},
                    {"label": "ㄷ", "runs": [T("x축 방향으로 1만큼 옮겨도 최솟값은 3이다.")]},
                ]},
            ],
            "choices": [{"label": i, "runs": [T(c)]} for i, c in enumerate(combos, start=1)],
        },
        "statements": [
            {"label": "ㄱ", "true": truth[0], "reason": "표준형 (x-2)^2+3"},
            {"label": "ㄴ", "true": truth[1], "reason": "x좌표는 2"},
            {"label": "ㄷ", "true": truth[2], "reason": "제시문: 꼭짓점만 옮겨짐"},
        ],
        "answer_label": answer_label,
        "solution": {
            "steps": [
                {"explanation": "표준형으로 바꾼다", "math": "y=(x-2)^2+3", "uses": ["S1", "C2"]},
                {"explanation": "제시문대로 옮겨도 최솟값은 그대로", "math": "y_{min}=3", "uses": ["C3", "X1"]},
            ],
            "final_answer": {"latex": "④"},
        },
        "design_note": "꼭짓점 이동을 제시문으로 주고 ㄷ에서 쓰게 함",
        "definition_quotes": {"X1": PASSAGE},
    }  # fmt: skip


@pytest.fixture
def scope() -> ScopeProfile:
    return ScopeProfile.model_validate(read_json(GFIX / "scope.json"))


def _req(**kw: Any) -> NewRequest:
    base: dict[str, Any] = {
        "idea": "꼭짓점 이동",
        "answer_format": "bogi",
        "points": 4,
        "extension": EXT,
    }
    return NewRequest(**(base | kw))


def test_check_bogi(scope: ScopeProfile) -> None:
    assert check_new(NewProblemDraft.model_validate(_bogi()), _req(), scope) == []
    wrong = check_new(NewProblemDraft.model_validate(_bogi(answer_label=3)), _req(), scope)
    assert any("참인 진술" in p for p in wrong) and any("모범답안의 답" in p for p in wrong)
    d = _bogi()
    d["item"]["choices"][1]["runs"] = [T("ㄹ")]
    assert any(
        "조합이 아님" in p for p in check_new(NewProblemDraft.model_validate(d), _req(), scope)
    )
    d = _bogi()
    d["solution"]["steps"][1]["uses"] = ["C3"]
    d["definition_quotes"] = {"X1": "없는 문장"}
    probs = check_new(NewProblemDraft.model_validate(d), _req(), scope)
    assert any("X1를 쓰지 않음" in p for p in probs) and any("그대로 없다" in p for p in probs)
    assert any("item_type" in p for p in check_new(NewProblemDraft.model_validate(_bogi()),
                                                   _req(answer_format="short"), scope))  # fmt: skip


def test_check_essay_parts(scope: ScopeProfile) -> None:
    d = _bogi()
    d["item"] = {
        "item_type": "essay",
        "stem": [{"type": "paragraph", "runs": [T("(1) 최솟값 (2) 이유")]}],
    }
    d.update(statements=[], answer_label=None, definition_quotes={},
             parts=[{"label": "(1)", "points": 2}, {"label": "(2)", "points": 3}])  # fmt: skip
    d["solution"]["steps"][1]["uses"] = ["C3"]
    probs = check_new(NewProblemDraft.model_validate(d), _req(answer_format="essay", n_parts=2,
                                                             extension=None), scope)  # fmt: skip
    assert probs == ["소문항 배점 합(5)이 4점이어야 함"]


def _svc(
    app_paths: AppPaths, tmp_path: Path, make_fake: MakeFake, script: list[Any]
) -> tuple[Services, Path]:
    exe = make_fake(
        "script", script=[{"response": json.dumps(r, ensure_ascii=False)} for r in script]
    )
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.agents["claude"].enabled = False
    cfg.agents["codex"].enabled = False
    save_config(app_paths, cfg)
    ws = create_workspace(tmp_path / "ws")
    ScopeStore(ws).save(ScopeProfile.model_validate(read_json(GFIX / "scope.json")))
    return open_services(ws.root, paths=app_paths), exe


def test_new_bogi_with_passage_to_bank(
    app_paths: AppPaths, tmp_path: Path, make_fake: MakeFake
) -> None:
    indep = copy.deepcopy(_bogi()["solution"])
    indep["final_answer"] = {"latex": "4번"}
    svc, exe = _svc(
        app_paths, tmp_path, make_fake, [_bogi(answer_label=3), _bogi(), indep, RUBRIC_OK]
    )
    s = make_new_problem(svc, idea="꼭짓점 이동으로 ㄷ을 판단하게", answer_format="bogi", points=4,
                         unit_ids=["phy1.1.1"], scope_id="scope-quadratic-minmax", extension=EXT)  # fmt: skip
    prompts = fake_prompts(exe)
    assert "ㄱㄴㄷ 합답형" in prompts[0] and "꼭짓점 이동으로 ㄷ을 판단하게" in prompts[0]
    assert "참인 진술" in prompts[1], "합답형 조합이 틀리면 수리"
    assert "꼭짓점 이동" in prompts[2] and "answer_label" not in prompts[2], "독립 풀이엔 정답 없이"
    assert s.kind == "new" and s.solution.verified and s.rubric is not None
    assert s.item.answer == ChoiceAnswer(kind="choice", labels=[4])
    assert "ㄷ: 참" in (s.note or "") and s.unit_ids == ["phy1.1.1"] and s.extension == EXT

    it = approve_to_bank(svc, s.id, by="김선생")
    assert it.origin == "new" and it.unit_ids == ["phy1.1.1"] and it.parent_id is None
    assert it.source.display() == "다워니 제작 · 새 문제"
    assert BankStore(svc.workspace).get(it.id).question.item.answer == ChoiceAnswer(
        kind="choice", labels=[4]
    )


def test_bogi_question_once(scope: ScopeProfile) -> None:
    """실측(agy): 발문을 <보기> 앞뒤로 두 번 씀."""
    d = _bogi()
    d["item"]["stem"].append(
        {"type": "paragraph", "runs": [T("<보기>에서 옳은 것만을 있는 대로 고른 것은?")]}
    )
    assert any("한 번만" in p for p in check_new(NewProblemDraft.model_validate(d), _req(), scope))
