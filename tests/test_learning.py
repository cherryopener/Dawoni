"""메모리·스킬·배우기: 상황이 맞는 스킬만, 상한 안의 메모만 프롬프트에 들어간다 (가짜 CLI)."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import MakeFake
from fastapi.testclient import TestClient
from test_server import _client, _wait  # pyright: ignore[reportPrivateUsage]
from tests_support import fake_prompts

from dawoni.agents import AgentClient
from dawoni.config import AppConfig, save_config
from dawoni.context import (
    ContextEngine,
    MemoryNote,
    MemoryStore,
    RuleStore,
    Skill,
    SkillStore,
    TaskDef,
    TaskInput,
    situation,
)
from dawoni.context.calllog import CallLog
from dawoni.context.learn import Correction, learn, propose
from dawoni.context.memory import MemoryFull
from dawoni.context.skills import parse_skill
from dawoni.models.base import DawoniModel
from dawoni.paths import AppPaths


class _Out(DawoniModel):
    title: str


INGEST: TaskDef[_Out] = TaskDef(
    name="simple_ingest", template="refine_rule.md.j2", output=_Out, rule_target="ingest",
    role="page_reader",
)  # fmt: skip
VARS = TaskInput(variables={"request": "요구", "existing": [], "targets": {"ingest": "자료"}})
OK = json.dumps({"title": "t"})


def _skill(**kw: Any) -> Skill:
    base: dict[str, Any] = dict(
        name="김승규 교재", description="김승규 교재를 DB화할 때", applies_to=["ingest"],
        triggers=["김승규"], body="소문항은 (가), (나)로 나눌 것.",
    )  # fmt: skip
    return Skill.model_validate({**base, **kw})


@pytest.fixture
def engine_for(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path
) -> Callable[..., tuple[ContextEngine, Path]]:
    def make(script: list[dict[str, Any]], **stores: Any) -> tuple[ContextEngine, Path]:
        exe = make_fake("script", script=script)
        cfg = AppConfig()
        cfg.agents["agy"].executable = str(exe)
        cfg.agents["claude"].enabled = False
        cfg.agents["codex"].enabled = False
        engine = ContextEngine(
            AgentClient(cfg, app_paths), calllog=CallLog(tmp_path / "calls.jsonl"), **stores
        )
        return engine, exe

    return make


# ---------------------------------------------------------------- 스킬


def test_skill_file_round_trip_and_foreign_skill_md(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    s = store.save(_skill(body="1. 첫째\n2. 둘째"))
    text = (tmp_path / "skills" / s.id / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n") and "2. 둘째" in text
    assert store.get(s.id) == s

    # 다른 곳에서 가져온 SKILL.md (YAML 문자열, 다워니 필드 없음)
    d = tmp_path / "skills" / "pdf-tables"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: pdf-tables\ndescription: 'PDF 표를 옮길 때'\n---\n표는 행 단위로.\n",
        encoding="utf-8",
    )
    foreign = store.get("pdf-tables")
    assert foreign is not None
    assert foreign.applies_to == ["all"] and foreign.body == "표는 행 단위로."

    with pytest.raises(ValueError):
        parse_skill("본문만", fallback_id="x")


def test_skill_injected_only_when_situation_matches(
    engine_for: Callable[..., tuple[ContextEngine, Path]], tmp_path: Path
) -> None:
    store = SkillStore(tmp_path / "skills")
    kim = store.save(_skill())
    store.save(_skill(name="강의용", applies_to=["lecture"], triggers=[], body="강의 스킬 본문"))
    store.save(_skill(name="꺼짐", enabled=False, body="꺼진 스킬 본문"))
    store.save(_skill(name="제안", proposed=True, body="제안된 스킬 본문"))
    store.save(_skill(name="항상", triggers=[], body="상황 단어 없는 스킬 본문"))
    engine, exe = engine_for([{"response": OK}] * 2, skills=store)

    engine.run(INGEST, VARS)  # 상황 없음
    with situation("2026 김승규 물리학1.pdf"):
        res = engine.run(INGEST, VARS)

    plain, matched = fake_prompts(exe)
    assert "상황 단어 없는 스킬 본문" in plain and "(가), (나)" not in plain
    assert "## [스킬" in matched and "### 김승규 교재\n소문항은 (가), (나)로" in matched
    for p in (plain, matched):
        assert "강의 스킬 본문" not in p
        assert "꺼진 스킬 본문" not in p and "제안된 스킬 본문" not in p
    assert kim.id in res.skill_ids and len(res.skill_ids) == 2
    assert kim.id in str(CallLog(tmp_path / "calls.jsonl").read()[-1]["skill_ids"])


def test_situation_nests_and_resets() -> None:
    from dawoni.context.engine import _SITUATION  # pyright: ignore[reportPrivateUsage]

    with situation("바깥.pdf", None):
        with situation("안쪽"):
            assert _SITUATION.get() == ("바깥.pdf", "안쪽")
        assert _SITUATION.get() == ("바깥.pdf",)
    assert _SITUATION.get() == ()


def test_skill_revision_proposal_overwrites_original(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    orig = store.save(_skill())
    prop = store.save(_skill(body="고친 본문", proposed=True, revises=orig.id))
    done = store.approve(prop.id)
    assert done.id == orig.id and done.body == "고친 본문" and not done.proposed
    assert [s.id for s in store.all()] == [orig.id]


# ---------------------------------------------------------------- 메모리


def test_memory_cap_and_merge(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory", limit_chars=30)
    a = store.save(MemoryNote(text="가" * 20, applies_to=["ingest"]))
    store.save(MemoryNote(text="나" * 20, applies_to=["lecture"]))  # 다른 작업은 따로 센다
    assert store.usage()["ingest"] == 20 and store.usage()["lecture"] == 20
    with pytest.raises(MemoryFull):
        store.save(MemoryNote(text="다" * 20, applies_to=["all"]))

    # 제안은 상한을 쓰지 않고, 승인할 때 합칠 메모를 지우고 들어간다
    prop = store.save(
        MemoryNote(text="라" * 25, applies_to=["ingest"], proposed=True, replaces=[a.id])
    )
    assert store.usage()["ingest"] == 20
    store.approve(prop.id)
    assert store.get(a.id) is None and store.usage()["ingest"] == 25

    big = store.save(MemoryNote(text="마" * 10, applies_to=["ingest"], proposed=True))
    with pytest.raises(MemoryFull):
        store.approve(big.id)


def test_memory_injected_for_its_task_only(
    engine_for: Callable[..., tuple[ContextEngine, Path]], tmp_path: Path
) -> None:
    store = MemoryStore(tmp_path / "memory")
    note = store.save(MemoryNote(text="문항 번호는 1), 2)로 쓴다.", applies_to=["ingest"]))
    store.save(MemoryNote(text="강의 메모", applies_to=["lecture"]))
    store.save(MemoryNote(text="제안 메모", applies_to=["ingest"], proposed=True))
    engine, exe = engine_for([{"response": OK}], memory=store)
    res = engine.run(INGEST, VARS)
    prompt = fake_prompts(exe)[0]
    assert "## [메모리" in prompt and "- 문항 번호는 1), 2)로 쓴다." in prompt
    assert "강의 메모" not in prompt and "제안 메모" not in prompt
    assert res.memory_ids == [note.id]


# ---------------------------------------------------------------- 배우기


def test_learn_repairs_unknown_id_and_saves_proposal(
    engine_for: Callable[..., tuple[ContextEngine, Path]], tmp_path: Path
) -> None:
    rules, memory = RuleStore(tmp_path / "rules"), MemoryStore(tmp_path / "memory")
    skills = SkillStore(tmp_path / "skills")
    old = memory.save(MemoryNote(text="번호는 1)로.", applies_to=["ingest"]))
    note: dict[str, Any] = {"text": "문항 번호는 1), 2) 형식.", "applies_to": ["ingest"]}
    bad = {"kind": "memory", "reason": "번호 형식", "memory": {**note, "replaces": ["mem-없는것"]}}
    good = {**bad, "memory": {**note, "replaces": [old.id]}}
    engine, exe = engine_for([{"response": json.dumps(bad)}, {"response": json.dumps(good)}])
    c = Correction(note="번호 형식 통일해 줘", before="①", after="1)", target="ingest",
                   situation="김승규.pdf")  # fmt: skip
    res = learn(engine, c, rules=rules, memory=memory, skills=skills)
    assert res.attempts == 2
    first = fake_prompts(exe)[0]
    assert old.id in first and "[AI가 낸 결과]\n①" in first and "김승규.pdf" in first
    assert "## [메모리" not in first, "기존 메모는 자료로만, 지시로 넣지 않음"

    saved = propose(res.value, c, memory=memory, skills=skills, by="김선생")
    assert isinstance(saved, MemoryNote) and saved.proposed and saved.replaces == [old.id]
    assert memory.active_for(["ingest"]) == [old]
    memory.approve(saved.id)
    assert [n.text for n in memory.active_for(["ingest"])] == ["문항 번호는 1), 2) 형식."]


def test_learn_needs_note_or_correction(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        learn(
            None,  # pyright: ignore[reportArgumentType]
            Correction(),
            rules=RuleStore(tmp_path / "r"),
            memory=MemoryStore(tmp_path / "m"),
            skills=SkillStore(tmp_path / "s"),
        )


def test_learn_task_is_internal() -> None:
    from dawoni.context.learn import LEARN

    assert LEARN.rule_target is None
    assert dataclasses.replace(LEARN).role == "light_helper"


# ---------------------------------------------------------------- API


def test_learning_api(app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path) -> None:
    lesson = {
        "kind": "skill", "reason": "이 채널 강의만의 형식",
        "skill": {"name": "물리 채널 요약", "description": "물리 채널 강의를 요약할 때",
                  "applies_to": ["lecture"], "triggers": ["물리채널"],
                  "body": "1. 개념 → 공식 → 예제 순서로 정리할 것."},
    }  # fmt: skip
    exe = make_fake("script", script=[{"response": json.dumps(lesson, ensure_ascii=False)}])
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    save_config(app_paths, cfg)
    c: TestClient = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})

    body = {"note": "요약은 개념→공식→예제 순으로", "target": "lecture", "situation": "물리채널"}
    job = _wait(c, c.post("/api/learn", json=body).json())
    assert job["status"] == "succeeded", job
    sid = job["result"]["saved_id"]
    skills = c.get("/api/skills").json()
    assert [s["id"] for s in skills] == [sid] and skills[0]["proposed"]
    assert c.post(f"/api/skills/{sid}/approve").json()["proposed"] is False
    patched = c.patch(f"/api/skills/{sid}", json={"triggers": ["물리", "채널"]}).json()
    assert patched["triggers"] == ["물리", "채널"]

    mine = {"name": "직접", "description": "설명", "applies_to": ["ingest"], "body": "본문"}
    made = c.post("/api/skills", json=mine).json()
    assert made["proposed"] is False
    assert c.delete(f"/api/skills/{made['id']}").json() == {"deleted": True}

    mem = c.get("/api/memory").json()
    assert mem["notes"] == [] and mem["limit"] > 0 and mem["usage"]["grade"] == 0


# ---------------------------------------------------------------- 수정에서 배우기


def test_diff_text_keeps_only_changed_parts() -> None:
    from dawoni.context.learn import diff_text

    before = {"stem": "다음을 구하시오", "choices": ["①", "②"], "id": "a", "orig": "x.png"}
    after = {"stem": "다음을 구하시오", "choices": ["1)", "②"], "id": "b", "orig": "y.png"}
    b, a = diff_text(before, after, ignore=("id", "orig"))
    assert (b, a) == ("choices[0]: ①", "choices[0]: 1)")
    assert diff_text({"x": 1}, {"x": 1}) == ("", "")
    assert diff_text({}, {"note": "새로"}) == ("note: (없음)", "note: 새로")


def test_grade_correction_shows_changed_items_with_criterion() -> None:
    from test_grading import GRADE_A, approved_rubric

    from dawoni.models.grading import GradeResult
    from dawoni.server.learning import grade_correction

    r = approved_rubric()
    g = GradeResult.model_validate(
        {**GRADE_A, "student_id": "A", "question_number": "1", "rubric_version": 1,
         "total": 4, "max_points": 4}
    )  # fmt: skip
    after = g.model_copy(
        update={
            "judgements": [g.judgements[0], g.judgements[1].model_copy(update={"awarded": 1})],
            "teacher_note": "결론 근거 부족",
            "teacher_part_notes": {"(가)": "식만 있으면 1점"},
        }
    )
    c = grade_correction(g, after, r, "1학기 중간")
    assert c.target == "grade" and c.situation == "1학기 중간"
    assert "R1" not in c.before and "R2 (2점, 기준: x=2일 때 최솟값 3을 제시): AI 2점" in c.before
    assert c.after.startswith("R2") and "강사 1점" in c.after
    assert c.note == "결론 근거 부족\n소문항 (가): 식만 있으면 1점"


def test_transcript_edit_with_learn_submits_lesson(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path
) -> None:
    from test_grading import FIX

    from dawoni.fsio import read_json

    lesson = {
        "kind": "memory", "reason": "판독 습관",
        "memory": {"text": "'ㅅ' 모양 기호는 √로 읽는다.", "applies_to": ["transcribe"]},
    }  # fmt: skip
    exe = make_fake("script", script=[{"response": json.dumps(lesson, ensure_ascii=False)}])
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    save_config(app_paths, cfg)
    c = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    scope = c.post("/api/scopes", json=read_json(FIX / "scope.json")).json()
    q1 = read_json(FIX / "questions.json")[:1]
    exam = c.post("/api/exams", json={"title": "중간고사 물리", "scope_id": scope["id"],
                                      "questions": q1}).json()  # fmt: skip
    base = f"/api/exams/{exam['id']}"
    t = c.post(f"{base}/students/A/answers", json={"question": "1", "text": "y=ㅅ2\n답 3"}).json()
    lines = t["lines"]

    # 바뀐 것 없이 저장: 배우기를 켜도 작업을 띄우지 않는다
    path = f"{base}/students/A/transcripts/1"
    c.put(path, json={"lines": lines, "learn": True})
    assert not [j for j in c.get("/api/jobs").json() if j["kind"] == "learn_lesson"]

    lines[0]["text"] = "y=√2"
    c.put(path, json={"lines": lines, "learn": True, "learn_note": "ㅅ은 루트"})
    job = next(j for j in c.get("/api/jobs").json() if j["kind"] == "learn_lesson")
    assert _wait(c, job)["status"] == "succeeded"
    prompt = fake_prompts(exe)[0]
    assert "[AI가 낸 결과]\n[0].글: y=ㅅ2" in prompt and "[0].글: y=√2" in prompt
    assert "ㅅ은 루트" in prompt and "중간고사 물리" in prompt
    notes = c.get("/api/memory").json()["notes"]
    assert len(notes) == 1 and notes[0]["proposed"] and notes[0]["applies_to"] == ["transcribe"]
