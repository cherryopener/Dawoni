"""루브릭을 만든 뒤에 고치기 (2026-11-10 사용자 요청).

- 문항: '확인하고 루브릭 만들기' 뒤에는 문항을 고칠 길이 없었다. 강사가 확인하면(`reset_rubric`)
  고치면서 루브릭을 비켜 두고, 다시 만들면 판 번호가 이어진다 (예전 판 채점 → '다시 채점 필요').
- 루브릭: 화면에서 채점 항목·부분 점수를 고친다. 승인·확정한 것이면 새 판(초안).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_code_grading import short
from test_grading import FIX
from test_server import _client, _wait  # pyright: ignore[reportPrivateUsage]

from dawoni.fsio import read_json
from dawoni.grading.pipeline import (
    PipelineError,
    approve_rubric,
    decide_candidates,
    edit_rubric_items,
)
from dawoni.models.grading import (
    AcceptedAlternative,
    AnswerCandidate,
    ExamQuestion,
    PartialCredit,
    Rubric,
    RubricEdit,
    RubricItem,
)
from dawoni.paths import AppPaths

# ---------------------------------------------------------------- 루브릭 고치기


def _essay() -> ExamQuestion:
    return ExamQuestion.model_validate(read_json(FIX / "questions.json")[0])  # 4점, 소문항 없음


def _rubric() -> Rubric:
    return Rubric(
        question_number="1",
        items=[
            RubricItem(
                id="R1",
                points=2,
                criterion="완전제곱식",
                evidence_hint="(x-2)^2",
                solution_steps=[1],
                concepts=["완전제곱"],
            ),
            RubricItem(id="R2", points=2, criterion="최솟값 3", evidence_hint="3"),
        ],
        partial=[PartialCredit(item_id="R1", condition="부호 실수", points=1)],
        accepted_alternatives=[
            AcceptedAlternative(id="E1", description="꼭짓점 공식", item_ids=["R1"]),
            AcceptedAlternative(id="E2", description="미분 없이 대칭축", item_ids=["R1", "R2"]),
        ],
        candidates=[
            AnswerCandidate(id="A1", part="", answer="3", argument="같음", item_ids=["R2"])
        ],
        status="approved",
        approved_by="김선생",
        finalized_by="김선생",
        finalized_note="일치 100%",
        version=3,
    )


def _edit(items: list[dict[str, Any]], partial: list[dict[str, Any]] | None = None) -> RubricEdit:
    return RubricEdit.model_validate({"items": items, "partial": partial or []})


def test_editing_an_approved_rubric_makes_a_new_draft() -> None:
    old = _rubric()
    edit = _edit(
        [
            {
                "id": "R1",
                "points": 3,
                "criterion": "완전제곱식으로 바꿈 (강사)",
                "evidence_hint": "",
            },
            {"id": "R3", "points": 1, "criterion": "최솟값 3을 씀", "evidence_hint": "3"},
        ],
        [
            {"item_id": "R1", "condition": "부호 실수", "points": 1},
            {"item_id": "R1", "condition": "식만 있음", "points": 2},
        ],
    )
    new = edit_rubric_items(old, edit, _essay(), None)
    assert (new.status, new.version, new.finalized_by, new.finalized_note) == (
        "draft",
        4,
        None,
        None,
    )
    r1 = new.items[0]
    assert r1.criterion == "완전제곱식으로 바꿈 (강사)" and r1.points == 3
    assert r1.solution_steps == [1] and r1.concepts == ["완전제곱"], "고치지 않는 필드는 그대로"
    assert [p.from_key for p in new.partial] == [False, True], "강사가 적은 부분 점수는 근거 있음"
    # 지운 R2를 가리키던 연결은 떼어 낸다 (E2는 R1만, A1은 연결 없음)
    assert [(a.id, a.item_ids) for a in new.accepted_alternatives] == [
        ("E1", ["R1"]),
        ("E2", ["R1"]),
    ]
    assert new.candidates[0].item_ids == []


def test_deciding_candidates_on_a_finalized_rubric_needs_finalizing_again() -> None:
    """확정한 루브릭에서 넓힌 표현을 인정·빼면 초안이 되는데 확정 기록이 남아, 다시 승인만 해도
    손채점과 맞춰 보지 않은 판이 '확정'으로 보였다 (2026-11-11 고침)."""
    r = decide_candidates(_rubric(), [], ["E1"])
    assert (r.status, r.version) == ("draft", 4)
    assert (r.approved_by, r.approved_at, r.finalized_by, r.finalized_note) == (None,) * 4
    again = approve_rubric(r, "김선생")
    assert again.status == "approved" and again.finalized_by is None


def test_unchanged_edit_keeps_approval() -> None:
    old = _rubric()
    same = _edit(
        [
            i.model_dump(include={"id", "part", "points", "criterion", "evidence_hint"})
            for i in old.items
        ],
        [p.model_dump(include={"item_id", "condition", "points"}) for p in old.partial],
    )
    assert edit_rubric_items(old, same, _essay(), None) is old


def test_bad_rubric_edits_are_refused() -> None:
    old = _rubric()
    two = [
        {"id": "R1", "points": 2, "criterion": "a"},
        {"id": "R2", "points": 1, "criterion": "b"},
    ]
    with pytest.raises(PipelineError, match="합"):
        edit_rubric_items(old, _edit(two), _essay(), None)
    two[1]["points"] = 2
    with pytest.raises(PipelineError, match="부분점수"):
        edit_rubric_items(old, _edit(two, [{"item_id": "R1", "condition": "c", "points": 2}]),
                          _essay(), None)  # fmt: skip
    with pytest.raises(PipelineError, match="중복"):
        edit_rubric_items(old, _edit([two[0], {**two[1], "id": "R1"}]), _essay(), None)


# ---------------------------------------------------------------- 문항 고치기 (API)


def test_question_edits_after_rubric_need_confirmation(app_paths: AppPaths, tmp_path: Path) -> None:
    c = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    scope = c.post("/api/scopes", json=read_json(FIX / "scope.json")).json()
    q = short("1").model_dump(mode="json")
    eid = c.post("/api/exams", json={"title": "단답", "scope_id": scope["id"], "questions": [q]})
    base = f"/api/exams/{eid.json()['id']}"
    q1 = f"{base}/questions/1"
    key = [{"label": "(1)", "answer": "\\(2\\)"}, {"label": "(2)", "answer": "\\(3\\)"}]
    c.put(f"{q1}/key-text", json={"parts": key, "by": "김선생"})
    assert (
        _wait(c, c.post(f"{base}/rubrics", json={"by": "김선생"}).json())["status"] == "succeeded"
    )
    c.put(f"{q1}/rubric/answers", json={"part": "(1)", "answers": ["2", "4"], "by": "김선생"})
    c.post(f"{q1}/rubric/approve", json={"by": "김선생"})
    assert c.get(q1).json()["rubric"]["version"] == 1

    # 확인 없이는 고치지 못한다 — 루브릭은 그대로
    points = {"parts": {"(1)": 3, "(2)": 2}}
    for res in (
        c.put(f"{q1}/points", json=points),
        c.put(f"{q1}/stem", json={"text": "빈칸을 모두 채우시오."}),
        c.put(f"{q1}/structure", json={"parts": [{"label": "(1)", "points": 4}]}),
    ):
        assert res.status_code == 400 and "확인" in res.json()["detail"]
    assert c.get(q1).json()["rubric"]["status"] == "approved"

    # 해설은 언제든 고친다 (루브릭 그대로)
    assert c.put(f"{q1}/key-text", json={"parts": key}).status_code == 200
    assert c.get(q1).json()["rubric"] is not None

    # 확인하면 고치고 루브릭을 비켜 둔다 (지우지 않음)
    res = c.put(f"{q1}/points", params={"reset_rubric": True}, json=points)
    assert res.status_code == 200 and res.json()["points"] == 5
    assert c.get(q1).json()["rubric"] is None
    retired = tmp_path / "ws" / "exams" / eid.json()["id"] / "rubrics_retired" / "1.json"
    assert read_json(retired)["version"] == 1
    # 루브릭이 없으면 확인 없이도 고친다
    stem = c.put(f"{q1}/stem", json={"text": "빈칸을 모두 채우시오."}).json()
    assert stem["notes"] == []

    # 다시 만들면 판 번호가 이어지고, 강사가 고친 정답은 그대로
    assert (
        _wait(c, c.post(f"{base}/rubrics", json={"by": "김선생"}).json())["status"] == "succeeded"
    )
    again = c.get(q1).json()["rubric"]
    assert (again["version"], again["status"]) == (2, "draft")
    assert again["answer_rules"][0]["answers"] == ["2", "4"]
    assert sum(i["points"] for i in again["items"]) == 5

    # 소문항 고치기도 같은 길 — 알림이 돌아온다
    res = c.put(
        f"{q1}/structure",
        params={"reset_rubric": True},
        json={"parts": [{"label": "(1)", "points": 3, "kind": "short_answer", "was": "(1)"},
                        {"label": "(2)", "points": 3, "kind": "short_answer", "was": "(2)"}]},
    ).json()  # fmt: skip
    assert res["question"]["points"] == 6 and "루브릭" in res["notes"][0]
    assert read_json(retired)["version"] == 2


def test_rubric_items_api(app_paths: AppPaths, tmp_path: Path) -> None:
    c = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    scope = c.post("/api/scopes", json=read_json(FIX / "scope.json")).json()
    q = short("1").model_dump(mode="json")
    eid = c.post("/api/exams", json={"title": "단답", "scope_id": scope["id"], "questions": [q]})
    q1 = f"/api/exams/{eid.json()['id']}/questions/1"
    missing = c.put(
        f"{q1}/rubric/items", json={"items": [{"id": "R1", "points": 4, "criterion": "x"}]}
    )
    assert missing.status_code == 400 and "루브릭이 없습니다" in missing.json()["detail"]

    key = [{"label": "(1)", "answer": "\\(2\\)"}, {"label": "(2)", "answer": "\\(3\\)"}]
    c.put(f"{q1}/key-text", json={"parts": key})
    _wait(c, c.post(f"/api/exams/{eid.json()['id']}/rubrics", json={}).json())
    c.post(f"{q1}/rubric/approve", json={})
    items = c.get(q1).json()["rubric"]["items"]
    body = {"items": [{**{k: i[k] for k in ("id", "part", "points", "evidence_hint")},
                       "criterion": i["criterion"] + " (강사)"} for i in items]}  # fmt: skip
    r = c.put(f"{q1}/rubric/items", json=body).json()
    assert (
        r["status"] == "draft"
        and r["version"] == 2
        and r["items"][0]["criterion"].endswith("(강사)")
    )
    lost = c.put(f"{q1}/rubric/items", json={"items": body["items"][:1]})
    assert lost.status_code == 400
