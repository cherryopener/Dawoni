# pyright: reportUnknownMemberType=false
"""0단계 해설 다듬기 (dawoni/keyprep.py, 2026-11-18 사용자 요청): 시험지·해설 HWPX → AI가 부분
점수를 촘촘하게 → 강사가 고르고 고침 → 다듬은 HWPX. AI 없이(가짜 CLI)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from conftest import MakeFake
from test_document import (
    _heq,  # pyright: ignore[reportPrivateUsage]
    _hnote,  # pyright: ignore[reportPrivateUsage]
    _hnum,  # pyright: ignore[reportPrivateUsage]
    _hp,  # pyright: ignore[reportPrivateUsage]
    _ht,  # pyright: ignore[reportPrivateUsage]
    _write_hwpx,  # pyright: ignore[reportPrivateUsage]
)
from test_readings import _services  # pyright: ignore[reportPrivateUsage]
from tests_support import fake_prompts

from dawoni import keyprep
from dawoni.config import load_config, save_config
from dawoni.export.key_hwpx import KeyBlock, write_key_hwpx
from dawoni.ingest.document import analyze_document
from dawoni.ingest.workflow import IngestRefused
from dawoni.models.keyprep import KeyRefineOutput, PrepPart
from dawoni.paths import AppPaths


def _exam_hwpx(path: Path) -> Path:
    """학원 시험지 모양 (11월 월례평가처럼): 1번 미주 = 답·해설 + "[부분점수 없음]", 2번의 부분 점수
    기준은 3번 미주의 번호 앞(앞 미주의 끝으로 보이는 곳), 3번은 해설이 없다."""
    q1_note = _hnote(
        _hp(_hnum(1), _ht(" 1) 장력은 "), _heq("W over 2")),
        _hp(_ht("[부분점수 없음]")),
        _hp(_ht("2) 장력은 W")),
        _hp(_ht("[부분점수 없음]")),
    )
    q2_note = _hnote(
        _hp(_hnum(2), _ht(" A: ②, B: ③")),
        _hp(_ht("파란 빛은 더 많이 굴절해 초점거리가 짧아진다.")),
    )
    q3_note = _hnote(
        _hp(_ht("[부분점수]")),
        _hp(_ht("기호 정답 2점, 이유 서술 6점")),
        _hp(_hnum(3), _ht(" (빈 미주)")),
    )
    paras = [
        _hp(_ht("1. 장력을 구하라."), q1_note),
        _hp(_ht("1) 정사면체일 때 [단답형 4점]")),
        _hp(_ht("2) 실 하나를 줄였을 때 [단답형 5점]")),
        _hp(_ht("2. 이유를 서술하라. [서술형 8점]"), q2_note),
        _hp(_ht("3. 전하의 부호를 쓰라. [단답형 3점]"), q3_note),
    ]
    return _write_hwpx(path, paras)


def _q1_out() -> dict[str, Any]:
    return {
        "checks": [{"label": "1)", "answer": r"\(W/2\)", "agrees": "same"}],
        "replace": [2, 4],
        "parts": [
            {"label": "1)", "points": 4, "no_partial": True, "lines": [
                {"kind": "accept", "text": r"같은 값의 다른 표기도 인정 (\(0.5W\))",
                 "source": "ai", "why": "같은 값"},
            ]},
            {"label": "2)", "points": 5, "no_partial": True, "lines": []},
        ],
    }  # fmt: skip


def _q2_out() -> dict[str, Any]:
    return {
        "checks": [{"label": "", "answer": "A ②, B ③", "agrees": "same"}],
        "replace": [3, 4],
        "parts": [
            {"label": "", "points": 8, "no_partial": False, "lines": [
                {"kind": "item", "text": "기호 정답", "points": 2, "source": "key"},
                {"kind": "item", "text": "파란 빛이 더 많이 굴절해 초점거리가 짧아짐", "points": 3,
                 "source": "ai", "why": "이유 서술 6점을 나눔"},
                {"kind": "level", "text": "굴절만 말하고 초점거리를 말하지 않음", "points": 1,
                 "source": "ai", "why": "단계"},
                {"kind": "item", "text": "배율 변화로 A·B를 설명", "points": 3, "source": "ai",
                 "why": "나눔"},
                {"kind": "accept", "text": "작도로 정확히 설명해도 인정", "source": "ask",
                 "why": "해설지에 없음", "question": "작도만으로 설명해도 인정할까요?"},
                {"kind": "note", "text": "각배율로 설명해도 인정", "source": "ask",
                 "why": "해설지에 없음", "question": "각배율 설명도 인정할까요?"},
            ]},
        ],
    }  # fmt: skip


def _q3_out() -> dict[str, Any]:
    return {
        "checks": [{"label": "", "answer": "(-)", "agrees": "unsure"}],
        "explanation": "1. 유도로 오른쪽 노즐은 (-)를 띤다.\n따라서 답은 (-)이다.",
        "replace": [],
        "parts": [
            {"label": "", "points": 3, "answer": "(-)", "no_partial": True, "lines": [
                {"kind": "item", "text": "(-)라고 씀", "points": 3, "source": "ai", "why": "답"},
            ]},
        ],
    }  # fmt: skip


@pytest.fixture
def prep(app_paths: AppPaths, tmp_path: Path) -> tuple[Any, keyprep.PrepStore]:
    svc = _services(app_paths, tmp_path)
    return svc, keyprep.create(svc, _exam_hwpx(tmp_path / "월례.hwpx"), level="물리")


def test_create_reads_questions_and_their_key_paragraphs(prep: Any) -> None:
    _, store = prep
    p = store.load()
    assert [(q.number, q.points) for q in p.questions] == [("1", 9), ("2", 8), ("3", 3)]
    q1, q2, q3 = p.questions
    assert q1.key_paragraphs == [
        r"1) 장력은 \(\frac{W}{2}\)",
        "[부분점수 없음]",
        "2) 장력은 W",
        "[부분점수 없음]",
    ]
    assert q2.key_paragraphs[-2:] == ["[부분점수]", "기호 정답 2점, 이유 서술 6점"], (
        "3번 미주의 번호 앞 글은 2번 해설"
    )
    assert q3.key_paragraphs == ["(빈 미주)"]
    assert keyprep.labels_of(q1) == ["1)", "2)"] and keyprep.labels_of(q2) == []
    material = keyprep.material(q2)
    assert "¶1 A: ②, B: ③" in material and "[강사 해설지] (문단 번호 ¶)" in material


def test_create_refuses_other_formats(app_paths: AppPaths, tmp_path: Path) -> None:
    svc = _services(app_paths, tmp_path)
    pdf = tmp_path / "시험.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(IngestRefused, match="HWP·HWPX"):
        keyprep.create(svc, pdf, level="물리")


def test_valid_refinements_pass(prep: Any) -> None:
    _, store = prep
    p = store.load()
    for q, out in zip(p.questions, (_q1_out(), _q2_out(), _q3_out()), strict=True):
        o = KeyRefineOutput.model_validate(out)
        assert keyprep.refine_problems(o, q, keyprep.labels_of(q)) == [], q.number


def test_no_partial_part_gets_no_partial_credit(prep: Any) -> None:
    """해설지의 "부분점수 없음"은 지킨다 — 낮은 단계·감점·만점이 아닌 요소, 점수를 나누자는
    질문도."""
    _, store = prep
    q1 = store.load().question("1")
    out = _q1_out()
    out["parts"][0]["lines"] += [
        {"kind": "item", "text": "식을 세움", "points": 2, "source": "ai", "why": "과정"},
        {"kind": "level", "text": "부호만 틀림", "points": 1, "source": "ask", "why": "x",
         "question": "부호만 틀리면 1점?"},
    ]  # fmt: skip
    problems = keyprep.refine_problems(KeyRefineOutput.model_validate(out), q1, ["1)", "2)"])
    joined = " / ".join(problems)
    assert "소문항 1): 부분점수 없음 — 채점 요소는 만점(4점) 하나만" in joined
    assert "점수를 나누는 질문도 넣지 않는다" in joined


def test_point_sums_levels_asks_and_replace_are_checked(prep: Any) -> None:
    _, store = prep
    p = store.load()
    q2 = p.question("2")
    out = _q2_out()
    lines = out["parts"][0]["lines"]
    lines[0]["points"] = 1  # 합 7점
    lines[2]["points"] = 4  # 낮은 단계가 앞 요소(3점)보다 큼
    lines[4]["kind"] = "item"  # 질문이 채점 요소
    lines[4]["points"] = 1
    out["replace"] = [99]
    problems = keyprep.refine_problems(KeyRefineOutput.model_validate(out), q2, [])
    joined = " / ".join(problems)
    assert "채점 요소 점수 합 7점 ≠ 배점 8점" in joined
    assert "낮은 단계 4점은 앞 채점 요소(3점)보다 작아야 함" in joined
    assert "질문(ask) 줄은 채점 요소가 될 수 없음" in joined
    assert "¶99는 없는 문단" in joined
    q1 = p.question("1")
    wrong = _q1_out()
    wrong["parts"] = list(reversed(wrong["parts"]))
    assert any("그대로 차례로" in x for x in keyprep.refine_problems(
        KeyRefineOutput.model_validate(wrong), q1, ["1)", "2)"]))  # fmt: skip


def test_without_key_the_ai_must_write_the_explanation(prep: Any) -> None:
    _, store = prep
    q3 = store.load().question("3").model_copy(update={"key_paragraphs": []})
    out = _q3_out()
    out["explanation"] = None
    out["parts"][0]["lines"][0]["source"] = "key"
    problems = keyprep.refine_problems(KeyRefineOutput.model_validate(out), q3, [])
    assert any("explanation에 직접 푼 해설" in x for x in problems)
    assert any("key 줄이 있을 수 없음" in x for x in problems)


def test_key_line_not_in_the_key_becomes_a_recommendation(prep: Any) -> None:
    _, store = prep
    q2 = store.load().question("2")
    out = _q2_out()
    out["parts"][0]["lines"][1]["source"] = "key"  # 해설 문장을 조금 고쳐 옮김
    out["parts"][0]["lines"][3]["source"] = "key"  # 해설지에 없는 문장
    q = keyprep.to_question(q2, KeyRefineOutput.model_validate(out))
    lines = q.parts[0].lines
    assert [ln.source for ln in lines[:4]] == ["key", "key", "ai", "ai"]
    assert lines[3].why and "고쳐 씀" in lines[3].why
    assert [ln.id for ln in lines] == ["L1", "L2", "L3", "L4", "L5", "L6"]
    assert q.pending == 2 and not lines[4].included, "질문은 답하기 전에는 넣지 않는다"


def _refined(store: keyprep.PrepStore) -> None:
    p = store.load()
    for q, out in zip(p.questions, (_q1_out(), _q2_out(), _q3_out()), strict=True):
        store.put_question(keyprep.to_question(q, KeyRefineOutput.model_validate(out)))


def test_export_rewrites_only_the_criteria_and_rereads(prep: Any, tmp_path: Path) -> None:
    svc, store = prep
    _refined(store)
    q2 = store.load().question("2")
    parts = [p.model_copy(deep=True) for p in q2.parts]
    parts[0].lines[4].decision = "in"  # 작도 질문: 넣기
    parts[0].lines[5].decision = "out"  # 각배율 질문: 빼기
    added = {"id": "new-1", "kind": "deduct", "points": None, "text": "기호와 모순되면 0점"}
    parts[0].lines.append(parts[0].lines[0].model_copy(update=added))
    edited = keyprep.edit_question(store, "2", parts, explanation=None, explanation_in=True,
                                   by="김선생")  # fmt: skip
    assert edited.parts[0].lines[-1].id == "T1" and edited.parts[0].lines[-1].source == "teacher"
    assert keyprep.question_problems(edited) == []

    res = keyprep.export(svc, store)
    assert res.path.name == "월례_다듬음.hwpx" and res.path.parent.name == "해설 다듬기"
    assert res.warnings == [] and res.skipped == [] and res.pending == 0
    keys = analyze_document(res.path, tmp_path / "re").keys
    k1, k2, k3 = keys["1"].text, keys["2"].text, keys["3"].text
    assert k1.startswith(r"1) 장력은 \(\frac{W}{2}\)") and "2) 장력은 W" in k1
    assert k1.count("[부분점수 없음]") == 2, "원래 두 줄은 지우고 소문항 머리에 다시"
    assert "1) 4점 [부분점수 없음]" in k1 and r"※ 같은 값의 다른 표기도 인정 (\(0.5 W\))" in k1
    assert "[부분점수]\n◆ 2점: 기호 정답\n◆ 3점: 파란 빛이" in k2
    assert "\n└ 1점: 굴절만 말하고" in k2 and "※ 작도로 정확히 설명해도 인정" in k2
    assert "각배율" not in k2 and "(감점) 기호와 모순되면 0점" in k2
    assert "기호 정답 2점, 이유 서술 6점" not in k2, "원래 기준 문단은 새 기준으로 바뀐다"
    assert k2.startswith("A: ②, B: ③\n파란 빛은"), "답·풀이 문단은 그대로"
    assert "[해설]\n· 1. 유도로" not in k3 and "[해설]\n1. 유도로 오른쪽" in k3, "미주 안"
    assert k3.endswith("[부분점수 없음]\n◆ 3점: (-)라고 씀")

    with zipfile.ZipFile(res.path) as z, zipfile.ZipFile(store.source) as orig:
        assert z.namelist()[0] == "mimetype"
        assert z.read("Contents/header.xml") == orig.read("Contents/header.xml")
        section = z.read("Contents/section0.xml").decode()
    assert section.startswith('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>')
    assert "<hp:script>0.5 W</hp:script>" in section, "식은 한글 수식으로"


def test_question_without_key_goes_to_a_key_section(tmp_path: Path) -> None:
    """미주가 없는 문항은 문서 끝 "정답 및 해설" 아래 "N."으로 — 다시 읽으면 그 문항 해설."""
    paras = [_hp(_ht("1. 첫 문항 [3점]")), _hp(_ht("2. 둘째 [4점]"))]
    src = _write_hwpx(tmp_path / "s.hwpx", paras)
    lines = ["[정답] 5", "[해설]", "2. 이런 줄은 다른 문항으로 읽히면 안 된다", "[부분점수 없음]"]
    res = write_key_hwpx(src, tmp_path / "out.hwpx", [KeyBlock("2", [], lines, marker="[정답] 5")])
    assert res.warnings == []
    a = analyze_document(res.path, tmp_path / "re")
    assert [r.label for r in a.layout.regions] == ["1", "2"]
    assert a.keys["2"].text == (
        "2. [정답] 5\n[해설]\n· 2. 이런 줄은 다른 문항으로 읽히면 안 된다\n[부분점수 없음]"
    )
    assert "1" not in a.keys


def test_edit_keeps_ids_and_drops_empty_lines(prep: Any) -> None:
    _, store = prep
    _refined(store)
    q1 = store.load().question("1")
    parts = [p.model_copy(deep=True) for p in q1.parts]
    parts[0].lines[0].text = "  "
    parts[1] = PrepPart(label="2)", points=5, no_partial=False, lines=[])
    q = keyprep.edit_question(store, "1", parts, explanation=None, explanation_in=True, by=None)
    assert q.parts[0].lines == [] and q.edited_by == "강사"
    assert keyprep.question_problems(q) == ["소문항 2): 채점 요소가 없음"]


def test_refine_with_the_ai(app_paths: AppPaths, tmp_path: Path, make_fake: MakeFake) -> None:
    """문항마다 한 번 (strong 등급 역할). 검증에 걸리면 수리 요청, 한 문항이 실패해도 나머지는
    끝까지."""
    bad = _q2_out()
    bad["parts"][0]["lines"][0]["points"] = 1
    responses = [_q1_out(), bad, _q2_out(), *(["?"] * 3)]
    exe = make_fake("script", script=[
        {"response": r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)}
        for r in responses
    ])  # fmt: skip
    svc = _services(app_paths, tmp_path, exe)
    svc.config.parallel_questions = 1
    store = keyprep.create(svc, _exam_hwpx(tmp_path / "월례.hwpx"), level="물리")
    p = keyprep.refine(svc, store)
    assert p.state == "done"
    assert [q.status for q in p.questions] == ["done", "done", "failed"]
    assert p.question("3").error
    prompts = fake_prompts(exe)
    assert "부분 점수 기준을 촘촘하게" in prompts[0] and "Role: key refiner" in prompts[0]
    assert "답이 그림인 소문항" in prompts[0] and "지어내지 않는다" in prompts[0]
    assert "소문항: 1), 2)" in prompts[0] and "¶2 [부분점수 없음]" in prompts[0]
    assert "채점 요소 점수 합 7점 ≠ 배점 8점" in prompts[2], "수리 요청"
    res = keyprep.export(svc, store)
    assert res.skipped == ["3"]


def test_stale_running_draft_shows_as_stopped(prep: Any) -> None:
    """앱을 닫아 작업이 사라졌는데 '다듬는 중'이면 멈춘 것으로 보인다 (저장은 그대로)."""
    _, store = prep
    p = store.load()
    assert p.state == "running"
    shown = keyprep.stale(p)
    assert shown.state == "failed" and {q.status for q in shown.questions} == {"failed"}
    assert store.load().state == "running"


def test_screen_api_round_trip(app_paths: AppPaths, tmp_path: Path, make_fake: MakeFake) -> None:
    """화면: 올리기 → 다듬기 작업 → 보기·고치기 → HWPX 받기 → 1단계로 넘길 파일 → 지우기."""
    from test_readings import _client_for  # pyright: ignore[reportPrivateUsage]
    from test_server import _wait  # pyright: ignore[reportPrivateUsage]

    exe = make_fake("script", script=[
        {"response": json.dumps(r, ensure_ascii=False)} for r in (_q1_out(), _q2_out(), _q3_out())
    ])  # fmt: skip
    svc = _services(app_paths, tmp_path, exe)
    cfg = load_config(app_paths)
    cfg.parallel_questions = 1  # 가짜 CLI는 부른 차례대로 답한다
    save_config(app_paths, cfg)
    c = _client_for(app_paths, svc)
    with (_exam_hwpx(tmp_path / "월례.hwpx")).open("rb") as f:
        job = c.post("/api/keyprep", files={"file": ("월례.hwpx", f)}, data={"level": "물리"})
    assert job.status_code == 200, job.text
    done = _wait(c, job.json())
    assert done["status"] == "succeeded", done
    pid = job.json()["params"]["prep"]
    assert done["result"]["done"] == 3 and done["result"]["pending"] == 2

    got = c.get(f"/api/keyprep/{pid}").json()
    q2 = next(q for q in got["questions"] if q["number"] == "2")
    assert q2["pending"] == 2
    assert q2["problems"] == ["답하지 않은 질문 2개 — 넣기를 고르기 전에는 넣지 않습니다"]
    for ln in q2["parts"][0]["lines"]:
        if ln["source"] == "ask":
            ln["decision"] = "in"
    put = c.put(f"/api/keyprep/{pid}/questions/2", json={"parts": q2["parts"], "by": "김선생"})
    assert put.status_code == 200 and put.json()["pending"] == 0 and put.json()["problems"] == []
    assert c.get(f"/api/keyprep/{pid}/hwpx").status_code == 404, "받기 전"

    out = c.post(f"/api/keyprep/{pid}/export").json()
    assert out["path"] == "exports/해설 다듬기/월례_다듬음.hwpx" and out["pending"] == 0
    hwpx = c.get(f"/api/keyprep/{pid}/hwpx")
    assert hwpx.status_code == 200 and hwpx.content[:2] == b"PK"
    assert c.get("/api/keyprep").json()[0]["exported"] == out["path"]
    assert c.get(f"/api/keyprep/{pid}/file", params={"path": "../draft.json"}).status_code == 400
    assert c.delete(f"/api/keyprep/{pid}").json() == {"ok": True}
    assert c.get(f"/api/keyprep/{pid}").status_code == 404
    assert (svc.workspace.root / out["path"]).is_file(), "받은 HWPX는 남는다"


def _fill_out() -> dict[str, Any]:
    """3번 답이 그래프인데 해설 그림에 값이 없어 AI가 정할 수 없다 → 강사가 적기."""
    return {
        "checks": [{"label": "", "answer": "(그래프: 감소하는 직선)", "agrees": "unsure"}],
        "explanation": "질량이 일정하게 줄어든다.",
        "replace": [],
        "parts": [
            {"label": "", "points": 3, "answer": "(그래프: 감소하는 직선)", "no_partial": False,
             "lines": [
                 {"kind": "item", "text": "개형: 시간에 따라 일정하게 줄어드는 직선", "points": 2,
                  "source": "ai", "why": "개형"},
                 {"kind": "item", "text": "\\(t\\)절편 □ s를 표시", "points": 1, "source": "fill",
                  "why": "그림에 눈금이 없음", "question": "t절편 값을 적어 주세요"},
             ]},
        ],
    }  # fmt: skip


def test_fill_lines_need_a_blank_and_what_to_write(prep: Any) -> None:
    """AI가 그림에서도 풀이로도 정할 수 없는 값은 지어내지 않고 강사에게 적게 한다 (2026-11-18)."""
    _, store = prep
    q3 = store.load().question("3")
    ok = KeyRefineOutput.model_validate(_fill_out())
    assert keyprep.refine_problems(ok, q3, []) == [], "fill 줄은 채점 요소로 배점 합에 든다"
    bad = _fill_out()
    lines = bad["parts"][0]["lines"]
    lines[1]["text"] = "t절편을 표시"
    lines[1]["question"] = None
    lines[0]["text"] = "개형 □"
    joined = " / ".join(keyprep.refine_problems(KeyRefineOutput.model_validate(bad), q3, []))
    assert "fill 줄의 text에는 강사가 채울 빈칸 □" in joined
    assert "fill 줄에는 무엇을 적을지 question" in joined
    assert "빈칸 □은 fill 줄에만" in joined


def test_export_waits_until_the_teacher_fills_the_blank(prep: Any, tmp_path: Path) -> None:
    svc, store = prep
    _refined(store)
    q3 = store.load().question("3")
    store.put_question(keyprep.to_question(q3, KeyRefineOutput.model_validate(_fill_out())))
    q3 = store.load().question("3")
    assert q3.blanks == 1 and q3.parts[0].lines[1].source == "fill"
    assert q3.parts[0].lines[1].question == "t절편 값을 적어 주세요"
    assert any("강사가 적을 곳 1개" in x for x in keyprep.question_problems(q3))
    with pytest.raises(ValueError, match=r"강사가 적을 곳이 남았습니다 \(3번\)"):
        keyprep.export(svc, store)

    parts = [p.model_copy(deep=True) for p in q3.parts]
    parts[0].lines[1].text = r"\(t\)절편 40 s를 표시"
    q3 = keyprep.edit_question(store, "3", parts, explanation=q3.explanation,
                               explanation_in=True, by="김선생")  # fmt: skip
    assert q3.blanks == 0 and q3.parts[0].lines[1].source == "fill", "누가 적었는지 남는다"
    res = keyprep.export(svc, store)
    k3 = analyze_document(res.path, tmp_path / "re").keys["3"].text
    assert r"◆ 1점: \(t\)절편 40 s를 표시" in k3 and "□" not in k3


def test_unmarked_points_follow_stage_one(app_paths: AppPaths, tmp_path: Path) -> None:
    """배점 표시가 없는 문항은 1단계 시험 읽기처럼 ⌊100 ÷ 문항 수⌋를 소문항에 고르게 — 다듬은 기준의
    배점이 1단계와 맞게 (실측 2026-11-18 현대물리 8번: AI가 5+5점을 지어냄)."""
    svc = _services(app_paths, tmp_path)
    paras = [
        _hp(_ht("1. 펄스 그래프를 그리시오.")),
        _hp(_ht("(1) y-x 그래프")),
        _hp(_ht("(2) y-t 그래프")),
        _hp(_ht("2. 둘째 문항")),
        _hp(_ht("3. 셋째 문항")),
    ]
    store = keyprep.create(svc, _write_hwpx(tmp_path / "n.hwpx", paras), level="물리")
    q1 = store.load().question("1")
    assert (q1.points, q1.points_guessed) == (33, True)
    out = {"checks": [], "explanation": "풀이", "replace": [], "parts": [
        {"label": lb, "points": pts, "no_partial": True, "answer": "(그래프)", "lines": [
            {"kind": "item", "text": "개형", "points": pts, "source": "ai", "why": "그림"}]}
        for lb, pts in (("(1)", 17), ("(2)", 16))
    ]}  # fmt: skip
    problems = keyprep.refine_problems(KeyRefineOutput.model_validate(out), q1, ["(1)", "(2)"])
    assert problems == [
        "배점 표시가 없는 문항 — 소문항 배점은 (1) 16점, (2) 17점 (1단계와 같은 규칙)"
    ]
