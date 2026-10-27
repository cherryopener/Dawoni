"""해설지 기준 루브릭: 소문항, 해설지 읽기, 해설지 vs AI 풀이 비교, 정답 후보, 검토.

실제 시험(2026-10-28)에서 겪은 것을 그대로 옮겼다:
- 시험지 PDF 뒤에 해설 쪽이 붙어 있고 "[부분 점수: …]"가 적혀 있다
- 소문항마다 배점·유형(서술형/단답형)이 다르다 (5/5, 2·2·2·4, 4/6)
- AI 풀이가 3(1)에서 해설지와 다른 오답을 냈고, 루브릭이 그 오답을 '감점하지 말 것'으로 인정했다
- AI가 3(2)에서 해설지와 다른 답을 냈는데, 그 답도 조건을 만족할 수 있다
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from pydantic import ValidationError
from tests_support import fake_prompts

from dawoni.agents import AgentClient
from dawoni.config import AppConfig
from dawoni.context import ContextEngine
from dawoni.fsio import read_json
from dawoni.grading import checks
from dawoni.grading.pipeline import (
    PipelineError,
    approve_rubric,
    decide_candidates,
    derive_rubric,
    edit_rubric,
    grade_answer,
)
from dawoni.grading.render import rubric_text
from dawoni.ingest.answer_key import check_key
from dawoni.ingest.layout import analyze, find_key_pages
from dawoni.models.answer_key import AnswerKey, KeyExtraction, KeyPart
from dawoni.models.grading import (
    ExamQuestion,
    Rubric,
    RubricDraft,
    ScopedSolution,
    SolutionDraft,
    Transcript,
    TranscriptLine,
)
from dawoni.models.scope import ScopeProfile
from dawoni.paths import AppPaths

FIX = Path(__file__).parent / "fixtures" / "grading"
MakeFake = Callable[..., Path]
EngineWith = Callable[[list[dict[str, Any]]], tuple[ContextEngine, Path]]


@pytest.fixture
def scope() -> ScopeProfile:
    return ScopeProfile.model_validate(read_json(FIX / "scope.json"))


@pytest.fixture
def q3() -> ExamQuestion:
    """실제 시험 3번 모양: (1) 단답형 4점(부분 점수 없음), (2) 서술형 6점(과정 4점/답 2점)."""
    base = read_json(FIX / "questions.json")[0]
    return ExamQuestion.model_validate(
        {
            **base,
            "number": "3",
            "points": 10,
            "parts": [
                {"label": "(1)", "points": 4, "kind": "short_answer"},
                {"label": "(2)", "points": 6, "kind": "essay"},
            ],
        }
    )


KEY: dict[str, Any] = {
    "question_number": "3",
    "source": "test.pdf 6쪽",
    "parts": [
        {
            "label": "(1)",
            "answer": "\\(v_0=(n+1)R\\cos\\alpha\\sqrt{\\frac{g}{2H}}\\)",
            "answer_expr": "(n+1)*R*cos(alpha)*sqrt(g/(2*H))",
            "solution": "수평 이동 시간과 연직 운동 시간이 일치해야 한다.",
            "credit_policy": "none",
            "credit_text": "[부분 점수: 없음]",
        },
        {
            "label": "(2)",
            "answer": "\\(\\sin\\theta=\\frac{1}{2}\\)",
            "answer_expr": "1/2",
            "solution": "세 번 반사하여 P로 되돌아오려면 두 번째 반사점은 A이다.",
            "credit_policy": "listed",
            "credits": [{"what": "과정", "points": 4}, {"what": "답", "points": 2}],
            "credit_text": "[부분 점수: 과정 4점/ 답 2점]",
        },
    ],
}

SOL: dict[str, Any] = {
    "steps": [
        {"explanation": "표준형으로 바꾼다", "math": "y=(x-2)^2+3", "uses": ["S1", "C2"]},
        {"explanation": "최솟값", "math": "y=3", "uses": ["C3"]},
    ],
    "final_answer": {"latex": "(1) nR\\cos\\alpha\\sqrt{g/2H}, (2) \\sin\\theta=1"},
    "part_answers": [
        {"label": "(1)", "latex": "v_0=nR\\cos\\alpha\\sqrt{\\frac{g}{2H}}",
         "expr": "n*R*cos(alpha)*sqrt(g/(2*H))"},
        {"label": "(2)", "latex": "\\sin\\theta=1", "expr": "1"},
    ],
}  # fmt: skip

GOOD: dict[str, Any] = {
    "items": [
        {"id": "R1", "points": 4, "part": "(1)", "criterion": "v0=(n+1)Rcosα√(g/2H)",
         "evidence_hint": "(n+1)R\\cos\\alpha", "solution_steps": []},
        {"id": "R2", "points": 2, "part": "(2)", "key_credit": 1, "criterion": "두 번째 반사점이 A",
         "evidence_hint": "점 A", "solution_steps": []},
        {"id": "R3", "points": 2, "part": "(2)", "key_credit": 1, "criterion": "l=√3R/2",
         "evidence_hint": "l=\\frac{\\sqrt3}{2}R", "solution_steps": []},
        {"id": "R4", "points": 2, "part": "(2)", "key_credit": 2, "criterion": "sinθ=1/2",
         "evidence_hint": "\\sin\\theta=\\frac12", "solution_steps": []},
    ],
    "checks": [
        {"part": "(1)", "key_answer": "(n+1)R…", "ai_answer": "nR…", "verdict": "ai_wrong",
         "note": "AI는 빠져나오는 마지막 구간을 세지 않았다"},
        {"part": "(2)", "key_answer": "1/2", "ai_answer": "1", "verdict": "other_valid",
         "note": "θ=90°면 내접 정삼각형 경로로 세 번 반사 후 P를 지난다"},
    ],
    "candidates": [
        {"id": "A1", "part": "(2)", "answer": "\\(\\sin\\theta=1\\)",
         "argument": "수평 발사 시 정삼각형 경로", "item_ids": ["R4"]},
    ],
}  # fmt: skip


def _q_sol() -> ScopedSolution:
    return ScopedSolution(**SOL, question_number="3", scope_version=1)


@pytest.fixture
def engine_with(app_paths: AppPaths, make_fake: MakeFake) -> EngineWith:
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


# ---------------------------------------------------------------- 모델


def test_part_points_must_add_up(q3: ExamQuestion) -> None:
    data = q3.model_dump()
    data["parts"][1]["points"] = 5
    with pytest.raises(ValidationError, match="소문항 배점 합"):
        ExamQuestion.model_validate(data)


def test_key_policy_needs_credits() -> None:
    with pytest.raises(ValidationError, match="credits"):
        KeyPart(label="(1)", credit_policy="listed")
    with pytest.raises(ValidationError, match="listed일 때만"):
        KeyPart(label="(1)", credit_policy="none", credits=[{"what": "답", "points": 2}])  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------- 해설지 쪽 찾기·읽기


def test_key_pages_found_and_kept_out_of_questions(tmp_path: Path) -> None:
    pdf = tmp_path / "with_key.pdf"
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((50, 100), "1. 첫 문항입니다. [총 10점]", fontname="korea", fontsize=10)  # pyright: ignore[reportUnknownMemberType]
    p = doc.new_page()
    p.insert_text((50, 100), "1) (1) 해설입니다", fontname="korea", fontsize=10)  # pyright: ignore[reportUnknownMemberType]
    p.insert_text((50, 130), "부분 점수", fontname="korea", fontsize=10)  # pyright: ignore[reportUnknownMemberType]
    p.insert_text((50, 160), "2. 해설 속 번호", fontname="korea", fontsize=10)  # pyright: ignore[reportUnknownMemberType]
    doc.save(str(pdf))  # pyright: ignore[reportUnknownMemberType]
    assert find_key_pages(pdf) == [2]
    mixed = analyze(pdf)
    assert [r.label for r in mixed.regions] == ["1", "2"], "해설 쪽을 빼지 않으면 섞인다"
    clean = analyze(pdf, exclude=[2])
    assert [(r.label, r.pages) for r in clean.regions] == [("1", [1])]


def _extraction(parts: list[dict[str, Any]], number: str = "3") -> KeyExtraction:
    return KeyExtraction.model_validate({"questions": [{"number": number, "parts": parts}]})


def test_check_key(q3: ExamQuestion) -> None:
    from dawoni.models.grading import Exam

    exam = Exam(title="t", scope_id="s", questions=[q3])
    source = "수평 이동 시간과 연직 운동 시간이 일치해야 한다. 세 번 반사하여 P로 되돌아오려면"
    good = _extraction(KEY["parts"])
    assert check_key(good, exam, source + " 두 번째 반사점은 A이다. 부분 점수 없음 과정 답") == []

    only_one = _extraction(KEY["parts"][:1])
    assert any("(2)']의 해설이 빠졌다" in p for p in check_key(only_one, exam, source))

    wrong_no = _extraction(KEY["parts"], number="9")
    assert any("시험에 없는 문항 번호" in p for p in check_key(wrong_no, exam, source))

    invented = copy.deepcopy(KEY["parts"])
    invented[0]["solution"] = "학생은 반드시 운동량 보존 법칙을 써서 정리해야만 만점이다"
    problems = check_key(_extraction(invented), exam, source)
    assert any("PDF 글자에 없는 한글" in p for p in problems)


# ---------------------------------------------------------------- 루브릭 검사


def _check(draft: dict[str, Any], q: ExamQuestion, **kw: Any) -> list[str]:
    key = AnswerKey.model_validate(KEY)
    return checks.check_rubric(RubricDraft.model_validate(draft), q, _q_sol(), key, **kw)


def test_good_rubric_passes(q3: ExamQuestion) -> None:
    assert _check(GOOD, q3) == []


def test_no_partial_credit_means_one_item(q3: ExamQuestion) -> None:
    bad = copy.deepcopy(GOOD)
    bad["items"][0]["points"] = 2
    bad["items"].insert(1, {**bad["items"][0], "id": "R9", "criterion": "식을 세움"})
    bad["partial"] = [{"item_id": "R1", "condition": "계수 실수", "points": 1}]
    problems = _check(bad, q3)
    assert any("'부분 점수 없음' — 항목을 하나로" in p for p in problems)
    assert any("부분점수(partial)를 넣었다" in p for p in problems)


def test_listed_credits_must_match(q3: ExamQuestion) -> None:
    bad = copy.deepcopy(GOOD)
    bad["items"][3]["key_credit"] = 1  # 답 2점 항목을 과정으로
    problems = _check(bad, q3)
    assert any("1번 '과정 4점'에 해당하는 항목 배점의 합이 6점" in p for p in problems)
    assert any("2번 '답 2점'에 해당하는 항목 배점의 합이 0점" in p for p in problems)
    bad["items"][3]["key_credit"] = None
    assert any("key_credit에 적을 것" in p for p in _check(bad, q3))


def test_part_sums(q3: ExamQuestion) -> None:
    bad = copy.deepcopy(GOOD)
    bad["items"][0]["points"] = 3
    bad["items"][3]["points"] = 3
    problems = _check(bad, q3)
    assert any("소문항 (1) 항목 배점의 합(3)" in p for p in problems)
    assert any("소문항 (2) 항목 배점의 합(7)" in p for p in problems)
    bad["items"][0]["part"] = "(3)"
    assert any("part '(3)'" in p for p in _check(bad, q3))


def test_verdict_must_match_code_comparison(q3: ExamQuestion) -> None:
    lying = copy.deepcopy(GOOD)
    lying["checks"][0]["verdict"] = "same"  # 코드로 보면 (n+1) ≠ n
    assert any("다르다. 어느 쪽이 맞는지" in p for p in _check(lying, q3))

    sol = copy.deepcopy(SOL)
    sol["part_answers"][0]["expr"] = "(n+1)*R*cos(alpha)*sqrt(g/(2*H))"
    same_sol = ScopedSolution(**sol, question_number="3", scope_version=1)
    problems = checks.check_rubric(
        RubricDraft.model_validate(GOOD), q3, same_sol, AnswerKey.model_validate(KEY)
    )
    assert any("코드로 비교하면 해설지 답과 AI 답이 같다" in p for p in problems)


def test_other_valid_needs_candidate(q3: ExamQuestion) -> None:
    bad = copy.deepcopy(GOOD)
    bad["candidates"] = []
    assert any("candidates에 후보로 올릴 것" in p for p in _check(bad, q3))


def test_teacher_edit_is_not_held_to_key_structure(q3: ExamQuestion) -> None:
    """강사가 해설지와 다르게 고치는 것은 강사의 결정 — 합계와 소문항 표시만 본다."""
    mine = copy.deepcopy(GOOD)
    mine["items"][0]["points"] = 2
    mine["items"][1]["points"] = 4
    assert _check(mine, q3, strict=False) == []
    assert _check(mine, q3) != []


def test_solution_needs_part_answers(q3: ExamQuestion) -> None:
    sol = copy.deepcopy(SOL)
    sol["part_answers"] = sol["part_answers"][:1]
    problems = checks.check_part_answers(SolutionDraft.model_validate(sol), q3)
    assert any("['(2)']의 답" in p for p in problems)


# ---------------------------------------------------------------- 만드는 흐름


def test_derive_with_key_audit_and_candidate_check(
    engine_with: EngineWith, q3: ExamQuestion, scope: ScopeProfile
) -> None:
    accepts_wrong = copy.deepcopy(GOOD)
    accepts_wrong["never_deduct"] = ["충돌 구간을 n개 또는 (n+1)개로 둔 경우"]
    bad_structure = copy.deepcopy(GOOD)
    bad_structure["items"][3]["key_credit"] = 1
    engine, exe = engine_with(
        [
            bad_structure,  # 코드 검사 → 수리
            accepts_wrong,
            {"issues": ["감점하지 말 것이 해설지와 다른 답 n개를 인정한다"]},  # 검토
            GOOD,  # 검토 의견 반영
            {"issues": []},
            {"verdict": "valid", "reasoning": "θ=90°면 세 번 반사 후 P를 지난다"},  # 후보 검산
            {"verdict": "valid", "reasoning": "(n+1)개 구간"},  # (1) 해설지 답
            {"verdict": "invalid", "reasoning": "n개면 빠져나오는 구간이 빠짐"},  # (1) AI 답
            {"verdict": "invalid", "reasoning": "θ=30°면 P로 돌아오지 않음"},  # (2) 해설지 답
            {"verdict": "valid", "reasoning": "θ=90°면 돌아옴"},  # (2) AI 답
        ]
    )
    key = AnswerKey.model_validate(KEY)
    r = derive_rubric(engine, q3, _q_sol(), scope, key=key)
    assert r.based_on_key and r.total == 10
    assert "충돌 구간을 n개" not in " ".join(r.never_deduct)
    (cand,) = r.candidates
    assert cand.status == "proposed" and cand.check == "valid"
    c1, c2 = r.checks
    assert (c1.key_check, c1.ai_check) == ("valid", "invalid"), "AI 오답이 검산으로 확인됨"
    assert (c2.key_check, c2.ai_check) == ("invalid", "valid")
    assert any("해설지 답은 검산을 통과하지 못했고" in n for n in r.review_notes)
    assert any("아직 확인하지 않았습니다" in n for n in r.review_notes)

    prompts = fake_prompts(exe)
    assert len(prompts) == 10
    first = prompts[0]
    assert "[강사 해설지]" in first and "[AI 독립 풀이]" in first and "과정 4점" in first
    assert "key_credit" in prompts[1] or "해당하는 항목 배점의 합" in prompts[1]
    assert "[검토 의견]" in prompts[3] and "n개를 인정한다" in prompts[3]
    check_prompt = prompts[5]
    assert "[확인할 답] 소문항 (2)" in check_prompt
    assert "[강사 해설지]" not in check_prompt and "[AI 독립 풀이]" not in check_prompt


def test_derive_without_key_says_so(
    engine_with: EngineWith, q3: ExamQuestion, scope: ScopeProfile
) -> None:
    no_key = copy.deepcopy(GOOD)
    no_key["checks"], no_key["candidates"] = [], []
    for it in no_key["items"]:
        it["key_credit"] = None
    engine, exe = engine_with([no_key])
    r = derive_rubric(engine, q3, _q_sol(), scope)
    assert not r.based_on_key and any("해설지 없이" in n for n in r.review_notes)
    assert len(fake_prompts(exe)) == 1, "해설지가 없으면 검토·검산 호출 없음"


# ---------------------------------------------------------------- 강사 결정·채점


def _rubric() -> Rubric:
    return Rubric(
        **RubricDraft.model_validate(GOOD).model_dump(), question_number="3", based_on_key=True
    )


def test_decide_candidates() -> None:
    r = approve_rubric(_rubric(), "강사")
    r2 = decide_candidates(r, ["A1"], [])
    assert r2.candidates[0].status == "accepted"
    assert r2.status == "draft" and r2.version == r.version + 1, "인정하면 다시 승인"
    assert decide_candidates(r2, ["A1"], []) == r2, "바뀐 것이 없으면 그대로"
    with pytest.raises(PipelineError, match="없는 ID"):
        decide_candidates(r, ["A9"], [])


def test_rubric_text_groups_by_part(q3: ExamQuestion) -> None:
    text = rubric_text(_rubric(), q3.parts)
    assert "소문항 (1) (4점, 단답형 — 최종 답만 채점)" in text
    assert "해설지 정답: (n+1)R…" in text
    assert "강사 미확인 정답 후보" in text and "A1" in text
    r = decide_candidates(_rubric(), ["A1"], [])
    assert "강사가 인정한 다른 정답" in rubric_text(r, q3.parts)


def test_grade_flags_unconfirmed_candidate(
    engine_with: EngineWith, q3: ExamQuestion, scope: ScopeProfile
) -> None:
    t = Transcript(
        student_id="학생",
        question_number="3",
        lines=[TranscriptLine(math="\\sin\\theta=1")],
        confirmed=True,
    )
    out = {
        "judgements": [
            {"rubric_item_id": i, "awarded": 0, "reason": "없음", "deduction_reason": "없음",
             "confidence": 0.9}
            for i in ("R1", "R2", "R3", "R4")
        ],
        "comment": "다시 풀어 보자",
        "matched_candidates": ["A1"],
    }  # fmt: skip
    engine, exe = engine_with([out])
    g = grade_answer(engine, q3, approve_rubric(_rubric(), "강사"), t, scope)
    assert g.status == "needs_review"
    assert any("강사 미확인 정답 후보 A1" in f for f in g.flags)
    assert "matched_candidates" in fake_prompts(exe)[0]


def test_edit_keeps_review_notes(q3: ExamQuestion) -> None:
    r = _rubric().model_copy(update={"review_notes": ["해설지 메모"]})
    edited = edit_rubric(r, RubricDraft.model_validate(GOOD), q3, _q_sol())
    assert edited.based_on_key and edited.review_notes == ["해설지 메모"]


def test_extract_answer_key_from_pdf(
    engine_with: EngineWith, q3: ExamQuestion, tmp_path: Path
) -> None:
    from dawoni.ingest.answer_key import extract_answer_key
    from dawoni.models.grading import Exam

    pdf = tmp_path / "key.pdf"
    doc = pymupdf.open()
    p = doc.new_page()
    for k, line in enumerate(
        [
            "3) (1) 수평 이동 시간과 연직 운동 시간이 일치해야 한다. 부분 점수 없음",
            "(2) 세 번 반사하여 P로 되돌아오려면 두 번째 반사점은 A이다. 부분 점수 과정 답",
        ]
    ):
        p.insert_text((40, 100 + 30 * k), line, fontname="korea", fontsize=9)  # pyright: ignore[reportUnknownMemberType]
    doc.save(str(pdf))  # pyright: ignore[reportUnknownMemberType]
    parts = copy.deepcopy(KEY["parts"])
    parts[1]["credits"][0]["points"] = 3  # 강사가 적은 합(5)이 배점(6)과 다름
    again = copy.deepcopy(parts)
    again[1]["solution"] += " \\(l=\\frac{\\sqrt{3}}{2}R\\)"  # 두 번째 읽기에만 있는 숫자
    engine, exe = engine_with(
        [{"questions": [{"number": "3", "parts": p}]} for p in (parts, again)]
    )
    exam = Exam(title="t", scope_id="s", questions=[q3])
    res = extract_answer_key(engine, pdf, [1], exam, tmp_path / "work")
    (key,) = res.keys
    assert key.source == "key.pdf 1쪽" and [p.label for p in key.parts] == ["(1)", "(2)"]
    assert any("부분 점수 합(5)이 배점(6)과 다름" in w for w in res.warnings)
    assert len(fake_prompts(exe)) == 2, "두 번 읽기 — 합의 차이는 수리하지 않고 경고"
    assert key.doubtful == ["(2)"] and not key.confirmed
    assert any("두 번 읽은 결과의 숫자가 다름" in w for w in res.warnings)
    assert key.pages == ["work/key_p1.png"]
    prompt = fake_prompts(exe)[0]
    assert "소문항 (1): 4점, 단답형" in prompt and "해설지 PDF 글자" in prompt
