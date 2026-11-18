# pyright: reportUnknownMemberType=false
"""채점 사례집 (dawoni/casebook.py, 2026-11-18 사용자 요청): 채점 검토에서 바꾼 점수 → md 사례 →
다음 시험의 해설 다듬기·루브릭에 그 문항과 맞는 것만. AI 없이(가짜 CLI)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from conftest import MakeFake
from test_answer_key import EXPANDED, KEY
from test_grading import RUBRIC_OK, approved_rubric
from test_readings import _client_for, _exam, _services  # pyright: ignore[reportPrivateUsage]
from test_server import _wait  # pyright: ignore[reportPrivateUsage]
from tests_support import fake_prompts

from dawoni import casebook
from dawoni.agents import AgentClient
from dawoni.casebook import Case, CaseStore
from dawoni.config import AppConfig
from dawoni.context import ContextEngine
from dawoni.fsio import read_json
from dawoni.grading.pipeline import expand_key_rubric
from dawoni.grading.store import ExamStore
from dawoni.models.answer_key import AnswerKey
from dawoni.models.base import utcnow
from dawoni.models.grading import (
    ExamQuestion,
    GradeResult,
    ItemJudgement,
    Transcript,
    TranscriptLine,
)
from dawoni.models.scope import ScopeProfile
from dawoni.paths import AppPaths, WorkspacePaths


def _case(**kw: Any) -> Case:
    data: dict[str, Any] = {
        "title": "'더 많이 꺾인다' = 굴절률이 크다",
        "verdict": "인정",
        "keywords": ["렌즈", "굴절", "초점거리"],
        "exam": "11월 월례",
        "question": "2",
        "body": "**다음부터**: '더 많이 꺾인다'고 써도 굴절률이 큰 것으로 인정한다.",
    }
    return Case.model_validate(data | kw)


def test_case_is_an_md_file_people_can_edit(tmp_path: Path) -> None:
    store = CaseStore(WorkspacePaths(tmp_path))
    c = store.save(_case())
    path = tmp_path / "casebook" / f"{c.id}.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\nid: ") and '"verdict": ' not in text and 'verdict: "인정"' in text
    assert text.rstrip().endswith("굴절률이 큰 것으로 인정한다.")
    # 메모장으로 고친 본문·앞머리 (따옴표 없는 값)도 읽힌다
    path.write_text(text.replace('verdict: "인정"', "verdict: 불인정") + "- 강사 덧붙임\n", "utf-8")
    again = store.get(c.id)
    assert again.verdict == "불인정" and again.body.endswith("- 강사 덧붙임")
    index = (tmp_path / "casebook" / "사례집.md").read_text(encoding="utf-8")
    assert "[인정] '더 많이 꺾인다' = 굴절률이 크다 — 11월 월례 2번" in index
    assert store.delete(c.id) and store.all() == []


def test_only_cases_matching_the_question_go_into_the_prompt(tmp_path: Path) -> None:
    """쌓여도 프롬프트는 커지지 않는다: 열쇠말이 문항 글과 겹치는 것만, 상한까지."""
    store = CaseStore(WorkspacePaths(tmp_path))
    lens = store.save(_case())
    store.save(_case(title="전하 부호", keywords=["대전", "전하", "유도"]))
    store.save(_case(title="꺼 둔 것", keywords=["렌즈", "굴절"], enabled=False))
    store.save(_case(title="이 문항만", keywords=["렌즈", "굴절"], reusable=False))
    store.save(_case(title="하나만 겹침", keywords=["렌즈", "거울", "반사"]))
    store.save(_case(title="흔한 말로만 겹침", keywords=["렌즈", "서술형", "그래프"]))
    old = store.save(_case(title="옛 렌즈 사례", keywords=["렌즈", "굴절"],
                           created_at=utcnow() - timedelta(days=30)))  # fmt: skip
    question = (
        "볼록 렌즈로 본 곰젤리 — 파란색이면 더 많이 굴절하여 초점거리가 짧아진다 [서술형 8점]"
    )
    got = store.relevant(question)
    assert [c.id for c in got] == [lens.id, old.id], "겹침 많은 것, 새 것부터"
    assert store.relevant("전기 회로의 저항") == []
    small = store.relevant(question, limit_chars=len(casebook.entry_text(lens)) + 5)
    assert [c.id for c in small] == [lens.id], "상한을 넘는 사례는 뺀다"
    text = casebook.prompt_text(got)
    assert text is not None and text.startswith("[지난 채점 사례]")
    assert "### [인정] '더 많이 꺾인다' = 굴절률이 크다 (11월 월례 2번)" in text


def _graded_exam(svc: Any) -> ExamStore:
    store = _exam(svc)
    r = approved_rubric(RUBRIC_OK)
    store.save_rubric(r)
    for sid, quote in (
        ("A", "꼭짓점이 (2,3)이라 가장 작은 값은 3"),
        ("B", "꼭짓점에서 제일 작다 3"),
    ):
        store.save_transcript(Transcript(
            student_id=sid, question_number="1", confirmed=True, source="ocr",
            lines=[TranscriptLine(text="(x-2)^2+3"), TranscriptLine(text=quote)],
        ))  # fmt: skip
        js = [
            ItemJudgement(rubric_item_id="R1", awarded=2, evidence_quote="(x-2)^2+3",
                          reason="표준형 변형", confidence=0.9),
            ItemJudgement(rubric_item_id="R2", awarded=0, evidence_quote=quote,
                          reason="'최솟값'이라는 말이 없음", deduction_reason="최솟값을 쓰지 않음",
                          confidence=0.6),
        ]  # fmt: skip
        store.save_grade(GradeResult(
            student_id=sid, question_number="1", rubric_version=r.version, judgements=js,
            comment="", total=2, max_points=4, agent="agy",
            item_points={i.id: i.points for i in r.items},
        ))  # fmt: skip
    return store


DRAFT: dict[str, Any] = {
    "keep": True,
    "title": "'가장 작은 값' = 최솟값",
    "expression": "가장 작은 값은 3",
    "criterion": "x=2일 때 최솟값 3을 제시",
    "lesson": "'최솟값'이라는 낱말 대신 '가장 작은 값', '제일 작다'로 써도 같은 뜻이면 인정한다.",
    "keywords": ["최솟값", "꼭짓점", "이차함수"],
    "reusable": True,
    "reason": "같은 뜻의 다른 표현",
}


def test_changed_score_becomes_a_case(
    app_paths: AppPaths, tmp_path: Path, make_fake: MakeFake
) -> None:
    """채점 검토에서 점수를 바꾸면 (이유는 선택) 사례 한 건 — 학생 표현·기준·AI 점수 → 강사 점수."""
    exe = make_fake("script", script=[{"response": json.dumps(DRAFT, ensure_ascii=False)}])
    svc = _services(app_paths, tmp_path, exe)
    store = _graded_exam(svc)
    c = _client_for(app_paths, svc)
    base = f"/api/exams/{store.exam_id}"
    same = c.put(f"{base}/students/A/grades/1/parts", json={"part_scores": {"": 2}, "learn": True})
    assert same.status_code == 200
    assert not [j for j in c.get("/api/jobs").json() if j["kind"] == "casebook"], "안 바뀌면 없음"

    body = {"part_scores": {"": 4}, "part_notes": {"": "가장 작은 값도 최솟값"}, "learn": True,
            "by": "김선생"}  # fmt: skip
    assert c.put(f"{base}/students/A/grades/1/parts", json=body).json()["total"] == 4
    job = next(j for j in c.get("/api/jobs").json() if j["kind"] == "casebook")
    done = _wait(c, job)
    assert done["status"] == "succeeded", done
    (got,) = c.get("/api/casebook").json()["cases"]
    assert got["title"] == "'가장 작은 값' = 최솟값" and got["verdict"] == "인정"
    assert (got["ai_points"], got["teacher_points"], got["max_points"]) == (2, 4, 4)
    assert got["created_by"] == "김선생" and got["exam"] == "10월 시험" and got["question"] == "1"
    assert "- 강사 메모: 가장 작은 값도 최솟값" in got["body"] and "A" not in got["keywords"]
    prompt = fake_prompts(exe)[0]
    assert "강사가 AI보다 점수를 **더 주었다**" in prompt and "가장 작은 값은 3" in prompt
    assert "[AI 채점] 2 / 4점" in prompt and "강사 이유: 가장 작은 값도 최솟값" in prompt
    assert "Role: light helper" in prompt, "가벼운 도우미 (fast 등급)"

    edited = c.patch(
        f"/api/casebook/{got['id']}", json={"enabled": False, "keywords": ["최솟값", " "]}
    )
    assert edited.json()["enabled"] is False and edited.json()["keywords"] == ["최솟값"]
    assert c.delete(f"/api/casebook/{got['id']}").json() == {"deleted": True}


def test_same_judgement_adds_a_source_instead_of_a_new_case(
    app_paths: AppPaths, tmp_path: Path, make_fake: MakeFake
) -> None:
    exe = make_fake("script", script=[{"response": json.dumps(DRAFT, ensure_ascii=False)}])
    svc = _services(app_paths, tmp_path, exe)
    store = _graded_exam(svc)
    before = store.grade("A", "1")
    assert before is not None
    after = before.model_copy(update={"teacher_part_scores": {"": 4.0}})
    rubric = store.rubric("1")
    (ch,) = casebook.changes("1", before, after, rubric, {})
    assert (ch.label, ch.part, ch.ai, ch.teacher, ch.max) == ("1", "", 2, 4, 4)
    first = casebook.record(svc, store, "1", "A", ch, by="김선생")
    assert first.added and first.case is not None

    dup = {"keep": True, "duplicate_of": first.case.id, "reason": "같은 판단"}
    nothing = {"keep": False, "reason": "판독 실수"}
    exe2 = make_fake("script", script=[{"response": json.dumps(x, ensure_ascii=False)}
                                       for x in (dup, nothing)])  # fmt: skip
    svc.config.agents["agy"].executable = str(exe2)
    again = casebook.record(svc, store, "1", "B", ch, by="김선생")
    assert not again.added and again.case is not None and again.case.id == first.case.id
    assert "- 같은 판단: 10월 시험 1번 (AI 2점 → 강사 4점" in again.case.body
    assert len(CaseStore(svc.workspace).all()) == 1
    assert f"ID {first.case.id}" in fake_prompts(exe2)[0], "비슷한 사례를 ID와 함께 보여 준다"
    skipped = casebook.record(svc, store, "1", "B", ch, by=None)
    assert skipped.case is None and skipped.reason == "판독 실수"


def test_rubric_and_key_refining_read_matching_cases(
    app_paths: AppPaths, tmp_path: Path, make_fake: MakeFake
) -> None:
    """다음 시험: 루브릭(1단계)·해설 다듬기(0단계) 프롬프트의 [기준 예시]에 맞는 사례만."""
    fix = Path(__file__).parent / "fixtures" / "grading"
    scope = ScopeProfile.model_validate(read_json(fix / "scope.json"))
    base = read_json(fix / "questions.json")[0]
    q3 = ExamQuestion.model_validate({
        **base, "number": "3", "points": 10,
        "parts": [{"label": "(1)", "points": 4, "kind": "short_answer"},
                  {"label": "(2)", "points": 6, "kind": "essay"}],
    })  # fmt: skip
    exe = make_fake("script", script=[{"response": json.dumps(EXPANDED, ensure_ascii=False)}])
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    engine = ContextEngine(AgentClient(cfg, app_paths))
    case = _case(title="반사점 A를 그림으로만 표시해도 인정", keywords=["반사", "반사점"])
    r = expand_key_rubric(engine, q3, AnswerKey.model_validate(KEY), scope, cases=[case])
    prompt = fake_prompts(exe)[0]
    assert "## [기준 예시]\n[지난 채점 사례]" in prompt and "반사점 A를 그림으로만" in prompt
    assert "지난 채점 사례**: 강사가 지난 시험의" in prompt, "과업에 쓰는 법"
    assert any("지난 채점 사례 1건 참고: 반사점 A를" in n for n in r.review_notes)


def test_key_refining_lists_the_cases_it_used(
    app_paths: AppPaths, tmp_path: Path, make_fake: MakeFake
) -> None:
    from test_keyprep import _exam_hwpx, _q1_out  # pyright: ignore[reportPrivateUsage]

    from dawoni import keyprep

    exe = make_fake("script", script=[{"response": json.dumps(_q1_out(), ensure_ascii=False)}])
    svc = _services(app_paths, tmp_path, exe)
    CaseStore(svc.workspace).save(_case(title="W를 쓴 장력 답", keywords=["장력", "정사면체"]))
    store = keyprep.create(svc, _exam_hwpx(tmp_path / "월례.hwpx"), level="물리")
    q = keyprep.refine_question(svc, store, "1")
    assert q.cases == ["W를 쓴 장력 답"]
    prompt = fake_prompts(exe)[0]
    assert "[지난 채점 사례]" in prompt and "W를 쓴 장력 답" in prompt
