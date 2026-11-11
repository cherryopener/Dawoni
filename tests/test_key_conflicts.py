"""해설지와 다른 기준 고르기 (2026-11-11 사용자 요청).

해설지의 0점 조건이 문제가 요구하지 않은 것을 요구하면 AI는 문제 기준으로 항목을 쓰고 해설지대로의
기준을 함께 적는다. 강사가 '문제 기준 / 해설지 기준'을 고르면 그 항목의 기준 문장이 바뀌고, 고친
기준이면 다시 승인한다. 고치기·보정 제안으로 항목을 바꾸면 선택은 그 문장에 맞춘다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_code_grading import short
from test_grading import FIX
from test_server import _client, _wait  # pyright: ignore[reportPrivateUsage]

from dawoni.fsio import read_json, write_json_atomic
from dawoni.grading.pipeline import (
    PipelineError,
    choose_key_conflicts,
    edit_rubric_items,
    sync_key_conflicts,
)
from dawoni.models.grading import ExamQuestion, KeyConflict, Rubric, RubricEdit, RubricItem
from dawoni.paths import AppPaths

QUESTION = "R2 전압과 전류를 이용해 반박함"
KEY = "R2 전압과 전류를 이용해 반박함 (밝기 표현 없으면 0점)"


def _essay() -> ExamQuestion:
    return ExamQuestion.model_validate(read_json(FIX / "questions.json")[0])  # 4점, 소문항 없음


def _rubric(**kw: object) -> Rubric:
    data: dict[str, object] = {
        "question_number": "1",
        "items": [
            RubricItem(id="R1", points=2, criterion="회로를 바르게 해석함", evidence_hint=""),
            RubricItem(id="R2", points=2, criterion=QUESTION, evidence_hint=""),
        ],
        "key_conflicts": [
            KeyConflict(
                id="K1",
                note="해설지는 밝기 표현 없으면 0점 — 문제는 전압·전류로 반박만 요구",
                item_id="R2",
                key_criterion=KEY,
                question_criterion=QUESTION,
            ),
            "예전 저장본: 설명만 있음",
        ],
        "status": "approved",
        "approved_by": "김선생",
        "finalized_by": "김선생",
        "finalized_note": "일치 100%",
        "version": 3,
    }
    return Rubric.model_validate({**data, **kw})


def test_old_saved_conflicts_load_as_confirm_only() -> None:
    old = Rubric.model_validate(
        {
            "question_number": "1",
            "items": [{"id": "R1", "points": 4, "criterion": "x", "evidence_hint": ""}],
            "key_conflicts": ["밝기 표현 없으면 0점 — 문제는 반박만 요구", "또 하나"],
        }
    )
    a, b = old.key_conflicts
    assert a.note == "밝기 표현 없으면 0점 — 문제는 반박만 요구"
    assert (a.id, a.choice, a.switchable) == ("K1", None, False)
    assert b.id == "K2"
    # 저장했다 다시 읽어도 같은 ID
    assert Rubric.model_validate(old.model_dump(mode="json")).key_conflicts == old.key_conflicts


def test_choosing_the_key_basis_rewrites_the_item_and_needs_approval() -> None:
    old = _rubric()
    r = choose_key_conflicts(old, {"K1": "key"})
    assert r.items[1].criterion == KEY and r.items[0] == old.items[0]
    assert r.key_conflicts[0].choice == "key"
    assert (r.status, r.version, r.approved_by, r.finalized_by) == ("draft", 4, None, None)
    # 초안에서 다시 문제 기준으로: 판은 그대로
    back = choose_key_conflicts(r, {"K1": "question"})
    assert back.items[1].criterion == QUESTION and back.key_conflicts[0].choice == "question"
    assert (back.status, back.version) == ("draft", 4)


def test_confirming_the_question_basis_keeps_approval() -> None:
    old = _rubric()
    r = choose_key_conflicts(old, {"K1": "question", "K2": "question"})
    assert [k.choice for k in r.key_conflicts] == ["question", "question"]
    assert (r.status, r.version, r.finalized_by) == ("approved", 3, "김선생"), "기준은 그대로"
    assert r.items == old.items
    assert choose_key_conflicts(r, {"K1": "question"}) is r, "바뀐 것이 없으면 그대로"


def test_bad_choices_are_refused() -> None:
    old = _rubric()
    with pytest.raises(PipelineError, match="없는 ID"):
        choose_key_conflicts(old, {"K9": "key"})
    with pytest.raises(PipelineError, match="해설지대로의 기준이 없습니다"):
        choose_key_conflicts(old, {"K2": "key"})
    with pytest.raises(PipelineError, match="고치기"):
        choose_key_conflicts(old, {"K1": "edited"})


def test_editing_the_item_follows_the_text() -> None:
    old = _rubric()
    fields = {"id", "part", "points", "criterion", "evidence_hint"}
    items = [i.model_dump(include=fields) for i in old.items]

    def edit(criterion: str | None) -> Rubric:
        rows = items if criterion is None else [items[0], {**items[1], "criterion": criterion}]
        return edit_rubric_items(old, RubricEdit.model_validate({"items": rows}), _essay(), None)

    assert edit("R2 강사가 고친 기준").key_conflicts[0].choice == "edited"
    assert edit(KEY).key_conflicts[0].choice == "key"
    assert edit(QUESTION) is old, "바뀐 것이 없으면 그대로 (아직 안 정한 것도 그대로)"
    assert old.key_conflicts[0].choice is None
    # 항목을 지우면 고를 수 없다 — 직접 고친 것으로
    one = RubricEdit.model_validate({"items": [{**items[0], "points": 4}]})
    gone = edit_rubric_items(old, one, _essay(), None)
    assert gone.key_conflicts[0].choice == "edited" and gone.key_conflicts[1].choice is None
    with pytest.raises(PipelineError, match="지워서"):
        choose_key_conflicts(gone, {"K1": "key"})


def test_sync_keeps_an_undecided_question_basis_undecided() -> None:
    r = _rubric()
    assert sync_key_conflicts(r.key_conflicts, r.items) == r.key_conflicts
    chosen = choose_key_conflicts(r, {"K1": "key"})
    reverted = [chosen.items[0], chosen.items[1].model_copy(update={"criterion": QUESTION})]
    assert sync_key_conflicts(chosen.key_conflicts, reverted)[0].choice == "question"


def test_key_conflicts_api(app_paths: AppPaths, tmp_path: Path) -> None:
    c = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    scope = c.post("/api/scopes", json=read_json(FIX / "scope.json")).json()
    q = short("1").model_dump(mode="json")
    eid = c.post("/api/exams", json={"title": "단답", "scope_id": scope["id"], "questions": [q]})
    exam = f"/api/exams/{eid.json()['id']}"
    q1 = f"{exam}/questions/1"
    key = [{"label": "(1)", "answer": "\\(2\\)"}, {"label": "(2)", "answer": "\\(3\\)"}]
    c.put(f"{q1}/key-text", json={"parts": key})
    _wait(c, c.post(f"{exam}/rubrics", json={}).json())
    c.post(f"{q1}/rubric/approve", json={})
    path = tmp_path / "ws" / "exams" / eid.json()["id"] / "rubrics" / "1.json"
    saved = read_json(path)
    item = saved["items"][0]
    saved["key_conflicts"] = [
        {"note": "해설지는 단위 없으면 0점", "item_id": item["id"],
         "key_criterion": item["criterion"] + " (단위 없으면 0점)",
         "question_criterion": item["criterion"]},
    ]  # fmt: skip
    write_json_atomic(path, saved)
    state = next(x for x in c.get(exam).json()["questions"] if x["number"] == "1")
    before = state["rubric"]["attention"]

    r = c.post(f"{q1}/rubric/key-conflicts", json={"choices": {"K1": "key"}}).json()
    assert r["items"][0]["criterion"].endswith("(단위 없으면 0점)")
    assert (r["status"], r["version"], r["key_conflicts"][0]["choice"]) == ("draft", 2, "key")
    state = next(x for x in c.get(exam).json()["questions"] if x["number"] == "1")
    assert state["rubric"]["attention"] == before - 1
    bad = c.post(f"{q1}/rubric/key-conflicts", json={"choices": {"K7": "key"}})
    assert bad.status_code == 400 and "없는 ID" in bad.json()["detail"]
