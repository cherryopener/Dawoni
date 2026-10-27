# pyright: reportPrivateUsage=false
"""유튜브 강의 요약본 → Word (A4 1장 또는 양면 1장, 2단).

머리: 제목·영상 정보·교재 범위 비교(다룬 개념 / 빠진 개념 / 범위 밖) + 영상 QR 코드.
본문: 부분마다 '▶ 시각'(누르면 영상의 그 시각으로) + 요점·식, 핵심 공식·요령·스스로 확인 상자.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from docx.shared import Cm, Emu

from dawoni.export.docx.builder import (
    DocWriter,
    PageSetup,
    loose_latex,
    set_keep_next,
    set_table_borders,
)
from dawoni.export.docx.fonts import FontSpec, add_run
from dawoni.lecture.summarize import LINES_PER_PAGE, estimate_lines, stamp
from dawoni.models.lecture import LectureSummary
from dawoni.models.scope import ScopeProfile


def _qr(url: str, out: Path) -> Path:
    import qrcode

    img = qrcode.make(url, box_size=8, border=1)  # pyright: ignore[reportUnknownMemberType]
    with out.open("wb") as fh:
        img.save(fh)  # pyright: ignore[reportUnknownMemberType]
    return out


def no_emoji(s: str) -> str:
    """영상 제목 등의 이모지(💥 등)는 맑은 고딕에 없어 Word가 이모지 폰트로 바꾼다 → 뺀다."""
    out = "".join(
        ch for ch in s
        if not (ord(ch) >= 0x1F000 or 0x2600 <= ord(ch) <= 0x27BF or ord(ch) in (0xFE0F, 0x200D))
    )  # fmt: skip
    return " ".join(out.split())


def _at(url: str, s: float) -> str:
    return f"{url}&t={int(s)}s"


def body_size(s: LectureSummary) -> float:
    """본문 글자 크기: 어림 줄 수가 쪽 수 한도를 넘으면 조금씩 줄인다 (8.5pt까지)."""
    over = estimate_lines(s) / LINES_PER_PAGE[s.pages]
    return 9.5 if over <= 1 else 9.0 if over <= 1.08 else 8.5


def build_lecture_summary(
    s: LectureSummary, fonts: FontSpec, scope: ScopeProfile | None = None
) -> DocWriter:
    small = FontSpec(body=fonts.body, math=fonts.math, size_pt=body_size(s))
    w = DocWriter(small, page=PageSetup(margin_cm=1.4, column_gap_cm=0.7), strict_math=False)
    latex = [f for sec in s.sections for f in sec.formulas] + s.key_formulas
    loose = [x for sec in s.sections for b in sec.bullets for x in loose_latex(b)]
    loose += [x for t in [*s.tips, *s.review_questions, s.overview] for x in loose_latex(t)]
    w.prepare_math(latex, loose)

    # 머리: 정보 | QR
    t = w.table(w.body, 1, 2, w.page.text_width_cm)
    set_table_borders(t, "none")
    left, right = t.cell(0, 0), t.cell(0, 1)
    left.width, right.width = Cm(w.page.text_width_cm - 3.2), Cm(3.2)
    p = w.par(left, space_after_pt=2)
    w.text(p, no_emoji(s.title), bold=True, size_pt=15)
    p = w.par(left, space_after_pt=1)
    w.hyperlink(p, s.url, no_emoji(s.video_title), size_pt=9)
    meta = [
        x
        for x in (
            no_emoji(s.channel) if s.channel else None,
            f"길이 {stamp(s.duration_s)}",
            "자동 자막" if s.caption_generated else None,
        )
        if x
    ]
    w.text(p, "  ·  " + "  ·  ".join(meta), size_pt=9)
    if s.overview:
        w.loose_text(w.par(left, space_after_pt=2), s.overview)
    if scope is not None:
        p = w.par(left, space_after_pt=0)
        w.text(p, f"교재 범위: {scope.title}", bold=True, size_pt=9)
        if s.covered:
            p = w.par(left, space_after_pt=0)
            w.text(p, "다룬 개념: " + ", ".join(map(scope.name_of, s.covered)), size_pt=9)
        if s.missing:
            p = w.par(left, space_after_pt=0)
            w.text(
                p, "강의에 없는 교재 개념: " + ", ".join(map(scope.name_of, s.missing)), size_pt=9
            )
        if s.beyond:
            p = w.par(left, space_after_pt=0)
            w.text(p, "범위 밖 내용: " + ", ".join(s.beyond), size_pt=9)
    with tempfile.TemporaryDirectory() as d:
        qr = _qr(s.url, Path(d) / "qr.png")
        p = w.par(right, align="center", space_after_pt=0)
        add_run(p, "", small).add_picture(str(qr), width=Emu(int(Cm(2.6))))
        w.text(w.par(right, align="center", space_after_pt=0), "영상 바로 열기", size_pt=8)

    w.start_columns(2)
    if s.pages == 2:
        w.page_numbers()
    for sec in s.sections:
        p = w.par(space_before_pt=5, space_after_pt=1)
        w.hyperlink(p, _at(s.url, sec.start_s), f"▶ {stamp(sec.start_s)}", bold=True, size_pt=9)
        w.text(p, f"  {no_emoji(sec.heading)}", bold=True, size_pt=10.5)
        set_keep_next(p._p)  # 단 끝에 제목만 남지 않게
        for b in sec.bullets:
            w.loose_text(
                w.par(left_indent_cm=0.35, first_line_cm=-0.3, space_after_pt=0.5), f"· {b}"
            )
        for f in sec.formulas:
            w.math(w.par(align="center", space_after_pt=1), f, display=True)

    def box(title: str, lines: list[str], *, math_lines: bool = False) -> None:
        if not lines:
            return
        cell = w.boxed(w.body, w.column_cm)
        w.text(w.par(cell, space_after_pt=1), title, bold=True)
        for x in lines:
            if math_lines:
                w.math(w.par(cell, align="center", space_after_pt=1), x, display=True)
            else:
                w.loose_text(
                    w.par(cell, left_indent_cm=0.35, first_line_cm=-0.3, space_after_pt=0.5),
                    f"· {x}",
                )
        w.par(space_after_pt=0, line_spacing=1.0)

    box("핵심 공식", s.key_formulas, math_lines=True)
    box("강사가 강조한 실수·요령", s.tips)
    box("스스로 확인", s.review_questions)
    return w
