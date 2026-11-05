"""DOCX·HWPX 시험지 읽기 (M1): 식 변환, 문항·미주 나누기, 문항·해설 구조화, 수준 프리셋."""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from lxml import etree
from PIL import Image
from tests_support import fake_prompts

from dawoni.agents import AgentClient
from dawoni.cli import main
from dawoni.config import AppConfig, save_config
from dawoni.context import ContextEngine
from dawoni.export.hwpx import from_eqedit
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.ingest.answer_key import DocKey, extract_answer_key
from dawoni.ingest.document import analyze_document, read_document
from dawoni.ingest.exam import extract_exam
from dawoni.levels import Level, ensure_preset, parse_level, preset_scope
from dawoni.mathx.latex import latex_error
from dawoni.mathx.omml import omml_to_latex
from dawoni.paths import AppPaths
from dawoni.workspace import create_workspace

MakeFake = Callable[..., Path]
REAL = Path(__file__).parent.parent / "real_data" / "docs"

# ---------------------------------------------------------------- Word 수식 → LaTeX

MNS = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'


def _omml(body: str) -> str:
    return omml_to_latex(etree.fromstring(f"<m:oMath {MNS}>{body}</m:oMath>"))


def _r(t: str, plain: bool = False) -> str:
    sty = '<m:rPr><m:sty m:val="p"/></m:rPr>' if plain else ""
    return f"<m:r>{sty}<m:t>{t}</m:t></m:r>"


@pytest.mark.parametrize(
    ("body", "want"),
    [
        (f"<m:sSub><m:e>{_r('f')}</m:e><m:sub>{_r('1')}</m:sub></m:sSub>", "f_{1}"),
        (f"<m:f><m:num>{_r('1')}</m:num><m:den>{_r('2')}</m:den></m:f>{_r('v')}", r"\frac{1}{2}v"),
        (f"<m:rad><m:radPr><m:degHide m:val='1'/></m:radPr><m:deg/><m:e>{_r('41')}</m:e></m:rad>",
         r"\sqrt{41}"),
        (f"<m:d><m:e>{_r('x+1')}</m:e></m:d>", r"\left(x+1\right)"),
        (f"<m:nary><m:naryPr><m:chr m:val='∑'/></m:naryPr><m:sub>{_r('i=1')}</m:sub>"
         f"<m:sup>{_r('n')}</m:sup><m:e>{_r('i')}</m:e></m:nary>", r"\sum_{i=1}^{n} i"),
        (f"<m:acc><m:accPr><m:chr m:val='⃗'/></m:accPr><m:e>{_r('v')}</m:e></m:acc>", r"\vec{v}"),
        (f"<m:func><m:fName>{_r('sin', plain=True)}</m:fName><m:e>{_r('θ')}</m:e></m:func>",
         r"\sin \theta"),
        (_r("r=2Ω") + _r("cm", plain=True), r"r=2\Omega\mathrm{cm}"),
        (f"<m:sSup><m:e>{_r('30')}</m:e><m:sup>{_r('∘')}</m:sup></m:sSup>", r"{30}^{\circ}"),
        (_r("전압"), r"\text{전압}"),
    ],
)  # fmt: skip
def test_omml_to_latex(body: str, want: str) -> None:
    got = _omml(body)
    assert got == want and latex_error(got) is None


@pytest.mark.parametrize(
    ("script", "want"),
    [
        ("v^2 overg", r"\frac{v^2}{g}"),
        ("2 pim overBq", r"2 \pi \frac{m}{Bq}"),
        ("Deltax_1", r"\Delta x_1"),
        ("90 DEG- theta", r"90 ^{\circ} - \theta"),
        ("L overv_0", r"\frac{L}{v_0}"),
        ("v oversqrt2", r"\frac{v}{\sqrt{2}}"),
        ('R "over" x', "R over x"),  # 따옴표 안은 그대로
    ],
)
def test_hangul_equation_glued_commands(script: str, want: str) -> None:
    """한글 수식 편집기는 붙여 쓴 명령을 알아본다 (실측 학원 시험지의 `overg`, `pim`)."""
    assert from_eqedit(script) == want


# ---------------------------------------------------------------- 문서 만들기 (픽스처)

W_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" ' + MNS
)


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (30, 20), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def _wp(*inner: str, num: bool = False, num_id: str = "1") -> str:
    ppr = f'<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="{num_id}"/></w:numPr></w:pPr>'
    return f"<w:p>{ppr if num else ''}{''.join(inner)}</w:p>"


def _wt(t: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{t}</w:t></w:r>'


def make_docx(path: Path) -> Path:
    """자동 번호 문항 2개 + Word 수식 + 그림 + 미주(답·해설, 미주 안의 자동 번호)."""
    math = f"<m:oMath><m:sSub><m:e>{_r('f')}</m:e><m:sub>{_r('1')}</m:sub></m:sSub></m:oMath>"
    pic = '<w:r><w:drawing><wp:inline><a:graphic><a:graphicData><a:blip r:embed="rId9"/>'
    pic += "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    body = "".join(
        [
            _wp(_wt("물리 월례 테스트")),
            _wp(
                _wt("다음 물음에 답하라. "),
                math,
                _wt("의 크기는?"),
                '<w:r><w:endnoteReference w:id="1"/></w:r>',
                num=True,
            ),
            _wp(pic),
            _wp(_wt("(1) 정지해 있을 때 힘")),
            _wp(_wt("두 저항의 합성저항을 구하시오. [4점]"), num=True),
            _wp(_wt("답안지")),
            _wp(_wt("이름: ( )")),
        ]
    )
    doc = f"<w:document {W_NS}><w:body>{body}</w:body></w:document>"
    numbering = (
        f"<w:numbering {W_NS}><w:abstractNum w:abstractNumId='0'><w:lvl w:ilvl='0'>"
        "<w:start w:val='1'/><w:numFmt w:val='decimal'/><w:lvlText w:val='%1.'/></w:lvl>"
        "</w:abstractNum><w:abstractNum w:abstractNumId='1'><w:lvl w:ilvl='0'>"
        "<w:start w:val='1'/><w:numFmt w:val='decimal'/><w:lvlText w:val='%1)'/></w:lvl>"
        "</w:abstractNum><w:num w:numId='1'><w:abstractNumId w:val='0'/></w:num>"
        "<w:num w:numId='2'><w:abstractNumId w:val='1'/></w:num></w:numbering>"
    )
    endnotes = (
        f"<w:endnotes {W_NS}><w:endnote w:type='separator' w:id='0'/><w:endnote w:id='1'>"
        + _wp(_wt("답 "), math, _wt(" 가 더 크다"), num=True, num_id="2")
        + _wp(_wt("부력이 줄어든다."))
        + "</w:endnote></w:endnotes>"
    )
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId9" Type="image" Target="media/image1.png"/></Relationships>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", doc)
        z.writestr("word/numbering.xml", numbering)
        z.writestr("word/endnotes.xml", endnotes)
        z.writestr("word/_rels/document.xml.rels", rels)
        z.writestr("word/media/image1.png", _png())
    return path


HP_NS = (
    'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
    'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
    'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core"'
)


def _hp(*inner: str) -> str:
    return f"<hp:p>{''.join(inner)}</hp:p>"


def _ht(t: str) -> str:
    return f"<hp:run><hp:t>{t}</hp:t></hp:run>"


def _heq(script: str) -> str:
    return f"<hp:run><hp:equation><hp:script>{script}</hp:script></hp:equation></hp:run>"


def _hnote(*paras: str) -> str:
    return f"<hp:run><hp:ctrl><hp:endNote><hp:subList>{''.join(paras)}</hp:subList></hp:endNote></hp:ctrl></hp:run>"


def make_hwpx(path: Path, *, key_section: bool = False) -> Path:
    """학원 시험지 모양: 머리 표, "1." 문항 + 미주(답·해설) + 그림 선지, 끝에 답안지 표."""
    pic = '<hp:run><hp:pic><hc:img binaryItemIDRef="image1"/></hp:pic></hp:run>'
    cell = "<hp:tc><hp:subList>{}</hp:subList></hp:tc>"
    table = "<hp:run><hp:tbl><hp:tr>{}</hp:tr></hp:tbl></hp:run>"
    q1_note = _hnote(_hp(_heq("2R")), _hp(_heq("3R"), _ht("의 저항들은 병렬 연결이다.")))
    q2_note = _hnote(_hp(_ht("①")), _hp(_ht("전류가 "), _heq("4 over3"), _ht("배가 되었다.")))
    paras = [
        _hp(table.format(cell.format(_hp(_ht("테스트(10/2주)_전기"))))),
        _hp(
            _ht("1. 점 A와 B 사이의 합성저항 "),
            _heq("R_AB"),
            _ht("는 얼마인가?"),
            *(() if key_section else (q1_note,)),
        ),
        _hp(pic),
        _hp(_ht("2. 올바른 저항의 연결은? [3점]"), *(() if key_section else (q2_note,))),
        _hp(_ht("① "), pic, _ht(" ② "), pic),
        _hp(_ht("답안지에 번호를 쓰시오.")),
        _hp(table.format(cell.format(_hp(_ht("답안지(10/2주)_전기"))))),
        _hp(table.format(cell.format(_hp(_ht("문항"))) + cell.format(_hp(_ht("정답"))))),
    ]
    if key_section:
        paras += [
            _hp(_ht("정답 및 해설")),
            _hp(_ht("1. "), _heq("2R")),
            _hp(_ht("병렬 연결이다.")),
            _hp(_ht("2. ①")),
        ]
    section = f"<hs:sec {HP_NS}>{''.join(paras)}</hs:sec>"
    hpf = (
        '<opf:package xmlns:opf="http://www.idpf.org/2007/opf/"><opf:manifest>'
        '<opf:item id="image1" href="BinData/image1.bmp" media-type="image/bmp"/>'
        "</opf:manifest></opf:package>"
    )
    bmp = io.BytesIO()
    Image.new("RGB", (30, 20), (30, 30, 200)).save(bmp, "BMP")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/content.hpf", hpf)
        z.writestr("Contents/section0.xml", section)
        z.writestr("BinData/image1.bmp", bmp.getvalue())
    return path


# ---------------------------------------------------------------- 나누기 (AI 없음)


def test_docx_numbering_math_figure_and_endnote(tmp_path: Path) -> None:
    doc = analyze_document(make_docx(tmp_path / "e.docx"), tmp_path / "work")
    regions = doc.layout.regions
    assert [r.label for r in regions] == ["1", "2"]
    q1, q2 = regions
    assert q1.text.startswith(r"1. 다음 물음에 답하라. \(f_{1}\)의 크기는?")
    assert "[그림]" in q1.text and "(1) 정지해 있을 때 힘" in q1.text
    assert [f.id for f in q1.figures] == ["F1"] and q1.figures[0].file is not None
    assert q1.figures[0].file.suffix == ".png" and q1.figures[0].file.is_file()
    assert q2.points == 4 and "답안지" not in q2.text and "이름" not in q2.text
    assert "물리 월례" not in q1.text, "첫 문항 앞 머리글은 어느 문항도 아님"
    key = doc.keys["1"].text
    assert key.startswith(r"1) 답 \(f_{1}\) 가 더 크다") and "부력이 줄어든다." in key
    assert "2" not in doc.keys


def test_hwpx_endnotes_become_question_keys(tmp_path: Path) -> None:
    doc = analyze_document(make_hwpx(tmp_path / "t.hwpx"), tmp_path / "work")
    q1, q2 = doc.layout.regions
    assert [q1.label, q2.label] == ["1", "2"]
    assert r"\(R_{AB}\)" in q1.text and "병렬" not in q1.text, "미주는 문항 글에서 빠진다"
    assert doc.keys["1"].text == "\\(2 R\\)\n\\(3 R\\)의 저항들은 병렬 연결이다."
    assert doc.keys["2"].text.startswith("①\n전류가 \\(\\frac{4}{3}\\)배")
    assert q2.points == 3 and "답안지에 번호를 쓰시오." in q2.text, "문항 안의 문장은 경계 아님"
    assert "답안지(" not in q2.text and "정답" not in q2.text
    assert [(f.id, f.near_choice) for f in q2.figures] == [("F1", 1), ("F2", 2)]
    assert "테스트(10/2주)" not in q1.text


def test_hwpx_key_section_after_mark(tmp_path: Path) -> None:
    doc = analyze_document(make_hwpx(tmp_path / "t.hwpx", key_section=True), tmp_path / "w")
    assert [r.label for r in doc.layout.regions] == ["1", "2"]
    assert doc.keys["1"].text == "1. \\(2 R\\)\n병렬 연결이다." and doc.keys["2"].text == "2. ①"
    assert all("정답 및 해설" not in r.text for r in doc.layout.regions)


def test_chunks_group_questions_for_calls(tmp_path: Path) -> None:
    from dawoni.ingest.document import QUESTIONS_PER_CALL, region_call

    paras = "".join(_hp(_ht(f"{n}. 문항 {n}")) for n in range(1, 10))
    path = tmp_path / "many.hwpx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Contents/section0.xml", f"<hs:sec {HP_NS}>{paras}</hs:sec>")
    regions = analyze_document(path, tmp_path / "w").layout.regions
    calls = [region_call(r) for r in regions]
    assert calls == sorted(calls) and calls.count(1) == QUESTIONS_PER_CALL and max(calls) == 3


# ---------------------------------------------------------------- 구조화 (가짜 AI)


def _p(*runs: dict[str, str]) -> dict[str, Any]:
    return {"type": "paragraph", "runs": list(runs)}


def _t(v: str) -> dict[str, str]:
    return {"t": "text", "v": v}


def _m(v: str) -> dict[str, str]:
    return {"t": "math", "v": v}


EXAM = {
    "questions": [
        {"label": "1", "item_type": "short_answer", "points": 4,
         "stem": [_p(_t("점 A와 B 사이의 합성저항 "), _m("R_{AB}"), _t("는 얼마인가?")),
                  {"type": "figure", "figure_id": "F1"}],
         "figures": [{"id": "F1", "role": "stem", "caption": "원형 도선과 저항 회로"}]},
        {"label": "2", "item_type": "multiple_choice", "points": 3,
         "stem": [_p(_t("올바른 저항의 연결은?"))],
         "choices": [{"label": 1, "figure_id": "F1"}, {"label": 2, "figure_id": "F2"},
                     {"label": 3, "runs": [_t("3")]}, {"label": 4, "runs": [_t("4")]},
                     {"label": 5, "runs": [_t("5")]}],
         "figures": [{"id": "F1", "role": "choice", "choice_label": 1, "caption": "직렬 두 개"},
                     {"id": "F2", "role": "choice", "choice_label": 2, "caption": "병렬 두 개"}]},
    ]
}  # fmt: skip
KEYS = {
    "questions": [
        {"number": "1", "parts": [{"label": "", "answer": "\\(2 R\\)",
                                   "solution": "\\(3 R\\)의 저항들은 병렬 연결이다.",
                                   "credit_policy": "unspecified",
                                   "concepts": ["병렬 연결 합성저항"]}]},
        {"number": "2", "parts": [{"label": "", "answer": "①", "solution": "전류가 \\(\\frac{4}{3}\\)배가 되었다.",
                                   "credit_policy": "unspecified", "concepts": ["옴의 법칙"]}]},
    ]
}  # fmt: skip


def _engine(app_paths: AppPaths, make_fake: MakeFake, *responses: dict[str, Any]) -> Any:
    exe = make_fake(
        "script", script=[{"response": json.dumps(r, ensure_ascii=False)} for r in responses]
    )
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.agents["claude"].enabled = False
    cfg.agents["codex"].enabled = False
    return ContextEngine(AgentClient(cfg, app_paths)), exe


def test_extract_exam_from_document(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path
) -> None:
    doc = analyze_document(make_hwpx(tmp_path / "t.hwpx"), tmp_path / "work")
    engine, exe = _engine(app_paths, make_fake, EXAM)
    res = extract_exam(engine, doc.layout, tmp_path / "work")
    assert [q.number for q in res.questions] == ["1", "2"]
    assert res.questions[1].item.choices[0].figure_id == "F1"
    assert set(res.figure_files["2"]) == {"F1", "F2"}
    (prompt,) = fake_prompts(exe)
    assert "문서 글자:" in prompt and "PDF 글자" not in prompt
    line = "- F2 (이미지, 첨부 3번째 파일, 글의 2번째 [그림] 자리) — 레이아웃상 2번 선지 기호 옆"
    assert line in prompt
    assert "문항 끝에 붙은 정답·해설(미주)은 [자료]에 없다" in prompt


def test_answer_key_from_document_reads_once(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path
) -> None:
    doc = analyze_document(make_hwpx(tmp_path / "t.hwpx"), tmp_path / "work")
    engine, exe = _engine(app_paths, make_fake, EXAM, KEYS)
    exam_res = extract_exam(engine, doc.layout, tmp_path / "work")
    from dawoni.models.grading import Exam

    exam = Exam(title="t", scope_id="s", questions=exam_res.questions)
    res = extract_answer_key(engine, DocKey(tmp_path / "t.hwpx", doc.keys), None, exam,
                             tmp_path / "key_work")  # fmt: skip
    assert [k.question_number for k in res.keys] == ["1", "2"]
    assert res.keys[0].concepts == ["병렬 연결 합성저항"]
    assert res.keys[0].source == "t.hwpx 문항별 해설(미주·해설 구간)"
    prompts = fake_prompts(exe)
    assert len(prompts) == 2, "해설은 한 번만 읽는다 (문서 글은 숫자가 깨지지 않음)"
    assert "[문항 2 해설]\n①" in prompts[1] and "해설지 PDF 글자" not in prompts[1]
    assert "`concepts`" in prompts[1]


def test_cli_ingest_hwpx_with_level(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = create_workspace(tmp_path / "ws")
    exe = make_fake(
        "script", script=[{"response": json.dumps(r, ensure_ascii=False)} for r in (EXAM, KEYS)]
    )
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.parallel_questions = 1
    save_config(app_paths, cfg)
    hwpx = make_hwpx(tmp_path / "주말반.hwpx")
    w = ["ingest", "--workspace", str(ws.root)]
    assert main([*w, "exam", str(hwpx), "--title", "주말반", "--level", "고1 물리"]) == 0
    out = capsys.readouterr().out
    m = re.search(r"시험 등록: (exam-[0-9a-f]+)", out)
    assert m is not None and "해설지: 문항별 미주·해설 → 2문항" in out
    store = ExamStore(ws, m.group(1))
    exam = store.exam()
    assert exam.scope_id == "scope_preset_h1_phys"
    assert (store.root / "figures" / "2" / "F2.png").is_file()
    key = store.answer_key("1")
    assert key is not None and key.concepts == ["병렬 연결 합성저항"]
    scope = ScopeStore(ws).get("scope_preset_h1_phys")
    assert "미분" in [f.name for f in scope.forbidden] and scope.concepts == []

    assert main(["ingest", "layout", str(hwpx)]) == 0
    out = capsys.readouterr().out
    assert "- 문항 2 [3점]: 묶음 1, 그림 2" in out and "해설 1: " in out


def test_hwp_without_hangul_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dawoni.ingest import convert
    from dawoni.ingest.workflow import IngestRefused, readable_copy

    monkeypatch.setattr(convert.sys, "platform", "linux")
    hwp = tmp_path / "시험.hwp"
    hwp.write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(IngestRefused, match="HWPX 문서"):
        readable_copy(hwp, tmp_path / "w")
    with pytest.raises(IngestRefused, match="PDF·DOCX·HWPX·HWP"):
        readable_copy(tmp_path / "시험.txt", tmp_path / "w")


# ---------------------------------------------------------------- 수준 프리셋


@pytest.mark.parametrize(
    ("spec", "want"),
    [("고1 물리", Level("h1", "phys")), ("h1_phys", Level("h1", "phys")),
     ("중3수학", Level("m3", "math")), ("고2 생명", Level("h2", "bio")),
     ("물리", Level("g", "phys")), ("g_geometry", Level("g", "geometry")),
     ("생물학", Level("g", "bio")), ("조합", Level("g", "combi"))],
)  # fmt: skip
def test_parse_level(spec: str, want: Level) -> None:
    assert parse_level(spec) == want


def test_parse_level_unknown() -> None:
    with pytest.raises(ValueError, match="수준을 알 수 없음"):
        parse_level("대학 물리")


def test_presets_block_calculus_below_university(tmp_path: Path) -> None:
    h1 = preset_scope(Level("h1", "phys"))
    assert {"미분", "적분", "라그랑지안", "벡터 외적"} <= {f.name for f in h1.forbidden}
    assert h1.level == "고등학교 1학년 물리"
    m3 = {f.name for f in preset_scope(Level("m3", "math")).forbidden}
    assert {"미분", "판별식"} <= m3 and "삼각비" not in m3, "중3 수학은 삼각비를 배운다"
    h3 = {f.name for f in preset_scope(Level("h3", "math")).forbidden}
    assert "미분" not in h3 and "편미분" in h3
    gifted = preset_scope(Level("g", "geometry"))
    assert gifted.subject == "기하" and gifted.id == "scope_preset_g_geometry"
    assert {"미분", "편미분"} <= {f.name for f in gifted.forbidden}
    assert "삼각함수" not in {f.name for f in gifted.forbidden}, (
        "영재 과목은 학년보다 앞선 내용을 쓴다"
    )

    ws = create_workspace(tmp_path / "ws")
    store = ScopeStore(ws)
    first = ensure_preset(store, Level("h1", "phys"))
    edited = first.model_copy(update={"forbidden": first.forbidden[:1], "version": 2})
    store.save(edited)
    assert ensure_preset(store, Level("h1", "phys")).version == 2, "강사가 고친 프리셋은 그대로"


# ---------------------------------------------------------------- 실제 자료 (real_data/docs)

_REAL_HWPX = sorted(REAL.glob("*.hwpx")) if REAL.is_dir() else []
_REAL_DOCX = sorted(REAL.glob("*.docx")) if REAL.is_dir() else []


@pytest.mark.skipif(not _REAL_HWPX, reason="real_data/docs 없음")
@pytest.mark.parametrize("path", _REAL_HWPX, ids=lambda p: p.stem)
def test_real_hwpx_every_question_has_endnote_key(path: Path, tmp_path: Path) -> None:
    """실측 학원 시험지 (HWP → 한글로 HWPX): 문항 7~8개, 모든 문항에 미주 답·해설."""
    doc = analyze_document(path, tmp_path)
    labels = [r.label for r in doc.layout.regions]
    assert labels == [str(n) for n in range(1, len(labels) + 1)] and len(labels) >= 7
    assert set(doc.keys) == set(labels)
    assert all("답안지" not in r.text for r in doc.layout.regions)
    _assert_math_ok([r.text for r in doc.layout.regions] + [k.text for k in doc.keys.values()])


@pytest.mark.skipif(not _REAL_DOCX, reason="real_data/docs 없음")
@pytest.mark.parametrize("path", _REAL_DOCX, ids=lambda p: p.stem)
def test_real_docx_auto_numbered_questions(path: Path, tmp_path: Path) -> None:
    """실측 월례 시험지 (DOCX): 자동 번호 문항 3개, 소문항 번호는 미주와 따로 센다."""
    doc = analyze_document(path, tmp_path)
    assert [r.label for r in doc.layout.regions] == ["1", "2", "3"]
    assert all(re.search(r"(?m)^1\) \[그림 1\]", r.text) for r in doc.layout.regions)
    assert set(doc.keys) == {"1", "2", "3"}
    _assert_math_ok([r.text for r in doc.layout.regions])


def _assert_math_ok(texts: list[str]) -> None:
    exprs = [m for t in texts for m in re.findall(r"\\\((.+?)\\\)", t)]
    bad = [(e, err) for e in exprs if (err := latex_error(e))]
    assert exprs and not bad
    leftover = [e for e in exprs if re.search(r"(?<![\\A-Za-z])(over|DEG)(?![A-Za-z])", e)]
    assert not leftover, "한글 수식 명령이 LaTeX로 바뀌지 않음"


def test_read_document_rejects_other_suffix(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="DOCX·HWPX"):
        read_document(tmp_path / "x.pdf")


def test_unmarked_document_points_are_not_invented() -> None:
    """배점 표시가 없는 문서 문항: AI 배점은 버리고, 소문항은 본문 (1)(2) 줄에서 (실측 주말반)."""
    from dawoni.ingest.exam import ExtractedQuestion, sub_labels, unmarked_points
    from dawoni.ingest.layout import Line, Rect, Region

    r = Region(label="5", lines=[Line(1, Rect(0, 0, 0, 0), t) for t in
                                 ("5. 두 물체가 만난다.", "(1) 거리를 구하시오.", "(2) 속력을 구하시오.")])  # fmt: skip
    q = ExtractedQuestion.model_validate(
        {"label": "5", "item_type": "short_answer", "points": 7, "stem": [_p(_t("두 물체"))]}
    )
    points, parts, why = unmarked_points(q, r, 4)
    assert points == 4 and [(p.label, p.points) for p in parts] == [("(1)", 2), ("(2)", 2)]
    assert "소문항마다 2점" in why
    assert sub_labels("(2) 먼저\n(1) 나중") == [] and sub_labels("(1) 하나뿐") == []
    assert sub_labels("1) [그림 1] 비커\n2) [그림 2] 마찰") == ["1)", "2)"]
    alone = Region(label="1", lines=[Line(1, Rect(0, 0, 0, 0), "1. 합성저항은?")])
    assert unmarked_points(q, alone, 4)[:2] == (4, [])
