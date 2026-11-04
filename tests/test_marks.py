"""손채점 답안지 (M5): 빨간 펜 소문항 점수 읽기 → 강사 확인 → 강사 확정 채점·소문항 일치도·확정.

실측 답안지는 소문항 줄마다 "배점 | 점수" 칸이 있고 강사가 점수 칸에 빨간 펜으로 적는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from test_teacher_scores import Setup, _ai, _q3, setup  # pyright: ignore[reportPrivateUsage]
from tests_support import fake_prompts

from dawoai.cli import main
from dawoai.grading import marks
from dawoai.grading.pipeline import PipelineError
from dawoai.grading.store import ExamStore
from dawoai.models.grading import Exam, StudentRecord
from dawoai.ocr import marks as ocr_marks
from dawoai.ocr.marks import MarksRead, ReadMark
from dawoai.roles import role_for_task

__all__ = ["setup"]  # 픽스처 (test_teacher_scores)


def _exam() -> Exam:
    return Exam(title="손채점", scope_id="s", questions=[_q3()])


def _read(*marks_: tuple[str, str, float | None]) -> dict[str, Any]:
    return {"marks": [{"question": q, "part": p, "points": v} for q, p, v in marks_]}


def _pages(store: ExamStore, student: str) -> None:
    """판독문의 원본 쪽 그림 (판독이 끝난 학생처럼)."""
    d = store.scan_dir(student)
    d.mkdir(parents=True, exist_ok=True)
    for n in (2, 3):
        Image.new("RGB", (40, 30), "white").save(d / f"page_{n:02d}.png")
    t = store.transcript(student, "3")
    assert t is not None
    pages = [f"scans/{student}/page_02.png", f"scans/{student}/page_03.png"]
    store.save_transcript(t.model_copy(update={"pages": pages, "confirmed": False}))


# ---------------------------------------------------------------- 칸 표기·검사 (코드)


def test_fix_marks_normalizes_slot_labels() -> None:
    v = MarksRead.model_validate(
        _read(("문항 3", "3-(1)", 2), ("3번", " (2) ", 4), ("3", "(2)", 4))
    )
    fixed, notes = ocr_marks.fix_marks(v, _exam())
    assert [(m.question, m.part, m.points) for m in fixed.marks] == [
        ("3", "(1)", 2),
        ("3", "(2)", 4),
    ]
    assert any("칸 표기" in n for n in notes) and any("두 번" in n for n in notes)


def test_check_marks_rejects_unknown_slot_and_points_over_cap() -> None:
    v = MarksRead(
        marks=[
            ReadMark(question="3", part="(3)", points=1),
            ReadMark(question="3", part="(1)", points=5),
        ]
    )
    problems = ocr_marks.check_marks(v, _exam())
    assert any("(3)" in p and "목록에 없다" in p for p in problems)
    assert any("배점 4점을 넘는다" in p for p in problems)
    ok = MarksRead(marks=[ReadMark(question="3", part="(1)", points=None)])
    assert ocr_marks.check_marks(ok, _exam()) == [], "못 읽은 칸은 null로 두면 된다"


def test_problems_flag_unread_and_cover_mismatch() -> None:
    r = StudentRecord(
        student_id="A", source="x", teacher_scores={"3": 6},
        teacher_marks={"3": {"(1)": 2, "(2)": 2}},
    )  # fmt: skip
    assert ocr_marks.problems(r, _exam()) == ["문항 3: 소문항 합 4점 ≠ 표지 6점"]
    r2 = r.model_copy(update={"teacher_marks": {"3": {"(1)": None, "(2)": 2}}})
    assert ocr_marks.problems(r2, _exam()) == ["3-(1): 점수를 읽지 못함"]


def test_suggest_rubric_is_the_calibrator_role() -> None:
    role = role_for_task("suggest_rubric")
    assert role is not None and role.name == "calibrator" and role.tier == "strong"
    reader = role_for_task("read_marks")
    assert reader is not None and reader.name == "cover_reader"


# ---------------------------------------------------------------- 읽기 → 확인 → 확정


def test_read_marks_repairs_then_saves_record(setup: Setup) -> None:
    bad = _read(("3", "(3)", 1), ("3", "(1)", 2))
    good = {**_read(("3", "(1)", 2), ("3", "(2)", None)), "notes": ["2쪽 점수 칸이 흐림"]}
    svc, store, exe = setup([bad, good])
    store.save_student(StudentRecord(student_id="A", source="scan 1-3쪽", teacher_scores={"3": 6}))
    _pages(store, "A")
    run = marks.read_teacher_marks(svc, store, students=["A", "B"])
    assert [r.student_id for r in run.read] == ["A"]
    assert [(k.student, k.reason) for k in run.skipped] == [("B", "답안 쪽 그림이 없음 (판독부터)")]
    prompts = fake_prompts(exe)
    assert len(prompts) == 2 and "문항 3 · (2) · 6" in prompts[0] and "(3)" in prompts[1]
    rec = store.student_record("A")
    assert rec is not None and rec.teacher_marks == {"3": {"(1)": 2.0, "(2)": 4.0}}, "표지 6점"
    assert rec.source == "scan 1-3쪽" and rec.marks_uncertain == [
        "2쪽 점수 칸이 흐림", "3-(2): 점수 칸이 비어 표지 총점 6점에 맞춰 4점으로 채움",
    ]  # fmt: skip
    again = marks.read_teacher_marks(svc, store, students=["A"])
    assert [k.reason for k in again.skipped] == ["이미 읽음"] and len(fake_prompts(exe)) == 2


def test_confirm_marks_needs_filled_slots_and_confirmed_transcript(setup: Setup) -> None:
    svc, store, _ = setup([])
    store.save_student(
        StudentRecord(student_id="A", source="x", teacher_scores={"3": 4},
                      teacher_marks={"3": {"(1)": 0, "(2)": None}})
    )  # fmt: skip
    _pages(store, "A")
    run = marks.confirm_marks(svc, store, ["A", "B"], by="강사")
    assert [(b.student, b.reason) for b in run.blocked] == [
        ("A", "3-(2): 점수를 읽지 못함"),
        ("B", "읽은 강사 점수가 없음 (`exam read-marks`)"),
    ]
    marks.set_marks(store, "A", "3", {"(2)": 2})
    run = marks.confirm_marks(svc, store, ["A"])
    assert "표지 4점" in run.blocked[0].reason, "표지 총점과 다르면 --force 없이는 확정 안 함"
    run = marks.confirm_marks(svc, store, ["A"], force=True)
    assert "판독문 확인 전" in run.blocked[0].reason
    t = store.transcript("A", "3")
    assert t is not None
    store.save_transcript(t.model_copy(update={"confirmed": True}))
    run = marks.confirm_marks(svc, store, ["A"], by="강사", force=True)
    assert run.confirmed == ["A"] and not run.blocked
    g = store.grade("A", "3")
    assert g is not None and g.status == "teacher_confirmed"
    assert g.teacher_part_scores == {"(1)": 0, "(2)": 2} and g.total == 2
    rec = store.student_record("A")
    assert rec is not None and rec.marks_confirmed and rec.marks_confirmed_by == "강사"
    with pytest.raises(PipelineError, match="0~4"):
        marks.set_marks(store, "A", "3", {"(1)": 5})
    with pytest.raises(PipelineError, match="없는 소문항"):
        marks.set_marks(store, "A", "3", {"(9)": 1})
    marks.set_marks(store, "A", "3", {"(1)": 1})
    rec = store.student_record("A")
    assert rec is not None and not rec.marks_confirmed, "고치면 다시 확정해야 한다"


# ---------------------------------------------------------------- 소문항 일치도·확정


def test_part_agreement_uses_ai_scores_before_teacher_fix(setup: Setup) -> None:
    svc, store, _ = setup([])
    store.save_grade(_ai("B", {"R1": 4, "R2": 2, "R3": 2, "R4": 2}))  # (1) 4, (2) 6
    store.save_student(StudentRecord(student_id="A", source="x",
                                     teacher_marks={"3": {"(1)": 4, "(2)": 0}}))  # fmt: skip
    store.save_student(StudentRecord(student_id="B", source="y",
                                     teacher_marks={"3": {"(1)": 4, "(2)": 5}}))  # fmt: skip
    store.save_student(StudentRecord(student_id="C", source="z",
                                     teacher_marks={"3": {"(1)": 0, "(2)": None}}))  # fmt: skip
    s = marks.part_agreement(store, "3")
    # A: AI (1) 4 = 4, (2) 2 ≠ 0 / B: (1) 4 = 4, (2) 6 ≠ 5 / C: AI 채점 없음
    assert (s.n, s.parts, s.same, s.ai_higher, s.ai_lower) == (2, 4, 2, 2, 0)
    assert s.diff_sum == 3 and s.within1 == 3 and s.per_part == {"(2)": 2}
    assert s.rate == 0.5
    only_a = marks.part_agreement(store, "3", students=["A"])
    assert (only_a.n, only_a.parts) == (1, 2)
    # 강사가 고친 뒤에도 비교는 AI가 처음 준 점수로
    t = store.transcript("A", "3")
    assert t is not None
    store.save_transcript(t.model_copy(update={"confirmed": True}))
    marks.confirm_marks(svc, store, ["A"])
    assert marks.part_agreement(store, "3", students=["A"]).same == 1
    total = marks.combine([s, only_a])
    assert total.parts == 6 and "소문항" in marks.agreement_line(total)
    r = marks.finalize_rubric(store, "3", by="강사")
    assert r.finalized_by == "강사" and r.finalized_note and "2/4" in r.finalized_note
    assert store.rubric("3") == r


def test_cli_marks_compare(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    svc, store, _ = setup([])
    store.save_student(StudentRecord(student_id="A", source="x", teacher_scores={"3": 2},
                                     teacher_marks={"3": {"(1)": 4, "(2)": None}}))  # fmt: skip
    w = ["--workspace", str(svc.workspace.root)]
    assert main(["exam", *w, "marks", store.exam_id]) == 0
    out = capsys.readouterr().out
    assert "A [확인 전]" in out and "(2) ?" in out and "점수를 읽지 못함" in out
    assert main(["exam", *w, "marks", store.exam_id, "A", "-q", "3", "--part", "(2)=0"]) == 0
    assert main(["exam", *w, "compare", store.exam_id, "--cases"]) == 0
    out = capsys.readouterr().out
    assert "소문항 1/2 일치" in out and "A: (2) 강사 0/AI 2" in out


def test_marks_json_roundtrip(tmp_path: Path) -> None:
    r = StudentRecord(student_id="A", source="x", teacher_marks={"3": {"(1)": None}})
    back = StudentRecord.model_validate(json.loads(r.model_dump_json()))
    assert back.teacher_marks == {"3": {"(1)": None}}


def test_api_marks_put_compare_finalize(setup: Setup, app_paths: Any) -> None:
    from test_server import _client  # pyright: ignore[reportPrivateUsage]

    svc, store, _ = setup([])
    store.save_student(StudentRecord(student_id="A", source="x", teacher_scores={"3": 4},
                                     teacher_marks={"3": {"(1)": 4, "(2)": None}}))  # fmt: skip
    c = _client(app_paths)
    assert c.post("/api/workspace", json={"path": str(svc.workspace.root)}).status_code == 200
    base = f"/api/exams/{store.exam_id}"
    rows = c.get(f"{base}/marks").json()
    assert rows[0]["problems"] == ["3-(2): 점수를 읽지 못함"] and not rows[0]["confirmed"]
    row = c.put(f"{base}/students/A/marks/3", json={"parts": {"(2)": 0}}).json()
    assert row["marks"] == {"3": {"(1)": 4, "(2)": 0}} and row["problems"] == []
    cmp = c.get(f"{base}/compare").json()
    assert cmp["overall"]["parts"] == 2 and cmp["questions"]["3"]["rate"] == 0.5
    r = c.post(f"{base}/calibration/3/finalize", json={"by": "강사"}).json()
    assert r["finalized_by"] == "강사" and "1/2" in r["finalized_note"]
    bad = c.put(f"{base}/students/A/marks/3", json={"parts": {"(1)": 9}})
    assert bad.status_code == 400


def test_fill_from_cover_only_when_determined() -> None:
    exam = _exam()
    m: ocr_marks.Marks = {"3": {"(1)": None, "(2)": None}}
    assert ocr_marks.fill_from_cover(m, {"3": 0}, exam) == [
        "3-(1), 3-(2): 점수 칸이 비어 표지 총점 0점에 맞춰 0, 0점으로 채움"
    ]
    assert m == {"3": {"(1)": 0.0, "(2)": 0.0}}
    m = {"3": {"(1)": 4, "(2)": None}}
    ocr_marks.fill_from_cover(m, {"3": 6}, exam)
    assert m["3"]["(2)"] == 2
    for cover in ({"3": 3}, {"3": 11}, {}):  # 나머지가 음수 / 배점 넘음 / 표지 없음
        m = {"3": {"(1)": 4, "(2)": None}}
        assert ocr_marks.fill_from_cover(m, cover, exam) == [] and m["3"]["(2)"] is None
    m = {"3": {"(1)": None, "(2)": None}}
    assert ocr_marks.fill_from_cover(m, {"3": 5}, exam) == [], "빈 칸 둘은 나눌 수 없음"


def test_problems_only_for_questions_to_import() -> None:
    r = StudentRecord(student_id="A", source="x", teacher_marks={"3": {"(1)": None, "(2)": 1}})
    assert ocr_marks.problems(r, _exam(), ["1"]) == []
    assert ocr_marks.problems(r, _exam(), ["3"]) == ["3-(1): 점수를 읽지 못함"]


def test_suggest_for_key_based_rubric_without_solution(setup: Setup) -> None:
    """해설지로 만든 루브릭(AI 풀이 없음)도 강사 사례로 개선 제안을 받는다 (실측 시험이 이 경우)."""
    import copy

    from test_answer_key import GOOD

    from dawoai.grading import workflow
    from dawoai.models.answer_key import AnswerKey, KeyPart

    revised = copy.deepcopy(GOOD)
    revised["items"][0]["criterion"] = "(n+1)개 구간 — n개로 쓰면 0점"
    suggestion = {
        "changes": [{"item_id": "R1", "kind": "clarify_criterion",
                     "summary": "n개로 쓴 답은 0점", "evidence": ["A"]}],
        "revised": revised,
    }  # fmt: skip
    svc, store, exe = setup([suggestion])
    (store.root / "solutions" / "3.json").unlink()
    r = store.rubric("3")
    assert r is not None
    store.save_rubric(r.model_copy(update={"based_on_key": True}))
    parts = [
        KeyPart(label="(1)", answer="(n+1)Rcosα√(g/2H)", credit_policy="unspecified"),
        KeyPart(label="(2)", answer="sinθ=1/2", credit_policy="unspecified"),
    ]
    store.save_key(AnswerKey(question_number="3", source="해설지 6쪽", parts=parts))
    workflow.import_teacher_scores(svc, store, workflow.parse_teacher_csv("A,3,(1),0,n+1개\n"))
    run = workflow.suggest_rubric_changes(svc, store, "3")
    assert run.suggestion.revised is not None
    prompt = fake_prompts(exe)[0]
    assert "(n+1)Rcosα√(g/2H)" in prompt and "[AI 풀이]" not in prompt
