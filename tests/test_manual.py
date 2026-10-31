"""직접 입력 (AI 없음): DB화에 실패한 문항 채우기, DB화한 문항을 원본과 비교해 고치기."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pdfgen import make_exam_pdf

from dawoni.bank import BankError, manual
from dawoni.bank import ingest as bi
from dawoni.bank.store import BankStore
from dawoni.ingest.sources import get_source
from dawoni.models.answer_key import KeyPart
from dawoni.models.bank import BankItem, OriginalSolution
from dawoni.models.content import Block, BogiBlock, FigureBlock, ParagraphBlock
from dawoni.models.source import SourceChunk
from dawoni.paths import AppPaths
from dawoni.services import Services, open_services
from dawoni.workspace import create_workspace

Ctx = tuple[Services, str]


@pytest.fixture
def ctx(app_paths: AppPaths, tmp_path: Path) -> Ctx:
    """시험지 PDF 2쪽을 한 구간으로 — DB화는 하지 않음 (모든 문항이 '실패한 문항')."""
    ws = create_workspace(tmp_path / "ws")
    svc = open_services(ws.root, paths=app_paths)
    pdf = make_exam_pdf(tmp_path / "시험.pdf")
    src = bi.add_source(svc, str(pdf), "problem_bank", title="OO 문제집", unit_trees=["phy1"])
    bi.add_chunks(svc, src, [SourceChunk(index=1, pages=[1, 2], unit_hint="phy1.1.1")])
    return svc, src.source_id


def _runs(*parts: tuple[str, str]) -> list[dict[str, str]]:
    return [{"t": t, "v": v} for t, v in parts]


def _bogi(text: str) -> BogiBlock:
    items = [{"label": "ㄱ", "runs": _runs(("text", text))}]
    return BogiBlock.model_validate({"type": "bogi", "items": items})


def test_text_and_stem_round_trip() -> None:
    runs = _runs(("text", "저항 "), ("math", "4\\,\\Omega"), ("text", "에"))
    stem: list[Block] = [
        ParagraphBlock.model_validate({"type": "paragraph", "runs": runs}),
        FigureBlock(type="figure", figure_id="F2"),
        _bogi("전류는 같다."),
    ]
    text, locked = manual.text_from_stem(stem)
    assert text == "저항 \\(4\\,\\Omega\\)에\n[그림 F2]\n[블록 3]"
    assert len(locked) == 1 and locked[0].startswith("[블록 3]") and "전류는 같다" in locked[0]
    assert manual.stem_from_text(text, stem) == stem
    with pytest.raises(BankError, match="원래 문항에 없는 블록"):
        manual.stem_from_text("[블록 9]", stem)


def test_chunk_drafts_from_layout_without_ai(ctx: Ctx) -> None:
    svc, sid = ctx
    m = manual.chunk_manual(svc, sid, 1)
    assert [q.number for q in m.questions] == ["1", "2", "3", "4", "5", "서술형 1"]
    essay = m.questions[-1]
    assert essay.draft.points == 6 and essay.draft.item_type == "short_answer"
    assert essay.draft.text.endswith("[그림 F1]")
    assert [f.id for f in essay.figures] == ["F1"] and essay.images
    assert (bi.work_dir(svc, sid, 1) / essay.figures[0].path).is_file()
    q4 = m.questions[3]
    assert q4.draft.item_type == "multiple_choice" and len(q4.draft.choices) == 5


def test_save_new_figure_question_with_parts(ctx: Ctx) -> None:
    """회로 그림이 곧 문항: 본문 한 줄 + 소문항마다 그림 하나 (실측: 기초물리학 51쪽)."""
    svc, sid = ctx
    manual.chunk_manual(svc, sid, 1)  # 화면을 열면 레이아웃 크롭(F1)이 먼저 생긴다
    crop = manual.crop_new(svc, sid, 1, "서술형 1", 2, (0.1, 0.1, 0.5, 0.3))
    assert crop.id == "F2"
    draft = manual.ManualDraft(
        number="서술형 1",
        item_type="short_answer",
        points=6,
        text="A, B 사이 합성저항을 구하시오.\n(1)\n[그림 F1]\n(2)\n[그림 F2]",
        parts=[
            manual.DraftPart(label="(1)", points=3, answer="\\(4\\,\\Omega\\)"),
            manual.DraftPart(label="(2)", points=3),
        ],
    )
    it = manual.save_new(svc, sid, 1, draft, by="김강사")
    src = get_source(svc.workspace, sid)
    assert it.id == bi.item_id_for(src, src.chunks[0], "서술형 1")
    figs = it.question.item.figures
    assert [f.id for f in figs] == ["F1", "F2"]
    assert all(f.orig and (BankStore(svc.workspace).dir / f.orig).is_file() for f in figs)
    assert [p.label for p in it.question.parts] == ["(1)", "(2)"]
    assert it.original and [p.answer for p in it.original.parts] == ["\\(4\\,\\Omega\\)"]
    assert it.reviewed and it.reviewed_by == "김강사" and it.note == manual.MANUAL_NOTE
    c = src.chunks[0]
    assert it.id in c.item_ids and c.status == "failed"
    assert "남은 문항 1, 2, 3, 4, 5" in (c.message or "")
    left = [q.number for q in manual.chunk_manual(svc, sid, 1).questions]
    assert left == ["1", "2", "3", "4", "5"]
    with pytest.raises(BankError, match="직접 입력할 문항 서술형 1번이 없습니다"):
        manual.save_new(svc, sid, 1, draft)


def test_save_new_rejects_bad_draft(ctx: Ctx) -> None:
    svc, sid = ctx

    def draft(**kw: Any) -> manual.ManualDraft:
        return manual.ManualDraft.model_validate({"number": "1", "points": 3, **kw})

    mc = draft(item_type="multiple_choice", text="고르시오", choices=["1", "", "3"])
    with pytest.raises(BankError, match="2번 선지가 비어 있습니다"):
        manual.save_new(svc, sid, 1, mc)
    with pytest.raises(BankError, match="없는 그림"):
        manual.save_new(svc, sid, 1, draft(item_type="short_answer", text="[그림 F9]"))


def test_chunk_done_after_last_question(ctx: Ctx) -> None:
    svc, sid = ctx
    for q in manual.chunk_manual(svc, sid, 1).questions:
        mc = q.draft.item_type == "multiple_choice"
        choices = [str(k) for k in range(1, 6)] if mc else []
        manual.save_new(
            svc, sid, 1, q.draft.model_copy(update={"text": "문항", "choices": choices})
        )
    c = get_source(svc.workspace, sid).chunks[0]
    assert c.status == "ingested" and len(c.item_ids) == 6


def test_key_failed_pages_read_from_old_message() -> None:
    msg = "20문항 추가 · 해설 9, 10쪽 못 읽음 — 다시 DB화하면"
    c = SourceChunk(index=1, pages=[1], message=msg)
    assert manual.chunk_key_failed(c) == [9, 10]
    assert manual.chunk_key_failed(c.model_copy(update={"key_failed_pages": [8]})) == [8]


def _ingested_like(svc: Services, sid: str) -> BankItem:
    """DB화한 문항처럼: <보기> 블록, 원본 해설, 'DB화: 원본과 비교' 메모."""
    q3 = next(q for q in manual.chunk_manual(svc, sid, 1).questions if q.number == "3")
    choices = ["ㄱ", "ㄴ", "ㄷ", "ㄱ, ㄴ", "ㄱ, ㄷ"]
    it = manual.save_new(
        svc, sid, 1, q3.draft.model_copy(update={"text": "고르시오", "choices": choices})
    )
    item = it.question.item.model_copy(update={"stem": [*it.question.item.stem, _bogi("곱은 6")]})
    key = KeyPart(label="", answer="③", solution="ㄱ만 옳다.", credit_policy="none")
    changed = it.model_copy(
        update={
            "question": it.question.model_copy(update={"item": item}),
            "original": OriginalSolution(level="full", parts=[key]),
            "note": "본문: PDF 글자에 없는 한글 40% — … 원본과 비교하세요",
            "reviewed": False,
        }
    )
    return BankStore(svc.workspace).save(changed, base_revision=it.revision)


def test_edit_existing_item_keeps_locked_blocks_and_solution(ctx: Ctx) -> None:
    svc, sid = ctx
    it = _ingested_like(svc, sid)
    m = manual.item_manual(svc, it.id)
    assert m.draft.text == "고르시오\n[블록 2]" and m.locked and m.draft.answer == "③"
    crop = manual.crop_item(svc, it.id, 1, (0.1, 0.1, 0.4, 0.2))
    text = f"옳은 것만 고르시오.\n[블록 2]\n[그림 {crop.id}]"
    d = m.draft.model_copy(update={"text": text, "answer": "④"})
    saved = manual.save_existing(svc, it.id, d, base_revision=it.revision, by="박강사")
    stem = saved.question.item.stem
    assert isinstance(stem[1], BogiBlock) and isinstance(stem[2], FigureBlock)
    assert [f.id for f in saved.question.item.figures] == [crop.id]
    assert saved.original and saved.original.parts[0].answer == "④"
    assert saved.original.parts[0].solution == "ㄱ만 옳다.", "원본 해설 본문은 그대로"
    assert saved.reviewed and saved.note is None, "원본과 비교 메모는 지움"


def test_existing_item_number_cannot_change(ctx: Ctx) -> None:
    svc, sid = ctx
    it = _ingested_like(svc, sid)
    d = manual.item_manual(svc, it.id).draft.model_copy(update={"number": "99"})
    assert manual.save_existing(svc, it.id, d, base_revision=it.revision).question.number == "3"


def test_page_image_cached(ctx: Ctx) -> None:
    svc, sid = ctx
    p = manual.page_image(svc, sid, 2)
    assert p.is_file() and manual.page_image(svc, sid, 2) == p
    with pytest.raises(BankError, match="9쪽은 없습니다"):
        manual.page_image(svc, sid, 9)
