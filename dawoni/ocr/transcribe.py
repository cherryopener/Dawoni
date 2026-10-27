"""학생 답안지 판독: 페이지 이미지 → 문항별 판독문 (강사 확인 전 상태).

    페이지 준비 → AI 판독(원문 그대로) → (선택) 교차 판독 → 문항별로 나눔 → 저장(미확인)

판독문은 `confirmed=False`로 저장된다. 강사가 원본과 나란히 확인해야 채점할 수 있다.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

from dawoni.config import AgentName
from dawoni.context import ContextEngine, TaskDef, TaskInput
from dawoni.grading.pipeline import SecondOpinion
from dawoni.grading.render import exam_question_text
from dawoni.models.grading import (
    Exam,
    TranscribedLine,
    Transcript,
    TranscriptionDraft,
    TranscriptLine,
)
from dawoni.ocr.crosscheck import cross_check

TRANSCRIBE: TaskDef[TranscriptionDraft] = TaskDef(
    name="transcribe_answer",
    template="transcribe_answer.md.j2",
    output=TranscriptionDraft,
    rule_target="transcribe",
    timeout_s=900,
)


def check_transcription(d: TranscriptionDraft, numbers: set[str], n_pages: int) -> list[str]:
    problems: list[str] = []
    for i, line in enumerate(d.lines, start=1):
        if line.question_number is not None and line.question_number not in numbers:
            problems.append(
                f"{i}번째 줄의 question_number '{line.question_number}'는 이 시험의 문항이 아니다. "
                f"{sorted(numbers)} 중 하나 또는 null"
            )
        if line.page > n_pages:
            problems.append(f"{i}번째 줄의 page {line.page}는 첨부 {n_pages}장을 넘는다")
        if not line.text.strip() and not (line.math or "").strip():
            problems.append(f"{i}번째 줄이 비어 있다 (text나 math 중 하나는 필요)")
        if line.math and "$" in line.math:
            problems.append(f"{i}번째 줄의 math에 $ 기호를 쓰지 말 것")
    return problems


@dataclass
class TranscriptionOutcome:
    transcripts: dict[str, Transcript]
    """문항 번호 → 판독문 (답이 있는 문항만)."""
    unassigned: list[TranscribedLine] = field(default_factory=lambda: list[TranscribedLine]())
    notes: list[str] = field(default_factory=lambda: list[str]())
    agent: str | None = None
    model: str | None = None


def _to_line(x: TranscribedLine) -> TranscriptLine:
    return TranscriptLine(
        text=x.text,
        math=x.math,
        confidence=x.confidence,
        uncertain=list(x.uncertain),
        struck=x.struck,
    )


def _run(
    engine: ContextEngine,
    exam: Exam,
    level: str,
    pages: list[Path],
    hints: str | None,
    agent: AgentName | None,
    model: str | None,
) -> tuple[TranscriptionDraft, str, str | None]:
    numbers = {q.number for q in exam.questions}

    def check(d: TranscriptionDraft) -> list[str]:
        return check_transcription(d, numbers, len(pages))

    material = "\n\n".join(exam_question_text(q) for q in exam.questions)
    if hints:
        material += "\n\n" + hints
    res = engine.run(
        dataclasses.replace(TRANSCRIBE, validator=check),
        TaskInput(
            variables={"level": level, "questions": exam.questions, "n_pages": len(pages)},
            material=material,
            attachments=tuple(pages),
        ),
        agent=agent,
        model=model,
    )
    return res.value, res.agent, res.model


def transcribe_student(
    engine: ContextEngine,
    exam: Exam,
    level: str,
    student_id: str,
    pages: list[Path],
    *,
    page_refs: list[str],
    cross: SecondOpinion | None = None,
    hints: str | None = None,
    agent: AgentName | None = None,
    model: str | None = None,
) -> TranscriptionOutcome:
    first, used_agent, used_model = _run(engine, exam, level, pages, hints, agent, model)
    notes = list(first.notes)
    lines = list(first.lines)
    by_q_second: dict[str | None, list[TranscribedLine]] = {}
    if cross is not None:
        second, _, second_model = _run(engine, exam, level, pages, hints, cross.agent, cross.model)
        for x in second.lines:
            by_q_second.setdefault(x.question_number, []).append(x)
        notes.append(f"교차 판독: {second_model or cross.model or '같은 설정'}")

    # 문항이 하나뿐이면 번호 없는 줄은 그 문항의 답으로 본다
    only = exam.questions[0].number if len(exam.questions) == 1 else None
    by_q: dict[str | None, list[TranscribedLine]] = {}
    for x in lines:
        by_q.setdefault(x.question_number or only, []).append(x)

    transcripts: dict[str, Transcript] = {}
    for q in exam.questions:
        q_lines = by_q.get(q.number, [])
        agreement: float | None = None
        q_notes: list[str] = []
        if cross is not None:
            cc = cross_check(q_lines, by_q_second.get(q.number, []))
            q_lines, agreement = cc.lines, cc.agreement
            q_notes += [f"교차 판독에만 있는 줄: {t}" for t in cc.only_in_second]
        if not q_lines and not q_notes:
            continue
        transcripts[q.number] = Transcript(
            student_id=student_id,
            question_number=q.number,
            lines=[_to_line(x) for x in q_lines],
            source="ocr",
            confirmed=False,
            pages=page_refs,
            cross_checked=cross is not None,
            agreement=agreement,
            notes=q_notes,
        )
    return TranscriptionOutcome(
        transcripts=transcripts,
        unassigned=by_q.get(None, []),
        notes=notes,
        agent=used_agent,
        model=used_model,
    )
