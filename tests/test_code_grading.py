"""M2 객관식·단답형 코드 채점 (PLAN.md §4-3): 루브릭·채점 모두 AI 0회, 애매한 단답만 문항당 1회.

완료 기준의 두 번째: 시험의 루브릭 호출 수 = (서술형) 문항 수 — 객관식·단답형은 호출하지 않는다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import MakeFake
from test_answer_key import EXPANDED, KEY
from test_grading import FIX
from tests_support import fake_prompts

from dawoni.config import AppConfig, save_config
from dawoni.fsio import read_json
from dawoni.grading import workflow
from dawoni.grading.answers import (
    choice_labels,
    code_rubric,
    grade_by_code,
    judge_value,
    set_answers,
    split_unit,
)
from dawoni.grading.pipeline import PipelineError, approve_rubric, edit_rubric, grade_answer
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.models.answer_key import AnswerKey
from dawoni.models.grading import Exam, ExamQuestion, RubricDraft, Transcript, TranscriptLine
from dawoni.models.item import ItemContent
from dawoni.models.scope import ScopeProfile
from dawoni.paths import AppPaths
from dawoni.services import Services, open_services
from dawoni.workspace import create_workspace

SCOPE = ScopeProfile.model_validate(read_json(FIX / "scope.json"))


def _stem(text: str) -> list[dict[str, Any]]:
    return [{"type": "paragraph", "runs": [{"t": "text", "v": text}]}]


def mc(number: str = "4", answer: list[int] | None = None) -> ExamQuestion:
    item: dict[str, Any] = {
        "item_type": "multiple_choice", "subject": "물리", "stem": _stem("옳은 것만을 고른 것은?"),
        "choices": [{"label": n, "runs": [{"t": "text", "v": t}]}
                    for n, t in enumerate(["ㄱ", "ㄴ", "ㄱ, ㄷ", "ㄴ, ㄷ", "ㄱ, ㄴ, ㄷ"], 1)],
    }  # fmt: skip
    if answer:
        item["answer"] = {"kind": "choice", "labels": answer}
    return ExamQuestion(number=number, points=3, item=ItemContent.model_validate(item))


def short(number: str = "5") -> ExamQuestion:
    """서술형 문항이지만 소문항이 모두 단답형 → 코드 채점."""
    return ExamQuestion.model_validate(
        {"number": number, "points": 4,
         "item": {"item_type": "essay", "subject": "물리", "stem": _stem("빈칸을 채우시오.")},
         "parts": [{"label": "(1)", "points": 2, "kind": "short_answer"},
                   {"label": "(2)", "points": 2, "kind": "short_answer"}]}
    )  # fmt: skip


SHORT_KEY = AnswerKey.model_validate(
    {"question_number": "5", "source": "합성", "confirmed": True,
     "parts": [{"label": "(1)", "answer": "\\(\\frac{1}{2}\\,\\mathrm{m/s}\\)",
                "answer_expr": "1/2", "credit_policy": "none"},
               {"label": "(2)", "answer": "북쪽", "credit_policy": "none"}]}
)  # fmt: skip


def t(student: str, q: str, *lines: tuple[str | None, str]) -> Transcript:
    return Transcript(
        student_id=student, question_number=q, confirmed=True,
        lines=[TranscriptLine(part=p, text=x) for p, x in lines],
    )  # fmt: skip


# ---------------------------------------------------------------- 비교 규칙


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("③", {3}),
        ("3번", {3}),
        ("답: 4", {4}),
        ("2, 4", {2, 4}),
        ("ㄱ, ㄷ", {3}),  # 합답형: 선지 내용으로
        ("3/2", None),  # 분수는 번호가 아님
        ("잘 모르겠음", None),
    ],
)
def test_choice_labels(text: str, want: set[int] | None) -> None:
    got = choice_labels(text, mc().item)
    assert (set(got) if got is not None else None) == want


@pytest.mark.parametrize(
    ("student", "answer", "verdict"),
    [
        ("0.5", "\\frac{1}{2}", "same"),
        ("답: 12", "12", "same"),
        ("\\sin\\theta = \\frac12", "\\sin\\theta=\\frac{1}{2}", "same"),
        ("1/3", "\\frac{1}{2}", "different"),
        ("3\\,\\mathrm{m/s}", "3 m/s", "same"),
        ("㉢", "㉠", "different"),
        ("2R", "3R", "different"),
        (
            "\\cos\\theta=\\frac12",
            "\\sin\\theta=\\frac12",
            None,
        ),  # 좌변이 다르면 코드가 정하지 않음
        ("북", "북쪽", None),  # 말로 쓴 답 → 도우미
        ("3 cm", "3\\,\\mathrm{m}", None),  # 단위가 다름 → 도우미
    ],
)
def test_judge_value(student: str, answer: str, verdict: str | None) -> None:
    assert judge_value(student, answer)[0] == verdict


def test_missing_unit_counts_but_is_noted() -> None:
    assert judge_value("3", "3\\,\\mathrm{m/s}") == (
        "same",
        "단위(m/s)를 쓰지 않음 — 감점할지 확인",
    )
    assert split_unit("2R") == ("2R", None), "모르는 글자는 단위가 아니라 변수"
    assert split_unit("90^\\circ") == ("90", "^\\circ")


# ---------------------------------------------------------------- 루브릭 (AI 없음)


def test_code_rubric_from_key_and_item() -> None:
    r = code_rubric(mc(), AnswerKey.model_validate(
        {"question_number": "4", "source": "s", "parts": [{"label": "", "answer": "ㄱ, ㄷ",
                                                           "credit_policy": "none"}]}))  # fmt: skip
    assert r.code_graded and r.answer_rules[0].answers == ["3"] and r.total == 3
    assert "③" in r.items[0].criterion and r.agent == "code"
    r = code_rubric(mc(answer=[2, 4]), None)
    assert r.answer_rules[0].answers == ["2", "4"]
    assert any("어느 것이든 정답" in n for n in r.review_notes)
    r = code_rubric(short(), SHORT_KEY)
    assert [x.part for x in r.answer_rules] == ["(1)", "(2)"] and r.total == 4


def test_approve_needs_an_answer() -> None:
    r = code_rubric(mc(), None)
    with pytest.raises(PipelineError, match="정답이 없는 소문항"):
        approve_rubric(r, "강사")
    r = approve_rubric(set_answers(r, "", ["③"], by="강사"), "강사")
    assert r.status == "approved" and r.answer_rules[0].answers == ["3"]
    again = set_answers(r, "", ["3", "4"])
    assert (again.status, again.version) == ("draft", r.version + 1), "고치면 다시 승인"
    with pytest.raises(PipelineError, match="선지 번호가 아닙니다"):
        set_answers(r, "", ["가"])


def test_teacher_edit_keeps_answer_rules() -> None:
    r = code_rubric(short(), SHORT_KEY)
    draft = RubricDraft.model_validate(r.model_dump(include={"items"}))
    edited = edit_rubric(r, draft, short(), None)
    assert edited.answer_rules == r.answer_rules


# ---------------------------------------------------------------- 채점


EngineWith = Callable[[list[dict[str, Any]]], tuple[Any, Path]]


@pytest.fixture
def engine_with(app_paths: AppPaths, make_fake: MakeFake) -> EngineWith:
    from dawoni.agents import AgentClient
    from dawoni.context import ContextEngine

    def make(responses: list[dict[str, Any]]) -> tuple[Any, Path]:
        exe = make_fake(
            "script", script=[{"response": json.dumps(r, ensure_ascii=False)} for r in responses]
        )
        cfg = AppConfig()
        cfg.agents["agy"].executable = str(exe)
        cfg.agents["claude"].enabled = False
        cfg.agents["codex"].enabled = False
        return ContextEngine(AgentClient(cfg, app_paths)), exe

    return make


def test_multiple_choice_needs_no_ai(engine_with: EngineWith) -> None:
    engine, exe = engine_with([])
    q = mc()
    r = approve_rubric(set_answers(code_rubric(q, None), "", ["3"]), "강사")
    ts = [t("A", "4", (None, "3")), t("B", "4", (None, "③")), t("C", "4", (None, "4")),
          t("D", "4"), t("E", "4", (None, "3, 4")), t("F", "4", (None, "잘 모르겠음"))]  # fmt: skip
    gs = {g.student_id: g for g in grade_by_code(engine, q, r, ts, SCOPE)}
    assert {k: g.total for k, g in gs.items()} == {"A": 3, "B": 3, "C": 0, "D": 0, "E": 0, "F": 0}
    assert gs["C"].judgements[0].deduction_reason == "정답은 ③"
    assert gs["F"].status == "needs_review" and gs["A"].status == "auto"
    assert all(g.agent == "code" for g in gs.values())
    assert fake_prompts(exe) == []


def test_multiple_and_all_correct(engine_with: EngineWith) -> None:
    engine, _ = engine_with([])
    q = mc()
    both = approve_rubric(set_answers(code_rubric(q, None), "", ["3", "4"]), "강사")
    pair = approve_rubric(set_answers(code_rubric(q, None), "", ["2,4"]), "강사")
    everyone = approve_rubric(set_answers(code_rubric(q, None), "", [], all_correct=True), "강사")
    ts = [t("A", "4", (None, "4")), t("B", "4", (None, "2, 4")), t("C", "4")]
    total = {k: [g.total for g in grade_by_code(engine, q, r, ts, SCOPE)]
             for k, r in [("both", both), ("pair", pair), ("all", everyone)]}  # fmt: skip
    assert total == {"both": [3, 0, 0], "pair": [0, 3, 0], "all": [3, 3, 3]}


def test_ambiguous_short_answers_share_one_helper_call(engine_with: EngineWith) -> None:
    engine, exe = engine_with(
        [{"answers": [{"id": "답1", "verdict": "same", "reason": "같은 방향"}]}]
    )
    q = short()
    r = approve_rubric(code_rubric(q, SHORT_KEY), "강사")
    ts = [t("A", "5", ("(1)", "0.5 m/s"), ("(2)", "북")),
          t("B", "5", ("(1)", "\\frac13"), ("(2)", "북쪽")),
          t("C", "5", ("(1)", "v = \\frac{1}{2}"), ("(2)", "북"))]  # fmt: skip
    gs = {g.student_id: g for g in grade_by_code(engine, q, r, ts, SCOPE)}
    assert {k: g.total for k, g in gs.items()} == {"A": 4, "B": 2, "C": 4}, [
        g.flags for g in gs.values()
    ]
    prompts = fake_prompts(exe)
    assert len(prompts) == 1, "같은 답 '북'은 한 번만 묻는다"
    assert "[답1] 소문항 (2): 북" in prompts[0] and "[답2]" not in prompts[0]
    assert "Role: light helper." in prompts[0]
    assert any("단위(m/s)를 쓰지 않음" in f for f in gs["C"].flags)
    assert gs["C"].status == "needs_review" and gs["B"].status == "auto"


def test_helper_failure_leaves_review_not_failure(engine_with: EngineWith) -> None:
    engine, _ = engine_with([{"answers": []}, {"answers": []}])
    q = short()
    r = approve_rubric(code_rubric(q, SHORT_KEY), "강사")
    (g,) = grade_by_code(engine, q, r, [t("A", "5", ("(1)", "0.5"), ("(2)", "북"))], SCOPE)
    assert g.total == 2 and g.status == "needs_review"
    assert any("'북' — 정답 북쪽와 같은지 확인" in f for f in g.flags)


def test_grade_answer_dispatches_to_code(engine_with: EngineWith) -> None:
    engine, exe = engine_with([])
    q = mc()
    r = approve_rubric(set_answers(code_rubric(q, None), "", ["3"]), "강사")
    g = grade_answer(engine, q, r, t("A", "4", (None, "3")), SCOPE)
    assert g.total == 3 and fake_prompts(exe) == []


# ---------------------------------------- 시험 하나: 호출 수 = 서술형 문항 수


def _essay_q3() -> ExamQuestion:
    base = read_json(FIX / "questions.json")[0]
    return ExamQuestion.model_validate(
        {**base, "number": "3", "points": 10,
         "parts": [{"label": "(1)", "points": 4, "kind": "short_answer"},
                   {"label": "(2)", "points": 6, "kind": "essay"}]}
    )  # fmt: skip


def _services(app_paths: AppPaths, tmp_path: Path, make_fake: MakeFake,
              script: list[dict[str, Any]]) -> tuple[Services, ExamStore, Path]:  # fmt: skip
    exe = make_fake(
        "script", script=[{"response": json.dumps(r, ensure_ascii=False)} for r in script]
    )
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.agents["claude"].enabled = False
    cfg.agents["codex"].enabled = False
    save_config(app_paths, cfg)
    ws = create_workspace(tmp_path / "ws")
    ScopeStore(ws).save(SCOPE)
    exam = Exam(title="혼합", scope_id=SCOPE.id, questions=[_essay_q3(), mc(), short()])
    store = ExamStore.create(ws, exam)
    store.save_key(AnswerKey.model_validate(KEY))
    store.save_key(SHORT_KEY)
    return open_services(ws.root, paths=app_paths), store, exe


def test_exam_rubric_calls_equal_essay_questions(
    app_paths: AppPaths, tmp_path: Path, make_fake: MakeFake
) -> None:
    svc, store, exe = _services(app_paths, tmp_path, make_fake, [EXPANDED])
    rubrics = {r.question_number: r for r in workflow.rubric_questions(svc, store)}
    assert len(fake_prompts(exe)) == 1, "서술형 1문항 = 호출 1번, 객관식·단답형은 0번"
    assert not rubrics["3"].code_graded
    assert rubrics["4"].code_graded and rubrics["5"].code_graded
    assert any("정답이 없음" in n for n in rubrics["4"].review_notes), "MC는 해설지·정답이 없음"

    store.save_rubric(approve_rubric(set_answers(rubrics["4"], "", ["3"]), "강사"))
    store.save_rubric(approve_rubric(rubrics["5"], "강사"))
    for st, ans in (("A", "3"), ("B", "1")):
        store.save_transcript(t(st, "4", (None, ans)))
        store.save_transcript(t(st, "5", ("(1)", "1/2"), ("(2)", "북쪽")))
    run = workflow.grade_exam(svc, store, only=["4", "5"])
    assert len(fake_prompts(exe)) == 1, "채점도 AI 호출 없음"
    totals = {(g.student_id, g.question_number): g.total for g in run.graded}
    assert totals == {("A", "4"): 3, ("B", "4"): 0, ("A", "5"): 4, ("B", "5"): 4}
    saved = store.grade("A", "4")
    assert saved is not None and saved.agent == "code"


def test_remaking_keeps_teacher_answers() -> None:
    first = set_answers(code_rubric(mc(), None), "", ["3", "4"])
    again = code_rubric(mc(answer=[1]), None, previous=first)
    assert again.answer_rules[0].answers == ["3", "4"] and again.version == first.version + 1
    assert any("고친 정답을 그대로" in n for n in again.review_notes)
