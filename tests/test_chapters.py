"""교재 챕터: 쪽 분류·챕터 제안(목차 있음/없음)·권말 해설 나누기·단원 맞추기, 챕터 DB화."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from conftest import MakeFake
from pdfgen import _t  # pyright: ignore[reportPrivateUsage]
from tests_support import fake_prompts

from dawoni.bank import BankError
from dawoni.bank import ingest as bi
from dawoni.bank.chapters import classify_pages, match_unit, norm, suggest_chapters
from dawoni.bank.curriculum import default_trees
from dawoni.bank.scope import compose_scope
from dawoni.bank.store import BankStore
from dawoni.config import AppConfig, save_config
from dawoni.grading.store import ScopeStore
from dawoni.ingest.exam import IngestResult
from dawoni.models.grading import ExamQuestion
from dawoni.models.source import SourceChunk
from dawoni.paths import AppPaths
from dawoni.services import open_services
from dawoni.workspace import create_workspace

TREES = [t for t in default_trees() if t.id == "phy1"]

CH1 = "1. 힘과 운동"
CH2 = "2. 에너지와 열"


def _concept(doc: pymupdf.Document, title: str, body: str) -> None:
    p = doc.new_page(width=595, height=842)
    _t(p, 50, 80, title, 16, width=480)
    for k in range(4):
        _t(
            p,
            50,
            130 + 30 * k,
            f"{body} 설명 {k + 1}: 물체에 작용하는 알짜힘과 가속도의 관계를 정리한다.",
            10,
            width=480,
        )


def _problems(doc: pymupdf.Document, subject: str) -> None:
    p = doc.new_page(width=595, height=842)
    _t(p, 50, 60, "확인 문제", 12, width=480)
    for k in range(3):
        _t(p, 50, 120 + 200 * k, f"{k + 1}. {subject}에 대한 설명으로 옳은 것은?", 11, width=480)
        _t(p, 60, 150 + 200 * k, "① 가   ② 나   ③ 다   ④ 라   ⑤ 마", 10, width=460)


def textbook(path: Path, *, toc: bool) -> Path:
    """1챕터 개념 | 1챕터 문제 | 2챕터 개념 | 2챕터 문제 | 정답과 해설(챕터 제목별) 2쪽."""
    doc = pymupdf.open()
    cover = doc.new_page(width=595, height=842)
    _t(cover, 200, 400, "물리학 I", 20, width=300)
    _concept(doc, CH1, "힘")
    _problems(doc, "힘")
    _concept(doc, CH2, "에너지")
    _problems(doc, "에너지")
    for title in (CH1, CH2):
        k = doc.new_page(width=595, height=842)
        _t(k, 50, 60, f"{title} 정답과 해설", 14, width=480)
        _t(k, 50, 100, "1 ③ 2 ② 3 ⑤ — 알짜힘이 0이면 속도가 일정하다.", 10, width=480)
    if toc:
        doc.set_toc([[1, "Ⅰ. 힘과 운동", 2], [2, "개념", 2], [2, "문제", 3],  # pyright: ignore[reportUnknownMemberType]
                     [1, "Ⅱ. 에너지와 열", 4], [1, "정답과 해설", 6]])  # fmt: skip
    doc.save(str(path))  # pyright: ignore[reportUnknownMemberType]
    return path


def test_norm_and_unit_match() -> None:
    assert (
        norm("Ⅰ. 힘과 운동") == norm("1단원 힘과 운동") == norm("Chapter 1 힘과운동") == "힘과운동"
    )
    assert match_unit(CH1, TREES) == "phy1.1.1"
    assert match_unit("02 에너지와 열", TREES) == "phy1.1.2"
    assert match_unit("특별 부록", TREES) is None, "확실하지 않으면 비워 둔다"


def test_classify_pages(tmp_path: Path) -> None:
    kinds = [p.kind for p in classify_pages(textbook(tmp_path / "b.pdf", toc=False))]
    assert kinds == ["other", "concept", "problem", "concept", "problem", "key", "key"]


@pytest.mark.parametrize("toc", [False, True])
def test_suggest_chapters(tmp_path: Path, toc: bool) -> None:
    s = suggest_chapters(textbook(tmp_path / "b.pdf", toc=toc), TREES)
    c1, c2 = s.chapters
    assert (c1.concept_pages, c1.pages, c1.key_pages) == ([2], [3], [6]), (
        "제목으로 나눔 (여유 쪽 없음)"
    )
    assert (c2.concept_pages, c2.pages, c2.key_pages) == ([4], [5], [7]), (
        "권말 해설을 챕터 제목으로 나눔"
    )
    assert (c1.unit_hint, c2.unit_hint) == ("phy1.1.1", "phy1.1.2")
    if toc:
        assert c1.label == "Ⅰ. 힘과 운동" and "목차" in s.notes[0]
        assert len(s.chapters) == 2, "해설만 있는 목차 항목은 챕터가 아님"
    else:
        assert c1.label == CH1 and "찾지 못해" in s.notes[0]


# ---------------------------------------------------------------- 챕터 DB화

CONCEPTS: dict[str, Any] = {
    "concepts": [
        {
            "name": "알짜힘",
            "summary": "물체에 작용하는 알짜힘과 가속도의 관계를 정리한다",
            "page": 2,
        },
    ],
    "skills": [],
}


def _mc(n: str) -> ExamQuestion:
    return ExamQuestion.model_validate({
        "number": n, "points": 2,
        "item": {"item_type": "multiple_choice",
                 "stem": [{"type": "paragraph", "runs": [{"t": "text", "v": "힘에 대한 설명으로 옳은 것은?"}]}],
                 "choices": [{"label": i, "runs": [{"t": "text", "v": x}]} for i, x in enumerate("가나다라마", 1)]},
    })  # fmt: skip


def test_ingest_chapters(
    app_paths: AppPaths, tmp_path: Path, make_fake: MakeFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dawoni.ingest.answer_key import KeyIngest

    def fake_extract(*_: Any, **__: Any) -> IngestResult:
        return IngestResult(questions=[_mc("1"), _mc("2")], figure_files={})

    def fake_key(*_: Any, **__: Any) -> KeyIngest:
        return KeyIngest(keys=[])

    monkeypatch.setattr("dawoni.ingest.exam.extract_exam", fake_extract)
    monkeypatch.setattr("dawoni.ingest.answer_key.extract_answer_key", fake_key)
    exe = make_fake("script", script=[{"response": json.dumps(CONCEPTS, ensure_ascii=False)}])
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.agents["claude"].enabled = False
    cfg.agents["codex"].enabled = False
    save_config(app_paths, cfg)
    ws = create_workspace(tmp_path / "ws")
    svc = open_services(ws.root, paths=app_paths)
    pdf = textbook(tmp_path / "book.pdf", toc=False)
    src = bi.add_source(svc, str(pdf), "textbook", title="OO 물리학 I", unit_trees=["phy1"])
    s = suggest_chapters(bi.source_file(ws, src), TREES)
    src = bi.set_chunks(svc, src, s.chapters[:1])

    (r,) = bi.ingest_chapters(svc, src.source_id, by="김선생", machine="pc-a")
    assert len(fake_prompts(exe)) == 1, "개념 정리 한 번, 문제는 풀지 않음"
    assert r.scope is not None and r.scope.unit_ids == ["phy1.1.1"]
    assert r.scope.title == "OO 물리학 I · 1. 힘과 운동" and r.scope.concepts[0].name == "알짜힘"
    assert r.problems is not None and len(r.problems.items) == 2
    assert all(it.solution is None and it.unit_ids == ["phy1.1.1"] for it in r.problems.items)
    c = bi.get_source(ws, src.source_id).chunks[0]
    assert (c.concept_status, c.status, c.scope_id) == ("ingested", "ingested", r.scope.id)

    again = bi.ingest_chapters(svc, src.source_id, machine="pc-a")
    assert again[0].scope is None and again[0].problems is None, "이어 하기: 끝난 부분은 건너뜀"

    # 챕터 개념은 '단원 체크로 범위 만들기'에 그대로 쓰인다
    composed = compose_scope(ScopeStore(ws).all(), TREES, ["phy1.1.1"], title="1단원 평가")
    assert [x.name for x in composed.scope.concepts][:1] == ["알짜힘"]
    assert len(BankStore(ws).ids()) == 2

    # 표 고치기: 이름·단원은 되고, DB화한 쪽은 못 바꾼다
    renamed = c.model_copy(update={"label": "1단원"})
    assert (
        bi.set_chunks(svc, bi.get_source(ws, src.source_id), [renamed]).chunks[0].label == "1단원"
    )
    moved = c.model_copy(update={"pages": [3, 4]})
    with pytest.raises(BankError, match="쪽을 바꿀 수 없습니다"):
        bi.set_chunks(svc, bi.get_source(ws, src.source_id), [moved])
    with pytest.raises(BankError, match="지울 수 없습니다"):
        bi.set_chunks(svc, bi.get_source(ws, src.source_id), [SourceChunk(index=9, pages=[5])])
    with pytest.raises(ValueError, match="문제 쪽이나 개념 쪽"):
        SourceChunk(index=1)


def test_chapter_api(app_paths: AppPaths, tmp_path: Path) -> None:
    from test_server import _client, _wait  # pyright: ignore[reportPrivateUsage]

    c = _client(app_paths)
    c.post("/api/workspace", json={"path": str(tmp_path / "ws"), "create": True})
    pdf = textbook(tmp_path / "book.pdf", toc=True)
    with pdf.open("rb") as f:
        r = c.post("/api/bank/sources", files={"file": ("book.pdf", f)},
                   data={"kind": "textbook", "title": "OO 물리학 I", "unit_trees": "phy1",
                         "level": "고등학교 물리학 I"})  # fmt: skip
    assert r.status_code == 200, r.text
    src = r.json()["source"]
    assert src["level"] == "고등학교 물리학 I" and src["chunks"] == []
    sid = src["source_id"]

    j = _wait(c, c.post(f"/api/bank/sources/{sid}/chapters/suggest").json())
    assert j["status"] == "succeeded", j
    chapters = j["result"]["chapters"]
    assert [x["unit_hint"] for x in chapters] == ["phy1.1.1", "phy1.1.2"]
    assert [p["kind"] for p in j["result"]["pages"]][:3] == ["other", "concept", "problem"]

    saved = c.put(f"/api/bank/sources/{sid}/chunks", json={"chunks": chapters}).json()
    assert [(x["index"], x["concept_pages"], x["pages"]) for x in saved["chunks"]] == [
        (1, [2], [3]),
        (2, [4], [5]),
    ]
    bad = c.put(f"/api/bank/sources/{sid}/chunks", json={"chunks": [{"index": 1, "label": "x"}]})
    assert bad.status_code == 422 and "문제 쪽이나 개념 쪽" in bad.text


def continuous_book(path: Path) -> Path:
    """글 목차 + 번호가 책 전체에서 이어짐(1~6) + 권말 해설(머리말은 첫 쪽에만, 번호만 한 줄)."""
    doc = pymupdf.open()
    toc = doc.new_page(width=595, height=842)
    _t(toc, 50, 100, "1. 힘과 운동 ........................................ 2", 11, width=480)
    _t(toc, 50, 130, "2. 에너지와 열 ...................................... 4", 11, width=480)
    _concept(doc, CH1, "힘")
    p = doc.new_page(width=595, height=842)
    for k, n in enumerate([1, 2, 3]):
        _t(p, 50, 120 + 200 * k, f"{n}. 힘에 대한 설명으로 옳은 것은?", 11, width=480)
    _concept(doc, CH2, "에너지")
    p = doc.new_page(width=595, height=842)
    _t(p, 50, 120, "4. 에너지에 대한 설명으로 옳은 것은?", 11, width=480)  # 한 쪽에 한 문항
    p = doc.new_page(width=595, height=842)
    for k, n in enumerate([5, 6]):
        _t(p, 50, 120 + 200 * k, f"{n}. 일과 에너지 문항", 11, width=480)
    k1 = doc.new_page(width=595, height=842)
    _t(k1, 50, 60, "정답 및 해설", 14, width=480)
    for k, n in enumerate([1, 2, 3, 4]):
        _t(k1, 50, 110 + 80 * k, str(n), 11, width=40)
        _t(k1, 60, 135 + 80 * k, "알짜힘이 0이면 속도가 일정하다. 1", 10, width=400)
    k2 = doc.new_page(width=595, height=842)  # 머리말 없음 (짝수 쪽에만 있는 책)
    for k, n in enumerate([5, 6]):
        _t(k2, 50, 110 + 80 * k, str(n), 11, width=40)
        _t(k2, 60, 135 + 80 * k, "역학적 에너지가 보존된다.", 10, width=400)
    _t(k2, 50, 400, "끝", 11, width=40)
    doc.save(str(path))  # pyright: ignore[reportUnknownMemberType]
    return path


def test_text_toc_and_key_by_numbers(tmp_path: Path) -> None:
    s = suggest_chapters(continuous_book(tmp_path / "c.pdf"), TREES)
    assert "글 목차" in s.notes[0] and "문항 번호로" in s.notes[1]
    c1, c2 = s.chapters
    assert (c1.label, c1.concept_pages, c1.pages) == ("1. 힘과 운동", [2], [3])
    assert (c2.concept_pages, c2.pages) == ([4], [5, 6]), "한 문항뿐인 쪽부터 문제"
    assert c1.key_pages == [7, 8] and c2.key_pages == [7, 8], (
        "머리말 없는 해설 쪽도 해설, 번호로 나눔"
    )
    assert s.pages[0].kind == "other", "목차 쪽의 '1. … 2.'는 문항이 아님"


def test_separate_key_pdf_is_appended(app_paths: AppPaths, tmp_path: Path) -> None:
    """기출 해설 PDF가 따로 있으면 문제 PDF 뒤에 붙여 한 자료로 — 붙인 쪽이 그대로 해설 쪽."""
    ws = create_workspace(tmp_path / "ws")
    svc = open_services(ws.root, paths=app_paths)
    exam = tmp_path / "exam.pdf"
    doc = pymupdf.open()
    for n in (1, 2):
        _t(doc.new_page(width=595, height=842), 50, 100, f"{n}. 물리 문항 {n}", 11, width=480)
    doc.save(str(exam))  # pyright: ignore[reportUnknownMemberType]
    key = tmp_path / "key.pdf"
    kd = pymupdf.open()
    _t(kd.new_page(width=595, height=842), 50, 100, "정답및해설 1. ③ 2. ⑤", 11, width=480)
    kd.save(str(key))  # pyright: ignore[reportUnknownMemberType]

    src = bi.add_source(svc, str(exam), "mock_exam", title="2011 수능 물리I", unit_trees=["phy1"],
                        key_path=str(key))  # fmt: skip
    assert src.pages == 3 and src.key_attached_from == 3
    c = bi.suggest_chunk(svc, src)
    assert (c.pages, c.key_pages) == ([1, 2], [3])
