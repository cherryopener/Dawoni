"""강사의 수정에서 배우기: 저장 API가 `learn`을 받으면, 바뀐 부분만 모아 배우기 작업을 띄운다.

저장 자체는 기다리지 않는다(응답 모양도 그대로). 배운 것은 '메모리 · 스킬' 화면에 승인 대기로 뜬다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from dawoni.context.learn import Correction, diff_text, learn, propose
from dawoni.models.grading import GradeResult, Rubric, Transcript
from dawoni.models.job import Job
from dawoni.server.jobs import JobContext
from dawoni.server.state import AppState


class LearnFields(BaseModel):
    """저장 요청에 붙이는 '이 수정에서 배우기'."""

    learn: bool = False
    learn_note: str = ""
    """무엇이 틀렸는지 강사의 한마디 (선택)."""


def submit(st: AppState, c: Correction, by: str | None) -> Job:
    svc = st.services()

    def run(ctx: JobContext) -> dict[str, Any]:
        ctx.progress(0, 1, "다음 작업에 쓸 것을 정리하는 중")
        res = learn(svc.engine, c, rules=svc.rules, memory=svc.memory, skills=svc.skills)
        saved = propose(res.value, c, memory=svc.memory, skills=svc.skills, by=by)
        ctx.progress(1, 1, "제안 완료 — 승인하면 적용됩니다" if saved else "배울 것이 없었습니다")
        return {
            "lesson": res.value.model_dump(mode="json"),
            "saved_id": saved.id if saved else None,
        }

    return st.jobs.submit("learn_lesson", run, {"note": c.note[:80]})


def from_edit(st: AppState, body: LearnFields, c: Correction, by: str | None) -> Job | None:
    """`learn`을 켰고 실제로 바뀐 것이 있을 때만 배우기 작업을 띄운다."""
    if not body.learn or not (c.after.strip() or body.learn_note.strip()):
        return None
    note = "\n".join(x for x in (body.learn_note.strip(), c.note) if x)
    return submit(st, Correction(note=note, before=c.before, after=c.after, target=c.target,
                                 situation=c.situation), by)  # fmt: skip


def cases_from_review(
    st: AppState,
    store: Any,
    number: str,
    student: str,
    before: GradeResult,
    after: GradeResult,
    rubric: Rubric,
    notes: dict[str, str],
    by: str | None,
) -> Job | None:
    """채점 검토에서 강사가 바꾼 소문항 점수 → 채점 사례집 (`dawoni.casebook`, 2026-11-18).
    바뀐 소문항마다 AI 정리 1번 (가벼운 도우미). 바뀐 것이 없으면 None.

    예전에는 메모리 제안(한 문장, 작업마다 600자 상한)으로 갔다 — 사례집은 상한 없이 쌓이고 문항에
    맞는 것만 꺼낸다."""
    from dawoni import casebook

    changed = casebook.changes(number, before, after, rubric, notes)
    if not changed:
        return None
    svc = st.services()

    def run(ctx: JobContext) -> dict[str, Any]:
        done: list[dict[str, Any]] = []
        for k, ch in enumerate(changed):
            ctx.progress(k, len(changed), f"채점 사례 정리 중 ({ch.label})")
            r = casebook.record(svc, store, number, student, ch, by=by)
            done.append({
                "part": ch.label, "case": r.case.id if r.case else None,
                "title": r.case.title if r.case else None, "added": r.added, "reason": r.reason,
            })  # fmt: skip
        ctx.progress(len(changed), len(changed), "채점 사례집에 정리함")
        return {"cases": done, "exam": store.exam_id}

    return st.jobs.submit("casebook", run, {"exam": store.exam_id, "question": number})


def grade_correction(
    before: GradeResult, after: GradeResult, rubric: Rubric, situation: str
) -> Correction:
    """채점 — 강사가 점수를 바꾸거나 이유를 적은 루브릭 항목만, 기준과 함께."""
    items = {i.id: i for i in rubric.items}
    old = {j.rubric_item_id: j for j in before.judgements}
    b: list[str] = []
    a: list[str] = []
    for j in after.judgements:
        o = old.get(j.rubric_item_id)
        why = after.teacher_item_notes.get(j.rubric_item_id, "")
        if o is not None and o.awarded == j.awarded and not why:
            continue
        it = items.get(j.rubric_item_id)
        head = f"{j.rubric_item_id}" + (f" ({it.points:g}점, 기준: {it.criterion})" if it else "")
        if o is not None:
            b.append(f"{head}: AI {o.awarded:g}점 — {o.reason}")
        a.append(f"{head}: 강사 {j.awarded:g}점" + (f" — {why}" if why else ""))
    parts = [f"소문항 {k}: {v}" for k, v in after.teacher_part_notes.items() if v.strip()]
    return Correction(
        note="\n".join([*([after.teacher_note] if after.teacher_note else []), *parts]),
        before="\n".join(b),
        after="\n".join(a),
        target="grade",
        situation=situation,
    )


def transcript_correction(before: Transcript, after: Transcript, situation: str) -> Correction:
    """손글씨 판독 — 고친 줄만."""

    def view(t: Transcript) -> list[dict[str, Any]]:
        return [{"글": ln.text, "식": ln.math, "지움": ln.struck or None} for ln in t.lines]

    b, a = diff_text(view(before), view(after))
    return Correction(before=b, after=a, target="transcribe", situation=situation)
