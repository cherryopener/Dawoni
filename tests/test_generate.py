"""M9 문항 제작: 값 조합 탐색(정수·기약분수만), 코드 검사, 5가지 제작 흐름(가짜 AI·수리 포함),
독립 풀이 대조, 승인 → 새 시험으로 묶기."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import MakeFake
from test_grading import FIX, RUBRIC_OK, SOL_OK
from tests_support import fake_prompts

from dawoni.config import AppConfig, save_config
from dawoni.fsio import read_json
from dawoni.generate import checks as gc
from dawoni.generate import pipeline as gp
from dawoni.generate.search import domain, search
from dawoni.generate.workflow import GenStore, exam_from_sets, generate, review
from dawoni.grading.pipeline import PipelineError
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.models.generation import NovelDraft, NumericTemplate, RewriteDraft, SymbolicTemplate
from dawoni.models.grading import Exam, ExamQuestion, ScopedSolution, SolutionDraft
from dawoni.models.item import ChoiceAnswer, ItemContent, ValueAnswer, Variable
from dawoni.models.scope import ExtensionConcept, ScopeProfile
from dawoni.paths import AppPaths
from dawoni.services import Services, open_services
from dawoni.workspace import create_workspace


def T(v: str) -> dict[str, Any]:
    return {"t": "text", "v": v}


def M(v: str) -> dict[str, Any]:
    return {"t": "math", "v": v}


Q1_TEMPLATE: dict[str, Any] = {
    "item": {
        "item_type": "essay",
        "stem": [{"type": "paragraph", "runs": [
            T("이차함수 "), M(r"y=x^2-\var{a}x+\var{b}"), T("의 최솟값을 구하는 과정을 서술하시오.")]}],
        "variables": {"a": {"value": 4, "min": 2, "max": 8, "step": 2},
                      "b": {"value": 7, "min": 1, "max": 9, "step": 1}},
    },
    "answer_expr": "b - a**2/4",
    "intermediate_exprs": ["a/2"],
    "note": "일차항 계수와 상수항을 바꿈",
}  # fmt: skip


def template(**over: Any) -> NumericTemplate:
    data = json.loads(json.dumps(Q1_TEMPLATE))
    data.update(over)
    return NumericTemplate.model_validate(data)


# ---------------------------------------------------------------- 값 조합 탐색


def test_domain() -> None:
    assert domain(Variable(value=3, min=1, max=5)) == [1, 2, 3, 4, 5], "정수 범위는 step 1"
    assert domain(Variable(value=0.2, min=0, max=0.4, step=0.1)) == [0, 0.1, 0.2, 0.3, 0.4]
    assert domain(Variable(value=30, choices=[30, 45, 60])) == [30, 45, 60]
    assert domain(Variable(value=0.5, min=0, max=1)) == [0.5], "간격을 모르면 원래 값만"


def test_search_clean_answers_only() -> None:
    t = template()
    t.item.variables["a"] = Variable(value=4, min=1, max=6, step=1)  # 홀수 a → a²/4 분수
    res = search(t, count=50, max_den=1)
    assert res.found and all(c.answer.is_Integer for c in res.found), "분모 1: 정수만"
    assert all(c.values["a"] % 2 == 0 for c in res.found), "a/2(중간값)도 정수여야"
    assert {"a": 4, "b": 7} not in [c.values for c in res.found], "원래 조합 제외"
    assert res.rejected["답이 깔끔하지 않음"] + res.rejected["중간값이 깔끔하지 않음"] > 0
    res2 = search(t, count=50, max_den=4)
    assert any(not c.answer.is_Integer for c in res2.found), "분모 4까지 기약분수 허용"
    answers = [c.answer for c in res2.found]
    assert len(answers) == len(set(answers)), "답이 서로 다른 조합"


def test_search_constraints_and_choices() -> None:
    t = template(answer_expr="b - a**2/4")
    t.item.constraints.append("b - a**2/4 > 0")
    assert all(float(c.answer) > 0 for c in search(t, count=50).found)
    mc = json.loads(json.dumps(Q1_TEMPLATE))
    mc["item"]["item_type"] = "multiple_choice"
    mc["item"]["choices"] = [
        {"label": i, "runs": [M(e)]}
        for i, e in enumerate(
            [r"\var{b-a**2/4}", r"\var{b+a**2/4}", r"\var{b}", r"\var{-a/2}", r"\var{b-a}"], 1
        )
    ]
    mc["item"]["answer"] = {"kind": "choice", "labels": [1]}
    res = search(NumericTemplate.model_validate(mc), count=50)
    assert res.found and all(c.choice_label == 1 for c in res.found)
    assert res.rejected["선지 값이 겹침"] > 0, "a=2,b=? 등에서 b-a == b-a²/4 겹침 → 버림"


def test_check_numeric() -> None:
    src = ItemContent.model_validate(read_json(FIX / "questions.json")[0]["item"])
    src_sol = SolutionDraft.model_validate(SOL_OK)
    assert gc.check_numeric(template(), src, src_sol) == []
    bad = gc.check_numeric(template(answer_expr="b + a"), src, src_sol)
    assert any("원래 정답(3)과 다르다" in p for p in bad)
    assert any(
        "정의되지 않은 변수" in p
        for p in gc.check_numeric(template(answer_expr="b - c"), src, src_sol)
    )


def test_check_symbolic_requires_assumptions_in_stem() -> None:
    t = SymbolicTemplate.model_validate({
        "item": {"item_type": "essay", "stem": [{"type": "paragraph", "runs": [
            T("이차함수 "), M("y=x^2-2ax+b"), T("의 최솟값을 a, b로 나타내시오.")]}]},
        "answer_expr": "b - a**2", "assumptions": ["a > 0"], "note": "문자로",
    })  # fmt: skip
    assert any("조건 'a > 0'" in p for p in gc.check_symbolic(t))
    ok = SymbolicTemplate.model_validate({**t.model_dump(), "item": {**t.item.model_dump(), "stem": [
        {"type": "paragraph", "runs": [T("상수 "), M(r"a \ge 0"), T("에 대하여 이차함수 "), M("y=x^2-2ax+b")]}]}})  # fmt: skip
    ok = ok.model_copy(update={"assumptions": ["a >= 0"]})
    assert gc.check_symbolic(ok) == []
    missing = ok.model_copy(update={"answer_expr": "b - a**2 + k"})
    assert any("['k']가 본문 수식에 없다" in p for p in gc.check_symbolic(missing))


def test_choice_label_of() -> None:
    assert gc.choice_label_of("③") == 3
    assert gc.choice_label_of(r"\text{④}") == 4
    assert gc.choice_label_of("정답은 2번") == 2
    assert gc.choice_label_of("1.5") is None


# ---------------------------------------------------------------- 제작 흐름 (가짜 AI)


@pytest.fixture
def env(
    app_paths: AppPaths, tmp_path: Path, make_fake: MakeFake
) -> Callable[[list[dict[str, Any]], dict[str, Any] | None], tuple[Services, ExamStore, Path]]:
    def make(
        script: list[dict[str, Any]], source: dict[str, Any] | None = None
    ) -> tuple[Services, ExamStore, Path]:
        exe = make_fake(
            "script", script=[{"response": json.dumps(r, ensure_ascii=False)} for r in script]
        )
        cfg = AppConfig()
        cfg.agents["agy"].executable = str(exe)
        cfg.agents["claude"].enabled = False
        cfg.agents["codex"].enabled = False
        save_config(app_paths, cfg)
        ws = create_workspace(tmp_path / f"ws{len(list(tmp_path.glob('ws*')))}")
        ScopeStore(ws).save(ScopeProfile.model_validate(read_json(FIX / "scope.json")))
        q = ExamQuestion.model_validate(source or read_json(FIX / "questions.json")[0])
        store = ExamStore.create(
            ws, Exam(title="원본", scope_id="scope-quadratic-minmax", questions=[q])
        )
        store.save_solution(ScopedSolution(**SolutionDraft.model_validate(SOL_OK).model_dump(),
                                           question_number=q.number, scope_version=1))  # fmt: skip
        return open_services(ws.root, paths=app_paths), store, exe

    return make


def solved(latex: str, expr: str | None, uses: tuple[str, str] = ("S1", "C3")) -> dict[str, Any]:
    return {
        "steps": [
            {"explanation": "표준형으로 바꾼다", "math": "y=(x-p)^2+q", "uses": [uses[0], "C2"]},
            {"explanation": "꼭짓점에서 최솟값", "math": latex, "uses": [uses[1]]},
        ],
        "final_answer": {"latex": latex, "expr": expr},
    }


def test_generate_numeric_with_repair_and_verification(env: Any) -> None:
    t = template()
    cand = search(t, count=1).found[0]
    ans = str(cand.answer)
    bad = {**Q1_TEMPLATE, "answer_expr": "b + a"}  # 원래 정답 3과 안 맞음 → 수리
    svc, store, exe = env([bad, Q1_TEMPLATE, solved(ans, ans), RUBRIC_OK])
    sets = generate(svc, store, "1", "numeric", opts=gp.Options(count=1))
    assert len(sets) == 1
    s = sets[0]
    assert s.solution.verified and s.rubric is not None and s.status == "draft"
    assert s.values == cand.values
    text = "".join(r.v for r in s.item.stem[0].runs)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]
    assert "\\var{" not in text and f"x^2-{cand.values['a']:g}x+{cand.values['b']:g}" in text
    prompts = fake_prompts(exe)
    assert "원래 정답(3)과 다르다" in prompts[1], "수리 요청"
    assert "y=x^2-" in prompts[2] and "\\var" not in prompts[2], "독립 풀이에는 값을 채운 문항"
    assert GenStore(svc.workspace).get(s.id).kind == "numeric"


def test_generate_numeric_mismatch_blocks_rubric(env: Any) -> None:
    svc, store, _ = env([Q1_TEMPLATE, solved("999", "999")])
    s = generate(svc, store, "1", "numeric", opts=gp.Options(count=1))[0]
    assert not s.solution.verified and s.rubric is None
    assert any("독립 풀이 답이 다름" in w for w in s.warnings)
    with pytest.raises(PipelineError, match="승인할 수 없습니다"):
        review(GenStore(svc.workspace), s.id, "approved", None)


FIG_SOURCE: dict[str, Any] = {
    "number": "4", "points": 3,
    "item": {
        "item_type": "multiple_choice",
        "stem": [{"type": "paragraph", "runs": [T("함수 "), M("y=2^x"), T("의 그래프로 알맞은 것은?")]}],
        "choices": [{"label": i, "figure_id": f"F{i}", "note": f"그래프 {i}"} for i in range(1, 6)],
        "answer": {"kind": "choice", "labels": [2]},
        "figures": [{"id": f"F{i}", "mode": "raster", "orig": f"figures/4/F{i}.png"} for i in range(1, 6)],
    },
}  # fmt: skip


def test_generate_condition_keeps_figure_choices(env: Any) -> None:
    good: dict[str, Any] = {"item": json.loads(json.dumps(FIG_SOURCE["item"])), "expected_answer": "3",
            "change_note": "밑을 1/2로 바꿔 감소 그래프(③)가 정답"}  # fmt: skip
    good["item"]["stem"][0]["runs"][1] = M(r"y=\left(\frac{1}{2}\right)^x")
    good["item"].pop("answer")
    moved = json.loads(json.dumps(good))
    moved["item"]["choices"][2]["note"] = "다른 그래프"  # 선지를 건드림 → 수리
    same_answer = {**good, "expected_answer": "2"}  # 정답이 원래와 같음 → 수리
    rubric3 = {
        "items": [{**RUBRIC_OK["items"][0], "points": 2}, {**RUBRIC_OK["items"][1], "points": 1}]
    }
    svc, store, exe = env([moved, same_answer, good, solved("③", None), rubric3], FIG_SOURCE)
    s = generate(svc, store, "4", "condition")[0]
    assert s.solution.verified, s.warnings
    assert [c.figure_id for c in s.item.choices] == [f"F{i}" for i in range(1, 6)]
    assert isinstance(s.item.answer, ChoiceAnswer) and s.item.answer.labels == [3]
    prompts = fake_prompts(exe)
    assert "그림 선지는 그대로 두고" in prompts[1] and "원래와 같은 2번" in prompts[2]
    with pytest.raises(PipelineError, match="수치형으로 만들 수 없습니다"):
        generate(svc, store, "4", "numeric")


def test_generate_novel_a_needs_different_skills(env: Any) -> None:
    item = {"item_type": "essay", "stem": [{"type": "paragraph", "runs": [
        M(r"1 \le x \le 4"), T("에서 이차함수 "), M("y=-x^2+6x"), T("의 최솟값을 구하시오.")]}]}  # fmt: skip
    same_skills = {"item": item, "solution": solved("5", "5", ("S1", "C3")), "differences": "범위"}
    new_skills = {
        "item": item,
        "solution": solved("5", "5", ("S3", "C4")),
        "differences": "끝값 비교가 핵심",
    }
    svc, store, exe = env([same_skills, new_skills, solved("5", "5", ("S3", "C4")), RUBRIC_OK])
    s = generate(svc, store, "1", "novel_a")[0]
    assert s.solution.verified and s.skill_ids == ["S3"]
    prompts = fake_prompts(exe)
    assert "S1, S2" in prompts[0], "기준 문항 기법(다른 풀이 포함)을 알려 줌"
    assert "풀이 기법이 겹친다" in prompts[1]


def test_generate_novel_b_definition_must_be_in_passage(env: Any) -> None:
    ext = ExtensionConcept(
        id="X1", name="이차함수의 평행이동", definition="y=f(x-m)+n 은 (m, n)만큼 평행이동한 그래프"
    )

    def draft(quote: str) -> dict[str, Any]:
        return {
            "item": {"item_type": "essay", "stem": [
                {"type": "box", "title": "제시문", "paragraphs": [[T("함수 "), M("y=f(x-m)+n"), T("의 그래프는 y=f(x)를 x축으로 m, y축으로 n만큼 평행이동한 것이다.")]]},
                {"type": "paragraph", "runs": [T("y=x^2을 평행이동해 꼭짓점이 (2, 3)이 되는 함수의 최솟값을 구하시오.")]}]},
            "solution": {"steps": [{"explanation": "평행이동", "math": "y=(x-2)^2+3", "uses": ["X1", "C1"]},
                                   {"explanation": "최솟값", "math": "3", "uses": ["C3"]}],
                         "final_answer": {"latex": "3", "expr": "3"}},
            "differences": "평행이동을 제시문으로",
            "definition_quotes": {"X1": quote},
        }  # fmt: skip

    wrong = draft("평행이동은 그래프를 옮기는 것이다")
    right = draft("y=f(x)를 x축으로 m, y축으로 n만큼 평행이동한 것이다")
    svc, store, exe = env([wrong, right, solved("3", "3", ("S1", "C3")), RUBRIC_OK])
    with pytest.raises(PipelineError, match="확장 개념이 필요"):
        generate(svc, store, "1", "novel_b")
    s = generate(svc, store, "1", "novel_b", extension=ext)[0]
    assert s.solution.verified and s.extension is not None and s.extension.id == "X1"
    prompts = fake_prompts(exe)
    assert "X1 이차함수의 평행이동" in prompts[0], "범위에 확장 개념 안내"
    assert "정의 인용" in prompts[1] and "제시문에 그대로 없다" in prompts[1]
    assert "확장 개념 (제시문으로 제공)" in prompts[2], "독립 풀이에도 확장 개념 허용"


def test_review_and_exam_from_sets(env: Any) -> None:
    t = template()
    ans = str(search(t, count=1).found[0].answer)
    svc, store, _ = env([Q1_TEMPLATE, solved(ans, ans), RUBRIC_OK])
    s = generate(svc, store, "1", "numeric", opts=gp.Options(count=1))[0]
    gs = GenStore(svc.workspace)
    with pytest.raises(PipelineError, match="승인하지 않은"):
        exam_from_sets(svc.workspace, [s.id], "동형 시험")
    review(gs, s.id, "approved", "김선생")
    exam = exam_from_sets(svc.workspace, [s.id], "동형 시험")
    new = ExamStore(svc.workspace, exam.id)
    assert new.solution("1") is not None and new.solution("1").verified  # pyright: ignore[reportOptionalMemberAccess]
    r = new.rubric("1")
    assert r is not None and r.status == "draft", "새 시험에서 다시 승인"
    assert gs.for_exam(store.exam_id)[0].status == "approved"


def test_novel_draft_model_requires_solution() -> None:
    with pytest.raises(ValueError):
        NovelDraft.model_validate(
            {"item": read_json(FIX / "questions.json")[0]["item"], "differences": "x"}
        )
    RewriteDraft.model_validate(
        {"item": FIG_SOURCE["item"], "expected_answer": "3", "change_note": "x"}
    )


def test_out_of_scope_resolve_is_not_verified(env: Any) -> None:
    oos = {**solved("3", "3"), "out_of_scope_note": "범위 밖 개념이 필요"}
    t = template()
    ans = str(search(t, count=1).found[0].answer)
    oos["final_answer"] = {"latex": ans, "expr": ans}
    svc, store, _ = env([Q1_TEMPLATE, oos])
    s = generate(svc, store, "1", "numeric", opts=gp.Options(count=1))[0]
    assert not s.solution.verified and s.rubric is None
    assert any("범위 밖이라고 판단" in w for w in s.warnings)


def test_answer_values_from_sentence() -> None:
    assert gc.answer_values(r"x=3\text{일 때 최솟값 } 10") == ["10"]
    assert gc.answer_values(r"\text{최댓값은 } \frac{3}{2}") == ["(3)/(2)"]
    assert gc.answer_values("10") == []


def test_generate_symbolic(env: Any) -> None:
    tpl = {
        "item": {"item_type": "essay", "stem": [{"type": "paragraph", "runs": [
            T("상수 "), M(r"a \ge 0"), T("에 대하여 이차함수 "), M("y=x^2-2ax+7"), T("의 최솟값을 a로 나타내시오.")]}]},
        "answer_expr": "7 - a**2", "assumptions": ["a >= 0"], "note": "일차항 계수를 문자로",
    }  # fmt: skip
    svc, store, _ = env([tpl, solved("7-a^2", "7 - a**2"), RUBRIC_OK])
    s = generate(svc, store, "1", "symbolic")[0]
    assert s.solution.verified and s.rubric is not None
    assert isinstance(s.item.answer, ValueAnswer) and s.item.answer.latex == "7 - a^{2}"


def test_numeric_variant_with_parametric_figure(env: Any) -> None:
    """변수 값을 바꾸면 본문·그림·정답이 함께 바뀐다 (M10 완료 기준)."""
    from dawoni.figures.render import render_figure

    incline = json.loads((FIX.parent / "figures" / "samples.json").read_text(encoding="utf-8"))[
        "incline"
    ]["spec"]
    tpl = json.loads(json.dumps(Q1_TEMPLATE))
    tpl["item"]["stem"].append({"type": "figure", "figure_id": "F1"})
    tpl["item"]["variables"]["theta"] = {"value": 30, "choices": [30, 45]}
    tpl["item"]["variables"]["m"] = {"value": 2}
    tpl["item"]["figures"] = [{"id": "F1", "mode": "parametric", "spec": incline}]
    t = NumericTemplate.model_validate(tpl)
    assert gp._unrenderable(t.item, {"a": 4, "b": 7, "theta": 85, "m": 2}), "85°는 그릴 수 없음"  # pyright: ignore[reportPrivateUsage]
    assert gp._unrenderable(t.item, {"a": 4, "b": 7, "theta": 30, "m": 2}) is None  # pyright: ignore[reportPrivateUsage]
    cand = search(t, count=1).found[0]
    ans = str(cand.answer)
    svc, store, _ = env([tpl, solved(ans, ans), RUBRIC_OK])
    s = generate(svc, store, "1", "numeric", opts=gp.Options(count=1))[0]
    assert s.values == cand.values
    fig = s.item.figures[0]
    assert s.item.variables["theta"].value == cand.values["theta"], "그림은 그 동형문제의 값으로"
    p1 = render_figure(fig, s.item, svc.workspace.root / "r")
    other = s.item.model_copy(deep=True)
    other.variables["theta"].value = 45 if cand.values["theta"] == 30 else 30
    p2 = render_figure(other.figures[0], other, svc.workspace.root / "r")
    assert p1.read_bytes() != p2.read_bytes(), "경사각이 바뀌면 그림도 바뀜"
