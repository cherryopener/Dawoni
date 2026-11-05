"""강사가 직접 매긴 소문항 점수 → 강사 확정 채점·기준 예시·루브릭 개선 사례.

강사는 빨간 펜으로 소문항마다 점수를 매긴다 (실측 24명). 루브릭 항목 단위가 아니다.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import MakeFake
from test_answer_key import GOOD, SOL
from test_grading import FIX
from tests_support import fake_prompts

from dawoni.cli import main
from dawoni.config import AppConfig, save_config
from dawoni.fsio import read_json
from dawoni.grading import calibration, workflow
from dawoni.grading.pipeline import (
    PipelineError,
    approve_rubric,
    spread_part_score,
    teacher_part_review,
)
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.models.grading import (
    Exam,
    ExamQuestion,
    GradeResult,
    ItemJudgement,
    Rubric,
    RubricDraft,
    ScopedSolution,
    Transcript,
    TranscriptLine,
)
from dawoni.models.scope import ScopeProfile
from dawoni.paths import AppPaths
from dawoni.services import Services, open_services
from dawoni.workspace import create_workspace

Setup = Callable[[list[dict[str, Any]]], tuple[Services, ExamStore, Path]]


def _q3() -> ExamQuestion:
    base = read_json(FIX / "questions.json")[0]
    return ExamQuestion.model_validate(
        {**base, "number": "3", "points": 10,
         "parts": [{"label": "(1)", "points": 4, "kind": "short_answer"},
                   {"label": "(2)", "points": 6, "kind": "essay"}]}
    )  # fmt: skip


def _rubric() -> Rubric:
    r = Rubric(**RubricDraft.model_validate(GOOD).model_dump(), question_number="3")
    return approve_rubric(r, "강사")


def _ai(student: str, scores: dict[str, float]) -> GradeResult:
    r = _rubric()
    js = [
        ItemJudgement(rubric_item_id=i.id, awarded=scores.get(i.id, 0), reason="AI",
                      deduction_reason=None if scores.get(i.id, 0) == i.points else "부족",
                      confidence=0.9)
        for i in r.items
    ]  # fmt: skip
    return GradeResult(
        student_id=student, question_number="3", rubric_version=r.version, judgements=js,
        comment="", total=sum(j.awarded for j in js), max_points=10, agent="agy",
        item_points={i.id: i.points for i in r.items},
    )  # fmt: skip


def _transcript(student: str) -> Transcript:
    return Transcript(
        student_id=student,
        question_number="3",
        confirmed=True,
        lines=[TranscriptLine(text="v0 = nR cos α √(g/2H)", part="(1)"),
               TranscriptLine(text="θ=90°이면 정삼각형, sinθ=1", part="(2)")],
    )  # fmt: skip


@pytest.fixture
def setup(app_paths: AppPaths, tmp_path: Path, make_fake: MakeFake) -> Setup:
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
        store = ExamStore.create(
            ws, Exam(title="강사 점수", scope_id="scope-quadratic-minmax", questions=[_q3()])
        )
        store.save_solution(ScopedSolution(**SOL, question_number="3", scope_version=1))
        store.save_rubric(_rubric())
        for st in ("A", "B", "C"):
            store.save_transcript(_transcript(st))
        store.save_grade(_ai("A", {"R1": 4, "R2": 2}))
        return open_services(ws.root, paths=app_paths), store, exe

    return make


# ---------------------------------------------------------------- 나누기


@pytest.mark.parametrize(
    ("items", "score", "want"),
    [
        ([("R1", 2, 2), ("R2", 2, 2)], 4, {"R1": 2, "R2": 2}),  # 같으면 그대로
        ([("R1", 2, 2), ("R2", 2, 2)], 2, {"R1": 1, "R2": 1}),  # AI 비율대로
        ([("R1", 2, 0), ("R2", 4, 0)], 5, {"R1": 2, "R2": 3}),  # AI 0점 → 앞부터
        ([("R1", 2, 2), ("R2", 2, 0)], 3, {"R1": 2, "R2": 1}),  # 배점을 넘지 않게
        ([("R1", 4, 1)], 4, {"R1": 4}),
    ],
)
def test_spread_part_score(
    items: list[tuple[str, float, float]], score: float, want: dict[str, float]
) -> None:
    got = spread_part_score(items, score)
    assert got == want and abs(sum(got.values()) - score) < 1e-9


def test_part_review_keeps_ai_and_notes() -> None:
    r = _rubric()
    g = teacher_part_review(
        _ai("A", {"R1": 4, "R2": 2}), r, _q3(), {"(1)": 0, "(2)": 2},
        part_notes={"(1)": "n개가 아니라 n+1개"}, by="강사",
    )  # fmt: skip
    assert g.status == "teacher_confirmed" and g.total == 2
    assert g.teacher_part_scores == {"(1)": 0, "(2)": 2}
    assert g.ai_judgements is not None and g.ai_judgements[0].awarded == 4
    assert g.teacher_item_notes["R1"] == "[(1)] n개가 아니라 n+1개"
    with pytest.raises(PipelineError, match="없는 소문항"):
        teacher_part_review(g, r, _q3(), {"(3)": 1})
    with pytest.raises(PipelineError, match="0~4"):
        teacher_part_review(g, r, _q3(), {"(1)": 5})


def test_parse_teacher_csv() -> None:
    rows = workflow.parse_teacher_csv(
        "학생,문항,소문항,점수,이유\n# 메모\nA,3,(1),0,n+1개\nB,1,,5\n\nC,3,전체,2\n"
    )
    assert [(r.student, r.question, r.part, r.points, r.note) for r in rows] == [
        ("A", "3", "(1)", 0.0, "n+1개"), ("B", "1", "", 5.0, ""), ("C", "3", "", 2.0, ""),
    ]  # fmt: skip
    with pytest.raises(PipelineError, match="숫자가 아닙니다"):
        workflow.parse_teacher_csv("A,3,(1),0\nB,3,(1),많이\n")


# ---------------------------------------------------------------- 넣기·예시·개선


GRADE_C = {
    "judgements": [
        {"rubric_item_id": "R1", "awarded": 0, "reason": "n개", "deduction_reason": "n+1개 아님",
         "confidence": 0.9},
        {"rubric_item_id": "R2", "awarded": 0, "reason": "-", "deduction_reason": "없음",
         "confidence": 0.9},
        {"rubric_item_id": "R3", "awarded": 0, "reason": "-", "deduction_reason": "없음",
         "confidence": 0.9},
        {"rubric_item_id": "R4", "awarded": 2, "reason": "sinθ=1", "evidence_quote": "sinθ=1",
         "confidence": 0.9},
    ],
    "comment": "",
}  # fmt: skip


def test_import_grades_missing_then_reviews(setup: Setup) -> None:
    svc, store, exe = setup([GRADE_C])
    rows = workflow.parse_teacher_csv(
        "A,3,(1),0,n개가 아니라 n+1개\nA,3,(2),2\nC,3,(2),4,과정 일부 인정\nZ,3,(1),0\nA,9,(1),1\n"
    )
    res = workflow.import_teacher_scores(svc, store, rows, by="강사")
    assert res.graded_first == ["C 3"], "AI 채점이 없던 답안은 먼저 채점"
    assert len(fake_prompts(exe)) == 1
    saved = {g.student_id: g for g in res.saved}
    assert saved["A"].total == 2 and saved["C"].teacher_part_scores == {"(2)": 4}
    assert {f.student for f in res.failed} == {"A"}, "없는 문항 9는 실패 (Z의 빈 답안 0점은 건너뜀)"

    rubric = store.rubric("3")
    assert rubric is not None
    cases = calibration.candidates(store, "3", rubric)
    assert {c.student for c in cases} == {"A", "C"}
    text = calibration.corrections_text(cases, rubric)
    assert "소문항 (1): 강사 0/4점 (AI는 4점) — n개가 아니라 n+1개" in text
    assert "소문항 (2): 강사 4/6점 (AI는 2점) — 과정 일부 인정" in text


def test_regrade_keeps_teacher_scores(setup: Setup) -> None:
    svc, store, _ = setup([])
    rows = workflow.parse_teacher_csv("A,3,(1),0\n")
    workflow.import_teacher_scores(svc, store, rows)
    run = workflow.grade_exam(svc, store, students=["A"], regrade=True)
    assert not run.graded and run.skipped[0].reason.startswith("강사 확정")
    g = store.grade("A", "3")
    assert g is not None and g.status == "teacher_confirmed" and g.total == 2


def test_suggest_keeps_key_checks_and_candidates(setup: Setup) -> None:
    revised = copy.deepcopy(GOOD)
    revised["items"][0]["criterion"] = "(n+1)개 구간 — n개로 쓰면 0점"
    revised["checks"], revised["candidates"] = [], []
    suggestion = {
        "changes": [{"item_id": "R1", "kind": "clarify_criterion",
                     "summary": "n개로 쓴 답은 0점", "evidence": ["A"]}],
        "revised": revised,
        "transcript_doubts": ["C (2): 판독문으로는 설명 안 되는 강사 점수"],
    }  # fmt: skip
    svc, store, exe = setup([suggestion])
    r = store.rubric("3")
    assert r is not None
    accepted = r.candidates[0].model_copy(update={"status": "accepted"})
    store.save_rubric(r.model_copy(update={"candidates": [accepted]}))
    workflow.import_teacher_scores(svc, store, workflow.parse_teacher_csv("A,3,(1),0,n+1개\n"))
    run = workflow.suggest_rubric_changes(svc, store, "3")
    s = run.suggestion.revised
    assert s is not None and s.items[0].criterion.startswith("(n+1)개")
    assert [c.part for c in s.checks] == ["(1)", "(2)"], "해설지 비교는 제안이 바꾸지 못함"
    assert s.candidates[0].status == "accepted", "강사가 인정한 후보 유지"
    assert run.suggestion.transcript_doubts == ["C (2): 판독문으로는 설명 안 되는 강사 점수"]
    prompt = fake_prompts(exe)[0]
    assert "소문항 (1): 강사 0/4점 (AI는 4점) — n+1개" in prompt
    assert "판독 오류" in prompt, "판독 오류를 기준으로 배우지 않게 안내"


def test_cli_teacher_scores(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    svc, store, _ = setup([])
    w = ["--workspace", str(svc.workspace.root)]
    assert main(["exam", *w, "teacher-scores", store.exam_id, "--student", "A", "-q", "3",
                 "--part", "(1)=0", "--note", "(1)=n+1개"]) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert "A 문항 3: 강사 2점" in out and "(1) 0 (AI 4)" in out
