"""PDF DB화: 레이아웃(결정적), 시험지 구조화 검증, 교재 → 범위 프로필, 명령줄."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from pdfgen import make_exam_pdf, make_textbook_pdf
from tests_support import fake_prompts

from dawoni.agents import AgentClient
from dawoni.cli import main
from dawoni.cli_ingest import parse_pages
from dawoni.config import AppConfig, save_config
from dawoni.context import ContextEngine
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.ingest.exam import PageExtraction, check_page, extract_exam, to_item
from dawoni.ingest.grounding import hangul_outside
from dawoni.ingest.layout import analyze
from dawoni.ingest.textbook import (
    ChapterExtraction,
    build_scope,
    check_extraction,
    chunks,
    page_texts,
)
from dawoni.paths import AppPaths
from dawoni.workspace import create_workspace

MakeFake = Callable[..., Path]
FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def exam_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_exam_pdf(tmp_path_factory.mktemp("pdf") / "exam.pdf")


@pytest.fixture(scope="module")
def textbook_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_textbook_pdf(tmp_path_factory.mktemp("pdf") / "textbook.pdf")


# ---------------------------------------------------------------- 레이아웃 (AI 없음)


def test_layout_regions_and_figures(exam_pdf: Path) -> None:
    layout = analyze(exam_pdf)
    assert not layout.scanned and layout.warnings == []
    assert [r.label for r in layout.regions] == ["1", "2", "3", "4", "5", "서술형 1"]
    fig_counts = {r.label: len(r.figures) for r in layout.regions}
    assert fig_counts == {"1": 0, "2": 1, "3": 0, "4": 5, "5": 1, "서술형 1": 1}


def test_header_banner_is_not_a_figure(exam_pdf: Path) -> None:
    layout = analyze(exam_pdf)
    page1 = layout.pages[0]
    assert page1.header_y > 70
    assert all(f.rect.y0 > page1.header_y for f in page1.figures)
    assert not any("모의평가" in line.text for r in layout.regions for line in r.lines)


def test_bogi_box_is_text_box_not_figure(exam_pdf: Path) -> None:
    q3 = analyze(exam_pdf).region("3")
    assert q3.figures == [] and len(q3.textboxes) == 1
    assert "ㄷ. 판별식의 값은 음수이다." in q3.text


def test_choice_figures_paired_by_marker_not_order(exam_pdf: Path) -> None:
    q4 = analyze(exam_pdf).region("4")
    assert [f.near_choice for f in q4.figures] == [1, 2, 3, 4, 5]
    assert [f.id for f in q4.figures] == ["F1", "F2", "F3", "F4", "F5"]


def test_question_continues_across_column_and_page(exam_pdf: Path) -> None:
    q5 = analyze(exam_pdf).region("5")
    assert q5.pages == [1, 2]
    assert "(단, 점 P는 제1사분면 위에 있다.)" in q5.text
    assert q5.figures[0].page == 2, "다음 쪽 위의 그림도 5번 문항"


def test_points_and_image(exam_pdf: Path) -> None:
    layout = analyze(exam_pdf)
    assert layout.region("2").points == 3
    essay = layout.region("서술형 1")
    assert essay.points == 6 and essay.figures[0].kind == "image"


def test_scanned_pdf_detected(tmp_path: Path) -> None:
    pdf = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(50, 50, 500, 700), fill=(0.9, 0.9, 0.9))  # pyright: ignore[reportUnknownMemberType]
    doc.save(str(pdf))  # pyright: ignore[reportUnknownMemberType]
    assert analyze(pdf).scanned


def test_out_of_sequence_number_is_not_anchor(tmp_path: Path) -> None:
    pdf = tmp_path / "seq.pdf"
    doc = pymupdf.open()
    p = doc.new_page()
    for y, s in [
        (100, "1. 첫 문항입니다."),
        (130, "15. 본문 속 번호"),
        (200, "2. 둘째 문항입니다."),
    ]:
        p.insert_text((50, y), s, fontname="korea", fontsize=10)  # pyright: ignore[reportUnknownMemberType]
    doc.save(str(pdf))  # pyright: ignore[reportUnknownMemberType]
    layout = analyze(pdf)
    assert [r.label for r in layout.regions] == ["1", "2"]
    assert "15. 본문 속 번호" in layout.region("1").text


def test_number_chain_not_starting_at_one(tmp_path: Path) -> None:
    """문제집 구간은 31번부터 — 앞의 개념 설명 속 "1.~4."가 아니라 가장 길게 이어지는 번호를 문항으로.
    세 자리 번호(132.)도 읽고, 소수("1.5 m")는 번호가 아니다."""
    from dawoni.ingest.layout import number_chain

    pdf = tmp_path / "chunk.pdf"
    doc = pymupdf.open()
    p = doc.new_page()
    lines = [(80, "1. 개념 설명 하나"), (100, "2. 개념 설명 둘"), (120, "1.5 m 떨어진 곳")]
    lines += [(160 + 40 * k, f"{n}. 문항 {n}") for k, n in enumerate([98, 99, 100, 101, 102])]
    for y, t in lines:
        p.insert_text((50, y), t, fontname="korea", fontsize=10)  # pyright: ignore[reportUnknownMemberType]
    doc.save(str(pdf))  # pyright: ignore[reportUnknownMemberType]
    assert [r.label for r in analyze(pdf).regions] == ["98", "99", "100", "101", "102"]
    # 길이가 같으면 읽는 순서상 가까운 앞 번호: 1, (본문 속 2), 진짜 2, 3 → 진짜 2
    assert number_chain([(0, 1), (1, 2), (2, 2), (3, 3)]) == {0, 2, 3}


def test_repeated_point_fragments_do_not_hide_question_number(tmp_path: Path) -> None:
    """실제 시험지: 쪽마다 맨 위 문항의 "[총 10점]"이 조각("[", "10", "점", "]")으로 같은
    자리에 반복돼 머리말로 잡혔고, 그 위의 "2." 줄까지 머리말로 지워졌다."""
    pdf = tmp_path / "frag.pdf"
    doc = pymupdf.open()
    for n in (1, 2, 3):
        p = doc.new_page(width=595, height=842)
        p.insert_text((70, 50), "2교시                 과학 영역", fontname="korea", fontsize=14)  # pyright: ignore[reportUnknownMemberType]
        p.insert_text((50, 80), f"{n}. 다음 물음에 답하라. 총", fontname="korea", fontsize=10)  # pyright: ignore[reportUnknownMemberType]
        for x, s in [(160, "["), (185, "10"), (200, "점"), (212, "]")]:
            p.insert_text((x, 84), s, fontname="korea", fontsize=10)  # pyright: ignore[reportUnknownMemberType]
        p.insert_text((50, 200), f"({n}) 소문항 본문", fontname="korea", fontsize=10)  # pyright: ignore[reportUnknownMemberType]
    doc.save(str(pdf))  # pyright: ignore[reportUnknownMemberType]
    layout = analyze(pdf)
    assert [(r.label, r.points) for r in layout.regions] == [("1", 10), ("2", 10), ("3", 10)]


# ---------------------------------------------------------------- 시험지 구조화 검증


def _p(*runs: dict[str, str]) -> dict[str, Any]:
    return {"type": "paragraph", "runs": list(runs)}


def _t(v: str) -> dict[str, str]:
    return {"t": "text", "v": v}


def _m(v: str) -> dict[str, str]:
    return {"t": "math", "v": v}


def _choices(*vals: str, math: bool = True) -> list[dict[str, Any]]:
    return [{"label": i, "runs": [_m(v) if math else _t(v)]} for i, v in enumerate(vals, 1)]


PAGE1: dict[str, Any] = {
    "questions": [
        {"label": "1", "item_type": "multiple_choice",
         "stem": [_p(_t("이차방정식 "), _m("x^2-5x+6=0"), _t(" 의 두 근의 합은?"))],
         "choices": _choices("1", "3", "5", "6", "7")},
        {"label": "2", "item_type": "short_answer", "points": 3,
         "stem": [_p(_t("그림과 같이 삼각형 ABC와 원이 있다.")), {"type": "figure", "figure_id": "F1"},
                  _p(_t("원의 반지름의 길이를 구하시오."))],
         "figures": [{"id": "F1", "role": "stem", "caption": "삼각형 ABC 안에 원"}]},
        {"label": "3", "item_type": "multiple_choice",
         "stem": [_p(_t("<보기>에서 옳은 것만을 있는 대로 고른 것은?")),
                  {"type": "bogi", "items": [
                      {"label": "ㄱ", "runs": [_t("두 근의 곱은 6이다.")]},
                      {"label": "ㄴ", "runs": [_t("두 근은 모두 양수이다.")]},
                      {"label": "ㄷ", "runs": [_t("판별식의 값은 음수이다.")]}]}],
         "choices": _choices("ㄱ", "ㄴ", "ㄱ, ㄴ", "ㄴ, ㄷ", "ㄱ, ㄴ, ㄷ", math=False)},
        {"label": "4", "item_type": "multiple_choice",
         "stem": [_p(_t("함수 "), _m("y=x"), _t(" 의 그래프로 옳은 것은?"))],
         "choices": [{"label": i, "figure_id": f"F{i}"} for i in range(1, 6)],
         "figures": [{"id": f"F{i}", "role": "choice", "choice_label": i, "caption": f"그래프 {i}"}
                     for i in range(1, 6)]},
        {"label": "5", "item_type": "multiple_choice",
         "stem": [_p(_t("다음은 직선 l 위의 점 P에 대한 설명이다.")),
                  _p(_t("점 P의 좌표를 "), _m("(a, b)"), _t("라 할 때, "), _m("a+b"), _t("의 값은?")),
                  _p(_t("(단, 점 P는 제1사분면 위에 있다.)")), {"type": "figure", "figure_id": "F1"}],
         "choices": _choices("1", "2", "3", "4", "5"),
         "figures": [{"id": "F1", "role": "stem", "caption": "직선 l 위의 점 P"}]},
    ]
}  # fmt: skip
PAGE2: dict[str, Any] = {
    "questions": [
        {"label": "서술형 1", "item_type": "essay", "points": 6,
         "stem": [_p(_t("다음 그림을 보고 물음에 답하시오.")), {"type": "figure", "figure_id": "F1"},
                  _p(_t("(1) 그림의 원의 넓이를 구하는 과정을 서술하시오."))],
         "figures": [{"id": "F1", "role": "stem", "caption": "사각형 안의 원"}]},
    ]
}  # fmt: skip


def test_check_page_accepts_good(exam_pdf: Path) -> None:
    layout = analyze(exam_pdf)
    regions = layout.regions[:5]
    assert check_page(PageExtraction.model_validate(PAGE1), regions, "대수") == []


def test_check_page_problems(exam_pdf: Path) -> None:
    regions = analyze(exam_pdf).regions[:5]

    def problems(mutate: Callable[[dict[str, Any]], None]) -> list[str]:
        d = copy.deepcopy(PAGE1)
        mutate(d)
        return check_page(PageExtraction.model_validate(d), regions, None)

    assert any("['5']이 빠졌다" in p for p in problems(lambda d: d["questions"].pop()))
    assert any(
        "PDF 글자에 없는 한글" in p
        for p in problems(
            lambda d: d["questions"][0]["stem"][0]["runs"].append(_t(" 단, 서로 다른 두 실근이다"))
        )
    )

    def swap(d: dict[str, Any]) -> None:
        d["questions"][3]["figures"][0]["choice_label"] = 2
        d["questions"][3]["figures"][1]["choice_label"] = 1

    assert any("F1는 1번 선지 기호 옆" in p for p in problems(swap))
    assert any(
        "그림 ['F1']의 역할" in p for p in problems(lambda d: d["questions"][1].update(figures=[]))
    )
    assert any(
        "주어지지 않은 그림 ID ['F9']" in p
        for p in problems(lambda d: d["questions"][1]["figures"].append(
            {"id": "F9", "role": "stem", "caption": "x"}))
    )  # fmt: skip
    assert any(
        "Word 수식으로 바꿀 수 없음" in p
        for p in problems(
            lambda d: d["questions"][0]["stem"][0]["runs"].append({"t": "math", "v": "\\frac{1}{"})
        )
    )


def test_to_item_uses_raster_crops_and_captions_as_notes(exam_pdf: Path) -> None:
    layout = analyze(exam_pdf)
    q4 = PageExtraction.model_validate(PAGE1).questions[3]
    item = to_item(q4, layout.region("4"), "대수")
    assert item.has_figure_choices
    assert all(f.mode == "raster" and f.orig == f"figures/4/{f.id}.png" for f in item.figures)
    assert [c.note for c in item.choices] == [f"그래프 {i}" for i in range(1, 6)]


def _engine(app_paths: AppPaths, exe: Path) -> ContextEngine:
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.agents["claude"].enabled = False
    cfg.agents["codex"].enabled = False
    return ContextEngine(AgentClient(cfg, app_paths))


def test_extract_exam_by_page_with_repair(
    app_paths: AppPaths, make_fake: MakeFake, exam_pdf: Path, tmp_path: Path
) -> None:
    bad = copy.deepcopy(PAGE1)
    bad["questions"].pop()  # 5번 누락 → 수리 요청
    exe = make_fake(
        "script",
        script=[{"response": json.dumps(d, ensure_ascii=False)} for d in (bad, PAGE1, PAGE2)],
    )
    result = extract_exam(_engine(app_paths, exe), analyze(exam_pdf), tmp_path / "work")
    assert [q.number for q in result.questions] == ["1", "2", "3", "4", "5", "서술형 1"]
    assert [q.points for q in result.questions] == [4, 3, 4, 4, 4, 6]
    assert sum("배점 표시가 없어" in w for w in result.warnings) == 4  # 1·3·4·5번
    prompts = fake_prompts(exe)
    assert len(prompts) == 3 and "['5']이 빠졌다" in prompts[1]
    assert "레이아웃상 1번 선지 기호 옆" in prompts[0] and "글 상자 1개" in prompts[0]
    assert prompts[0].count("inputs/input_") == 6, "5번은 1·2쪽 두 상자 → 이미지 2장"
    assert set(result.figure_files["4"]) == {"F1", "F2", "F3", "F4", "F5"}
    assert all(p.exists() for p in result.figure_files["4"].values())


def test_extract_exam_partial_keeps_pages_and_retries_only_failed(
    app_paths: AppPaths, make_fake: MakeFake, exam_pdf: Path, tmp_path: Path
) -> None:
    """실측: 17쪽 중 1쪽이 검증을 끝내 통과하지 못해 구간 전체를 잃음 → 나머지는 돌려주고,
    다시 하면 실패한 쪽만 읽는다 (성공한 쪽은 작업 폴더에 남김)."""
    bad = copy.deepcopy(PAGE1)
    bad["questions"].pop()
    first = make_fake("script", script=[{"response": json.dumps(d, ensure_ascii=False)}
                                        for d in (bad, bad, bad, PAGE2)])  # fmt: skip
    work = tmp_path / "work"
    r1 = extract_exam(_engine(app_paths, first), analyze(exam_pdf), work, partial=True)
    assert [q.number for q in r1.questions] == ["서술형 1"]
    assert r1.failed_pages == {1: ["1", "2", "3", "4", "5"]}
    assert any("1쪽(문항 1, 2, 3, 4, 5) 구조화 실패" in w for w in r1.warnings)

    again = make_fake("script", script=[{"response": json.dumps(PAGE1, ensure_ascii=False)}])
    r2 = extract_exam(_engine(app_paths, again), analyze(exam_pdf), work, partial=True)
    assert [q.number for q in r2.questions] == ["1", "2", "3", "4", "5", "서술형 1"]
    assert len(fake_prompts(again)) == 1, "2쪽은 남겨 둔 결과를 쓰고 1쪽만 다시 읽음"
    assert r2.failed_pages == {}


def test_extract_exam_partial_drops_only_failing_question(
    app_paths: AppPaths, make_fake: MakeFake, exam_pdf: Path, tmp_path: Path
) -> None:
    """한 문항이 끝내 검증을 못 넘으면 그 문항만 빼고 같은 쪽을 다시 읽는다 (실측: 그래프 선지 18번
    때문에 같은 쪽 17·19·20번까지 잃음)."""
    bad = copy.deepcopy(PAGE1)
    q3 = next(q for q in bad["questions"] if q["label"] == "3")
    q3["stem"] = [
        {
            "type": "paragraph",
            "runs": [
                {"t": "text", "v": "학생은 반드시 운동량 보존 법칙을 써서 정리해야만 만점이다"}
            ],
        }
    ]
    rest = copy.deepcopy(PAGE1)
    rest["questions"] = [q for q in rest["questions"] if q["label"] != "3"]
    script = [bad, bad, bad, rest, PAGE2]
    exe = make_fake(
        "script", script=[{"response": json.dumps(d, ensure_ascii=False)} for d in script]
    )
    r = extract_exam(_engine(app_paths, exe), analyze(exam_pdf), tmp_path / "work", partial=True)
    assert [q.number for q in r.questions] == ["1", "2", "4", "5", "서술형 1"]
    assert r.failed_pages == {1: ["3"]}, "PDF 글자가 멀쩡한 문항에 지어낸 문장은 여전히 거부"
    assert any("1쪽(문항 3) 구조화 실패" in w for w in r.warnings)
    assert r.notes == {}


def test_extract_exam_accepts_ungrounded_text_beside_image(
    app_paths: AppPaths, make_fake: MakeFake, exam_pdf: Path, tmp_path: Path
) -> None:
    """그림 이미지가 있는 문항: 수리해도 'PDF 글자에 없는 한글'만 남으면 받아들이고 강사 확인
    메모 (실측: 파동 62쪽 '실험 A, B' 상자가 이미지 — Gemini·Claude 모두 3번씩 실패)."""
    boxed = copy.deepcopy(PAGE2)
    boxed["questions"][0]["stem"].insert(
        1, _p(_t("실험 가: 공기 중에서 크라운 유리로 된 프리즘에 백색광을 비춘다."))
    )
    script = [PAGE1, boxed, boxed, boxed]
    exe = make_fake(
        "script", script=[{"response": json.dumps(d, ensure_ascii=False)} for d in script]
    )
    r = extract_exam(_engine(app_paths, exe), analyze(exam_pdf), tmp_path / "work", partial=True)
    assert r.failed_pages == {}
    assert [q.number for q in r.questions][-1] == "서술형 1"
    assert set(r.notes) == {"서술형 1"} and "원본과 비교" in r.notes["서술형 1"]
    assert any("문항 서술형 1: 본문: PDF 글자에 없는 한글" in w for w in r.warnings)


# ---------------------------------------------------------------- 교재 → 범위 프로필


CH: dict[str, Any] = {
    "concepts": [
        {"name": "이차함수의 표준형", "summary": "y = a(x - p)² + q 의 그래프의 꼭짓점은 (p, q)이다.", "page": 1},
        {"name": "완전제곱식으로 고치기", "summary": "y = x² + bx + c 는 완전제곱식으로 고칠 수 있다.", "page": 1},
        {"name": "이차함수의 최댓값과 최솟값", "summary": "a > 0 이면 꼭짓점에서 최솟값을 갖는다.", "page": 1},
    ],
    "skills": [
        {"name": "완전제곱식으로 최솟값 구하기", "description": "표준형으로 고쳐 꼭짓점에서 최솟값",
         "concept_names": ["완전제곱식으로 고치기", "이차함수의 최댓값과 최솟값"], "page": 2},
    ],
    "forbidden_suggestions": [{"name": "미분", "reason": "수학II"}],
    "conventions": ["최솟값은 그때의 x값과 함께 쓴다"],
}  # fmt: skip


def test_textbook_text_and_chunks(textbook_pdf: Path) -> None:
    texts = page_texts(textbook_pdf)
    assert set(texts) == {1, 2} and "개념 1  이차함수의 표준형" in texts[1]
    assert len(chunks(texts, limit=10)) == 2 and len(chunks(texts)) == 1


def test_check_extraction_grounding(textbook_pdf: Path) -> None:
    chunk = page_texts(textbook_pdf)
    assert check_extraction(ChapterExtraction.model_validate(CH), chunk, set()) == []
    bad = copy.deepcopy(CH)
    bad["concepts"].append({"name": "판별식", "summary": "D = b² - 4ac", "page": 1})
    bad["concepts"].append({"name": "이차함수의 표준형", "summary": "x", "page": 9})
    bad["skills"][0]["concept_names"].append("없는 개념")
    problems = check_extraction(ChapterExtraction.model_validate(bad), chunk, set())
    assert any("'판별식'이 1쪽 글에 없다" in p for p in problems)
    assert any("page 9" in p for p in problems)
    assert any("['없는 개념']" in p for p in problems)


def test_build_scope_dedupes_and_assigns_ids() -> None:
    second = {
        "concepts": [{"name": "이차함수의 표준형", "summary": "중복", "page": 2}],
        "skills": [],
    }
    draft = build_scope(
        [ChapterExtraction.model_validate(CH), ChapterExtraction.model_validate(second)],
        title="최대·최소", level="고1", subject="대수",
    )  # fmt: skip
    s = draft.scope
    assert [c.id for c in s.concepts] == ["C1", "C2", "C3"]
    assert s.concepts[0].source == "p.1"
    assert s.skills[0].id == "S1" and s.skills[0].concept_ids == ["C2", "C3"]
    assert [f.name for f in s.forbidden] == ["미분"]


def test_hangul_outside() -> None:
    assert hangul_outside("두 근의 합", "이차방정식의 두 근의 합은?") == 0
    assert hangul_outside("세 근", "두 근") == 0.5


def test_parse_pages() -> None:
    assert parse_pages("1-3, 5,3") == [1, 2, 3, 5]
    assert parse_pages(None) is None


# ---------------------------------------------------------------- 명령줄


def test_cli_ingest_textbook_then_exam(
    app_paths: AppPaths,
    make_fake: MakeFake,
    tmp_path: Path,
    exam_pdf: Path,
    textbook_pdf: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws = create_workspace(tmp_path / "ws")
    exe = make_fake(
        "script",
        script=[{"response": json.dumps(d, ensure_ascii=False)} for d in (CH, PAGE1, PAGE2)],
    )
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.parallel_questions = 1  # 가짜 AI 응답 순서를 고정
    save_config(app_paths, cfg)
    w = ["ingest", "--workspace", str(ws.root)]

    assert main([*w, "textbook", str(textbook_pdf), "--title", "최대·최소", "--level", "고1"]) == 0
    out = capsys.readouterr().out
    m = re.search(r"scope-[0-9a-f]+", out)
    assert m is not None and "금지: 미분" in out
    scope = ScopeStore(ws).get(m.group(0))
    assert len(scope.concepts) == 3

    assert main([*w, "exam", str(exam_pdf), "--title", "모의", "--scope", scope.id]) == 0
    out = capsys.readouterr().out
    m2 = re.search(r"시험 등록: (exam-[0-9a-f]+)", out)
    assert m2 is not None
    exam = ExamStore(ws, m2.group(1)).exam()
    assert len(exam.questions) == 6
    assert (ws.exams / exam.id / "figures" / "4" / "F3.png").exists()
    assert (ws.sources / "exam.pdf").exists()

    assert main([*w, "exam", str(exam_pdf), "--title", "모의", "--scope", scope.id]) == 1
    err = capsys.readouterr().err
    assert "이미 등록된 시험지" in err and exam.id in err, "기존 시험으로 안내"


def test_cli_layout_overlay(
    tmp_path: Path, exam_pdf: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["ingest", "layout", str(exam_pdf), "--overlay", str(tmp_path / "ov")]) == 0
    out = capsys.readouterr().out
    assert "문항 4: 1쪽, 그림 5" in out and "←3번 선지" in out
    assert (tmp_path / "ov" / "page_01.png").exists()
