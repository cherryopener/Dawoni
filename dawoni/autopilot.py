# pyright: basic, reportMissingTypeStubs=false
"""한 번에 채점 (2026-11-18 사용자 요청: "채팅에서 파일을 올리고 명령하듯이, 버튼 한두 개로").

    시험지(해설 포함) · 학생 답안지 PDF · (명단) → [채점 시작] → 점수표 + 채점 답안지 PDF

1. 루브릭: 없으면 만들고(해설지 넓히기), **모두 먼저 승인**한다 — AI가 넓힌 표현은 받아들이고,
   해설지와 문제가 다르다고 본 곳은 **강사의 해설지를 따른다**(강사가 정한 부분 점수 규칙이 기준).
   독립 검산이 맞다고 한 다른 정답 후보만 받아들이고, 나머지 후보는 강사에게 묻는다(그 답을 쓴
   학생은 검토 필요로 남는다).
2. 답안지: 학생 한 명의 쪽 수를 묻지 않는다 — 표지끼리 닮은 것으로 찾는다(`pages_per_student`).
   이름을 못 읽었거나 같은 반에 같은 이름이 있어도 멈추지 않는다(나중에 고친다).
3. 채점: 모든 학생 × 문항. `[판독불가]`가 남아도 채점하고 "검토 필요"로 둔다 (원본을 보고 채워
   확인하면 다시 채점).
4. 출력: 엑셀 점수표, 반마다 채점 답안지 PDF (`export.red_pen`).

강사에게 물을 것(`Ask`)은 정말 애매한 것만: 이름, 판독불가, 검산이 엇갈린 정답 후보, 승인할 수 없는
루브릭(정답이 없는 소문항). 채점 결과의 "검토 필요"는 물음이 아니라 '확인 권장' 숫자로만 보인다.
진행 상황·물을 것은 시험 폴더 `autopilot.json`에 둔다 (결과 화면이 읽는다).
"""

from __future__ import annotations

import itertools
import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pymupdf
from PIL import Image
from rapidfuzz.distance import Levenshtein

from dawoni.agents import AgentError
from dawoni.context import TaskFailed
from dawoni.fsio import read_json, write_json_atomic
from dawoni.grading.pipeline import (
    PipelineError,
    approve_rubric,
    choose_key_conflicts,
    decide_candidates,
)
from dawoni.grading.store import ExamStore
from dawoni.models.base import utcnow
from dawoni.models.grading import KeyChoice
from dawoni.parallel import stops_all
from dawoni.services import Services

log = logging.getLogger(__name__)
Progress = Callable[[int, int, str], None]
FILE = "autopilot.json"

AskKind = Literal["name", "unreadable", "candidate", "rubric", "pages"]


def _noop(done: int, total: int, message: str) -> None:
    pass


@dataclass
class Ask:
    """강사에게 물을 것 — 채점은 이미 끝났고, 답하면 그 학생·문항만 다시."""

    kind: AskKind
    text: str
    student: str | None = None
    question: str | None = None


@dataclass
class AutoState:
    state: Literal["running", "done", "failed"] = "running"
    stage: str = ""
    started_at: str = ""
    finished_at: str | None = None
    approved: list[str] = field(default_factory=lambda: list[str]())
    """이번에 승인한 문항."""
    decisions: list[str] = field(default_factory=lambda: list[str]())
    """자동으로 정한 것 (넓힌 표현 받아들임, 해설지 기준을 따름 …) — 강사가 볼 수 있게."""
    files: list[str] = field(default_factory=lambda: list[str]())
    students: int = 0
    graded: int = 0
    review: int = 0
    asks: list[Ask] = field(default_factory=lambda: list[Ask]())
    warnings: list[str] = field(default_factory=lambda: list[str]())
    error: str | None = None


def load_state(store: ExamStore) -> AutoState | None:
    path = store.root / FILE
    if not path.is_file():
        return None
    raw: dict[str, Any] = read_json(path)
    asks = [Ask(**a) for a in raw.pop("asks", [])]
    known = {k: v for k, v in raw.items() if k in AutoState.__dataclass_fields__}
    return AutoState(**known, asks=asks)


def save_state(store: ExamStore, s: AutoState) -> None:
    write_json_atomic(store.root / FILE, asdict(s))


# ---------------------------------------------------------------- 1. 루브릭


def approve_all(
    svc: Services, store: ExamStore, *, by: str | None, progress: Progress = _noop
) -> tuple[list[str], list[str], list[Ask]]:
    """루브릭이 없으면 만들고 모두 승인한다. (승인한 문항, 자동으로 정한 것, 물을 것)."""
    from dawoni.grading import workflow

    exam = store.exam()
    approved: list[str] = []
    decided: list[str] = []
    asks: list[Ask] = []
    missing = [q.number for q in exam.questions if store.rubric(q.number) is None]
    for k, number in enumerate(missing):  # 문항마다 — 하나가 안 돼도 나머지는 만든다
        progress(k, len(missing), f"루브릭 만드는 중 ({number}번)")
        try:
            workflow.rubric_questions(svc, store, [number], by=by, progress=progress)
        except (PipelineError, TaskFailed, AgentError) as e:
            if isinstance(e, AgentError) and stops_all(e):
                raise
            asks.append(Ask("rubric", f"{number}번 루브릭을 만들지 못함: {e}", question=number))
    for q in exam.questions:
        r = store.rubric(q.number)
        if r is None:
            continue  # 만들지 못한 이유는 위에서 물음으로
        if r.status == "approved":
            continue
        alts = [a.id for a in r.accepted_alternatives if a.id and a.status == "proposed"]
        good = [c.id for c in r.candidates if c.status == "proposed" and c.check == "valid"]
        for c in r.candidates:
            if c.status == "proposed" and c.check != "valid":
                text = (
                    f"{q.number}번 다른 정답 후보 {c.id} '{c.answer}' — 정답으로 인정할까요? "
                    f"(검산: {c.check or '안 함'}) 그 답을 쓴 학생은 검토 필요로 둡니다"
                )
                asks.append(Ask("candidate", text, question=q.number))
        if alts or good:
            r = decide_candidates(r, [*alts, *good], [])
            decided.append(
                f"{q.number}번: AI가 넓힌 표현·검산된 정답 {', '.join([*alts, *good])}을 인정"
            )
        choices: dict[str, KeyChoice] = {}
        for c in r.key_conflicts:
            if c.id and c.choice is None:
                choices[c.id] = "key" if c.switchable else "question"
        if choices:
            r = choose_key_conflicts(r, choices)
            follow = [k for k, v in choices.items() if v == "key"]
            if follow:
                decided.append(
                    f"{q.number}번: 해설지와 문제가 다른 곳 {', '.join(follow)}은 해설지대로"
                )
        try:
            r = approve_rubric(r, by)
        except PipelineError as e:
            store.save_rubric(r)
            asks.append(Ask("rubric", str(e), question=q.number))
            continue
        store.save_rubric(r)
        approved.append(q.number)
    return approved, decided, asks


# ---------------------------------------------------------------- 2. 답안지


COVER_SIMILAR = 0.6
"""첫 쪽(표지)과 이만큼 닮은 쪽을 표지로 본다 (실측 2026-11-18: 표지끼리 0.90~0.99, 답안 쪽
−0.1~0.15)."""


def pages_per_student(pdf: Path, fallback: int = 2) -> tuple[int, str | None]:
    """학생 한 명의 쪽 수 (표지 포함). 표지끼리 닮은 것으로 찾는다 — 표지 간격이 고르지 않으면 가장
    흔한 간격과 알림."""
    doc = pymupdf.open(pdf)
    n = doc.page_count
    thumbs: list[np.ndarray] = []
    for page in doc:
        pix = page.get_pixmap(dpi=12, colorspace=pymupdf.csGRAY)
        im = Image.frombytes("L", (pix.width, pix.height), pix.samples).resize((40, 56))
        a = np.asarray(im, dtype=np.float64)
        thumbs.append(((a - a.mean()) / (a.std() + 1e-6)).ravel())
    covers = [
        i for i, t in enumerate(thumbs) if float(np.dot(thumbs[0], t)) / t.size > COVER_SIMILAR
    ]
    gaps = Counter(b - a for a, b in itertools.pairwise(covers))
    if gaps:
        per, _ = gaps.most_common(1)[0]
        if n % per == 0:
            note = None
            if len(gaps) > 1:
                note = f"{pdf.name}: 학생마다 쪽 수가 다른 곳이 있음 — {per}쪽씩 나눔"
            return per, note
    if n % fallback == 0:
        return fallback, f"{pdf.name}: 표지를 찾지 못해 {fallback}쪽씩 나눔"
    return n, f"{pdf.name}: 표지를 찾지 못해 한 학생으로 봄"


def _class_key(s: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s or "").upper()


def _close(a: str, b: str) -> bool:
    return a.startswith(b) or b.startswith(a) or Levenshtein.distance(a, b) <= 2


def unify_classes(store: ExamStore, pdf_name: str) -> list[str]:
    """한 답안지 PDF는 보통 한 반이다 — 표지 손글씨 반 이름이 조금씩 다르게 읽힌 것(MSS1-J, MSS1J,
    MSS-1, MSS, TSS1 …; 2026-11-18 실측 18명이 반 6개로 갈라져 채점 답안지 PDF가 6개)을 그 묶음에서
    가장 많은 반으로 맞춘다. 명부와 이어진 학생, 많이 다른 반 이름은 그대로. 읽은 그대로는
    `uncertain`에."""
    recs = [
        r for r in store.student_records()
        if r.source.split(" ")[0] == pdf_name and r.roster_id is None
    ]  # fmt: skip
    keys = Counter(k for r in recs if (k := _class_key(r.class_name)))
    if not keys:
        return []
    top, n = keys.most_common(1)[0]
    if n < max(2, 0.4 * len(recs)):
        return []
    name = Counter(r.class_name for r in recs if _class_key(r.class_name) == top).most_common(1)
    klass = name[0][0]
    changed: list[str] = []
    for r in recs:
        k = _class_key(r.class_name)
        if r.class_name == klass or (k and k != top and not _close(k, top)):
            continue
        note = f"반: 표지에서 '{r.class_name or '못 읽음'}' — 같은 답안지 묶음의 '{klass}'로 맞춤"
        store.save_student(
            r.model_copy(update={"class_name": klass, "uncertain": [*r.uncertain, note]})
        )
        changed.append(f"{r.name or r.student_id} ({r.class_name or '반 없음'} → {klass})")
    return changed


# ---------------------------------------------------------------- 전체


def run(
    svc: Services,
    store: ExamStore,
    pdfs: list[Path],
    *,
    by: str | None = None,
    progress: Progress = _noop,
) -> AutoState:
    """루브릭 → 답안지 → 채점 → 점수표·채점 답안지 PDF. 학생 한 명·문항 하나가 실패해도 나머지는
    끝까지 간다 (실패는 `warnings`)."""
    from dawoni.export.exam_files import export_scores, marked_dir
    from dawoni.export.red_pen import write_marked
    from dawoni.grading import workflow

    s = AutoState(started_at=utcnow().isoformat())

    def stage(name: str) -> None:
        s.stage = name
        save_state(store, s)

    try:
        stage("루브릭")
        s.approved, s.decisions, asks = approve_all(svc, store, by=by, progress=progress)
        s.asks += asks
        exam = store.exam()
        fallback = max(2, 1 + max((p for q in exam.questions for p in q.answer_pages), default=1))
        for k, pdf in enumerate(pdfs, start=1):
            stage(f"답안지 {k}/{len(pdfs)}")
            per, note = pages_per_student(pdf, fallback)
            if note:
                s.warnings.append(note)
            res = workflow.scan_batch(svc, store, pdf, per_student=per, progress=progress)
            if moved := unify_classes(store, pdf.name):
                s.decisions.append(f"{pdf.name}: 반 이름을 한 반으로 맞춤 — {', '.join(moved)}")
            s.warnings += res.warnings
            s.warnings += [f"{f.student}: {f.reason}" for f in res.failed]
        stage("채점")
        ready = [  # 승인하지 못한 루브릭(물음)의 문항은 빼고
            q.number
            for q in store.exam().questions
            if (r := store.rubric(q.number)) is not None and r.status == "approved"
        ]
        if ready:
            g = workflow.grade_exam(
                svc, store, only=ready, by=by, allow_unreadable=True, progress=progress
            )
            s.warnings += [f"{f.student} {f.question}번: {f.reason}" for f in g.failed]
        stage("내보내기")
        export_scores(svc.workspace, store)
        marked = write_marked(store, marked_dir(svc.workspace, store), progress=progress)
        s.files = [p.name for p in marked.written]
        s.warnings += [w for w in marked.warnings if "검토 필요" not in w and "이름 확인" not in w]
        s.state = "done"
    except Exception as e:
        s.state = "failed"
        s.error = str(e)
        log.exception("한 번에 채점 실패")
        raise
    finally:
        _tally(store, s)
        s.finished_at = utcnow().isoformat()
        save_state(store, s)
    return s


def _tally(store: ExamStore, s: AutoState) -> None:
    """채점 수, 검토 필요 수, 이름·판독불가 물음 (지금 상태로 — 강사가 고친 것은 빠진다)."""
    exam = store.exam()
    records = {r.student_id: r for r in store.student_records()}
    students = sorted(set(store.students()) | set(records))
    s.students = len(students)
    s.graded = s.review = 0
    s.asks = [a for a in s.asks if a.kind not in ("name", "unreadable")]
    for sid in students:
        r = records.get(sid)
        if r is not None and r.name_pending:
            why = "같은 반에 같은 이름" if r.needs_rename else (r.roster_note or "이름 확인")
            s.asks.append(Ask("name", f"{r.name or sid}: {why}", student=sid))
        elif r is None or not r.name:
            s.asks.append(Ask("name", f"{sid}: 표지 이름을 읽지 못함", student=sid))
        for q in exam.questions:
            g = store.grade(sid, q.number)
            if g is not None:
                s.graded += 1
                s.review += g.status == "needs_review"
            t = store.transcript(sid, q.number)
            if t is not None and t.unreadable and not t.confirmed:
                who = (r.name if r else None) or sid
                text = f"{who} {q.number}번: [판독불가] {t.unreadable}곳"
                s.asks.append(Ask("unreadable", text, student=sid, question=q.number))


def refresh(store: ExamStore) -> AutoState | None:
    """강사가 이름·판독문을 고친 뒤 물을 것을 다시 센다."""
    s = load_state(store)
    if s is None or s.state == "running":
        return s
    _tally(store, s)
    save_state(store, s)
    return s
