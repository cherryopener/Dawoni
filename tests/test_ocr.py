"""손글씨 판독: 페이지 준비, 판독 검증, 교차 판독, 외부 OCR 참고, 강사 확인 관문."""

from __future__ import annotations

import json
import re
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from PIL import Image
from tests_support import fake_prompts

from dawoni.agents import AgentClient
from dawoni.cli import main
from dawoni.config import AppConfig, OcrSettings, save_config
from dawoni.context import ContextEngine
from dawoni.fsio import read_json
from dawoni.grading.pipeline import SecondOpinion
from dawoni.grading.store import ExamStore
from dawoni.models.grading import Exam, TranscribedLine, TranscriptionDraft
from dawoni.ocr.crosscheck import cross_check
from dawoni.ocr.hints import ClovaOcr, MathpixOcr, collect_hints, providers_from
from dawoni.ocr.pages import PageError, prepare_pages
from dawoni.ocr.transcribe import check_transcription, transcribe_student
from dawoni.paths import AppPaths
from dawoni.workspace import create_workspace

FIX = Path(__file__).parent / "fixtures" / "grading"
SCAN = FIX / "scans" / "F_q1.jpg"
MakeFake = Callable[..., Path]


# ---------------------------------------------------------------- 페이지 준비


def test_prepare_image_and_keep_original(tmp_path: Path) -> None:
    big = tmp_path / "큰 사진.JPG"
    Image.new("RGB", (4000, 3000), "white").save(big)
    pages = prepare_pages([big, SCAN], tmp_path / "out")
    assert [p.number for p in pages] == [1, 2]
    with Image.open(pages[0].image) as im:
        assert max(im.size) == 2400 and im.format == "PNG"
    assert pages[0].original.name == "original_01.jpg" and pages[0].original.exists()


def test_exif_rotation_is_applied(tmp_path: Path) -> None:
    src = tmp_path / "rotated.jpg"
    im = Image.new("RGB", (300, 100), "white")
    exif = im.getexif()
    exif[0x0112] = 6  # 90° 회전해서 보라는 표시 (폰 세로 사진)
    im.save(src, exif=exif)
    page = prepare_pages([src], tmp_path / "out")[0]
    with Image.open(page.image) as out:
        assert out.size == (100, 300)


def test_pdf_pages(tmp_path: Path) -> None:
    pdf = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page()
    doc.save(str(pdf))  # pyright: ignore[reportUnknownMemberType]
    pages = prepare_pages([pdf], tmp_path / "out")
    assert [p.image.name for p in pages] == ["page_01.png", "page_02.png"]
    assert pages[0].original == pages[1].original


def test_unsupported_and_missing(tmp_path: Path) -> None:
    doc = tmp_path / "답안.hwp"
    doc.write_bytes(b"x")
    with pytest.raises(PageError, match="지원하지 않는"):
        prepare_pages([doc], tmp_path / "out")
    with pytest.raises(PageError, match="없습니다"):
        prepare_pages([tmp_path / "없음.jpg"], tmp_path / "out")


# ---------------------------------------------------------------- 판독 검증·교차 판독


def _line(
    text: str = "", math: str | None = None, q: str | None = "1", **kw: Any
) -> TranscribedLine:
    return TranscribedLine(
        question_number=q, page=1, text=text, math=math, confidence=kw.pop("confidence", 0.9), **kw
    )


def test_check_transcription() -> None:
    d = TranscriptionDraft(
        lines=[_line(math="y=1"), _line("x", q="9"), _line(""), _line(math="$x$")]
    )
    problems = check_transcription(d, {"1", "2"}, n_pages=1)
    assert any("'9'는 이 시험의 문항이 아니다" in p for p in problems)
    assert any("3번째 줄이 비어 있다" in p for p in problems)
    assert any("$ 기호" in p for p in problems)
    d2 = TranscriptionDraft(lines=[_line(math="y=1").model_copy(update={"page": 3})])
    assert any("첨부 1장을 넘는다" in p for p in check_transcription(d2, {"1"}, 1))


def test_cross_check_identical() -> None:
    a = [_line(math="y=(x-2)^2+3"), _line("최솟값 3")]
    b = [_line(math="y = (x-2)^{2} + 3"), _line("최솟값 3")]
    cc = cross_check(a, b)
    assert cc.agreement == 1.0
    assert all(not x.uncertain for x in cc.lines)


def test_cross_check_marks_disagreement() -> None:
    a = [_line(math="y=(x-2)^2+3"), _line("최솟값 3"), _line("끝")]
    b = [_line(math="y=(x-2)^2+8"), _line("최솟값 3"), _line("추가 줄"), _line("끝")]
    cc = cross_check(a, b)
    assert cc.lines[0].uncertain == ["교차 판독은 'y=(x-2)^2+8'"]
    assert cc.lines[0].confidence <= 0.5
    assert not cc.lines[1].uncertain
    assert cc.only_in_second == ["추가 줄"]
    assert 0 < cc.agreement < 1


def test_cross_check_missing_line() -> None:
    cc = cross_check([_line("가"), _line("나")], [_line("가")])
    assert cc.lines[1].uncertain == ["교차 판독에는 이 줄이 없음"]


# ---------------------------------------------------------------- 외부 OCR (선택)


def test_clova_and_mathpix_parsing(tmp_path: Path) -> None:
    img = tmp_path / "p.png"
    Image.new("RGB", (10, 10)).save(img)
    sent: list[urllib.request.Request] = []

    def clova_open(req: urllib.request.Request, timeout: float) -> bytes:
        sent.append(req)
        fields = [
            {"inferText": "y=", "lineBreak": False},
            {"inferText": "(x-2)²+3", "lineBreak": True},
            {"inferText": "최솟값", "lineBreak": False},
            {"inferText": "3", "lineBreak": True},
        ]
        return json.dumps({"images": [{"fields": fields}]}).encode()

    assert (
        ClovaOcr("https://ocr.example/invoke", "s", clova_open).read(img) == "y= (x-2)²+3\n최솟값 3"
    )
    assert sent[0].get_header("X-ocr-secret") == "s"
    body = json.loads(sent[0].data)  # type: ignore[arg-type]
    assert body["version"] == "V2" and body["images"][0]["format"] == "png"

    def mathpix_open(req: urllib.request.Request, timeout: float) -> bytes:
        return json.dumps({"text": "\\( y=(x-2)^{2}+3 \\)"}).encode()

    assert MathpixOcr("id", "key", mathpix_open).read(img).startswith("\\( y=")


def test_hints_are_opt_in_and_failures_dont_block(tmp_path: Path) -> None:
    assert providers_from(OcrSettings()) == []
    assert providers_from(OcrSettings(clova_enabled=True)) == [], "키가 없으면 켜지지 않음"

    class Broken:
        name = "고장"

        def read(self, image: Path) -> str:
            raise OSError("네트워크 없음")

    class Works:
        name = "참고"

        def read(self, image: Path) -> str:
            return "x=2"

    text, warnings = collect_hints([Broken(), Works()], [tmp_path / "p.png"])
    assert text is not None and "참고용 외부 OCR 결과" in text and "x=2" in text
    assert warnings == ["고장 1쪽 실패: 네트워크 없음"]


# ---------------------------------------------------------------- 판독 파이프라인 (가짜 CLI)

DRAFT: dict[str, Any] = {
    "lines": [
        {"question_number": "1", "page": 1, "math": "y=x^2-4x+7", "confidence": 0.95},
        {"question_number": "1", "page": 1, "math": "=(x-2)^2+7-4", "confidence": 0.9},
        {"question_number": "1", "page": 1, "math": "=(x-2)^2+11", "confidence": 0.8,
         "struck": True},
        {"question_number": "1", "page": 1, "math": "=(x-2)^2+3", "confidence": 0.9},
        {"question_number": None, "page": 1, "text": "x=2 일 때 최솟값 3", "confidence": 0.85},
    ],
    "notes": [],
}  # fmt: skip


@pytest.fixture
def exam_q1() -> Exam:
    return Exam(title="t", scope_id="s", questions=read_json(FIX / "questions.json")[:1])


def _engine(app_paths: AppPaths, exe: Path) -> ContextEngine:
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.agents["claude"].enabled = False
    cfg.agents["codex"].enabled = False
    return ContextEngine(AgentClient(cfg, app_paths))


def test_transcribe_assigns_lines_and_keeps_unconfirmed(
    app_paths: AppPaths, make_fake: MakeFake, exam_q1: Exam, tmp_path: Path
) -> None:
    exe = make_fake("script", script=[{"response": json.dumps(DRAFT, ensure_ascii=False)}])
    page = prepare_pages([SCAN], tmp_path / "scan")[0]
    out = transcribe_student(
        _engine(app_paths, exe), exam_q1, "고1", "F", [page.image], page_refs=["scans/F/x.jpg"]
    )
    t = out.transcripts["1"]
    assert not t.confirmed and t.source == "ocr" and len(t.lines) == 5
    assert t.lines[2].struck and t.lines[4].text == "x=2 일 때 최솟값 3"
    assert out.unassigned == [], "문항이 하나면 번호 없는 줄은 그 문항"
    prompt = fake_prompts(exe)[0]
    assert "inputs/input_01.png" in prompt and "고쳐 읽지 않는다" in prompt
    assert "[문항 1]" in prompt


def test_transcribe_with_cross_check(
    app_paths: AppPaths, make_fake: MakeFake, exam_q1: Exam, tmp_path: Path
) -> None:
    other = json.loads(json.dumps(DRAFT))
    other["lines"][3]["math"] = "=(x-2)^2+8"
    exe = make_fake(
        "script",
        script=[{"response": json.dumps(d, ensure_ascii=False)} for d in (DRAFT, other)],
    )
    page = prepare_pages([SCAN], tmp_path / "scan")[0]
    out = transcribe_student(
        _engine(app_paths, exe), exam_q1, "고1", "F", [page.image],
        page_refs=[], cross=SecondOpinion(),
    )  # fmt: skip
    t = out.transcripts["1"]
    assert t.cross_checked and t.agreement is not None and t.agreement < 1
    assert t.lines[3].uncertain == ["교차 판독은 '=(x-2)^2+8'"]
    assert t.needs_attention


# ---------------------------------------------------------------- 명령줄: 판독 → 확인 → 채점


def test_cli_scan_confirm_then_grade(
    app_paths: AppPaths,
    make_fake: MakeFake,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tests.test_grading import GRADE_A, RUBRIC_OK, SOL_OK

    grade = json.loads(json.dumps(GRADE_A))
    grade["judgements"][0]["evidence_quote"] = "(x-2)^2+3"
    grade["judgements"][1]["evidence_quote"] = "x=2 일 때 최솟값 3"
    ws = create_workspace(tmp_path / "ws")
    exe = make_fake(
        "script",
        script=[
            {"response": json.dumps(r, ensure_ascii=False)}
            for r in (SOL_OK, RUBRIC_OK, DRAFT, grade)
        ],
    )
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.ocr.cross_check = False
    save_config(app_paths, cfg)
    w = ["--workspace", str(ws.root)]
    assert main(["scope", *w, "import", str(FIX / "scope.json")]) == 0
    q = tmp_path / "q1.json"
    q.write_text(json.dumps(read_json(FIX / "questions.json")[:1]), encoding="utf-8")
    assert main(["exam", *w, "create", "--title", "t", "--scope", "scope-quadratic-minmax",
                 "--questions", str(q)]) == 0  # fmt: skip
    m = re.search(r"exam-[0-9a-f]+", capsys.readouterr().out)
    assert m is not None
    exam_id = m.group(0)
    assert main(["exam", *w, "solve", exam_id]) == 0
    assert main(["exam", *w, "rubric", exam_id]) == 0
    assert main(["exam", *w, "approve", exam_id, "-q", "1"]) == 0

    assert main(["exam", *w, "scan", exam_id, "--student", "F", str(SCAN)]) == 0
    out = capsys.readouterr().out
    assert "미확인" in out and "~~=(x-2)^2+11~~" in out.replace("식: ", "")
    store = ExamStore(ws, exam_id)
    t = store.transcript("F", "1")
    assert t is not None and not t.confirmed and t.pages == ["scans/F/original_01.jpg"]
    assert (store.scan_dir("F") / "page_01.png").exists()

    assert main(["exam", *w, "grade", exam_id]) == 0
    assert "판독문 확인 대기 — 건너뜀" in capsys.readouterr().out, "확인 전에는 채점하지 않음"

    assert main(["exam", *w, "transcript", exam_id, "--student", "F", "-q", "1",
                 "--confirm", "--by", "강사"]) == 0  # fmt: skip
    assert main(["exam", *w, "grade", exam_id, "--regrade"]) == 0
    assert "F 문항 1: 4/4" in capsys.readouterr().out
