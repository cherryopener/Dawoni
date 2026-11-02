"""M12 강의 요약: 주소 해석, 구간 나누기, 코드 검사, 요약 흐름(가짜 자막·가짜 AI), Word, API."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from conftest import MakeFake
from test_grading import FIX
from test_server import _client, _wait  # pyright: ignore[reportPrivateUsage]
from tests_support import fake_prompts

from dawoni.config import AppConfig, save_config
from dawoni.export.docx.check import check_docx
from dawoni.export.docx.fonts import FontSpec
from dawoni.export.docx.lecture import body_size
from dawoni.fsio import read_json
from dawoni.grading.store import ScopeStore
from dawoni.lecture import workflow as lw
from dawoni.lecture.summarize import (
    LINES_PER_PAGE,
    Chunk,
    check_chunk,
    check_summary,
    chunks,
    estimate_lines,
    stamp,
    summarize,
)
from dawoni.lecture.youtube import LectureError, canonical_url, video_id_of
from dawoni.models.lecture import (
    CaptionLine,
    ChunkNotes,
    LectureSource,
    LectureSummary,
    SummaryDraft,
)
from dawoni.models.scope import ScopeProfile
from dawoni.paths import AppPaths
from dawoni.server.state import AppState
from dawoni.services import Services, open_services
from dawoni.workspace import create_workspace

LFIX = Path(__file__).parent / "fixtures" / "lecture"
VID = "abcdefghijk"
SCOPE_ID = "scope-quadratic-minmax"


def source(minutes: int = 25) -> LectureSource:
    lines = [
        CaptionLine(start=float(t), text=f"이차함수 설명 {t}초") for t in range(0, minutes * 60, 5)
    ]
    return LectureSource(
        video_id=VID, url=canonical_url(VID), title="이차함수의 최대 최소", channel="예시 채널",
        language="ko", generated=True, duration_s=lines[-1].start + 5, lines=lines,
    )  # fmt: skip


def notes(start: float, ids: list[str], beyond: list[str] | None = None) -> dict[str, Any]:
    return {
        "topics": [
            {"title": f"주제 {start:g}", "start_s": start, "points": ["표준형으로 바꾼다"],
             "formulas": ["y=a(x-p)^2+q"], "concept_ids": ids}
        ],
        "beyond_scope": beyond or [],
    }  # fmt: skip


def draft(
    n_sections: int = 3, *, ids: list[str] | None = None, bullet: str = "핵심"
) -> dict[str, Any]:
    return {
        "title": "이차함수의 최대·최소",
        "overview": "표준형으로 꼭짓점을 찾는다.",
        "sections": [
            {"heading": f"부분 {k}", "start_s": 60.0 * k, "bullets": [bullet],
             "formulas": ["x=-\\frac{b}{2a}"], "concept_ids": ids or ["C1"]}
            for k in range(n_sections)
        ],
        "key_formulas": ["y=a(x-p)^2+q"],
        "tips": ["끝값도 비교한다"],
        "review_questions": ["최솟값은?"],
    }  # fmt: skip


def scope() -> ScopeProfile:
    return ScopeProfile.model_validate(read_json(FIX / "scope.json"))


# ---------------------------------------------------------------- 주소·구간


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VID}&t=30s",
        f"https://youtu.be/{VID}?si=xyz",
        f"https://www.youtube.com/shorts/{VID}",
        f"https://www.youtube.com/embed/{VID}",
        f"https://m.youtube.com/watch?feature=share&v={VID}",
        VID,
    ],
)
def test_video_id_of(url: str) -> None:
    assert video_id_of(url) == VID


def test_video_id_rejects_other_sites() -> None:
    with pytest.raises(LectureError):
        video_id_of("https://example.com/watch")


def test_stamp() -> None:
    assert stamp(5) == "0:05" and stamp(605.7) == "10:05" and stamp(3725) == "1:02:05"


def test_chunks_split_by_time_and_chars() -> None:
    cs = chunks(source(25), span_s=600)
    assert [round(c.start) for c in cs] == [0, 600, 1200]
    assert cs[-1].end == pytest.approx(1500)
    assert sum(len(c.lines) for c in cs) == len(source(25).lines), "자막 줄을 빠뜨리지 않음"
    assert "[0:00]" in cs[0].text() and "[0:30]" in cs[0].text(), "30초마다 시각 표시"
    by_chars = chunks(source(25), span_s=600, max_chars=500)
    assert len(by_chars) > 3 and all(sum(len(x.text) for x in c.lines) <= 500 for c in by_chars)


# ---------------------------------------------------------------- 코드 검사


def test_check_chunk() -> None:
    c = Chunk(600, 1200, source().lines[120:240])
    allowed = scope().allowed_ids()
    assert check_chunk(ChunkNotes.model_validate(notes(700, ["C1"])), c, allowed) == []
    probs = check_chunk(ChunkNotes.model_validate(notes(100, ["C9"])), c, allowed)
    assert any("구간(10:00~20:00) 밖" in p for p in probs)
    assert any("['C9']" in p and "beyond_scope" in p for p in probs)
    bad_math = notes(700, ["C1"])
    bad_math["topics"][0]["formulas"] = ["\\frac{1}{"]
    assert check_chunk(ChunkNotes.model_validate(bad_math), c, allowed)


def test_check_summary_budget_and_ids() -> None:
    src = source()
    ok = SummaryDraft.model_validate(draft())
    assert check_summary(ok, src, {"C1"}, 1) == []
    probs = check_summary(SummaryDraft.model_validate(draft(ids=["C3"])), src, {"C1"}, 1)
    assert any("노트에 없는 범위 ID ['C3']" in p for p in probs), (
        "강의에서 안 다룬 개념을 다뤘다고 못 함"
    )
    long = SummaryDraft.model_validate(draft(12, bullet="가" * 200))
    assert estimate_lines(long) > LINES_PER_PAGE[1]
    assert any("A4 1장" in p and "줄일 것" in p for p in check_summary(long, src, {"C1"}, 1))
    late = draft()
    late["sections"][0]["start_s"] = 99999
    assert any(
        "영상 길이 밖" in p
        for p in check_summary(SummaryDraft.model_validate(late), src, {"C1"}, 1)
    )


# ---------------------------------------------------------------- 흐름 (가짜 자막·가짜 AI)


@pytest.fixture
def env(
    app_paths: AppPaths, tmp_path: Path, make_fake: MakeFake
) -> Callable[[list[dict[str, Any]]], tuple[Services, Path]]:
    def make(script: list[dict[str, Any]]) -> tuple[Services, Path]:
        exe = make_fake(
            "script", script=[{"response": json.dumps(r, ensure_ascii=False)} for r in script]
        )
        cfg = AppConfig()
        cfg.agents["agy"].executable = str(exe)
        cfg.agents["claude"].enabled = False
        cfg.agents["codex"].enabled = False
        cfg.parallel_questions = 1  # 가짜 AI 응답 순서를 고정
        ws = create_workspace(tmp_path / "ws")
        cfg.workspace = ws.root
        save_config(app_paths, cfg)
        ScopeStore(ws).save(scope())
        return open_services(ws.root, paths=app_paths), exe

    return make


def test_summary_flow_with_repair_and_coverage(env: Any) -> None:
    long = draft(12, ids=["C1", "S1"], bullet="가" * 200)
    svc, exe = env([
        notes(10, ["C1", "S1"]),
        notes(1250, ["C2"], beyond=["미분"]),
        notes(2450, ["C9"]), notes(2450, ["C1"], beyond=["미분", " 극값 "]),  # 범위 밖 ID → 수리
        long, draft(ids=["C1", "C2"]),  # 분량 초과 → 수리
    ])  # fmt: skip
    calls = {"n": 0}

    def loader(url: str) -> LectureSource:
        calls["n"] += 1
        return source(50)

    s = lw.run_summary(svc, f"https://youtu.be/{VID}", scope_id=SCOPE_ID, loader=loader)
    assert s.covered == ["C1", "C2", "S1"]
    assert s.missing == ["C3", "C4"], "교재 개념 중 강의 노트에 없는 것 (코드가 계산)"
    assert s.beyond == ["미분", "극값"]
    assert s.caption_generated and s.scope_id == SCOPE_ID and s.duration_s == 3000
    prompts = fake_prompts(exe)
    assert len(prompts) == 6
    assert "[20:00]" in prompts[1] and "C1" in prompts[0], "구간 자막 + 범위"
    assert "['C9']" in prompts[3]
    assert "줄일 것" in prompts[5]
    store = lw.LectureStore(svc.workspace)
    assert [x.id for x in store.all()] == [s.id]
    assert len(read_json(svc.workspace.lectures / VID / f"{s.id}.notes.json")) == 3
    # 같은 영상은 자막을 다시 받지 않는다
    assert lw.get_source(svc.workspace, VID, loader=loader).video_id == VID and calls["n"] == 1
    lw.get_source(svc.workspace, VID, loader=loader, refresh=True)
    assert calls["n"] == 2

    res = lw.export_summary(svc.workspace, s, FontSpec())
    assert res.check.ok and res.check.images == 1 and res.check.equations >= 4
    assert res.path.name == "이차함수의_최대·최소_강의요약.docx"
    store.delete(s.id)
    assert store.all() == []


def test_summary_without_scope(env: Any) -> None:
    svc, exe = env(
        [notes(10, []), {**draft(), "sections": [{**draft()["sections"][0], "concept_ids": []}]}]
    )
    s = lw.run_summary(svc, VID, loader=lambda _: source(5))
    assert s.scope_id is None and s.covered == [] and s.missing == []
    assert "완전제곱식으로 표준형 만들기" not in fake_prompts(exe)[0], "범위 층 없음"


# ---------------------------------------------------------------- Word


def test_lecture_docx_fixture(tmp_path: Path) -> None:
    from dawoni.export.docx.lecture import build_lecture_summary

    s = LectureSummary.model_validate(read_json(LFIX / "summary.json"))
    assert body_size(s) == 9.5
    w = build_lecture_summary(s, FontSpec(), scope())
    out = w.save(tmp_path / "lecture.docx")
    rep = check_docx(out)
    assert rep.ok and rep.images == 1 and rep.latex_fallbacks == 0
    import zipfile

    with zipfile.ZipFile(out) as z:
        doc = z.read("word/document.xml").decode()
        rels = z.read("word/_rels/document.xml.rels").decode()
    assert doc.count("<w:hyperlink") == len(s.sections) + 1, "영상 제목 + 부분마다 시각 링크"
    assert f"{s.url}&amp;t=190s" in rels
    assert 'w:num="2"' in doc, "2단"
    assert "강의에 없는 교재 개념" not in doc and "범위 밖 내용: 미분을 이용한 극값" in doc
    big = s.model_copy(update={"sections": s.sections * 3})
    assert body_size(big) < 9.5, "넘치면 글자를 줄인다"


# ---------------------------------------------------------------- API


def test_lecture_api(app_paths: AppPaths, env: Any) -> None:
    svc, _ = env([notes(10, ["C1"]), draft()])
    c = _client(app_paths)
    state = cast(AppState, c.app.state.dawoni)  # pyright: ignore[reportAttributeAccessIssue, reportFunctionMemberAccess, reportUnknownMemberType]
    state.lecture_loader = lambda _url: source(5)
    r = c.post("/api/lectures/summarize", json={"url": "https://example.com/x"})
    assert r.status_code == 400 and "유튜브" in r.json()["detail"]
    assert (
        c.post("/api/lectures/summarize", json={"url": VID, "scope_id": "nope"}).status_code == 404
    )
    j = _wait(
        c,
        c.post(
            "/api/lectures/summarize", json={"url": VID, "scope_id": SCOPE_ID, "pages": 2}
        ).json(),
    )
    assert j["status"] == "succeeded", j
    sid = j["result"]["id"]
    lst = c.get("/api/lectures").json()
    assert [x["id"] for x in lst] == [sid] and lst[0]["pages"] == 2
    assert c.get(f"/api/lectures/{sid}").json()["covered"] == ["C1"]
    ex = c.post(f"/api/lectures/{sid}/export").json()
    assert ex["ok"] and ex["path"].startswith("exports/")
    assert (svc.workspace.root / ex["path"]).exists()
    assert c.delete(f"/api/lectures/{sid}").json() == {"ok": True}
    assert c.get(f"/api/lectures/{sid}").status_code == 404


def test_rerun_reuses_finished_chunk_notes(env: Any) -> None:
    """긴 강의가 마지막 구간에서 실패해도, 다시 하면 끝난 구간은 AI를 부르지 않는다."""
    garbage = {"nope": 1}  # 스키마 위반 3번 → 실패
    svc, exe = env([notes(10, ["C1"]), garbage, garbage, garbage, notes(1250, ["C2"]), draft()])
    from dawoni.parallel import ParallelError

    with pytest.raises(ParallelError, match="lecture_chunk_2"):
        lw.run_summary(svc, VID, scope_id=SCOPE_ID, loader=lambda _url: source(25))
    n = len(fake_prompts(exe))
    s = lw.run_summary(svc, VID, scope_id=SCOPE_ID, loader=lambda _url: source(25))
    assert len(fake_prompts(exe)) == n + 2, "1구간은 저장된 노트, 2구간 + 요약만 호출"
    assert s.covered == ["C1", "C2"]


def test_chunks_are_summarized_in_parallel() -> None:
    """구간 노트는 서로 기다리지 않는다 (동시에 돈 구간 수를 잰다). 요약은 구간이 다 끝난 뒤."""
    import threading
    import time
    from types import SimpleNamespace

    from dawoni.models.lecture import ChunkNotes

    lock = threading.Lock()
    now = peak = 0
    order: list[str] = []

    class Engine:
        def run(self, task: Any, inp: Any) -> Any:
            nonlocal now, peak
            if task.name == "lecture_summary":
                order.append("summary")
                return SimpleNamespace(value=SummaryDraft.model_validate(draft()), agent="agy",
                                       model=None)  # fmt: skip
            with lock:
                now += 1
                peak = max(peak, now)
            time.sleep(0.05)
            hms = inp.variables["span"].split("~")[0].split(":")
            start = sum(int(x) * 60**i for i, x in enumerate(reversed(hms))) + 10.0
            with lock:
                now -= 1
                order.append(task.name)
            return SimpleNamespace(value=ChunkNotes.model_validate(notes(start, ["C1"])))

    run = summarize(Engine(), source(65), scope=scope(), workers=3)  # pyright: ignore[reportArgumentType]
    assert peak == 3, "65분 강의 = 20분 구간 4개, 동시에 3개"
    assert order[-1] == "summary" and len(run.notes) == 4
    starts = [n.topics[0].start_s for n in run.notes]
    assert starts == [10, 1210, 2410, 3610], "결과는 구간 순서대로"


def test_overescaped_latex_is_fixed() -> None:
    """agy 실측: JSON 이스케이프를 한 번 더 해 `0 \\\\le x` → Word 수식 변환 실패가 3번 반복."""
    d = draft()
    d["key_formulas"] = ["0 \\\\le x \\\\le 7", "x = -\\\\frac{b}{2a}"]
    d["sections"][0]["bullets"] = ["범위 \\\\(0 \\\\le x\\\\)에서 비교"]
    s = SummaryDraft.model_validate(d)
    assert s.key_formulas == ["0 \\le x \\le 7", "x = -\\frac{b}{2a}"]
    assert s.sections[0].bullets == ["범위 \\(0 \\le x\\)에서 비교"]
    assert check_summary(s, source(), {"C1"}, 1) == []


def test_no_emoji_in_docx_text() -> None:
    from dawoni.export.docx.lecture import no_emoji

    assert (
        no_emoji("[고1 수학] 이차함수 완전 정복💥 개념+유형 ✨총정리!")
        == "[고1 수학] 이차함수 완전 정복 개념+유형 총정리!"
    )
    assert no_emoji("①·②·△·○·※ 최대·최소") == "①·②·△·○·※ 최대·최소", (
        "맑은 고딕에 있는 기호는 그대로"
    )
