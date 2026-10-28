"""교재·문제집·기출 PDF → 문제은행 (구간 단위).

구간 하나 = 문제 쪽 + 정답·해설 쪽. 문제집은 단원마다 번호가 1번부터 다시 시작하므로
해설 번호는 구간 안에서만 맞춘다. 순서:
1. 레이아웃 분석 → 쪽별 문항 구조화 (시험지 DB화와 같은 `extract_exam`)
2. 정답·해설 쪽이 있으면 읽는다 (`extract_answer_key`)
3. 문항마다 AI가 **원본을 보지 않고** 단원표 범위로 푼다 → 풀이에 쓴 개념으로 단원을 정한다
4. 원본 답이 있으면 코드로 비교, 다르거나 비교할 수 없으면 두 답을 각각 독립 검산
   (실측: 해설지 자체가 틀린 경우가 있다 — θ·α 뒤바뀜). 원본 답이 없으면 한 번 더 풀어 비교
5. 원본 해설이 충분하면 원본을, 없거나 짧으면 AI 해설을 쓴다. 원본은 지우지 않고 둔다
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from dawoni.bank import BankError
from dawoni.bank.store import BankStore
from dawoni.bank.units import UnitStore
from dawoni.grading import checks
from dawoni.grading.pipeline import SecondOpinion, _verify_answer, solve_question  # pyright: ignore[reportPrivateUsage]
from dawoni.ingest.layout import analyze, find_key_pages, page_count
from dawoni.ingest.sources import get_source, register_source, source_file, update_source
from dawoni.models.answer_key import KeyPart
from dawoni.models.bank import (
    AnswerComparison,
    BankItem,
    BankSourceRef,
    OriginalLevel,
    OriginalSolution,
)
from dawoni.models.base import utcnow
from dawoni.models.grading import Exam, ExamQuestion, FinalAnswer, ScopedSolution
from dawoni.models.scope import Concept, ScopeProfile
from dawoni.models.source import Source, SourceChunk, SourceIngestState, SourceKind
from dawoni.models.units import UnitTree
from dawoni.services import Services

Progress = Callable[[int, int, str], None]

BRIEF_CHARS = 80
"""원본 해설이 이보다 짧으면 '짧은 해설' (강사용 한 줄 풀이) → AI가 학생용 해설을 쓴다."""

LOCK_FRESH = timedelta(minutes=20)
"""다른 PC의 'DB화 중' 표식이 이보다 오래되면 끊긴 것으로 본다."""

MAX_UNITS = 3


def _noop(done: int, total: int, message: str) -> None:
    pass


# ---------------------------------------------------------------- 단원표 → 풀이 범위


def unit_scope(trees: list[UnitTree]) -> tuple[ScopeProfile, dict[str, str]]:
    """단원표의 단원을 개념(C1…)으로 둔 범위 프로필 + 개념 ID → 단원 ID.

    풀이 단계마다 쓴 개념 ID를 적게 하므로, 그 문항이 어느 단원 문제인지가 풀이에서 나온다.
    """
    concepts: list[Concept] = []
    mapping: dict[str, str] = {}
    for t in trees:
        for u, path in t.walk():
            cid = f"C{len(concepts) + 1}"
            concepts.append(
                Concept(id=cid, name=u.title, summary=" > ".join((t.subject, *path, u.title)))
            )
            mapping[cid] = u.id
    if not concepts:
        raise BankError("단원표에 단원이 없습니다")
    subjects = " · ".join(t.subject for t in trees)
    scope = ScopeProfile(
        id="units-" + "-".join(t.id for t in trees),
        title=f"단원표 {subjects}",
        subject=subjects,
        level=f"고등학교 {subjects} (교육과정 범위)",
        concepts=concepts,
        version=sum(t.version for t in trees),
    )
    return scope, mapping


def units_from_solution(
    sol: ScopedSolution, mapping: dict[str, str], hint: str | None = None
) -> list[str]:
    """풀이에서 많이 쓴 단원 순 (구간 단원 힌트가 있으면 맨 앞)."""
    counts = Counter(mapping[u] for s in sol.steps for u in s.uses if u in mapping)
    out = [hint] if hint else []
    out += [u for u, _ in counts.most_common() if u != hint]
    return out[:MAX_UNITS]


# ---------------------------------------------------------------- 원본 해설 · 비교


def original_level(parts: list[KeyPart]) -> OriginalLevel:
    if not parts or not any(p.answer or p.answer_expr or p.solution.strip() for p in parts):
        return "none"
    sol = [p.solution.strip() for p in parts]
    if not any(sol):
        return "answer_only"
    if all(len(s) < BRIEF_CHARS for s in sol):
        return "brief"
    return "full"


_CIRCLED = {c: i for i, c in enumerate("①②③④⑤⑥⑦⑧⑨", start=1)}


def choice_numbers(s: str | None) -> set[int]:
    """'③', '3', '3번', '\\text{③}' → {3}. 복수 정답도."""
    if not s:
        return set()
    out = {n for c, n in _CIRCLED.items() if c in s}
    if not out:
        out = {int(d) for d in re.findall(r"(?<![\d.])([1-9])(?![\d.])", s)}
    return out


def _agree(q: ExamQuestion, kp: KeyPart, ai: FinalAnswer) -> bool | None:
    if q.item.item_type == "multiple_choice":
        a, b = choice_numbers(kp.answer or kp.answer_expr), choice_numbers(ai.latex)
        return (a == b) if a and b else None
    return checks.key_answer_agrees(kp.answer, kp.answer_expr, ai)


def compare_original(
    q: ExamQuestion,
    sol: ScopedSolution,
    parts: list[KeyPart],
    verify: Callable[[str, str], tuple[str, str]],
) -> AnswerComparison:
    """원본 답 ↔ AI 답 (소문항별). `verify(소문항, 답)` → (valid|invalid|unsure, 이유)."""
    labels = [p.label for p in q.parts] or [""]
    by_label = {p.label: p for p in parts}
    if len(parts) == 1 and len(labels) == 1:
        by_label = {labels[0]: parts[0]}
    verdicts: list[bool | None] = []
    notes: list[str] = []
    orig_answers: list[str] = []
    ai_answers: list[str] = []
    for label in labels:
        kp = by_label.get(label)
        ai = checks.ai_part_answer(sol, label)
        name = f"소문항 {label}" if label else "문항"
        if kp is None or not (kp.answer or kp.answer_expr):
            continue
        orig = kp.answer or kp.answer_expr or ""
        orig_answers.append(f"{label} {orig}".strip())
        if ai is None:
            verdicts.append(None)
            notes.append(f"{name}: AI 풀이에 답이 없음")
            continue
        ai_answers.append(f"{label} {ai.latex}".strip())
        same = _agree(q, kp, ai)
        verdicts.append(same)
        if same:
            continue
        ov, on = verify(label, orig)
        av, an = verify(label, ai.latex)
        head = (
            "원본 답과 AI 답이 다름" if same is False else "원본 답과 AI 답을 자동 비교할 수 없음"
        )
        notes.append(
            f"{name}: {head} — 원본 {orig} (검산 {ov}: {on}) / AI {ai.latex} (검산 {av}: {an})"
        )
    if not verdicts:
        return AnswerComparison(verdict="no_original")
    verdict = "same" if all(verdicts) else ("differs" if False in verdicts else "unsure")
    return AnswerComparison(
        verdict=verdict,
        original_answer=", ".join(orig_answers) or None,
        ai_answer=", ".join(ai_answers) or None,
        notes=notes,
    )


# ---------------------------------------------------------------- 문항 하나


def solve_bank_question(
    svc: Services,
    q: ExamQuestion,
    scope: ScopeProfile,
    parts: list[KeyPart],
) -> tuple[ScopedSolution, AnswerComparison]:
    """원본 답이 있으면 한 번 풀어 비교, 없으면 두 번 독립으로 풀어 비교."""
    has_answer = any(p.answer or p.answer_expr for p in parts)
    sol = solve_question(svc.engine, q, scope, verify=None if has_answer else SecondOpinion())
    if not has_answer:
        if sol.verified:
            return sol, AnswerComparison(verdict="no_original", ai_answer=sol.final_answer.latex)
        return sol, AnswerComparison(
            verdict="unsure",
            ai_answer=sol.final_answer.latex,
            notes=[
                "원본 답이 없고, AI가 두 번 푼 답이 같지 않거나 비교할 수 없음",
                *sol.check_notes,
            ],
        )

    def verify(label: str, answer: str) -> tuple[str, str]:
        r = _verify_answer(svc.engine, q, scope, label, answer, agent=None, model=None)
        return r.verdict, r.reasoning

    return sol, compare_original(q, sol, parts, verify)


# ---------------------------------------------------------------- 자료 · 구간


def add_source(
    svc: Services,
    path_or_id: str,
    kind: SourceKind,
    *,
    title: str,
    publisher: str | None = None,
    year: int | None = None,
    unit_trees: list[str],
) -> Source:
    """자료 등록 (이미 있으면 정보만 고침). 구간은 `add_chunks`로."""
    trees = UnitStore(svc.workspace)
    for t in unit_trees:
        trees.get(t)
    if not unit_trees:
        raise BankError("분류할 단원표를 하나 이상 고르세요 (예: 물리학 I)")
    p = Path(path_or_id)
    src = register_source(svc.workspace, p, kind, title=title) if p.exists() else (
        get_source(svc.workspace, path_or_id)
    )  # fmt: skip
    pages = page_count(source_file(svc.workspace, src))
    src = src.model_copy(
        update={"kind": kind, "title": title, "publisher": publisher, "year": year,
                "unit_trees": unit_trees, "pages": pages}
    )  # fmt: skip
    update_source(svc.workspace, src)
    return src


def suggest_chunk(svc: Services, src: Source) -> SourceChunk:
    """처음 제안: 해설 표시("정답 및 해설" 등)가 있는 쪽은 해설로, 나머지는 문제로."""
    pdf = source_file(svc.workspace, src)
    key = find_key_pages(pdf)
    pages = [p for p in range(1, page_count(pdf) + 1) if p not in key]
    return SourceChunk(index=len(src.chunks) + 1, pages=pages or [1], key_pages=key)


def add_chunks(svc: Services, src: Source, chunks: list[SourceChunk]) -> Source:
    total = src.pages or page_count(source_file(svc.workspace, src))
    trees = UnitStore(svc.workspace)
    out = list(src.chunks)
    for c in chunks:
        bad = [p for p in [*c.pages, *c.key_pages] if not 1 <= p <= total]
        if bad:
            raise BankError(f"없는 쪽: {bad} (전체 {total}쪽)")
        if c.unit_hint:
            t = trees.find(c.unit_hint)
            if t is None or t.resolve(c.unit_hint) is None:
                raise BankError(f"없는 단원: {c.unit_hint}")
        out.append(c.model_copy(update={"index": len(out) + 1, "status": "new", "item_ids": []}))
    src = src.model_copy(update={"chunks": out})
    update_source(svc.workspace, src)
    return src


def _chunk(src: Source, index: int) -> SourceChunk:
    for c in src.chunks:
        if c.index == index:
            return c
    raise KeyError(f"구간 {index} 없음")


def _set_chunk(svc: Services, src_id: str, chunk: SourceChunk) -> Source:
    src = get_source(svc.workspace, src_id)
    chunks = [chunk if c.index == chunk.index else c for c in src.chunks]
    done = [c for c in chunks if c.status == "ingested"]
    status = "ingested" if len(done) == len(chunks) else (
        "ingesting" if any(c.status == "ingesting" for c in chunks) else src.ingest.status
    )  # fmt: skip
    src = src.model_copy(
        update={"chunks": chunks,
                "ingest": SourceIngestState(status=status,
                                            item_count=sum(len(c.item_ids) for c in chunks),
                                            message=f"구간 {len(done)}/{len(chunks)} 완료")}
    )  # fmt: skip
    update_source(svc.workspace, src)
    return src


def _check_lock(src: Source, machine: str) -> None:
    for c in src.chunks:
        other = c.status == "ingesting" and c.machine and c.machine != machine
        if other and utcnow() - c.updated_at < LOCK_FRESH:
            who = f"{c.by} 선생님" if c.by else "다른 PC"
            raise BankError(
                f"{who}이 이 자료를 DB화하고 있습니다 (구간 {c.index}) — 끝난 뒤 다시 하세요"
            )


@dataclass
class ChunkResult:
    chunk: SourceChunk
    items: list[BankItem] = field(default_factory=lambda: list[BankItem]())
    skipped: list[str] = field(default_factory=lambda: list[str]())
    """이미 있는 문항 (이어 하기 — 강사가 고친 것을 덮어쓰지 않는다)."""
    warnings: list[str] = field(default_factory=lambda: list[str]())


def item_id_for(src: Source, chunk: SourceChunk, k: int) -> str:
    return f"bk-{src.source_id[:10]}-c{chunk.index}-{k}"


def ingest_chunk(
    svc: Services,
    source_id: str,
    index: int,
    *,
    by: str | None = None,
    machine: str = "",
    progress: Progress = _noop,
) -> ChunkResult:
    from dawoni.ingest.answer_key import extract_answer_key
    from dawoni.ingest.exam import extract_exam

    ws = svc.workspace
    src = get_source(ws, source_id)
    _check_lock(src, machine)
    chunk = _chunk(src, index)
    trees = [UnitStore(ws).get(t) for t in src.unit_trees]
    if not trees:
        raise BankError("자료에 분류할 단원표가 정해지지 않았습니다")
    scope, mapping = unit_scope(trees)
    pdf = source_file(ws, src)
    bank = BankStore(ws)

    chunk = chunk.model_copy(
        update={"status": "ingesting", "machine": machine, "by": by, "updated_at": utcnow(),
                "message": None}
    )  # fmt: skip
    _set_chunk(svc, source_id, chunk)
    try:
        total = page_count(pdf)
        keep = set(chunk.pages)
        layout = analyze(pdf, exclude=[p for p in range(1, total + 1) if p not in keep])
        if layout.scanned:
            raise BankError("글자 정보가 없는 스캔 PDF입니다. 아직 디지털 PDF만 지원합니다.")
        if not layout.regions:
            raise BankError(f"구간 {index}의 쪽에서 문항을 찾지 못했습니다")
        work = ws.bank / ".work" / f"{source_id[:10]}-c{index}"
        steps = 2 + len(layout.regions)
        progress(0, steps, f"문항 {len(layout.regions)}개 구조화")
        subject = " · ".join(t.subject for t in trees)
        extracted = extract_exam(svc.engine, layout, work, subject=subject, level=scope.level)
        res = ChunkResult(chunk, warnings=list(extracted.warnings))

        keys: dict[str, list[KeyPart]] = {}
        if chunk.key_pages:
            progress(1, steps, f"정답·해설 {len(chunk.key_pages)}쪽 읽기")
            try:
                exam = Exam(title=src.title or src.filename, scope_id=scope.id,
                            questions=extracted.questions)  # fmt: skip
            except ValueError as e:
                raise BankError(
                    f"구간 안에 같은 번호의 문항이 있습니다 — 단원별로 구간을 나누세요 ({e})"
                ) from e
            key = extract_answer_key(
                svc.engine, pdf, chunk.key_pages, exam, work / "key", level=scope.level, cross=False
            )
            keys = {k.question_number: k.parts for k in key.keys}
            res.warnings += key.warnings

        ref_kind = src.kind if src.kind != "handwriting" else "other"
        for k, q in enumerate(extracted.questions, start=1):
            item_id = item_id_for(src, chunk, k)
            progress(1 + k, steps, f"문항 {q.number} 풀이·비교")
            chunk = chunk.model_copy(update={"updated_at": utcnow()})
            _set_chunk(svc, source_id, chunk)
            if bank.exists(item_id):
                res.skipped.append(item_id)
                continue
            parts = keys.get(q.number, [])
            sol, cmp = solve_bank_question(svc, q, scope, parts)
            level = original_level(parts)
            figs = bank.add_figures(item_id, extracted.figure_files.get(q.number, {}))
            item = q.item.model_copy(
                update={"figures": [f.model_copy(update={"orig": figs.get(f.id, f.orig)})
                                    for f in q.item.figures]}
            )  # fmt: skip
            region = next((r for r in layout.regions if r.label == q.number), None)
            it = BankItem(
                id=item_id,
                question=q.model_copy(update={"item": item}),
                subject=trees[0].subject if len(trees) == 1 else subject,
                unit_ids=units_from_solution(sol, mapping, chunk.unit_hint),
                origin="ingested",
                source=BankSourceRef(
                    source_id=src.source_id, title=src.title or src.filename, kind=ref_kind,
                    pages=sorted({p for p, _ in region.boxes}) if region else [],
                    number_label=q.number,
                ),
                original=OriginalSolution(level=level, parts=parts, pages=chunk.key_pages)
                if parts else OriginalSolution(level="none"),
                solution=sol,
                solution_by="source" if level == "full" and cmp.verdict == "same" else "ai",
                concept_units={c: u for c, u in mapping.items()
                               if any(c in s.uses for s in sol.steps)},
                answer_check=cmp,
            )  # fmt: skip
            it = bank.save(it, by=by)
            res.items.append(it)
            chunk = chunk.model_copy(update={"item_ids": [*chunk.item_ids, item_id]})
        chunk = chunk.model_copy(
            update={"status": "ingested", "machine": None, "updated_at": utcnow(),
                    "item_ids": sorted({*chunk.item_ids, *res.skipped}),
                    "message": f"{len(res.items)}문항 추가"
                    + (f", {len(res.skipped)}문항은 이미 있음" if res.skipped else "")}
        )  # fmt: skip
        res.chunk = chunk
        _set_chunk(svc, source_id, chunk)
        progress(steps, steps, chunk.message or "완료")
        return res
    except BaseException as e:
        failed = chunk.model_copy(
            update={"status": "failed", "machine": None, "updated_at": utcnow(),
                    "message": safe_message(e)}
        )  # fmt: skip
        _set_chunk(svc, source_id, failed)
        raise


def safe_message(e: BaseException) -> str:
    return (str(e).strip() or type(e).__name__)[:300]
