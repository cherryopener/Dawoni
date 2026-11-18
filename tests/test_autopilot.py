# pyright: reportUnknownMemberType=false
"""한 번에 채점 (dawoni/autopilot.py, 2026-11-18 사용자 요청): 루브릭 자동 승인 규칙, 학생 쪽 수
찾기, `[판독불가]`가 남아도 채점, 결과 화면이 읽는 상태 — AI 없이(가짜 CLI)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pymupdf
from conftest import MakeFake
from test_grading import FIX, GRADE_A, approved_rubric
from test_readings import _exam, _services  # pyright: ignore[reportPrivateUsage]

from dawoni import autopilot
from dawoni.grading import workflow
from dawoni.models.grading import (
    AcceptedAlternative,
    AnswerCandidate,
    KeyConflict,
    Rubric,
    RubricItem,
    StudentRecord,
    Transcript,
    TranscriptLine,
)
from dawoni.paths import AppPaths


def _pdf(path: Path, students: int, per: int) -> Path:
    """표지(같은 양식 + 학생마다 다른 이름) + 답안 쪽(표)."""
    doc = pymupdf.open()
    for s in range(students):
        for k in range(per):
            page = doc.new_page(width=595, height=842)
            if k == 0:
                page.draw_rect(pymupdf.Rect(60, 60, 535, 200), color=(0, 0, 0), width=3)
                page.draw_rect(pymupdf.Rect(60, 260, 300, 320), color=(0, 0, 0), width=2)
                page.draw_rect(pymupdf.Rect(400, 400, 535, 520), color=(0, 0, 0), width=2)
                page.insert_text((80, 300), f"student {s}", fontsize=14)
            else:
                for y in range(80, 800, 90 + 10 * k):
                    page.draw_line((40, y), (555, y), color=(0, 0, 0), width=1)
                page.draw_line((480, 80), (480, 790), color=(0, 0, 0), width=1)
    doc.save(str(path))  # pyright: ignore[reportUnknownMemberType]
    return path


def test_pages_per_student_from_similar_covers(tmp_path: Path) -> None:
    assert autopilot.pages_per_student(_pdf(tmp_path / "a.pdf", 4, 3)) == (3, None)
    assert autopilot.pages_per_student(_pdf(tmp_path / "b.pdf", 5, 2)) == (2, None)
    blank = pymupdf.open()
    for _ in range(6):
        blank.new_page()
    blank.save(str(tmp_path / "blank.pdf"))  # pyright: ignore[reportUnknownMemberType]
    per, note = autopilot.pages_per_student(tmp_path / "blank.pdf", fallback=3)
    assert per == 3 and note and "표지를 찾지 못해" in note


def _draft(**kw: Any) -> Rubric:
    data: dict[str, Any] = {
        "question_number": "1",
        "items": [
            RubricItem(id="R1", points=2, criterion="꼭짓점을 구함", evidence_hint=""),
            RubricItem(id="R2", points=2, criterion="문제 기준", evidence_hint=""),
        ],
        "accepted_alternatives": [
            AcceptedAlternative(
                id="E1", description="다른 표현", item_ids=["R1"], status="proposed"
            )
        ],
        "candidates": [
            AnswerCandidate(id="A1", part="", answer="3", argument="검산함", check="valid"),
            AnswerCandidate(id="A2", part="", answer="5", argument="?", check="unsure"),
        ],
        "key_conflicts": [
            KeyConflict(
                id="K1",
                note="해설지는 단위 없으면 0점",
                item_id="R2",
                key_criterion="해설지 기준 (단위 없으면 0점)",
                question_criterion="문제 기준",
            ),
        ],
    }
    return Rubric.model_validate({**data, **kw})


def test_approve_all_accepts_expansions_and_follows_teacher_key(
    app_paths: AppPaths, tmp_path: Path
) -> None:
    """넓힌 표현·검산이 맞은 후보는 받아들이고, 해설지와 다른 기준은 강사의 해설지대로, 검산이
    엇갈린 후보만 묻는다 — 그리고 모두 승인."""
    svc = _services(app_paths, tmp_path)
    store = _exam(svc)
    store.save_rubric(_draft())
    approved, decided, asks = autopilot.approve_all(svc, store, by="김선생")
    r = store.rubric("1")
    assert r is not None and r.status == "approved" and approved == ["1"]
    assert [a.status for a in r.accepted_alternatives] == ["accepted"]
    assert [c.status for c in r.candidates] == ["accepted", "proposed"]
    assert r.items[1].criterion == "해설지 기준 (단위 없으면 0점)", "해설지를 따른다"
    assert [a.kind for a in asks] == ["candidate"] and "A2" in asks[0].text
    assert any("해설지대로" in d for d in decided) and any("E1" in d for d in decided)
    again = autopilot.approve_all(svc, store, by="김선생")
    assert again == ([], [], []), "승인된 루브릭은 그대로"


def test_unreadable_answer_is_graded_and_flagged(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path
) -> None:
    exe = make_fake("script", script=[{"response": json.dumps(GRADE_A, ensure_ascii=False)}])
    svc = _services(app_paths, tmp_path, exe)
    store = _exam(svc)
    store.save_rubric(approved_rubric())
    text = (FIX / "answers" / "A_q1.txt").read_text(encoding="utf-8").splitlines()
    lines = [TranscriptLine(text=x) for x in text if x.strip()]
    lines.append(TranscriptLine(text="[판독불가] 이다"))
    store.save_transcript(Transcript(student_id="A", question_number="1", lines=lines))
    store.save_student(StudentRecord(student_id="A", name=None, class_name="2반", source="t"))
    skipped = workflow.grade_exam(svc, store)
    assert not skipped.graded and "입력 대기" in skipped.skipped[0].reason, "예전 흐름은 기다림"
    run = workflow.grade_exam(svc, store, allow_unreadable=True)
    (g,) = run.graded
    assert g.status == "needs_review" and "[판독불가] 1곳" in g.flags[0]
    s = autopilot.AutoState(state="done")
    autopilot.save_state(store, s)
    got = autopilot.refresh(store)
    assert got is not None and got.graded == 1 and got.review == 1
    assert sorted(a.kind for a in got.asks) == ["name", "unreadable"], "이름 없음·판독불가는 묻는다"


def test_drawn_answers_send_the_page_to_the_grader(tmp_path: Path) -> None:
    """학생이 그린 작도·그래프가 있는 답안만 원본 쪽 그림을 채점에 함께 보낸다 (2026-11-18 실측:
    렌즈 문항 광선 작도가 "R광선, B광선"으로만 남아 "작도로 설명해도 인정"인데 0점)."""
    from dawoni.grading.pipeline import drawing_pages

    page = tmp_path / "scans" / "가" / "page_02.png"
    page.parent.mkdir(parents=True)
    page.write_bytes(b"png")
    lines = [TranscriptLine(text="R광선, B광선"), TranscriptLine(text="③")]
    drawn = Transcript(
        student_id="가", question_number="2", pages=["scans/가/page_02.png"], lines=lines
    )
    words = drawn.model_copy(update={"lines": [TranscriptLine(text="파란 빛이 더 꺾인다")]})
    unsure = words.model_copy(
        update={"lines": [TranscriptLine(text="x", uncertain=["광선 작도가 있음"])]}
    )
    assert drawing_pages(drawn, tmp_path) == [page]
    assert drawing_pages(words, tmp_path) == [], "글만 있으면 그림을 보내지 않음"
    assert drawing_pages(unsure, tmp_path) == [page], "판독 AI가 남긴 메모도 본다"


def test_class_names_in_one_batch_are_unified(app_paths: AppPaths, tmp_path: Path) -> None:
    """표지 손글씨 반 이름이 조금씩 다르게 읽혀도 한 묶음은 한 반으로 (실측: 18명이 반 6개로)."""
    svc = _services(app_paths, tmp_path)
    store = _exam(svc)
    read = ["MSS1-J", "MSS1-J", "MSS1-J", "MSS1J", "MSS-1", "MSS", "TSS1", None, "고3"]
    for k, klass in enumerate(read):
        store.save_student(StudentRecord(student_id=f"s{k}", name=f"학생{k}", class_name=klass,
                                         source=f"m1j.pdf {2 * k + 1}-{2 * k + 2}쪽"))  # fmt: skip
    store.save_student(StudentRecord(student_id="other", class_name="2반", source="m2j.pdf 1-2쪽"))
    moved = autopilot.unify_classes(store, "m1j.pdf")
    got = {r.student_id: r.class_name for r in store.student_records()}
    assert [got[f"s{k}"] for k in range(8)] == ["MSS1-J"] * 8
    assert got["s8"] == "고3", "많이 다른 반 이름은 그대로"
    assert got["other"] == "2반", "다른 묶음은 건드리지 않음"
    assert len(moved) == 5
    s4 = store.student_record("s4")
    assert s4 is not None and any("MSS-1" in u for u in s4.uncertain), "읽은 그대로는 남긴다"


def test_quick_api_adds_answers_to_an_exam(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path
) -> None:
    """결과 화면의 '답안지 더 넣기': 이미 읽은 학생은 판독을 건너뛰고(AI 없음), 채점 안 된 답만
    채점해(가짜 CLI 1번) 상태·점수표·채점 답안지를 다시."""
    from test_readings import _client_for  # pyright: ignore[reportPrivateUsage]
    from test_server import _wait  # pyright: ignore[reportPrivateUsage]

    exe = make_fake("script", script=[{"response": json.dumps(GRADE_A, ensure_ascii=False)}])
    svc = _services(app_paths, tmp_path, exe)
    store = _exam(svc)
    store.save_rubric(approved_rubric())
    pdf = _pdf(tmp_path / "2반.pdf", 1, 2)
    store.save_student(StudentRecord(student_id="가", name="가", class_name="2반",
                                     source="2반.pdf 1-2쪽"))  # fmt: skip
    text = (FIX / "answers" / "A_q1.txt").read_text(encoding="utf-8").splitlines()
    lines = [TranscriptLine(text=x) for x in text if x.strip()]
    store.save_transcript(Transcript(student_id="가", question_number="1", lines=lines))
    c = _client_for(app_paths, svc)
    bad = c.post("/api/quick", data={"exam_id": store.exam_id},
                 files={"answers": ("a.txt", b"x", "text/plain")})  # fmt: skip
    assert bad.status_code == 400 and "PDF" in bad.json()["detail"]
    upload = {"answers": ("2반.pdf", pdf.read_bytes(), "application/pdf")}
    job = c.post("/api/quick", data={"exam_id": store.exam_id}, files=upload).json()
    done = _wait(c, job)
    assert done["status"] == "succeeded", done
    assert done["result"]["graded"] == 1 and done["params"]["exam"] == store.exam_id
    state = c.get(f"/api/exams/{store.exam_id}/autopilot").json()
    assert state["state"] == "done" and state["students"] == 1 and state["graded"] == 1
    assert (svc.workspace.scan_inbox / "2반.pdf").is_file(), "올린 답안지 원본은 스캔본 폴더에"
    summary = c.get(f"/api/exams/{store.exam_id}").json()["summary"]
    assert summary["auto"] and summary["step"] == "result"


def test_grade_kept_when_only_a_quote_is_off() -> None:
    """수리 뒤에도 '인용이 판독문에 없다'만 남으면 그 인용만 빼고 점수는 두고 확인할 점 (빈칸으로
    남지 않게). 다른 오류가 섞이면 받지 않는다."""
    from dawoni.context import TaskFailed
    from dawoni.grading.pipeline import accept_quote_misses

    rubric = approved_rubric()
    last = json.dumps(GRADE_A, ensure_ascii=False)
    miss = "R2: 인용 'x=2일 때 … 3'가 학생 답안에 없다. 답안에 실제로 적힌 글·식만 그대로 인용할 것"
    kept = accept_quote_misses(TaskFailed("grade_answer", [miss], 3, last), rubric)
    assert kept is not None
    assert [j.evidence_quote for j in kept.judgements] == ["(x-2)^2+3", None]
    assert [j.awarded for j in kept.judgements] == [2, 2] and "R2" in kept.flags[0]
    other = TaskFailed("grade_answer", [miss, "R3: 루브릭에 없는 항목"], 3, last)
    assert accept_quote_misses(other, rubric) is None


def test_single_letter_part_labels_do_not_eat_student_words() -> None:
    """소문항 표시가 A·B인 문항: 인용 속 학생 글의 B("B가 더 높아")를 표시로 보고 지우면 안 된다
    (2026-11-18 실측: 실제로 있는 인용을 없다고 해 수리 세 번 끝에 채점 실패)."""
    from dawoni.grading.checks import quote_in_transcript

    t = Transcript(
        student_id="가",
        question_number="2",
        lines=[
            TranscriptLine(part="A", text="∴ 오른쪽 그림과 같이 뒤쪽의 연장선이 B가 더 높아"),
            TranscriptLine(part="A", text="더 큰 정립상이 나오게 된다"),
            TranscriptLine(part="B", text="∴ ③"),
        ],
    )
    assert quote_in_transcript("뒤쪽의 연장선이 B가 더 높아 … 더 큰 정립상이 나오게 된다", t)
    assert quote_in_transcript("A ∴ 오른쪽 그림과 같이", t), "줄 앞 표시는 그대로 빼 준다"
    assert not quote_in_transcript("B가 더 낮아", t)
