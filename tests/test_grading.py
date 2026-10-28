"""채점 파이프라인: 코드 검증, 수리 루프, 강사 단계 관문, 이중 채점, 명령줄 관통."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tests_support import fake_prompts

from dawoni.agents import AgentClient
from dawoni.cli import main
from dawoni.config import AppConfig, save_config
from dawoni.context import ContextEngine
from dawoni.fsio import read_json
from dawoni.grading import checks
from dawoni.grading.pipeline import (
    PipelineError,
    SecondOpinion,
    approve_rubric,
    derive_rubric,
    grade_answer,
    solve_question,
)
from dawoni.grading.store import ExamStore, safe_name
from dawoni.models.grading import (
    DEFAULT_NEVER_DEDUCT,
    ExamQuestion,
    FinalAnswer,
    GradeOutput,
    Rubric,
    RubricDraft,
    ScopedSolution,
    SolutionDraft,
    Transcript,
    TranscriptLine,
)
from dawoni.models.scope import ScopeProfile
from dawoni.paths import AppPaths
from dawoni.workspace import create_workspace

FIX = Path(__file__).parent / "fixtures" / "grading"
MakeFake = Callable[..., Path]


@pytest.fixture
def scope() -> ScopeProfile:
    return ScopeProfile.model_validate(read_json(FIX / "scope.json"))


@pytest.fixture
def q1() -> ExamQuestion:
    return ExamQuestion.model_validate(read_json(FIX / "questions.json")[0])


def transcript_of(name: str, student: str = "학생") -> Transcript:
    from dawoni.cli_exam import parse_answer_text

    text = (FIX / "answers" / name).read_text(encoding="utf-8")
    return Transcript(
        student_id=student, question_number="1", lines=parse_answer_text(text), confirmed=True
    )


SOL_OK: dict[str, Any] = {
    "steps": [
        {
            "explanation": "완전제곱식으로 표준형을 만든다",
            "math": "y=(x-2)^2+3",
            "uses": ["S1", "C2"],
        },
        {
            "explanation": "a>0이므로 꼭짓점에서 최솟값을 갖는다",
            "math": "x=2,\\ y=3",
            "uses": ["C3"],
        },
    ],
    "final_answer": {"latex": "3", "expr": "3"},
    "alternatives": [
        {
            "summary": "꼭짓점의 x좌표 공식",
            "steps": [{"explanation": "x=-b/(2a)", "math": "x=2", "uses": ["S2"]}],
        }
    ],
}
SOL_BAD: dict[str, Any] = {
    "steps": [
        {"explanation": "도함수가 0인 점을 찾는다", "math": "y'=2x-4=0", "uses": ["C9"]},
        {"explanation": "그때의 값", "math": "y=3", "uses": ["C3"]},
    ],
    "final_answer": {"latex": "3", "expr": "3"},
}
RUBRIC_OK: dict[str, Any] = {
    "items": [
        {
            "id": "R1",
            "points": 2,
            "criterion": "표준형으로 변형하거나 꼭짓점의 x좌표를 구함",
            "evidence_hint": "(x-2)^2+3",
            "solution_steps": [1],
        },
        {
            "id": "R2",
            "points": 2,
            "criterion": "x=2일 때 최솟값 3을 제시",
            "evidence_hint": "최솟값 3",
            "solution_steps": [2],
        },
    ],
    "partial": [{"item_id": "R2", "condition": "계산 실수 후 결론이 일관됨", "points": 1}],
    "accepted_alternatives": [{"description": "꼭짓점 공식", "item_ids": ["R1"]}],
}
GRADE_A: dict[str, Any] = {
    "judgements": [
        {"rubric_item_id": "R1", "awarded": 2, "evidence_quote": "(x-2)^2+3",
         "reason": "표준형 변형", "confidence": 0.95},
        {"rubric_item_id": "R2", "awarded": 2, "evidence_quote": "x=2일 때 최솟값은 3",
         "reason": "결론 정확", "confidence": 0.95},
    ],
    "comment": "완전제곱식으로 깔끔하게 풀었습니다.",
}  # fmt: skip


def approved_rubric(draft: dict[str, Any] = RUBRIC_OK) -> Rubric:
    r = Rubric(**RubricDraft.model_validate(draft).model_dump(), question_number="1")
    return approve_rubric(r, "강사")


# ---------------------------------------------------------------- 코드 검증


def test_solution_scope_check(scope: ScopeProfile) -> None:
    assert checks.check_solution_scope(SolutionDraft.model_validate(SOL_OK), scope) == []
    problems = checks.check_solution_scope(SolutionDraft.model_validate(SOL_BAD), scope)
    assert any("['C9']" in p for p in problems)
    assert any("미분" in p for p in problems), "y'= 같은 미분 표기도 잡는다"


@pytest.mark.parametrize(
    ("quote", "ok"),
    [
        ("(x-2)^2+3", True),
        ("( x - 2 )^{2} + 3", True),
        ("\\left(x-2\\right)^2+3", True),
        ("\\((x-2)^2+3\\)", True),
        ("y=x^2-4x+7 … 최솟값은 3", True),
        ("최솟값은 3 … y=x^2-4x+7", False),  # 순서가 뒤바뀜
        ("(x-1)^2+6", False),  # 지어낸 인용
        ("…", False),
    ],
)
def test_quote_in_transcript(quote: str, ok: bool) -> None:
    assert checks.quote_in_transcript(quote, transcript_of("A_q1.txt")) is ok


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        (("3", "3"), ("3", "6/2"), True),
        (("", "(x-2)**2+3"), ("", "x**2-4*x+7"), True),
        (("", "(-1+sqrt(5))/2"), ("", "(sqrt(5)-1)/2"), True),
        (("3", "3"), ("4", "4"), False),
        (("\\frac{1}{2}", None), ("\\dfrac{1}{2}", None), True),
        (("A", None), ("B", None), None),
    ],
)
def test_answers_agree(
    a: tuple[str, str | None], b: tuple[str, str | None], same: bool | None
) -> None:
    fa = FinalAnswer(latex=a[0] or "-", expr=a[1])
    fb = FinalAnswer(latex=b[0] or "-", expr=b[1])
    assert checks.answers_agree(fa, fb) is same


def _grade(data: dict[str, Any]) -> GradeOutput:
    return GradeOutput.model_validate(data)


def test_check_grade_accepts_good(scope: ScopeProfile) -> None:
    assert (
        checks.check_grade(_grade(GRADE_A), approved_rubric(), transcript_of("A_q1.txt"), scope)
        == []
    )


def test_check_grade_problems(scope: ScopeProfile) -> None:
    rubric, t = approved_rubric(), transcript_of("A_q1.txt")
    g = copy.deepcopy(GRADE_A)
    g["judgements"] = g["judgements"][:1]
    assert any(
        "['R2']에 대한 판정이 없다" in p for p in checks.check_grade(_grade(g), rubric, t, scope)
    )

    g = copy.deepcopy(GRADE_A)
    g["judgements"][0]["awarded"] = 3
    assert any("배점 2을 넘는다" in p for p in checks.check_grade(_grade(g), rubric, t, scope))

    g = copy.deepcopy(GRADE_A)
    g["judgements"][0]["evidence_quote"] = "(x-1)^2+6"
    assert any("학생 답안에 없다" in p for p in checks.check_grade(_grade(g), rubric, t, scope))

    g = copy.deepcopy(GRADE_A)
    g["judgements"][0].update(awarded=1, deduction_reason="미분으로 검산하지 않음")
    assert any("요구하지 않는 기법" in p for p in checks.check_grade(_grade(g), rubric, t, scope))

    g = copy.deepcopy(GRADE_A)
    g["judgements"][1].update(awarded=1)
    assert any("감점 이유" in p for p in checks.check_grade(_grade(g), rubric, t, scope))


def test_blank_answer_cannot_score(scope: ScopeProfile) -> None:
    blank = transcript_of("E_q1.txt")
    assert blank.is_blank
    problems = checks.check_grade(_grade(GRADE_A), approved_rubric(), blank, scope)
    assert any("비어 있는데" in p for p in problems)


def test_rubric_total_check(q1: ExamQuestion) -> None:
    sol = SolutionDraft.model_validate(SOL_OK)
    bad = copy.deepcopy(RUBRIC_OK)
    bad["items"][1]["points"] = 3
    problems = checks.check_rubric(RubricDraft.model_validate(bad), q1, sol)
    assert any("합(5)이 문항 배점(4)" in p for p in problems)
    assert checks.check_rubric(RubricDraft.model_validate(RUBRIC_OK), q1, sol) == []


def test_rubric_model_rejects_bad_refs() -> None:
    bad = copy.deepcopy(RUBRIC_OK)
    bad["partial"][0]["points"] = 5
    with pytest.raises(ValueError, match="부분점수"):
        RubricDraft.model_validate(bad)


def test_safe_name() -> None:
    assert safe_name("서술형 1") == "서술형_1"
    assert safe_name('a/b:c*?"<>|') == "a_b_c"
    with pytest.raises(ValueError):
        safe_name(" / ")


# ---------------------------------------------------------------- 파이프라인 (가짜 CLI)


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


EngineWith = Callable[[list[dict[str, Any]]], tuple[ContextEngine, Path]]


def test_solve_repairs_out_of_scope_step(
    engine_with: EngineWith, q1: ExamQuestion, scope: ScopeProfile
) -> None:
    engine, exe = engine_with([SOL_BAD, SOL_OK])
    sol = solve_question(engine, q1, scope)
    assert isinstance(sol, ScopedSolution) and sol.steps[0].uses == ["S1", "C2"]
    first, second = fake_prompts(exe)
    assert "## [풀이 범위]" in first and "쓰지 않는 기법" in first and "- 미분" in first
    assert "['C9']" in second and "미분" in second


def test_solve_verify_detects_mismatch(
    engine_with: EngineWith, q1: ExamQuestion, scope: ScopeProfile
) -> None:
    other = copy.deepcopy(SOL_OK)
    other["final_answer"] = {"latex": "4", "expr": "4"}
    engine, _ = engine_with([SOL_OK, other])
    sol = solve_question(engine, q1, scope, verify=SecondOpinion())
    assert not sol.verified and "재풀이 답이 다름" in sol.check_notes[0]

    engine, _ = engine_with([SOL_OK, SOL_OK])
    assert solve_question(engine, q1, scope, verify=SecondOpinion()).verified


def test_solve_out_of_scope_note_is_reported_not_repaired(
    engine_with: EngineWith, q1: ExamQuestion, scope: ScopeProfile
) -> None:
    note = {**SOL_BAD, "out_of_scope_note": "범위 안의 방법이 없다"}
    engine, exe = engine_with([note])
    sol = solve_question(engine, q1, scope)
    assert len(fake_prompts(exe)) == 1
    assert sol.out_of_scope_note and sol.check_notes
    with pytest.raises(PipelineError, match="범위 안 해설이 없어"):
        derive_rubric(engine, q1, sol, scope)


def test_rubric_repairs_total_and_adds_defaults(
    engine_with: EngineWith, q1: ExamQuestion, scope: ScopeProfile
) -> None:
    bad = copy.deepcopy(RUBRIC_OK)
    bad["items"][1]["points"] = 3
    sol = ScopedSolution(**SOL_OK, question_number="1", scope_version=1)
    engine, exe = engine_with([bad, RUBRIC_OK])
    r = derive_rubric(engine, q1, sol, scope)
    assert r.total == 4 and r.status == "draft" and r.version == 1
    assert all(d in r.never_deduct for d in DEFAULT_NEVER_DEDUCT)
    assert "합(5)이 문항 배점(4)" in fake_prompts(exe)[1]
    assert "[모범 해설]" in fake_prompts(exe)[0]


def test_grade_gates(engine_with: EngineWith, q1: ExamQuestion, scope: ScopeProfile) -> None:
    engine, _ = engine_with([GRADE_A])
    draft = Rubric(**RubricDraft.model_validate(RUBRIC_OK).model_dump(), question_number="1")
    with pytest.raises(PipelineError, match="승인되지 않았습니다"):
        grade_answer(engine, q1, draft, transcript_of("A_q1.txt"), scope)
    unconfirmed = transcript_of("A_q1.txt").model_copy(update={"confirmed": False})
    with pytest.raises(PipelineError, match="확인하지 않았습니다"):
        grade_answer(engine, q1, approved_rubric(), unconfirmed, scope)


def test_grade_repairs_fabricated_quote(
    engine_with: EngineWith, q1: ExamQuestion, scope: ScopeProfile
) -> None:
    fake = copy.deepcopy(GRADE_A)
    fake["judgements"][0]["evidence_quote"] = "(x-1)^2+6"
    engine, exe = engine_with([fake, GRADE_A])
    g = grade_answer(engine, q1, approved_rubric(), transcript_of("A_q1.txt"), scope)
    assert g.total == 4 and g.max_points == 4 and g.status == "auto"
    first, second = fake_prompts(exe)
    assert "[학생 답안]" in first and "[루브릭]" in first and "감점하지 말 것" in first
    assert "학생 답안에 없다" in second


def test_grade_double_disagreement_needs_review(
    engine_with: EngineWith, q1: ExamQuestion, scope: ScopeProfile
) -> None:
    other = copy.deepcopy(GRADE_A)
    other["judgements"][1].update(awarded=1, deduction_reason="x값을 함께 쓰지 않음")
    engine, _ = engine_with([GRADE_A, other])
    g = grade_answer(
        engine, q1, approved_rubric(), transcript_of("A_q1.txt"), scope, double=SecondOpinion()
    )
    assert g.disagreements == ["R2"] and g.status == "needs_review"
    assert g.total == 4 and g.second_opinion is not None


def test_low_confidence_needs_review(
    engine_with: EngineWith, q1: ExamQuestion, scope: ScopeProfile
) -> None:
    low = copy.deepcopy(GRADE_A)
    low["judgements"][0]["confidence"] = 0.3
    engine, _ = engine_with([low])
    g = grade_answer(engine, q1, approved_rubric(), transcript_of("A_q1.txt"), scope)
    assert g.status == "needs_review" and any("확신도" in f for f in g.flags)


# ---------------------------------------------------------------- 명령줄 관통


def test_cli_exam_end_to_end(
    app_paths: AppPaths,
    make_fake: MakeFake,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws = create_workspace(tmp_path / "ws")
    exe = make_fake(
        "script",
        script=[
            {"response": json.dumps(r, ensure_ascii=False)} for r in (SOL_OK, RUBRIC_OK, GRADE_A)
        ],
    )
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    save_config(app_paths, cfg)
    w = ["--workspace", str(ws.root)]

    assert main(["scope", *w, "import", str(FIX / "scope.json")]) == 0
    one_q = tmp_path / "q1.json"
    one_q.write_text(json.dumps(read_json(FIX / "questions.json")[:1]), encoding="utf-8")
    assert main(["exam", *w, "create", "--title", "모의 채점", "--scope",
                 "scope-quadratic-minmax", "--questions", str(one_q)]) == 0  # fmt: skip
    found = re.search(r"exam-[0-9a-f]+", capsys.readouterr().out)
    assert found is not None
    exam_id = found.group(0)

    assert main(["exam", *w, "solve", exam_id]) == 0
    assert main(["exam", *w, "rubric", exam_id]) == 0
    assert main(["exam", *w, "answer", exam_id, "--student", "A", "-q", "1",
                 str(FIX / "answers" / "A_q1.txt")]) == 0  # fmt: skip
    capsys.readouterr()
    assert main(["exam", *w, "grade", exam_id]) == 1, "승인 전에는 채점 불가"
    assert "승인되지 않았습니다" in capsys.readouterr().err
    assert main(["exam", *w, "approve", exam_id, "-q", "1", "--by", "강사"]) == 0
    assert main(["exam", *w, "grade", exam_id]) == 0
    assert main(["exam", *w, "report", exam_id]) == 0
    out = capsys.readouterr().out
    assert "문항 1: 4/4" in out and "합계: 4/4" in out and "“(x-2)^2+3”" in out
    for kind in ("paper", "key", "report", "scores"):
        assert main(["export", *w, kind, exam_id]) == 0, kind
    assert main(["export", *w, "key", exam_id, "--with-rubric"]) == 0
    out = capsys.readouterr().out
    assert out.count("저장:") == 5 and "✗" not in out
    assert len(list(ws.exports.glob("*.docx"))) == 4
    store = ExamStore(ws, exam_id)
    assert store.grade("A", "1") is not None
    assert store.transcript("A", "1") == Transcript(
        student_id="A",
        question_number="1",
        lines=[
            TranscriptLine(math="y=x^2-4x+7=(x-2)^2+3"),
            TranscriptLine(text="따라서 x=2일 때 최솟값은 3이다."),
        ],
        confirmed=True,
    )


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (
            r"q = \frac{A}{2B}\text{일 때 최댓값 } \frac{A^2}{4B}",
            r"q = \frac{A}{2B}, \quad R = \frac{A^2}{4B}",
            True,
        ),
        (
            r"x=4\text{일 때 최댓값 } 11, \; x=1\text{일 때 최솟값 } 2",
            r"x=1\text{일 때 최솟값 } 2, x=4\text{일 때 최댓값 } 11",
            True,
        ),
        (r"x=3\text{일 때 최솟값 } 10", r"x=3\text{일 때 최솟값 } 9", None),
        (r"\frac{1}{", "1", None),
        # 실측: 답 끝의 한글 괄호 설명 → 짝 안 맞는 조각 ")"에서 sympy가 IndexError를 내 해설이 죽음
        (r"T_1=2\sqrt{\frac{R}{g}} (P의 위치와 무관)", r"T_1=2\sqrt{\frac{R}{g}}", None),
        (r"T_1 = 2\sqrt{\frac{R}{g}} \text{ (}P\text{의 위치에 무관)}", r")(", None),
    ],
)
def test_answers_agree_multi_value(a: str, b: str, expected: bool | None) -> None:
    """여러 값으로 된 답(문장 답)도 값 집합으로 비교 (M9 실측: 신규 B 독립 풀이)."""
    from dawoni.models.grading import FinalAnswer

    assert checks.answers_agree(FinalAnswer(latex=a), FinalAnswer(latex=b)) is expected


def test_quote_with_part_labels_and_lines() -> None:
    """소문항 표시가 붙은 판독문: 여러 줄 인용도, 표시를 앞에 붙인 인용도 찾는다 (실측)."""
    t = Transcript(
        student_id="s",
        question_number="2",
        confirmed=True,
        lines=[
            TranscriptLine(text="만약 ㉡으로가는 전류량이 더적다면", part="(1)①"),
            TranscriptLine(text="밝기가 같다.", part="(1)①"),
            TranscriptLine(text="(가) 2Ω", part="(2)"),
        ],
    )
    assert checks.quote_in_transcript("만약 ㉡으로가는 전류량이 더적다면\n밝기가 같다.", t)
    assert checks.quote_in_transcript("(1)① 만약 ㉡으로가는 전류량이 더적다면", t)
    assert checks.quote_in_transcript("(2) (가) 2Ω", t)
    assert not checks.quote_in_transcript("밝기가 다르다", t)
