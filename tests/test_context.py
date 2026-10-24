"""메타프롬프트·컨텍스트 엔진: 조립, 예산, JSON 추출, 수리 루프, 전역 규칙."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import Field, ValidationError
from tests_support import fake_prompts

from dawoni.agents import AgentClient, AgentError
from dawoni.cli import main
from dawoni.config import AppConfig, save_config
from dawoni.context import (
    ContextBudgetError,
    ContextEngine,
    GlobalRule,
    RuleStore,
    Shrinkable,
    TaskDef,
    TaskFailed,
    TaskInput,
    TemplateStore,
)
from dawoni.context.assembler import assemble, compact_schema, estimate_tokens
from dawoni.context.calllog import CallLog
from dawoni.context.extract import describe_validation_error, extract_json_object
from dawoni.context.refine import RefinedRule, refine_rule
from dawoni.models.base import DawoniModel
from dawoni.paths import AppPaths
from dawoni.workspace import create_workspace

MakeFake = Callable[..., Path]

# ---------------------------------------------------------------- JSON 추출


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('네, 결과입니다.\n```json\n{"a": 1}\n```\n이상입니다.', {"a": 1}),
        ('결과: {"a": {"b": "괄호 } 포함"}} 끝', {"a": {"b": "괄호 } 포함"}}),
        ('{"x": 1} 그리고 더 긴 것 {"a": 1, "b": [1, 2, 3]}', {"a": 1, "b": [1, 2, 3]}),
        ('```\n{"a": 2}\n```', {"a": 2}),
    ],
)
def test_extract_json_object(text: str, expected: dict[str, Any]) -> None:
    assert extract_json_object(text) == expected


@pytest.mark.parametrize("text", ["", "JSON 없음", "[1, 2]", '{"a": 1'])
def test_extract_json_object_none(text: str) -> None:
    assert extract_json_object(text) is None


class _Out(DawoniModel):
    title: str
    score: int = Field(ge=0, le=10)


def test_describe_validation_error() -> None:
    with pytest.raises(ValidationError) as ei:
        _Out.model_validate({"score": 11, "extra": 1})
    msgs = describe_validation_error(ei.value)
    assert "필드 `title` 가 빠졌다 (필수)." in msgs
    assert "필드 `extra` 는 스키마에 없다 — 지울 것." in msgs
    assert any(m.startswith("`score`:") for m in msgs)


# ---------------------------------------------------------------- 조립·예산


def test_estimate_tokens_is_conservative_for_korean() -> None:
    assert estimate_tokens("가나다라") == 4
    assert estimate_tokens("abcdefgh") == 2


def test_compact_schema_keeps_fields_named_title() -> None:
    s = compact_schema(_Out.model_json_schema())
    assert "title" not in s  # 모델 제목은 제거
    assert "title" in s["properties"]  # 'title'이라는 필드는 유지
    assert "title" not in s["properties"]["score"]


def test_template_version_and_strict_undefined(tmp_path: Path) -> None:
    (tmp_path / "t.md.j2").write_text("{#- version: 7 -#}\n안녕 {{ name }}", encoding="utf-8")
    (tmp_path / "noversion.md.j2").write_text("x", encoding="utf-8")
    store = TemplateStore(tmp_path)
    assert store.version("t.md.j2") == "7"
    assert store.render("t.md.j2", name="강사") == "안녕 강사"
    with pytest.raises(Exception, match="name"):
        store.render("t.md.j2")  # 빠진 변수는 빈칸이 되지 않고 오류
    with pytest.raises(ValueError, match="version"):
        store.version("noversion.md.j2")


def _rule(title: str, text: str, applies: list[Any], enabled: bool = True) -> GlobalRule:
    return GlobalRule(title=title, text=text, applies_to=applies, enabled=enabled)


def _assemble(**kw: Any) -> Any:
    args: dict[str, Any] = dict(
        task_template="refine_rule.md.j2",
        variables={"request": "요구", "existing": [], "targets": {"grade": "채점"}},
        output_schema=_Out.model_json_schema(),
        rules=[],
        budget_tokens=50_000,
    )
    args.update(kw)
    return assemble(TemplateStore(), **args)


def test_layer_order() -> None:
    p = _assemble(
        rules=[_rule("존댓말", "첨삭은 존댓말로 쓸 것.", ["feedback"])],
        scope="허용 개념: 근의 공식",
        material="문항 1 …",
        examples="예시 답안 …",
        target="학생 답안 …",
        repair="[이전 응답의 문제 …]",
    )
    heads = [
        "## [과업]",
        "## [반드시 지킬 전역 규칙 (강사 설정)]",
        "## [풀이 범위]",
        "## [자료]",
        "## [기준 예시]",
        "## [대상]",
        "## [출력 형식]",
        "[이전 응답의 문제",
    ]
    positions = [p.text.index(h) for h in heads]
    assert positions == sorted(positions)
    assert p.text.startswith("너는 학원 강사용 프로그램 Dawoni")
    assert "- [존댓말] 첨삭은 존댓말로 쓸 것." in p.text
    assert set(p.template_versions) == {"contract.md.j2", "refine_rule.md.j2"}


def test_empty_layers_are_omitted() -> None:
    p = _assemble()
    assert "## [반드시 지킬 전역 규칙" not in p.text
    assert "## [풀이 범위]" not in p.text
    assert p.rule_ids == []


def test_budget_shrinks_examples_before_scope() -> None:
    big = "가" * 3000
    p = _assemble(
        examples=Shrinkable([big, "예시 요약"]),
        scope=Shrinkable([big, "범위 요약"]),
        budget_tokens=estimate_tokens(_assemble().text) + 3500,
    )
    assert p.shrunk == ["examples"]
    assert "예시 요약" in p.text and big in p.text  # 범위는 그대로


def test_budget_error_instead_of_silent_truncation() -> None:
    with pytest.raises(ContextBudgetError, match="예산"):
        _assemble(target="가" * 10_000, budget_tokens=2_000)


# ---------------------------------------------------------------- 전역 규칙 저장소


def test_rule_store(tmp_path: Path) -> None:
    store = RuleStore(tmp_path / "rules")
    a = store.save(_rule("존댓말", "첨삭은 존댓말로.", ["feedback"]))
    b = store.save(_rule("전체", "항상 한국어로.", ["all"]))
    c = store.save(_rule("꺼짐", "꺼진 규칙.", ["grade"], enabled=False))
    assert [r.id for r in store.all()] == [a.id, b.id, c.id]
    assert [r.title for r in store.active_for("grade")] == ["전체"]
    assert [r.title for r in store.active_for("feedback")] == ["존댓말", "전체"]
    store.set_enabled(c.id, True)
    assert [r.title for r in store.active_for("grade")] == ["전체", "꺼짐"]
    assert store.resolve(a.id[-6:]).id == a.id
    assert store.delete(a.id) and not store.delete(a.id)
    (tmp_path / "rules" / "rule-broken.json").write_text("{깨짐", encoding="utf-8")
    assert len(store.all()) == 2, "깨진 파일은 건너뛴다"


# ---------------------------------------------------------------- 엔진 + 수리 루프


SIMPLE = TaskDef(name="simple", template="refine_rule.md.j2", output=_Out, rule_target="grade")
VARS = TaskInput(variables={"request": "요구", "existing": [], "targets": {"grade": "채점"}})


@pytest.fixture
def engine_for(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path
) -> Callable[..., tuple[ContextEngine, Path]]:
    def make(
        script: list[dict[str, Any]], rules: RuleStore | None = None
    ) -> tuple[ContextEngine, Path]:
        exe = make_fake("script", script=script)
        cfg = AppConfig()
        cfg.agents["agy"].executable = str(exe)
        cfg.agents["claude"].enabled = False
        cfg.agents["codex"].enabled = False
        engine = ContextEngine(
            AgentClient(cfg, app_paths),
            rules=rules,
            calllog=CallLog(tmp_path / "calls.jsonl"),
        )
        return engine, exe

    return make


GOOD: dict[str, Any] = {"title": "제목", "score": 7}


def test_first_try_success(engine_for: Callable[..., tuple[ContextEngine, Path]]) -> None:
    engine, exe = engine_for([{"response": json.dumps(GOOD, ensure_ascii=False)}])
    res = engine.run(SIMPLE, VARS)
    assert res.value == _Out.model_validate(GOOD)
    assert res.attempts == 1
    assert len(fake_prompts(exe)) == 1


def test_repair_after_broken_json(engine_for: Callable[..., tuple[ContextEngine, Path]]) -> None:
    engine, exe = engine_for(
        [{"response": '네, 결과입니다: {"title": '}, {"response": json.dumps(GOOD)}]
    )
    res = engine.run(SIMPLE, VARS)
    assert res.attempts == 2
    second = fake_prompts(exe)[1]
    assert "[이전 응답의 문제" in second
    assert "JSON 객체를 찾지 못했다" in second
    assert second.index("## [출력 형식]") < second.index("[이전 응답의 문제")


def test_repair_after_schema_violation(
    engine_for: Callable[..., tuple[ContextEngine, Path]],
) -> None:
    engine, exe = engine_for(
        [{"response": '{"score": 99, "memo": "x"}'}, {"response": json.dumps(GOOD)}]
    )
    res = engine.run(SIMPLE, VARS)
    assert res.attempts == 2
    second = fake_prompts(exe)[1]
    assert "필드 `title` 가 빠졌다" in second
    assert "필드 `memo` 는 스키마에 없다" in second
    assert '"score": 99' in second  # 이전 응답 일부를 보여 준다


def test_repair_after_task_validator(
    engine_for: Callable[..., tuple[ContextEngine, Path]],
) -> None:
    def no_zero(v: _Out) -> list[str]:
        return ["score가 0이면 근거를 함께 적어야 한다"] if v.score == 0 else []

    task = dataclasses.replace(SIMPLE, validator=no_zero)
    engine, exe = engine_for(
        [{"response": '{"title": "t", "score": 0}'}, {"response": json.dumps(GOOD)}]
    )
    res = engine.run(task, VARS)
    assert res.attempts == 2
    assert "score가 0이면" in fake_prompts(exe)[1]


def test_structured_output_preferred(
    engine_for: Callable[..., tuple[ContextEngine, Path]],
) -> None:
    engine, _ = engine_for([{"response": "설명 문장만", "structured": GOOD}])
    assert engine.run(SIMPLE, VARS).value.score == 7


def test_gives_up_after_max_repairs(
    engine_for: Callable[..., tuple[ContextEngine, Path]], tmp_path: Path
) -> None:
    engine, exe = engine_for([{"response": "계속 틀림"}])
    with pytest.raises(TaskFailed) as ei:
        engine.run(SIMPLE, VARS)
    assert ei.value.attempts == 3
    assert len(fake_prompts(exe)) == 3
    log = CallLog(tmp_path / "calls.jsonl").read()
    assert [r["attempt"] for r in log] == [1, 2, 3]
    assert not any(r["ok"] for r in log)


def test_call_log_success(
    engine_for: Callable[..., tuple[ContextEngine, Path]], tmp_path: Path
) -> None:
    engine, _ = engine_for([{"response": "x"}, {"response": json.dumps(GOOD)}])
    engine.run(SIMPLE, VARS)
    log = CallLog(tmp_path / "calls.jsonl").read()
    assert [r["ok"] for r in log] == [False, True]
    assert log[1]["agent"] == "agy" and log[1]["task"] == "simple"
    assert log[1]["template_versions"]


def test_rules_injected_only_when_on_and_applicable(
    engine_for: Callable[..., tuple[ContextEngine, Path]], tmp_path: Path
) -> None:
    store = RuleStore(tmp_path / "rules")
    store.save(_rule("채점-켬", "부분점수는 0.5점 단위로.", ["grade"]))
    store.save(_rule("채점-끔", "이 규칙은 꺼져 있다.", ["grade"], enabled=False))
    store.save(_rule("첨삭", "첨삭은 존댓말로.", ["feedback"]))
    engine, exe = engine_for([{"response": json.dumps(GOOD)}], rules=store)
    res = engine.run(SIMPLE, VARS)
    prompt = fake_prompts(exe)[0]
    assert "부분점수는 0.5점 단위로." in prompt
    assert "이 규칙은 꺼져 있다." not in prompt
    assert "첨삭은 존댓말로." not in prompt
    assert len(res.rule_ids) == 1

    internal = dataclasses.replace(SIMPLE, rule_target=None)
    engine.run(internal, VARS)
    assert "부분점수는 0.5점" not in fake_prompts(exe)[1]


def test_agent_error_propagates(app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path) -> None:
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(make_fake("quota"))
    cfg.agents["claude"].enabled = False
    cfg.agents["codex"].enabled = False
    engine = ContextEngine(AgentClient(cfg, app_paths), calllog=CallLog(tmp_path / "c.jsonl"))
    with pytest.raises(AgentError):
        engine.run(SIMPLE, VARS)
    assert CallLog(tmp_path / "c.jsonl").read()[0]["error_code"] == "agent_quota"


# ---------------------------------------------------------------- 규칙 정제


def test_refine_rule_repairs_unknown_conflict_id(
    engine_for: Callable[..., tuple[ContextEngine, Path]], tmp_path: Path
) -> None:
    store = RuleStore(tmp_path / "rules")
    existing = store.save(_rule("짧게", "첨삭은 두 문장 이내.", ["feedback"]))
    bad = {
        "title": "자세한 첨삭",
        "text": "첨삭은 풀이 단계마다 자세히 쓸 것.",
        "applies_to": ["feedback"],
        "conflicts": [{"rule_id": "rule-없는것", "reason": "길이 충돌"}],
    }
    good = {**bad, "conflicts": [{"rule_id": existing.id, "reason": "길이 충돌"}]}
    engine, exe = engine_for([{"response": json.dumps(bad)}, {"response": json.dumps(good)}])
    res = refine_rule(engine, "첨삭 좀 자세하게 해줘", store)
    assert res.attempts == 2
    assert isinstance(res.value, RefinedRule)
    first = fake_prompts(exe)[0]
    assert existing.id in first and "첨삭 좀 자세하게 해줘" in first
    assert "## [반드시 지킬 전역 규칙" not in first, "기존 규칙은 자료로만, 지시로 넣지 않음"
    rule = res.value.to_rule("첨삭 좀 자세하게 해줘")
    assert rule.request == "첨삭 좀 자세하게 해줘" and rule.enabled


# ---------------------------------------------------------------- 명령줄


def test_cli_rules_flow(
    app_paths: AppPaths,
    make_fake: MakeFake,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws = create_workspace(tmp_path / "ws")
    refined = {"title": "존댓말 첨삭", "text": "첨삭은 존댓말로 쓸 것.", "applies_to": ["feedback"]}
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(
        make_fake("script", script=[{"response": json.dumps(refined, ensure_ascii=False)}])
    )
    save_config(app_paths, cfg)
    w = ["rules", "--workspace", str(ws.root)]

    assert main([*w, "add", "존댓말로 첨삭해 줘"]) == 0
    assert "저장하지 않았습니다" in capsys.readouterr().out
    assert main([*w, "add", "존댓말로 첨삭해 줘", "--yes"]) == 0
    rule_id = capsys.readouterr().out.strip().splitlines()[-1].split()[-1]

    assert main([*w, "preview", "feedback"]) == 0
    assert "첨삭은 존댓말로 쓸 것." in capsys.readouterr().out
    assert main([*w, "off", rule_id[-6:]]) == 0
    assert main([*w, "preview", "feedback"]) == 0
    assert "(없음)" in capsys.readouterr().out
    assert main([*w, "list"]) == 0
    assert "[OFF]" in capsys.readouterr().out
    assert main([*w, "rm", rule_id]) == 0
    assert main([*w, "list"]) == 0
    assert "등록된 규칙이 없습니다" in capsys.readouterr().out


def test_cli_rules_without_workspace(
    app_paths: AppPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["rules", "list"]) == 1
    assert "작업공간" in capsys.readouterr().err


# ---------------------------------------------------------------- CLI용 스키마


def test_agent_schema_inlines_refs_and_drops_openapi_keywords() -> None:
    from dawoni.context.schema import agent_schema
    from dawoni.models import ItemContent

    for model in (RefinedRule, ItemContent):
        text = json.dumps(agent_schema(model))
        assert "$ref" not in text and "$defs" not in text
        assert '"discriminator"' not in text


def test_inline_refs_keeps_sibling_description_and_detects_cycles() -> None:
    from dawoni.context.schema import SchemaError, inline_refs

    s = {
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/A", "description": "설명"}},
        "$defs": {"A": {"type": "string", "title": "A"}},
    }
    assert inline_refs(s)["properties"]["a"] == {"type": "string", "description": "설명"}
    cyclic = {"$ref": "#/$defs/N", "$defs": {"N": {"properties": {"n": {"$ref": "#/$defs/N"}}}}}
    with pytest.raises(SchemaError, match="순환"):
        inline_refs(cyclic)
