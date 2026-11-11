"""해설지 부분 점수 표기: "각 N점"·단계 점수, 단답형 부분 점수.

실측 2026-11-11 (README "HWP 시험지 15개"):

- "답 각 1점"(답이 둘)을 1점 하나로 읽어 거짓 경고가 나고, 구조 검사가 맞는 배분을 고치게 했다.
- "2가지 정답 1점, 3가지 2점"처럼 하나만 받는 단계를 더해서 보았다.
- 단답형 소문항에 부분 점수가 적혀 있어도 코드 채점(맞으면 만점, 아니면 0점)이라 줄 수 없었다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from conftest import MakeFake
from test_code_grading import SCOPE, short, t
from tests_support import fake_prompts

from dawoni.config import AppConfig, save_config
from dawoni.grading import checks, workflow
from dawoni.grading.answers import answerless_parts, code_gradable, partial_parts, pre_grade
from dawoni.grading.render import credit_text
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.ingest.answer_key import key_warnings
from dawoni.models.answer_key import AnswerKey, KeyPart
from dawoni.models.grading import Exam, ExamQuestion, Rubric, RubricDraft
from dawoni.paths import AppPaths
from dawoni.services import open_services
from dawoni.workspace import create_workspace


def _part(label: str = "(1)", **kw: Any) -> KeyPart:
    return KeyPart.model_validate({"label": label, "credit_policy": "listed", **kw})


EACH = _part(credits=[{"what": "답", "points": 1, "each": 2}, {"what": "내용", "points": 2}])
"""5회 모의고사 3번(1) 같은 모양: "답 각 1점 / 내용 2점" (답이 둘) = 4점."""
TIERS = _part(
    credits=[{"what": "3가지 정답", "points": 2}, {"what": "2가지 정답", "points": 1}],
    credit_tiers=True,
)
"""2604 경기형 2회 1번: "A, B, C 중 2가지 정답 1점, 3가지 정답 2점"."""


def test_each_and_tiers_totals() -> None:
    assert EACH.credit_total == 4 and TIERS.credit_total == 2
    old = KeyPart.model_validate(
        {"label": "", "credit_policy": "listed", "credits": [{"what": "답", "points": 3}]}
    )
    assert old.credits[0].each == 1 and not old.credit_tiers, "예전 저장본은 그대로 읽힌다"


def test_gives_partial() -> None:
    assert EACH.gives_partial(4) and TIERS.gives_partial(2)
    assert _part(credits=[{"what": "답", "points": 2}]).gives_partial(4), "'각 2점'을 하나로 읽어도"
    assert not _part(credits=[{"what": "답", "points": 2}]).gives_partial(2)
    assert not KeyPart(label="", credit_policy="none").gives_partial(2)
    assert not KeyPart(label="", credit_policy="unspecified").gives_partial(2)
    assert KeyPart(label="", credit_policy="range", credit_min=1, credit_max=5).gives_partial(5)


def test_credit_text_shows_each_and_tiers() -> None:
    assert "1점씩 2개 (합 2점)" in credit_text(EACH)
    assert credit_text(TIERS).startswith("단계 — 이 중 하나만 받음")


def _q(points: float = 4) -> ExamQuestion:
    return ExamQuestion.model_validate(
        {"number": "3", "points": points,
         "item": {"item_type": "essay", "subject": "물리",
                  "stem": [{"type": "paragraph", "runs": [{"t": "text", "v": "답하라."}]}]},
         "parts": [{"label": "(1)", "points": points}]}
    )  # fmt: skip


def _key(part: KeyPart) -> AnswerKey:
    return AnswerKey(question_number="3", parts=[part], source="합성")


def test_no_false_sum_warning() -> None:
    def sums(part: KeyPart, points: float) -> list[str]:
        return [w for w in key_warnings(_key(part), _q(points)) if "부분 점수 합" in w]

    assert sums(EACH, 4) == [] and sums(TIERS, 2) == []
    added = _part(credits=[{"what": "3가지", "points": 2}, {"what": "2가지", "points": 1}])
    assert any("부분 점수 합(3)" in w for w in sums(added, 2)), "더하는 표기면 경고"


def _draft(*items: tuple[str, float, int | None], partial: list[dict[str, Any]] | None = None
           ) -> RubricDraft:  # fmt: skip
    return RubricDraft.model_validate({
        "items": [{"id": i, "part": "(1)", "points": p, "key_credit": k, "criterion": "x",
                   "evidence_hint": ""} for i, p, k in items],
        "partial": partial or [],
    })  # fmt: skip


def test_structure_check_counts_each_and_skips_tiers() -> None:
    # 답 두 개를 1점씩(R1·R2), 내용 2점(R3) — "각"을 개수만큼 본다
    ok = _draft(("R1", 1, 1), ("R2", 1, 1), ("R3", 2, 2))
    assert checks.check_rubric(ok, _q(4), None, _key(EACH)) == []
    # 예전처럼 답 둘에 합 1점으로 몰면 이제 고치라고 한다
    squeezed = _draft(("R1", 0.5, 1), ("R2", 0.5, 1), ("R3", 3, 2))
    assert any(
        "'답 1점 × 2개'" in p for p in checks.check_rubric(squeezed, _q(4), None, _key(EACH))
    )
    # 단계: 항목 하나 + 낮은 단계 부분점수 — 표기마다 합(key_credit)을 묻지 않는다
    tiered = _draft(("R1", 2, None), partial=[{"item_id": "R1", "condition": "2가지", "points": 1}])
    assert checks.check_rubric(tiered, _q(2), None, _key(TIERS)) == []


def test_short_answers_with_key_partials_are_not_code_graded() -> None:
    q = short("5")
    none = AnswerKey.model_validate(
        {"question_number": "5", "source": "합성",
         "parts": [{"label": "(1)", "answer": "1/2", "credit_policy": "none"},
                   {"label": "(2)", "answer": "북쪽", "credit_policy": "none"}]}
    )  # fmt: skip
    assert code_gradable(q, none) and code_gradable(q) and partial_parts(q, none) == []
    two = _part("(2)", answer="북쪽, 남쪽", credits=[{"what": "답", "points": 1, "each": 2}])
    each = none.model_copy(update={"parts": [none.parts[0], two]})
    assert partial_parts(q, each) == ["(2)"] and not code_gradable(q, each)


def test_short_answers_drawn_in_the_key_are_not_code_graded() -> None:
    """8월 월례평가 1번 ①: "힘을 화살표로 그려라" — 해설지는 그림 설명뿐이라 코드 채점은 비교할
    답이 없다."""
    q = short("5")
    key = AnswerKey.model_validate(
        {"question_number": "5", "source": "합성",
         "parts": [{"label": "(1)", "answer": "1/2", "credit_policy": "none"},
                   {"label": "(2)", "solution": "(그림: 중력 50N 아래쪽, 장력 30N 위쪽 화살표)",
                    "credit_policy": "unspecified"}]}
    )  # fmt: skip
    assert answerless_parts(q, key) == ["(2)"] and not code_gradable(q, key)
    empty = key.model_copy(
        update={"parts": [key.parts[0], KeyPart(label="(2)", credit_policy="none")]}
    )
    assert answerless_parts(q, empty) == [], "해설도 없으면 여기서 다루지 않음 (해설지 없는 문항)"
    choices = [{"label": i, "runs": [{"t": "text", "v": f"답 {i}"}]} for i in range(1, 6)]
    mc = ExamQuestion.model_validate(
        {"number": "5", "points": 3,
         "item": {"item_type": "multiple_choice", "subject": "물리", "choices": choices,
                  "stem": [{"type": "paragraph", "runs": [{"t": "text", "v": "고르시오."}]}]}}
    )  # fmt: skip
    drawn = AnswerKey.model_validate(
        {"question_number": "5", "source": "합성",
         "parts": [{"label": "", "solution": "(그림)", "credit_policy": "none"}]}
    )  # fmt: skip
    assert answerless_parts(mc, drawn) == [], "선택형은 강사가 번호를 적는다"


def test_rubric_workflow_sends_partial_short_answers_to_ai(
    app_paths: AppPaths, tmp_path: Path, make_fake: MakeFake
) -> None:
    rubric = {
        "items": [
            {"id": "R1", "part": "(1)", "points": 2, "criterion": "1/2", "evidence_hint": ""},
            {"id": "R2", "part": "(2)", "points": 1, "key_credit": 1, "criterion": "북쪽",
             "evidence_hint": ""},
            {"id": "R3", "part": "(2)", "points": 1, "key_credit": 1, "criterion": "남쪽",
             "evidence_hint": ""},
        ],
    }  # fmt: skip
    exe = make_fake("script", script=[{"response": json.dumps(rubric, ensure_ascii=False)}])
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.agents["claude"].enabled = False
    cfg.agents["codex"].enabled = False
    save_config(app_paths, cfg)
    ws = create_workspace(tmp_path / "ws")
    ScopeStore(ws).save(SCOPE)
    store = ExamStore.create(ws, Exam(title="단답", scope_id=SCOPE.id, questions=[short("5")]))
    store.save_key(
        AnswerKey.model_validate(
            {"question_number": "5", "source": "합성",
             "parts": [{"label": "(1)", "answer": "1/2", "credit_policy": "none"},
                       {"label": "(2)", "answer": "북쪽, 남쪽", "credit_policy": "listed",
                        "credits": [{"what": "답", "points": 1, "each": 2}]}]}
        )
    )  # fmt: skip
    (r,) = workflow.rubric_questions(open_services(ws.root, paths=app_paths), store)
    assert not r.code_graded and len(r.items) == 3
    assert "단답형이지만 해설지에 부분 점수가 있어((2))" in r.review_notes[0]
    assert len(fake_prompts(exe)) == 1


def test_pre_grade_leaves_partial_credit_to_ai() -> None:
    q = ExamQuestion.model_validate(
        {"number": "6", "points": 4,
         "item": {"item_type": "essay", "subject": "물리",
                  "stem": [{"type": "paragraph", "runs": [{"t": "text", "v": "답하라."}]}]},
         "parts": [{"label": "(1)", "points": 2, "kind": "short_answer"},
                   {"label": "(2)", "points": 2, "kind": "essay"}]}
    )  # fmt: skip
    base: dict[str, Any] = {
        "question_number": "6",
        "items": [{"id": "R1", "part": "(1)", "points": 2, "criterion": "3", "evidence_hint": ""},
                  {"id": "R2", "part": "(2)", "points": 2, "criterion": "x", "evidence_hint": ""}],
        "checks": [{"part": "(1)", "key_answer": "3", "ai_answer": "3", "verdict": "same"}],
    }  # fmt: skip
    answer = t("A", "6", ("(1)", "5"), ("(2)", "설명"))
    plain = pre_grade(q, Rubric.model_validate(base), answer)
    assert plain.judgements["R1"].awarded == 0, "부분 점수가 없으면 코드가 0점"
    with_partial = Rubric.model_validate(
        {**base, "partial": [{"item_id": "R1", "condition": "단위만 틀림", "points": 1}]}
    )
    assert "R1" not in pre_grade(q, with_partial, answer).judgements


def test_blank_key_essay_is_solved_not_expanded(
    app_paths: AppPaths, tmp_path: Path, make_fake: MakeFake
) -> None:
    """해설지에 답·해설이 하나도 없는 서술형: 빈 해설지를 넓혀 루브릭을 지어내지 않고 AI가 푼다
    (실측 2026-11-11 9_2주차 전기 8번: "강사가 확정한 정답과 같다"뿐인 루브릭)."""
    from test_grading import FIX, RUBRIC_OK, SOL_OK

    from dawoni.fsio import read_json

    q = ExamQuestion.model_validate(read_json(FIX / "questions.json")[0])  # 서술형 4점
    exe = make_fake(
        "script",
        script=[{"response": json.dumps(r, ensure_ascii=False)} for r in (SOL_OK, RUBRIC_OK)],
    )
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.agents["claude"].enabled = False
    cfg.agents["codex"].enabled = False
    save_config(app_paths, cfg)
    ws = create_workspace(tmp_path / "ws")
    ScopeStore(ws).save(SCOPE)
    store = ExamStore.create(ws, Exam(title="빈 해설", scope_id=SCOPE.id, questions=[q]))
    blank = AnswerKey(
        question_number=q.number,
        parts=[KeyPart(label="", credit_policy="unspecified")],
        source="합성",
    )
    assert blank.empty
    store.save_key(blank)
    (r,) = workflow.rubric_questions(open_services(ws.root, paths=app_paths), store)
    prompts = fake_prompts(exe)
    assert len(prompts) == 2 and "[강사 해설지]" not in prompts[0], "풀이 → 루브릭 (넓히기 아님)"
    assert not r.based_on_key and any("AI 풀이로 만든 루브릭" in n for n in r.review_notes)
    assert store.solution(q.number) is not None
