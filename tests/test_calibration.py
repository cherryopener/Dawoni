"""M8 강사 보정: 기준 예시 고르기(자기 답안 제외·고정·다양성), 프롬프트 반영, 일치도,
강사 직접 채점, 보정 효과 측정, 루브릭 개선 제안."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import MakeFake
from test_grading import FIX, GRADE_A, RUBRIC_OK, SOL_OK, approved_rubric, transcript_of
from tests_support import fake_prompts

from dawoni.config import AppConfig, save_config
from dawoni.fsio import read_json
from dawoni.grading import calibration, workflow
from dawoni.grading.pipeline import (
    PipelineError,
    check_suggestion,
    teacher_grade,
    teacher_review,
)
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.models.calibration import CalibrationSettings, RubricSuggestion
from dawoni.models.grading import (
    Exam,
    ExamQuestion,
    GradeResult,
    ItemJudgement,
    Rubric,
    ScopedSolution,
    SolutionDraft,
)
from dawoni.models.scope import ScopeProfile
from dawoni.paths import AppPaths
from dawoni.services import Services, open_services
from dawoni.workspace import create_workspace


def ai_grade(student: str, r1: float, r2: float, *, status: str = "auto") -> GradeResult:
    return GradeResult(
        student_id=student,
        question_number="1",
        rubric_version=1,
        judgements=[
            ItemJudgement(rubric_item_id="R1", awarded=r1, reason=f"{student} R1", confidence=0.9),
            ItemJudgement(rubric_item_id="R2", awarded=r2, reason=f"{student} R2", confidence=0.9),
        ],
        comment="",
        total=r1 + r2,
        max_points=4,
        status=status,  # pyright: ignore[reportArgumentType]
        agent="agy",
        item_points={"R1": 2, "R2": 2},
    )


@pytest.fixture
def setup(
    app_paths: AppPaths, tmp_path: Path, make_fake: MakeFake
) -> Callable[[list[dict[str, Any]]], tuple[Services, ExamStore, Path]]:
    """학생 A(강사 확인)·B(직접 채점)·D(강사가 고침)·E(자동 채점, 미확정) + 채점 대상 F."""

    def make(script: list[dict[str, Any]]) -> tuple[Services, ExamStore, Path]:
        exe = make_fake(
            "script", script=[{"response": json.dumps(r, ensure_ascii=False)} for r in script]
        )
        cfg = AppConfig()
        cfg.agents["agy"].executable = str(exe)
        cfg.agents["claude"].enabled = False
        cfg.agents["codex"].enabled = False
        save_config(app_paths, cfg)
        ws = create_workspace(tmp_path / "ws")
        ScopeStore(ws).save(ScopeProfile.model_validate(read_json(FIX / "scope.json")))
        q1 = ExamQuestion.model_validate(read_json(FIX / "questions.json")[0])
        store = ExamStore.create(
            ws, Exam(title="보정", scope_id="scope-quadratic-minmax", questions=[q1])
        )
        store.save_solution(ScopedSolution(**SolutionDraft.model_validate(SOL_OK).model_dump(),
                                           question_number="1", scope_version=1))  # fmt: skip
        rubric = approved_rubric()
        store.save_rubric(rubric)
        for name, st in (
            ("A_q1.txt", "A"),
            ("B_q1.txt", "B"),
            ("D_q1.txt", "D"),
            ("E_q1.txt", "E"),
            ("A_q1.txt", "F"),
        ):
            store.save_transcript(transcript_of(name, st))
        store.save_grade(teacher_review(ai_grade("A", 2, 2), rubric, {}, by="강사"))
        store.save_grade(
            teacher_grade(
                transcript_of("B_q1.txt", "B"),
                rubric,
                {"R1": 2, "R2": 2},
                item_notes={"R1": "꼭짓점 공식도 인정"},
                by="강사",
            )
        )
        store.save_grade(
            teacher_review(
                ai_grade("D", 2, 1),
                rubric,
                {"R1": 1, "R2": 0},
                note="계산 실수 후 결론이 틀림",
                item_notes={"R2": "최솟값 11은 0점"},
            )
        )
        store.save_grade(ai_grade("E", 0, 0))
        return open_services(ws.root, paths=app_paths), store, exe

    return make


def test_candidates_and_selection(setup: Any) -> None:
    _, store, _ = setup([])
    rubric = store.rubric("1")
    cands = calibration.candidates(store, "1", rubric)
    kinds = {c.student: c.kind for c in cands}
    assert kinds == {"A": "confirmed", "B": "manual", "D": "correction"}, "미확정(E)은 후보 아님"

    target = store.transcript("A", "1")
    mine = calibration.candidates(store, "1", rubric, exclude_student="A")
    assert "A" not in {c.student for c in mine}, "채점 대상 자신의 답안은 예시가 될 수 없음"

    order = [c.student for c in calibration.select_examples(cands, target, 3)]
    assert order[0] == "D", "강사가 고친 사례가 먼저"
    assert set(order) == {"A", "B", "D"}
    assert [c.student for c in calibration.select_examples(cands, target, 2)] == ["D", "B"], (
        "같은 총점(A·B 4점)이 겹치지 않게: D(1점) 다음은 가중치가 높은 B"
    )

    calibration.save_settings(
        store, CalibrationSettings(question_number="1", pinned=["A"], excluded=["D"])
    )
    cands = calibration.candidates(store, "1", rubric)
    assert {c.student for c in cands} == {"A", "B"}
    assert [c.student for c in calibration.select_examples(cands, target, 1)] == ["A"], "고정 우선"


def test_incompatible_rubric_examples_are_skipped(setup: Any) -> None:
    _, store, _ = setup([])
    r = store.rubric("1")
    assert r is not None
    data = r.model_dump()
    data["items"][0]["points"] = 1
    data["items"][1]["points"] = 3  # 합계(4)는 같고 항목 배점만 다름
    r2 = Rubric.model_validate({**data, "version": 2})
    assert calibration.candidates(store, "1", r2) == [], (
        "배점 구조가 바뀐 루브릭에는 옛 예시를 쓰지 않음"
    )
    same = Rubric.model_validate({**r.model_dump(), "version": 2})
    assert len(calibration.candidates(store, "1", same)) == 3, "구조가 같으면 버전이 달라도 사용"


def test_exemplar_text_marks_corrections(setup: Any) -> None:
    _, store, _ = setup([])
    rubric = store.rubric("1")
    d = next(c for c in calibration.candidates(store, "1", rubric) if c.student == "D")
    text = calibration.exemplar_text(d, 1, rubric)
    assert "강사가 AI 점수를 고친 채점" in text and "총점 1/4" in text
    assert "R2 0/2점 (AI는 1점을 줬으나 강사가 0점으로 고침): 최솟값 11은 0점" in text
    assert "강사 메모: 계산 실수 후 결론이 틀림" in text
    assert "(x-2)^2+11" in text, "예시 답안 포함"
    layer = calibration.examples_layer([d], rubric)
    assert layer is not None and len(layer.variants) == 3 and "(x-2)^2+11" not in layer.variants[1]


def test_agreement_stats(setup: Any) -> None:
    _, store, _ = setup([])
    st = calibration.question_agreement(store, "1")
    assert st.n == 2, "AI 채점을 강사가 확정한 A·D만 (직접 채점 B 제외)"
    assert st.exact == 0.5 and st.within1 == 0.5
    assert st.item_agreement == 0.5 and st.item_disagreements == {"R1": 1, "R2": 1}
    assert st.bias == 1.0 and st.mean_abs_diff == 1.0, "D: AI 3점 vs 강사 1점 → AI가 후함"


def test_teacher_grade_validation(setup: Any) -> None:
    _, store, _ = setup([])
    rubric = store.rubric("1")
    t = store.transcript("F", "1")
    assert rubric is not None and t is not None
    with pytest.raises(PipelineError, match="정하지 않은 항목"):
        teacher_grade(t, rubric, {"R1": 2})
    with pytest.raises(PipelineError, match="사이여야"):
        teacher_grade(t, rubric, {"R1": 3, "R2": 0})
    g = teacher_grade(t, rubric, {"R1": 2, "R2": 1}, item_notes={"R2": "x값 누락"})
    assert g.agent is None and g.status == "teacher_confirmed" and g.total == 3
    assert g.judgements[1].deduction_reason == "x값 누락"


def test_grading_uses_examples_but_never_own_answer(setup: Any) -> None:
    svc, store, exe = setup([GRADE_A])
    run = workflow.grade_exam(svc, store, students=["F"])
    assert len(run.graded) == 1
    g = run.graded[0]
    assert g.examples_used and "F" not in g.examples_used
    prompt = fake_prompts(exe)[0]
    assert "[기준 예시 1 — 강사가 AI 점수를 고친 채점]" in prompt
    assert "같은 실수를 반복하지 말라" in prompt, "채점 지침 10번"
    assert prompt.index("[기준 예시 1") < prompt.index("[학생 답안]")

    svc2, store2, exe2 = setup([GRADE_A])
    calibration.save_settings(store2, CalibrationSettings(question_number="1", use_examples=False))
    g2 = workflow.grade_exam(svc2, store2, students=["F"]).graded[0]
    assert g2.examples_used == [] and "[기준 예시 1" not in fake_prompts(exe2)[0]


def test_evaluate_calibration(setup: Any) -> None:
    # 강사 확정 A(4)·B(4)·D(1) 각각: 예시 없이 → 예시와 함께 (자기 답안 제외)
    # A: 4 → 4, B: 4 → 4, D: 3 → 1  (예시가 D의 과대 채점을 바로잡음)
    script = [GRADE_A, GRADE_A, _b(), _b(), _d(2, 1), _d(1, 0)]
    svc, store, exe = setup(script)
    ev = workflow.evaluate_calibration(svc, store, "1")
    by = {c.student_id: c for c in ev.cases}
    assert [c.error for c in ev.cases] == [None, None, None]
    assert by["D"].before_total == 3 and by["D"].after_total == 1
    assert "D" not in by["D"].examples_used and by["D"].examples_used, "자기 답안 제외"
    assert ev.before.exact is not None and ev.after.exact is not None
    assert ev.after.exact > ev.before.exact and ev.after.mean_abs_diff == 0
    assert calibration.list_evals(store, "1")[0].id == ev.id
    prompts = fake_prompts(exe)
    assert "[기준 예시 1" not in prompts[0] and "[기준 예시 1" in prompts[1]
    assert store.grade("D", "1").total == 1, "측정은 저장된 채점을 건드리지 않음"  # pyright: ignore[reportOptionalMemberAccess]


def _b() -> dict[str, Any]:
    return {
        "judgements": [
            {"rubric_item_id": "R1", "awarded": 2, "evidence_quote": "x=-\\frac{-4}{2\\times1}=2",
             "reason": "꼭짓점 공식", "confidence": 0.9},
            {"rubric_item_id": "R2", "awarded": 2, "evidence_quote": "최솟값 3 (x=2)",
             "reason": "결론", "confidence": 0.9},
        ],
        "comment": "좋습니다.",
    }  # fmt: skip


def _d(r1: float, r2: float) -> dict[str, Any]:
    """D 답안(식: y=(x-2)^2+11 / 그러므로 최솟값은 11)에 맞는 채점 응답."""
    return {
        "judgements": [
            {"rubric_item_id": "R1", "awarded": r1, "evidence_quote": "y=(x-2)^2+11" if r1 else None,
             "reason": "표준형", "deduction_reason": None if r1 == 2 else "상수항 계산 실수", "confidence": 0.9},
            {"rubric_item_id": "R2", "awarded": r2, "evidence_quote": "최솟값은 11" if r2 else None,
             "reason": "결론", "deduction_reason": "최솟값 틀림", "confidence": 0.9},
        ],
        "comment": "계산을 확인하세요.",
    }  # fmt: skip


def test_evaluate_needs_two_confirmed(setup: Any) -> None:
    svc, store, _ = setup([])
    calibration.save_settings(store, CalibrationSettings(question_number="1", excluded=["A", "B"]))
    with pytest.raises(PipelineError, match="2개 이상"):
        workflow.evaluate_calibration(svc, store, "1")


SUGGESTION: dict[str, Any] = {
    "changes": [
        {"item_id": "R2", "kind": "clarify_criterion",
         "summary": "결론의 최솟값이 틀리면 R2는 0점임을 기준에 명시", "evidence": ["D"]},
    ],
    "revised": {**RUBRIC_OK, "items": [
        RUBRIC_OK["items"][0],
        {**RUBRIC_OK["items"][1], "criterion": "x=2일 때 최솟값 3을 제시 (최솟값이 틀리면 0점)"},
    ]},
}  # fmt: skip


def test_suggest_rubric_repairs_and_merges_defaults(setup: Any) -> None:
    bad = json.loads(json.dumps(SUGGESTION))
    bad["changes"][0]["evidence"] = ["Z"]
    bad["revised"]["items"][0]["points"] = 3
    svc, store, exe = setup([bad, SUGGESTION])
    run = workflow.suggest_rubric_changes(svc, store, "1")
    assert run.cases == ["D"], "강사가 고친 사례만 자료로"
    s = run.suggestion
    assert s.revised is not None and "최솟값이 틀리면 0점" in s.revised.items[1].criterion
    assert any("범위 밖의 고급 기법" in x for x in s.revised.never_deduct), "기본 감점 금지 병합"
    prompts = fake_prompts(exe)
    assert len(prompts) == 2 and "강사가 고침" in prompts[0] and "최솟값 11은 0점" in prompts[0]
    assert "['Z']는 [자료]의 사례가 아니다" in prompts[1] and "합" in prompts[1]


def test_check_suggestion_consistency(setup: Any) -> None:
    _, store, _ = setup([])
    q = store.exam().question("1")
    sol = store.solution("1")
    assert sol is not None
    empty = RubricSuggestion.model_validate({"changes": [], "revised": None})
    assert any("no_change_reason" in p for p in check_suggestion(empty, q, sol, {"D"}))
    half = RubricSuggestion.model_validate({"changes": SUGGESTION["changes"], "revised": None})
    assert any("revised" in p for p in check_suggestion(half, q, sol, {"D"}))
    ok = RubricSuggestion.model_validate(SUGGESTION)
    assert check_suggestion(ok, q, sol, {"D"}) == []


def test_suggest_needs_corrections(setup: Any) -> None:
    svc, store, _ = setup([])
    calibration.save_settings(store, CalibrationSettings(question_number="1", excluded=["D"]))
    with pytest.raises(PipelineError, match="고친 사례가 없습니다"):
        workflow.suggest_rubric_changes(svc, store, "1")
