"""채점 파이프라인 자료를 프롬프트용 텍스트로.

수식은 `\\( … \\)`로 감싸 문장과 구분한다 (프롬프트 안에서만; 저장 데이터에는 구분자 없음).
"""

from __future__ import annotations

from dawoni.context.assembler import Shrinkable
from dawoni.models.content import (
    Block,
    BogiBlock,
    BoxBlock,
    ConditionsBlock,
    DisplayMathBlock,
    FigureBlock,
    MathRun,
    ParagraphBlock,
    TableBlock,
    TextRun,
)
from dawoni.models.grading import Rubric, RubricDraft, SolutionDraft, SolutionStep, Transcript
from dawoni.models.item import ItemContent
from dawoni.models.scope import ScopeProfile

_CIRCLED = "①②③④⑤⑥⑦⑧⑨"


def runs_text(runs: list[TextRun | MathRun]) -> str:
    return "".join(r.v if isinstance(r, TextRun) else f"\\({r.v}\\)" for r in runs)


def blocks_text(blocks: list[Block]) -> str:
    out: list[str] = []
    for b in blocks:
        match b:
            case ParagraphBlock():
                out.append(runs_text(b.runs))
            case DisplayMathBlock():
                out.append(f"\\[{b.latex}\\]")
            case FigureBlock():
                out.append(f"[그림 {b.figure_id}]")
            case BogiBlock():
                out.append(f"<{b.title}>")
                out += [f"  {it.label}. {runs_text(it.runs)}" for it in b.items]
            case ConditionsBlock():
                out += [f"  {it.label} {runs_text(it.runs)}" for it in b.items]
            case BoxBlock():
                out.append(f"[상자{': ' + b.title if b.title else ''}]")
                out += [f"  {runs_text(p)}" for p in b.paragraphs]
            case TableBlock():
                out += [" | ".join(runs_text(cell) for cell in row) for row in b.rows]
    return "\n".join(out)


def question_text(number: str, points: float, item: ItemContent) -> str:
    lines = [f"[문항 {number}] ({points:g}점)", blocks_text(item.stem)]
    for c in item.choices:
        mark = _CIRCLED[c.label - 1] if c.label <= len(_CIRCLED) else f"({c.label})"
        body = runs_text(c.runs) if c.runs else (c.note or "")
        lines.append(f"{mark} {body}" + (f" [그림 {c.figure_id}]" if c.figure_id else ""))
    for f in item.figures:
        if f.caption:
            lines.append(f"[그림 {f.id} 설명] {f.caption}")
    return "\n".join(lines)


def scope_layer(scope: ScopeProfile, *, with_extensions: bool = False) -> Shrinkable:
    """범위 프로필: 자세한 판본(요약 포함)과 짧은 판본(이름만)."""

    def build(detail: bool) -> str:
        lines = [f"수준: {scope.level}", "", "허용 개념:"]
        for c in scope.concepts:
            lines.append(f"- {c.id} {c.name}" + (f": {c.summary}" if detail else ""))
        if scope.skills:
            lines += ["", "허용 기법:"]
            for s in scope.skills:
                lines.append(f"- {s.id} {s.name}" + (f": {s.description}" if detail else ""))
        if with_extensions and scope.extensions:
            lines += ["", "확장 개념 (제시문으로 제공):"]
            for x in scope.extensions:
                lines.append(f"- {x.id} {x.name}: {x.definition}")
        if scope.forbidden:
            lines += ["", "쓰지 않는 기법 (이 수준에서 배우지 않음 — 쓰지도, 요구하지도 않는다):"]
            lines += [
                f"- {f.name}" + (f" ({f.reason})" if detail and f.reason else "")
                for f in scope.forbidden
            ]
        if scope.conventions:
            lines += ["", "답안 관례:"] + [f"- {c}" for c in scope.conventions]
        return "\n".join(lines)

    return Shrinkable([build(True), build(False)])


def _step_line(n: int, s: SolutionStep) -> str:
    math = f"  식: \\({s.math}\\)" if s.math else ""
    return f"{n}. {s.explanation}{math}  [사용: {', '.join(s.uses)}]"


def solution_text(sol: SolutionDraft) -> str:
    lines = ["[모범 해설]"] + [_step_line(i, s) for i, s in enumerate(sol.steps, start=1)]
    lines.append(f"최종 답: \\({sol.final_answer.latex}\\)")
    for k, alt in enumerate(sol.alternatives, start=1):
        lines.append(f"[다른 풀이 {k}] {alt.summary}")
        lines += [_step_line(i, s) for i, s in enumerate(alt.steps, start=1)]
    return "\n".join(lines)


def rubric_text(rubric: RubricDraft | Rubric) -> str:
    lines = ["[루브릭]"]
    for it in rubric.items:
        lines.append(f"- {it.id} ({it.points:g}점) {it.criterion}  (예: {it.evidence_hint})")
    if rubric.partial:
        lines.append("부분점수:")
        lines += [f"- {p.item_id}: {p.condition} → {p.points:g}점" for p in rubric.partial]
    if rubric.accepted_alternatives:
        lines.append("인정하는 다른 풀이:")
        lines += [
            f"- {a.description} → {', '.join(a.item_ids)} 점수 인정"
            for a in rubric.accepted_alternatives
        ]
    if rubric.never_deduct:
        lines.append("감점하지 말 것:")
        lines += [f"- {d}" for d in rubric.never_deduct]
    lines.append(f"배점 합계: {rubric.total:g}점")
    return "\n".join(lines)


def transcript_text(t: Transcript) -> str:
    if t.is_blank:
        return "(학생 답안이 비어 있음)"
    lines = ["[학생 답안] (줄 번호는 참고용 — 인용할 때는 빼고 적힌 그대로)"]
    for n, line in enumerate(t.lines, start=1):
        body = line.joined
        if line.struck:
            body = f"(지운 흔적) {body}"
        if line.uncertain:
            body += f"  [?: {', '.join(line.uncertain)}]"
        lines.append(f"[{n}] {body}")
    return "\n".join(lines)
