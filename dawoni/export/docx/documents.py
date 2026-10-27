"""시험 자료 → Word 문서: 시험지·해설지(정답표)·채점 리포트(강사용)·성적표(학생용).

모든 문서는 먼저 문서 안의 수식을 한 번에 변환·검사하고(`prepare_math`) 쓰기 시작한다.
시험지·해설지는 수식 하나라도 변환되지 않으면 내보내지 않는다. 리포트·성적표의 학생 답안은
원문 그대로여야 하므로 변환되지 않는 식만 원문 글자 스타일로 보여 준다.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from dawoni.export.docx.builder import (
    DocWriter,
    FigureContext,
    circled,
    item_latex,
    loose_latex,
    set_table_borders,
    shade_cell,
)
from dawoni.export.docx.fonts import FontSpec
from dawoni.export.fill import fill_item
from dawoni.grading.store import ExamStore
from dawoni.models.base import utcnow
from dawoni.models.content import MathRun, iter_runs, runs_from_text
from dawoni.models.grading import (
    Exam,
    ExamQuestion,
    GradeResult,
    Rubric,
    ScopedSolution,
    SolutionStep,
)
from dawoni.models.item import ChoiceAnswer, ItemContent, TextAnswer, ValueAnswer


def _latex_in_text(s: str | None) -> Iterator[str]:
    if s:
        for r in runs_from_text(s):
            if isinstance(r, MathRun):
                yield r.v


def _filled(exam: Exam) -> list[tuple[ExamQuestion, ItemContent]]:
    return [(q, fill_item(q.item)) for q in exam.questions]


def _title(w: DocWriter, title: str, subtitle: str | None = None) -> None:
    p = w.par(align="center", space_after_pt=2)
    w.text(p, title, bold=True, size_pt=w.fonts.size_pt + 7)
    if subtitle:
        p = w.par(align="center", space_after_pt=6)
        w.text(p, subtitle, size_pt=w.fonts.size_pt)


# ---------------------------------------------------------------- 시험지


@dataclass(frozen=True)
class PaperOptions:
    columns: int = 2
    show_points: bool = True
    answer_lines: int = 8
    """서술형 문항 아래 풀이 칸 높이(줄 수). 0이면 칸 없음."""
    student_line: bool = True
    subtitle: str | None = None


def build_exam_paper(
    exam: Exam, exam_dir: Path, fonts: FontSpec, opts: PaperOptions | None = None
) -> DocWriter:
    opts = opts or PaperOptions()
    w = DocWriter(fonts, strict_math=True)
    items = _filled(exam)
    latex: list[str] = []
    for _, it in items:
        latex += [r.v for r in iter_runs(it.stem) if isinstance(r, MathRun)]
        latex += [r.v for c in it.choices for r in c.runs if isinstance(r, MathRun)]
    w.prepare_math(latex)

    _title(w, exam.title, opts.subtitle)
    if opts.student_line:
        p = w.par(align="right", space_after_pt=8)
        w.text(p, "이름 ______________    번호 ________")
    if opts.columns > 1:
        w.start_columns(opts.columns)
    for q, it in items:
        w.question(
            q.number,
            q.points if opts.show_points else None,
            it,
            FigureContext.of(it, exam_dir),
            answer_lines=opts.answer_lines if it.item_type == "essay" else 0,
        )
    w.page_numbers()
    return w


# ---------------------------------------------------------------- 해설지


@dataclass(frozen=True)
class KeyOptions:
    with_questions: bool = False
    with_rubric: bool = False
    """강사용: 승인된(또는 초안) 루브릭을 함께."""


def _answer_cell_latex(item: ItemContent, sol: ScopedSolution | None) -> list[str]:
    match item.answer:
        case ValueAnswer(latex=x):
            return [x]
        case TextAnswer(runs=runs):
            return [r.v for r in runs if isinstance(r, MathRun)]
        case _:
            pass
    if isinstance(item.answer, ChoiceAnswer):
        return []
    return [sol.final_answer.latex] if sol else []


def _steps_latex(steps: list[SolutionStep]) -> list[str]:
    out: list[str] = []
    for s in steps:
        out += _latex_in_text(s.explanation)
        if s.math:
            out.append(s.math)
    return out


def build_answer_key(
    exam: Exam, store: ExamStore, fonts: FontSpec, opts: KeyOptions | None = None
) -> DocWriter:
    opts = opts or KeyOptions()
    w = DocWriter(fonts, strict_math=True)
    items = _filled(exam)
    sols = {q.number: store.solution(q.number) for q in exam.questions}
    rubrics = {q.number: store.rubric(q.number) for q in exam.questions} if opts.with_rubric else {}

    latex: list[str] = []
    loose: list[str] = []
    for q, it in items:
        sol = sols[q.number]
        latex += _answer_cell_latex(it, sol)
        if opts.with_questions:
            latex += item_latex(it)
        if sol:
            latex += [*_steps_latex(sol.steps), sol.final_answer.latex]
            for alt in sol.alternatives:
                latex += _steps_latex(alt.steps)
        elif it.solution:
            latex += [r.v for r in iter_runs(it.solution) if isinstance(r, MathRun)]
        if sol:
            loose += [x for st in sol.steps for x in loose_latex(st.explanation)]
            for alt in sol.alternatives:
                loose += loose_latex(alt.summary)
                loose += [x for st in alt.steps for x in loose_latex(st.explanation)]
            loose += loose_latex(sol.out_of_scope_note)
        r = rubrics.get(q.number)
        if r:
            texts = [i.criterion for i in r.items] + [i.evidence_hint for i in r.items]
            texts += [pc.condition for pc in r.partial] + r.never_deduct
            texts += [a.description for a in r.accepted_alternatives]
            loose += [x for t in texts for x in loose_latex(t)]
    w.prepare_math(latex, loose)

    _title(w, f"{exam.title} 정답 및 해설")

    # 정답표
    t = w.table(w.body, len(items) + 1, 3, w.column_cm)
    set_table_borders(t, "grid", size=4)
    for j, h in enumerate(("문항", "정답", "배점")):
        cell = t.cell(0, j)
        w.text(w.par(cell, align="center", space_after_pt=0), h, bold=True)
        shade_cell(cell)
    for i, (q, it) in enumerate(items, start=1):
        w.text(w.par(t.cell(i, 0), align="center", space_after_pt=0), q.number)
        p = w.par(t.cell(i, 1), align="center", space_after_pt=0)
        sol = sols[q.number]
        match it.answer:
            case ChoiceAnswer(labels=labels):
                w.text(p, ", ".join(circled(x) for x in labels))
            case ValueAnswer(latex=x, unit=unit):
                w.math(p, x)
                if unit:
                    w.text(p, f" {unit}")
            case TextAnswer(runs=runs):
                w.runs(p, runs)
            case None if sol is not None:
                w.math(p, sol.final_answer.latex)
            case _:
                w.text(p, "—")
                w.warnings.append(f"문항 {q.number}: 정답 정보 없음")
        w.text(w.par(t.cell(i, 2), align="center", space_after_pt=0), f"{q.points:g}")

    for q, it in items:
        w.heading(
            f"문항 {q.number}  ({q.points:g}점)", size_pt=fonts.size_pt + 2, space_before_pt=14
        )
        ctx = FigureContext.of(it, store.root)
        if opts.with_questions:
            w.blocks(w.body, it.stem, ctx, w.column_cm)
            w.choices(w.body, it, ctx, w.column_cm)
            w.par(space_after_pt=2)
        sol = sols[q.number]
        if sol is not None:
            _solution(w, sol)
        elif it.solution:
            w.blocks(w.body, it.solution, ctx, w.column_cm)
        else:
            w.text(w.par(), "(해설 없음)")
            w.warnings.append(f"문항 {q.number}: 해설 없음 (`exam solve`로 만들 수 있음)")
        r = rubrics.get(q.number)
        if opts.with_rubric:
            if r is None:
                w.warnings.append(f"문항 {q.number}: 루브릭 없음")
            else:
                _rubric(w, r)
    w.page_numbers()
    return w


def _steps(w: DocWriter, steps: list[SolutionStep]) -> None:
    for k, s in enumerate(steps, start=1):
        p = w.par(left_indent_cm=0.6, first_line_cm=-0.6)
        w.text(p, f"{k}) ")
        w.loose_text(p, s.explanation)
        if s.math:
            w.math(w.par(align="center"), s.math, display=True)


def _solution(w: DocWriter, sol: ScopedSolution) -> None:
    if sol.out_of_scope_note:
        w.text(w.par(), f"※ {sol.out_of_scope_note}", bold=True)
    _steps(w, sol.steps)
    p = w.par(space_before_pt=2)
    w.text(p, "따라서 답은 ", bold=True)
    w.math(p, sol.final_answer.latex)
    for k, alt in enumerate(sol.alternatives, start=1):
        p = w.par(space_before_pt=6)
        w.text(p, f"[다른 풀이{' ' + str(k) if len(sol.alternatives) > 1 else ''}] ", bold=True)
        w.loose_text(p, alt.summary)
        _steps(w, alt.steps)


def _rubric(w: DocWriter, r: Rubric) -> None:
    state = "승인됨" if r.status == "approved" else "초안 — 승인 전"
    w.heading(f"채점 기준 (v{r.version}, {state})", size_pt=w.fonts.size_pt + 1, space_before_pt=6)
    t = w.table(w.body, len(r.items) + 1, 3, w.column_cm)
    set_table_borders(t, "grid", size=4)
    widths = (0.12, 0.12, 0.76)
    for j, h in enumerate(("항목", "배점", "채점 기준")):
        cell = t.cell(0, j)
        w.text(w.par(cell, align="center", space_after_pt=0), h, bold=True)
        shade_cell(cell)
    for i, ri in enumerate(r.items, start=1):
        w.text(w.par(t.cell(i, 0), align="center", space_after_pt=0), ri.id)
        w.text(w.par(t.cell(i, 1), align="center", space_after_pt=0), f"{ri.points:g}")
        p = w.par(t.cell(i, 2), space_after_pt=0)
        w.loose_text(p, ri.criterion)
        p = w.par(t.cell(i, 2), space_after_pt=0)
        w.text(p, "예: ", size_pt=w.fonts.size_pt * 0.9)
        w.loose_text(p, ri.evidence_hint, size_pt=w.fonts.size_pt * 0.9)
    _column_widths(w, t, widths)
    for pc in r.partial:
        w.loose_text(w.par(), f"· 부분점수 {pc.item_id}: {pc.condition} → {pc.points:g}점")
    for alt in r.accepted_alternatives:
        w.loose_text(
            w.par(), f"· 인정하는 다른 풀이 ({', '.join(alt.item_ids)}): {alt.description}"
        )
    if r.never_deduct:
        w.text(w.par(space_before_pt=2), "감점하지 않는 경우", bold=True)
        for s in r.never_deduct:
            w.loose_text(w.par(left_indent_cm=0.4), f"· {s}")


def _column_widths(w: DocWriter, t: object, fractions: tuple[float, ...]) -> None:
    from docx.shared import Cm
    from docx.table import Table

    assert isinstance(t, Table)
    for j, f in enumerate(fractions):
        for cell in t.columns[j].cells:
            cell.width = Cm(w.column_cm * f)


# ---------------------------------------------------------------- 채점 리포트 (강사용)


def _status_mark(g: GradeResult | None) -> str:
    if g is None:
        return "—"
    # 기호는 맑은 고딕에 있는 것만 (⚠·✓는 없어 Word가 이모지 폰트로 바꿈)
    mark = " △" if g.status == "needs_review" else (" ○" if g.status == "teacher_confirmed" else "")
    return f"{g.total:g}{mark}"


def _summary_table(w: DocWriter, exam: Exam, store: ExamStore, students: list[str]) -> None:
    cols = len(exam.questions) + 2
    t = w.table(w.body, len(students) + 1, cols, w.column_cm)
    set_table_borders(t, "grid", size=4)
    heads = ["학생", *[f"{q.number} ({q.points:g})" for q in exam.questions], "합계"]
    for j, h in enumerate(heads):
        cell = t.cell(0, j)
        w.text(
            w.par(cell, align="center", space_after_pt=0),
            h,
            bold=True,
            size_pt=w.fonts.size_pt * 0.9,
        )
        shade_cell(cell)
    full = sum(q.points for q in exam.questions)
    for i, st in enumerate(students, start=1):
        w.text(w.par(t.cell(i, 0), align="center", space_after_pt=0), st)
        total = 0.0
        for j, q in enumerate(exam.questions, start=1):
            g = store.grade(st, q.number)
            total += g.total if g else 0
            w.text(w.par(t.cell(i, j), align="center", space_after_pt=0), _status_mark(g))
        w.text(
            w.par(t.cell(i, cols - 1), align="center", space_after_pt=0),
            f"{total:g}/{full:g}",
            bold=True,
        )


def _report_latex(exam: Exam, store: ExamStore, students: list[str]) -> list[str]:
    out: list[str] = []
    for st in students:
        for q in exam.questions:
            t = store.transcript(st, q.number)
            if t:
                for line in t.lines:
                    out += loose_latex(line.text)
                    if line.math:
                        out.append(line.math)
            g = store.grade(st, q.number)
            if g:
                for j in g.judgements:
                    for s in (j.reason, j.deduction_reason, j.evidence_quote):
                        out += loose_latex(s)
                out += loose_latex(g.comment)
                for f in g.flags:
                    out += loose_latex(f)
    return out


def build_grading_report(
    exam: Exam, store: ExamStore, fonts: FontSpec, students: list[str] | None = None
) -> DocWriter:
    w = DocWriter(fonts, strict_math=False)
    students = students or store.students()
    w.prepare_math([], _report_latex(exam, store, students))
    _title(w, f"{exam.title} 채점 리포트", f"{utcnow():%Y-%m-%d} · 학생 {len(students)}명")
    _summary_table(w, exam, store, students)
    p = w.par(space_before_pt=4)
    w.text(
        p,
        "△ 검토 필요(불확실·이중 채점 불일치)  ○ 강사 확인  — 미채점",
        size_pt=fonts.size_pt * 0.85,
    )

    for st in students:
        w.page_break()
        w.heading(f"학생 {st}", size_pt=fonts.size_pt + 4, space_before_pt=0)
        for q in exam.questions:
            g = store.grade(st, q.number)
            t = store.transcript(st, q.number)
            status = {
                "auto": "",
                "needs_review": " — 검토 필요",
                "teacher_confirmed": " — 강사 확인",
            }
            head = f"문항 {q.number}: " + (
                f"{g.total:g}/{g.max_points:g}{status[g.status]}" if g else "미채점"
            )
            w.heading(head, size_pt=fonts.size_pt + 1.5, space_before_pt=10)
            if t is not None:
                cell = w.boxed(w.body, w.column_cm)
                note = "" if t.confirmed else "  (강사 확인 전)"
                w.text(
                    w.par(cell, space_after_pt=1),
                    f"학생 답안 (판독문){note}",
                    bold=True,
                    size_pt=fonts.size_pt * 0.85,
                )
                for k, line in enumerate(t.lines, start=1):
                    p = w.par(cell, space_after_pt=0, left_indent_cm=0.7, first_line_cm=-0.7)
                    w.text(p, f"{k:>2}  ", size_pt=fonts.size_pt * 0.8)
                    if line.text:
                        w.loose_text(p, line.text, strike=line.struck)
                    if line.math:
                        if line.text:
                            w.text(p, " ")
                        w.math(p, line.math)
                    if line.uncertain:
                        w.text(
                            p,
                            f"  (불확실: {'; '.join(line.uncertain)})",
                            size_pt=fonts.size_pt * 0.8,
                        )
            if g is None:
                continue
            _judgement_table(w, g)
            p = w.par(space_before_pt=2)
            w.text(p, "첨삭: ", bold=True)
            w.loose_text(p, g.comment)
            for f in g.flags:
                w.loose_text(w.par(left_indent_cm=0.4), f"※ {f}")
            if g.disagreements:
                w.text(w.par(left_indent_cm=0.4), f"※ 이중 채점 차이: {', '.join(g.disagreements)}")
    w.page_numbers()
    return w


def _judgement_table(w: DocWriter, g: GradeResult) -> None:
    second = {j.rubric_item_id: j for j in g.second_opinion or []}
    t = w.table(w.body, len(g.judgements) + 1, 4, w.column_cm)
    set_table_borders(t, "grid", size=4)
    for j, h in enumerate(("항목", "점수", "근거 (답안 인용)", "판정")):
        cell = t.cell(0, j)
        w.text(
            w.par(cell, align="center", space_after_pt=0),
            h,
            bold=True,
            size_pt=w.fonts.size_pt * 0.9,
        )
        shade_cell(cell)
    small = w.fonts.size_pt * 0.9
    for i, jd in enumerate(g.judgements, start=1):
        w.text(
            w.par(t.cell(i, 0), align="center", space_after_pt=0), jd.rubric_item_id, size_pt=small
        )
        p = w.par(t.cell(i, 1), align="center", space_after_pt=0)
        w.text(p, f"{jd.awarded:g}", size_pt=small)
        other = second.get(jd.rubric_item_id)
        if other is not None and other.awarded != jd.awarded:
            w.text(p, f" (재채점 {other.awarded:g})", size_pt=small * 0.9)
        w.loose_text(w.par(t.cell(i, 2), space_after_pt=0), jd.evidence_quote or "—", size_pt=small)
        p = w.par(t.cell(i, 3), space_after_pt=0)
        w.loose_text(p, jd.reason, size_pt=small)
        if jd.deduction_reason:
            p = w.par(t.cell(i, 3), space_after_pt=0)
            w.text(p, "감점: ", bold=True, size_pt=small)
            w.loose_text(p, jd.deduction_reason, size_pt=small)
    _column_widths(w, t, (0.1, 0.12, 0.38, 0.4))


# ---------------------------------------------------------------- 성적표 (학생용)


def build_score_sheets(
    exam: Exam, store: ExamStore, fonts: FontSpec, students: list[str] | None = None
) -> DocWriter:
    """학생에게 줄 성적표: 학생마다 한 쪽, 문항별 점수·첨삭·감점 내용. 내부 표시(△·※)는 빼고."""
    w = DocWriter(fonts, strict_math=False)
    students = students or store.students()
    w.prepare_math([], _report_latex(exam, store, students))
    full = sum(q.points for q in exam.questions)
    for n, st in enumerate(students):
        if n:
            w.page_break()
        _title(w, f"{exam.title} 성적표")
        p = w.par(align="right", space_after_pt=6)
        w.text(p, f"학생: {st}", bold=True)
        grades = {q.number: store.grade(st, q.number) for q in exam.questions}
        t = w.table(w.body, len(exam.questions) + 2, 4, w.column_cm)
        set_table_borders(t, "grid", size=4)
        for j, h in enumerate(("문항", "배점", "점수", "첨삭")):
            cell = t.cell(0, j)
            w.text(w.par(cell, align="center", space_after_pt=0), h, bold=True)
            shade_cell(cell)
        total = 0.0
        for i, q in enumerate(exam.questions, start=1):
            g = grades[q.number]
            w.text(w.par(t.cell(i, 0), align="center", space_after_pt=0), q.number)
            w.text(w.par(t.cell(i, 1), align="center", space_after_pt=0), f"{q.points:g}")
            w.text(
                w.par(t.cell(i, 2), align="center", space_after_pt=0), f"{g.total:g}" if g else "—"
            )
            if g:
                total += g.total
                w.loose_text(
                    w.par(t.cell(i, 3), space_after_pt=0), g.comment, size_pt=fonts.size_pt * 0.9
                )
        last = len(exam.questions) + 1
        w.text(w.par(t.cell(last, 0), align="center", space_after_pt=0), "합계", bold=True)
        w.text(w.par(t.cell(last, 1), align="center", space_after_pt=0), f"{full:g}", bold=True)
        w.text(w.par(t.cell(last, 2), align="center", space_after_pt=0), f"{total:g}", bold=True)
        _column_widths(w, t, (0.12, 0.12, 0.12, 0.64))

        deductions = [
            (q.number, jd)
            for q in exam.questions
            if (g := grades[q.number]) is not None
            for jd in g.judgements
            if jd.deduction_reason
        ]
        if deductions:
            w.heading("감점 내용", size_pt=fonts.size_pt + 1, space_before_pt=10)
            for number, jd in deductions:
                p = w.par(left_indent_cm=0.4)
                w.text(p, f"문항 {number} ({jd.rubric_item_id}): ", bold=True)
                w.loose_text(p, jd.deduction_reason or "")
    w.page_numbers()
    return w
