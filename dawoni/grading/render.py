"""채점 파이프라인 자료를 프롬프트용 텍스트로.

수식은 `\\( … \\)`로 감싸 문장과 구분한다 (프롬프트 안에서만; 저장 데이터에는 구분자 없음).
"""

from __future__ import annotations

from dawoni.context.assembler import Shrinkable
from dawoni.models.answer_key import AnswerKey, KeyPart
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
from dawoni.models.grading import (
    ExamPart,
    ExamQuestion,
    Rubric,
    RubricDraft,
    RubricItem,
    SolutionDraft,
    SolutionStep,
    Transcript,
)
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


KIND_TEXT = {
    "essay": "서술형 — 풀이 과정까지 채점",
    "short_answer": "단답형 — 최종 답만 채점",
    None: "유형 표시 없음",
}


def parts_text(parts: list[ExamPart] | tuple[ExamPart, ...]) -> str:
    return "소문항 배점:\n" + "\n".join(
        f"- {p.label}: {p.points:g}점 ({KIND_TEXT[p.kind]})" for p in parts
    )


def question_text(
    number: str,
    points: float,
    item: ItemContent,
    parts: list[ExamPart] | tuple[ExamPart, ...] = (),
) -> str:
    lines = [f"[문항 {number}] ({points:g}점)", blocks_text(item.stem)]
    for c in item.choices:
        mark = _CIRCLED[c.label - 1] if c.label <= len(_CIRCLED) else f"({c.label})"
        body = runs_text(c.runs) if c.runs else (c.note or "")
        lines.append(f"{mark} {body}" + (f" [그림 {c.figure_id}]" if c.figure_id else ""))
    for f in item.figures:
        if f.caption:
            lines.append(f"[그림 {f.id} 설명] {f.caption}")
    if parts:
        lines.append(parts_text(parts))
    return "\n".join(lines)


def exam_question_text(q: ExamQuestion) -> str:
    return question_text(q.number, q.points, q.item, q.parts)


def credit_text(p: KeyPart) -> str:
    match p.credit_policy:
        case "listed":
            body = " / ".join(f"{i}) {c.what} {c.points:g}점" for i, c in enumerate(p.credits, 1))
        case "none":
            body = "없음 (맞으면 만점, 아니면 0점)"
        case "range":
            body = f"범위 {p.credit_min or 0:g}~{p.credit_max or 0:g}점 (정도에 따라)"
        case "unspecified":
            body = "표기 없음"
    raw = f"  (원문: {p.credit_text})" if p.credit_text else ""
    return body + raw


def key_text(key: AnswerKey, q: ExamQuestion) -> str:
    lines = [f"[강사 해설지] ({key.source}{', 강사 확인' if key.confirmed else ', 강사 확인 전'})"]
    if key.doubtful:
        lines.append(
            f"(두 번 읽은 결과가 다른 소문항: {', '.join(key.doubtful)} — 원본과 확인할 것)"
        )
    for p in key.parts:
        part = q.part(p.label)
        head = f"소문항 {p.label}" if p.label else "문항 전체"
        if part:
            head += f" ({part.points:g}점, {KIND_TEXT[part.kind]})"
        lines.append(head)
        lines.append(f"  답: {p.answer or '(해설지에 따로 없음)'}")
        if p.solution:
            lines.append(f"  해설: {p.solution}")
        lines.append(f"  부분 점수: {credit_text(p)}")
        lines += [f"  0점 조건: {z}" for z in p.zero_if]
    lines += [f"해설지 메모: {n}" for n in key.notes]
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


def solution_text(sol: SolutionDraft, title: str = "[모범 해설]") -> str:
    lines = [title] + [_step_line(i, s) for i, s in enumerate(sol.steps, start=1)]
    lines.append(f"최종 답: \\({sol.final_answer.latex}\\)")
    lines += [f"  {a.label} 답: \\({a.latex}\\)" for a in sol.part_answers]
    for k, alt in enumerate(sol.alternatives, start=1):
        lines.append(f"[다른 풀이 {k}] {alt.summary}")
        lines += [_step_line(i, s) for i, s in enumerate(alt.steps, start=1)]
    return "\n".join(lines)


def _item_line(it: RubricItem) -> str:
    return f"- {it.id} ({it.points:g}점) {it.criterion}  (예: {it.evidence_hint})"


def rubric_text(
    rubric: RubricDraft | Rubric, parts: list[ExamPart] | tuple[ExamPart, ...] = ()
) -> str:
    """`parts`: 시험 문항의 소문항 (유형·배점). 루브릭 항목을 소문항별로 묶어 보여 준다."""
    lines = ["[루브릭]"]
    if isinstance(rubric, Rubric) and not rubric.based_on_key:
        lines.append("(강사 해설지 없이 AI 풀이로 만든 루브릭)")
    answers = {c.part: c.key_answer for c in rubric.checks}
    labels = [p.label for p in parts] or list(dict.fromkeys(i.part or "" for i in rubric.items))
    for label in labels:
        items = rubric.part_items(label)
        part = next((p for p in parts if p.label == label), None)
        if label or part:
            head = f"소문항 {label}"
            if part:
                head += f" ({part.points:g}점, {KIND_TEXT[part.kind]})"
            lines.append(head)
        if label in answers:
            lines.append(f"  해설지 정답: {answers[label]}")
        lines += [("  " if label else "") + _item_line(it) for it in items]
    rest = [it for it in rubric.items if (it.part or "") not in labels]
    lines += [_item_line(it) for it in rest]
    if rubric.partial:
        lines.append("부분점수:")
        lines += [
            f"- {p.item_id}: {p.condition} → {p.points:g}점" + ("" if p.from_key else " (AI 제안)")
            for p in rubric.partial
        ]
    if rubric.accepted_alternatives:
        lines.append("인정하는 다른 풀이:")
        lines += [
            f"- {a.description} → {', '.join(a.item_ids)} 점수 인정"
            for a in rubric.accepted_alternatives
        ]
    accepted = [c for c in rubric.candidates if c.status == "accepted"]
    proposed = [c for c in rubric.candidates if c.status == "proposed"]
    rejected = [c for c in rubric.candidates if c.status == "rejected"]
    if accepted:
        lines.append("강사가 인정한 다른 정답 (해설지 정답과 같은 점수):")
        lines += [
            f"- {c.id} 소문항 {c.part or '전체'}: {c.answer} → {', '.join(c.item_ids)}"
            for c in accepted
        ]
    if proposed:
        lines.append(
            "강사 미확인 정답 후보 (이 답이면 점수를 주지 말고 matched_candidates에 ID를 적을 것):"
        )
        lines += [f"- {c.id} 소문항 {c.part or '전체'}: {c.answer}" for c in proposed]
    if rejected:
        lines.append("강사가 오답으로 정한 답:")
        lines += [f"- 소문항 {c.part or '전체'}: {c.answer}" for c in rejected]
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
        body = (f"{line.part} " if line.part else "") + line.joined
        if line.struck:
            body = f"(지운 흔적) {body}"
        if line.uncertain:
            body += f"  [?: {', '.join(line.uncertain)}]"
        lines.append(f"[{n}] {body}")
    return "\n".join(lines)
