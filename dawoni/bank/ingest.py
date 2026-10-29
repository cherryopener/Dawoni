"""교재·문제집·기출 PDF → 문제은행 (구간 단위).

구간 하나 = 문제 쪽 + 정답·해설 쪽. 문제집은 단원마다 번호가 1번부터 다시 시작하므로
해설 번호는 구간 안에서만 맞춘다.

**DB화** (`ingest_chunk`) — AI 풀이 없이:
1. 레이아웃 분석 → 쪽별 문항 구조화 (시험지 DB화와 같은 `extract_exam`)
2. 정답·해설 쪽이 있으면 읽는다 (`extract_answer_key`) — 원본을 옮겨 적을 뿐 풀지 않는다
3. 단원은 구간 단원으로 둔다

**풀이** (`solve_items`, 강사가 요청할 때 — DB화하면서 같이 할 수도 있음):
1. AI가 **원본을 보지 않고** 단원표 범위로 푼다 → 풀이에 쓴 개념으로 단원을 정한다
2. 원본 답이 있으면 코드로 비교, 다르거나 비교할 수 없으면 두 답을 각각 독립 검산
   (실측: 해설지 자체가 틀린 경우가 있다 — θ·α 뒤바뀜). 원본 답이 없으면 한 번 더 풀어 비교
3. 원본 해설이 충분하면 원본을, 없거나 짧으면 AI 해설을 쓴다. 원본은 지우지 않고 둔다
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
    level: str | None = None,
    unit_trees: list[str],
    key_path: str | None = None,
) -> Source:
    """자료 등록 (이미 있으면 정보만 고침). 구간은 `add_chunks`로.

    `key_path`: 해설 PDF가 따로 있으면 (기출 등) 문제 PDF 뒤에 붙여 한 자료로 등록한다."""
    trees = UnitStore(svc.workspace)
    for t in unit_trees:
        trees.get(t)
    if not unit_trees:
        raise BankError("분류할 단원표를 하나 이상 고르세요 (예: 물리학 I)")
    p = Path(path_or_id)
    attached: int | None = None
    if key_path and p.exists():
        attached = page_count(p) + 1
        p = merge_key_pdf(p, Path(key_path), svc.paths.cache / "merged")
    src = register_source(svc.workspace, p, kind, title=title) if p.exists() else (
        get_source(svc.workspace, path_or_id)
    )  # fmt: skip
    pages = page_count(source_file(svc.workspace, src))
    src = src.model_copy(
        update={"kind": kind, "title": title, "publisher": publisher, "year": year,
                "level": level or src.level, "unit_trees": unit_trees, "pages": pages,
                "key_attached_from": attached or src.key_attached_from}
    )  # fmt: skip
    update_source(svc.workspace, src)
    return src


def merge_key_pdf(exam: Path, key: Path, out_dir: Path) -> Path:
    """문제 PDF 뒤에 해설 PDF를 붙인 새 PDF (원본 둘은 그대로)."""
    import pymupdf

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{exam.stem} + 해설.pdf"
    with pymupdf.open(exam) as a, pymupdf.open(key) as b:
        a.insert_pdf(b)  # pyright: ignore[reportUnknownMemberType]
        a.save(str(out))  # pyright: ignore[reportUnknownMemberType]
    return out


def suggest_chunk(svc: Services, src: Source) -> SourceChunk:
    """처음 제안: 해설 표시("정답 및 해설" 등)가 있는 쪽은 해설로, 나머지는 문제로.
    해설 PDF를 따로 붙였으면 붙인 쪽 전부가 해설."""
    pdf = source_file(svc.workspace, src)
    if src.key_attached_from:
        total = page_count(pdf)
        return SourceChunk(index=len(src.chunks) + 1,
                           pages=list(range(1, src.key_attached_from)),
                           key_pages=list(range(src.key_attached_from, total + 1)))  # fmt: skip
    key = find_key_pages(pdf)
    pages = [p for p in range(1, page_count(pdf) + 1) if p not in key]
    return SourceChunk(index=len(src.chunks) + 1, pages=pages or [1], key_pages=key)


def _check_chunk(svc: Services, src: Source, c: SourceChunk) -> None:
    total = src.pages or page_count(source_file(svc.workspace, src))
    bad = [p for p in [*c.pages, *c.key_pages, *c.concept_pages] if not 1 <= p <= total]
    if bad:
        raise BankError(f"없는 쪽: {bad} (전체 {total}쪽)")
    if c.unit_hint:
        t = UnitStore(svc.workspace).find(c.unit_hint)
        if t is None or t.resolve(c.unit_hint) is None:
            raise BankError(f"없는 단원: {c.unit_hint}")


_FRESH: dict[str, object] = {"status": "new", "item_ids": list[str](), "concept_status": "new",
                              "scope_id": None, "message": None, "concept_message": None,
                              "machine": None}  # fmt: skip


def add_chunks(svc: Services, src: Source, chunks: list[SourceChunk]) -> Source:
    out = list(src.chunks)
    for c in chunks:
        _check_chunk(svc, src, c)
        out.append(c.model_copy(update={"index": len(out) + 1, **_FRESH}))
    src = src.model_copy(update={"chunks": out})
    update_source(svc.workspace, src)
    return src


def set_chunks(svc: Services, src: Source, chunks: list[SourceChunk]) -> Source:
    """챕터(구간) 표 전체를 바꾼다. 이미 DB화한 부분의 쪽은 바꿀 수 없다 (이름·단원은 된다).

    같은 번호의 구간은 상태·문항·범위 프로필을 그대로 둔다. 번호는 표 순서대로 다시 매긴다.
    """
    _check_lock(src, "")
    old = {c.index: c for c in src.chunks}
    out: list[SourceChunk] = []
    for n, c in enumerate(chunks, start=1):
        _check_chunk(svc, src, c)
        prev = old.get(c.index)
        if prev is None:
            out.append(c.model_copy(update={"index": n, **_FRESH}))
            continue
        # 문제 쪽만 잠근다 (문항 ID가 걸림). 해설 쪽은 고쳐서 다시 DB화하면 원본만 붙는다
        if prev.status != "new" and prev.pages != c.pages:
            raise BankError(f"구간 {prev.index}의 문제는 이미 DB화해 쪽을 바꿀 수 없습니다")
        if prev.concept_status != "new" and prev.concept_pages != c.concept_pages:
            raise BankError(f"구간 {prev.index}의 개념은 이미 DB화해 쪽을 바꿀 수 없습니다")
        keep = {k: getattr(prev, k) for k in _FRESH}
        out.append(c.model_copy(update={**keep, "index": n, "updated_at": prev.updated_at}))
    dropped = [c for i, c in old.items() if i not in {x.index for x in chunks}]
    if any(c.item_ids or c.scope_id for c in dropped):
        raise BankError("DB화한 구간은 표에서 지울 수 없습니다 (문항·범위 프로필이 있음)")
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
        busy = c.status == "ingesting" or c.concept_status == "ingesting"
        other = busy and c.machine and c.machine != machine
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
    originals_added: list[str] = field(default_factory=lambda: list[str]())
    """이미 있던 문항 중 이번에 원본 정답·해설을 붙인 것."""


def work_dir(svc: Services, source_id: str, index: int) -> Path:
    """구간 DB화의 중간 결과(쪽 그림·쪽별 읽기 캐시).

    공유 폴더가 아니라 이 PC의 앱 데이터 폴더에 둔다 — 팀원에게 동기화할 필요가 없고 크다
    (실측: 교재 한 권 70MB). 문항 그림은 문항 폴더로 복사되므로 이 폴더를 지워도 된다."""
    import hashlib

    ws_key = hashlib.sha256(str(svc.workspace.root.resolve()).encode()).hexdigest()[:12]
    return svc.paths.cache / "bank-work" / ws_key / f"{source_id[:10]}-c{index}"


def item_id_for(src: Source, chunk: SourceChunk, number: str) -> str:
    """문항 번호로 정한다 (순번이면 한 쪽이 빠졌다 다시 할 때 뒤 문항 ID가 밀려 겹쳐 들어감)."""
    import hashlib

    slug = number if re.fullmatch(r"\d+", number) else (
        "x" + hashlib.sha1(number.encode()).hexdigest()[:8]
    )  # fmt: skip
    return f"bk-{src.source_id[:10]}-c{chunk.index}-{slug}"


def ingest_chunk(
    svc: Services,
    source_id: str,
    index: int,
    *,
    by: str | None = None,
    machine: str = "",
    solve: bool = False,
    progress: Progress = _noop,
) -> ChunkResult:
    """구간 하나를 DB화. `solve`면 문항마다 AI 풀이·원본 비교도 한다.

    풀이는 느려서 보통은 끄고, 필요할 때 `solve_items`로."""
    from dawoni.ingest.answer_key import extract_answer_key
    from dawoni.ingest.exam import extract_exam

    ws = svc.workspace
    src = get_source(ws, source_id)
    _check_lock(src, machine)
    chunk = _chunk(src, index)
    if not chunk.pages:
        raise BankError(f"구간 {index}에는 문제 쪽이 없습니다")
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
        work = work_dir(svc, source_id, index)
        steps = 2 + len(layout.regions)
        progress(0, steps, f"문항 {len(layout.regions)}개 구조화")
        subject = " · ".join(t.subject for t in trees)
        workers = svc.config.parallel_questions

        def page_progress(done: int, total: int, msg: str) -> None:
            progress(0, steps, f"문항 구조화 — {msg}")

        extracted = extract_exam(svc.engine, layout, work, subject=subject, level=scope.level,
                                 workers=workers, progress=page_progress,
                                 partial=True)  # fmt: skip
        res = ChunkResult(chunk, warnings=list(extracted.warnings))

        keys: dict[str, list[KeyPart]] = {}
        key_failed_pages: list[int] = []
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
                svc.engine, pdf, chunk.key_pages, exam, work / "key", level=scope.level,
                cross=False, ignore_other_numbers=True, workers=workers, partial=True,
                pages_per_call=1,  # 실측: 수식 많은 3쪽을 한 번에 → 900초 초과
            )  # fmt: skip
            keys = {k.question_number: k.parts for k in key.keys}
            key_failed_pages = key.failed_pages
            res.warnings += key.warnings

        ref_kind = src.kind if src.kind != "handwriting" else "other"
        for k, q in enumerate(extracted.questions, start=1):
            item_id = item_id_for(src, chunk, q.number)
            progress(1 + k, steps, f"문항 {q.number} {'풀이·비교' if solve else '저장'}")
            chunk = chunk.model_copy(update={"updated_at": utcnow()})
            _set_chunk(svc, source_id, chunk)
            if bank.exists(item_id):
                res.skipped.append(item_id)
                old = bank.get(item_id)
                parts = keys.get(q.number, [])
                if parts and (old.original is None or old.original.level == "none"):
                    # 전에 해설 쪽을 못 읽었던 문항: 원본 정답·해설만 붙인다 (AI 없음)
                    orig = OriginalSolution(level=original_level(parts), parts=parts,
                                            pages=chunk.key_pages)  # fmt: skip
                    bank.save(old.model_copy(update={"original": orig}), by=by,
                              base_revision=old.revision)  # fmt: skip
                    res.originals_added.append(item_id)
                continue
            parts = keys.get(q.number, [])
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
                unit_ids=[chunk.unit_hint] if chunk.unit_hint else [],
                origin="ingested",
                source=BankSourceRef(
                    source_id=src.source_id, title=src.title or src.filename, kind=ref_kind,
                    pages=sorted({p for p, _ in region.boxes}) if region else [],
                    number_label=q.number,
                ),
                original=OriginalSolution(level=level, parts=parts, pages=chunk.key_pages)
                if parts else OriginalSolution(level="none"),
            )  # fmt: skip
            if solve:
                it = with_solution(it, *solve_bank_question(svc, q, scope, parts), mapping)
            it = bank.save(it, by=by)
            res.items.append(it)
            chunk = chunk.model_copy(update={"item_ids": [*chunk.item_ids, item_id]})
        missing = [n for nums in extracted.failed_pages.values() for n in nums]
        key_failed = key_failed_pages
        message = f"{len(res.items)}문항 추가" + (
            f", {len(res.skipped)}문항은 이미 있음" if res.skipped else ""
        )
        if res.originals_added:
            message += f", 원본 정답·해설 {len(res.originals_added)}문항 붙임"
        if key_failed:
            message += (f" · 해설 {', '.join(map(str, key_failed))}쪽 못 읽음 — 다시 DB화하면 "
                        "그 쪽만 읽어 원본 정답·해설을 붙입니다")  # fmt: skip
        if missing:
            pages = ", ".join(f"{p}쪽" for p in sorted(extracted.failed_pages))
            message += (
                f" · {pages}(문항 {', '.join(missing)}) 실패 — 다시 DB화하면 그 쪽만 읽습니다"
            )
        chunk = chunk.model_copy(
            update={"status": "failed" if missing or key_failed else "ingested", "machine": None,
                    "updated_at": utcnow(), "item_ids": sorted({*chunk.item_ids, *res.skipped}),
                    "message": message}
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


# ---------------------------------------------------------------- 교재 챕터: 개념 + 문제


def chapter_level(svc: Services, src: Source) -> str:
    if src.level:
        return src.level
    trees = [UnitStore(svc.workspace).get(t) for t in src.unit_trees]
    subject = " · ".join(t.subject for t in trees) or "교재"
    return f"고등학교 {subject}" if any(t.curriculum for t in trees) else subject


def ingest_concepts(
    svc: Services,
    source_id: str,
    index: int,
    *,
    by: str | None = None,
    machine: str = "",
    progress: Progress = _noop,
) -> ScopeProfile:
    """챕터의 개념 쪽 → 범위 프로필 (챕터 단원이 붙어 '단원 체크로 범위 만들기'에 쓰인다).

    다시 하면 같은 범위 프로필을 고쳐 쓴다."""
    from dawoni.grading.store import ScopeStore
    from dawoni.ingest.textbook import extract_scope

    ws = svc.workspace
    src = get_source(ws, source_id)
    _check_lock(src, machine)
    chunk = _chunk(src, index)
    if not chunk.concept_pages:
        raise BankError(f"구간 {index}에는 개념 쪽이 없습니다")
    title = " · ".join(x for x in (src.title or src.filename, chunk.label) if x)
    chunk = chunk.model_copy(
        update={"concept_status": "ingesting", "machine": machine, "by": by,
                "updated_at": utcnow(), "concept_message": None}
    )  # fmt: skip
    _set_chunk(svc, source_id, chunk)
    try:
        progress(0, 1, f"{chunk.label or f'구간 {index}'} 개념 {len(chunk.concept_pages)}쪽 정리")
        subject = " · ".join(UnitStore(ws).get(t).subject for t in src.unit_trees) or None
        draft = extract_scope(svc.engine, source_file(ws, src), title=title,
                              level=chapter_level(svc, src), subject=subject,
                              pages=chunk.concept_pages)  # fmt: skip
        update: dict[str, object] = {"unit_ids": [chunk.unit_hint] if chunk.unit_hint else []}
        if chunk.scope_id:
            update["id"] = chunk.scope_id
        scope = draft.scope.model_copy(update=update)
        ScopeStore(ws).save(scope)
        n = len(scope.concepts)
        chunk = chunk.model_copy(
            update={"concept_status": "ingested", "scope_id": scope.id, "machine": None,
                    "updated_at": utcnow(),
                    "concept_message": f"개념 {n}개 · 기법 {len(scope.skills)}개"}
        )  # fmt: skip
        _set_chunk(svc, source_id, chunk)
        progress(1, 1, chunk.concept_message or "완료")
        return scope
    except BaseException as e:
        failed = chunk.model_copy(
            update={"concept_status": "failed", "machine": None, "updated_at": utcnow(),
                    "concept_message": safe_message(e)}
        )  # fmt: skip
        _set_chunk(svc, source_id, failed)
        raise


@dataclass
class ChapterResult:
    scope: ScopeProfile | None = None
    problems: ChunkResult | None = None


def ingest_chapters(
    svc: Services,
    source_id: str,
    indexes: list[int] | None = None,
    *,
    concepts: bool = True,
    problems: bool = True,
    solve: bool = False,
    by: str | None = None,
    machine: str = "",
    progress: Progress = _noop,
) -> list[ChapterResult]:
    """챕터마다 개념 → 범위 프로필, 문제 → 문제은행 (풀이는 `solve`일 때만).

    `indexes`가 없으면 아직 안 한 부분만 (끊긴 뒤 이어 하기)."""
    src = get_source(svc.workspace, source_id)
    todo = [c for c in src.chunks if indexes is None or c.index in indexes]
    out: list[ChapterResult] = []
    for k, c in enumerate(todo):
        name = c.label or f"구간 {c.index}"

        def step(done: int, total: int, msg: str, k: int = k, name: str = name) -> None:
            progress(k, len(todo), f"{name}: {msg}")

        r = ChapterResult()
        again = indexes is not None
        if concepts and c.concept_pages and (again or c.concept_status != "ingested"):
            r.scope = ingest_concepts(svc, source_id, c.index, by=by, machine=machine,
                                      progress=step)  # fmt: skip
        if problems and c.pages and (again or c.status != "ingested"):
            r.problems = ingest_chunk(svc, source_id, c.index, by=by, machine=machine,
                                      solve=solve, progress=step)  # fmt: skip
        out.append(r)
    progress(len(todo), len(todo), f"챕터 {len(todo)}개 완료")
    return out


# ---------------------------------------------------------------- 풀이 (요청할 때)


def with_solution(
    it: BankItem, sol: ScopedSolution, cmp: AnswerComparison, mapping: dict[str, str]
) -> BankItem:
    """AI 풀이·원본 비교를 문항에 넣고, 풀이에 쓴 개념으로 단원을 정한다 (구간 단원이 맨 앞)."""
    level = it.original.level if it.original else "none"
    hint = it.unit_ids[0] if it.unit_ids else None
    return it.model_copy(
        update={
            "solution": sol,
            "solution_by": "source" if level == "full" and cmp.verdict == "same" else "ai",
            "concept_units": {c: u for c, u in mapping.items()
                              if any(c in st.uses for st in sol.steps)},
            "answer_check": cmp,
            "unit_ids": units_from_solution(sol, mapping, hint) if mapping else it.unit_ids,
        }
    )  # fmt: skip


def solve_trees(svc: Services, it: BankItem) -> list[UnitTree]:
    """풀 때 쓸 단원표: DB화한 자료의 단원표, 없으면 문항 단원의 단원표."""
    units = UnitStore(svc.workspace)
    ids: list[str] = []
    if it.source.source_id:
        try:
            ids = get_source(svc.workspace, it.source.source_id).unit_trees
        except KeyError:
            ids = []
    if not ids:
        ids = list(dict.fromkeys(u.split(".", 1)[0] for u in it.unit_ids))
    if not ids:
        raise BankError(f"{it.id}: 단원이 없는 문항입니다 — 단원을 먼저 정하세요")
    return [units.get(t) for t in ids]


@dataclass
class SolveResult:
    solved: list[BankItem] = field(default_factory=lambda: list[BankItem]())
    skipped: list[str] = field(default_factory=lambda: list[str]())
    """이미 풀이가 있어 건너뜀 (`redo`가 아니면)."""


def solve_items(
    svc: Services,
    item_ids: list[str],
    *,
    by: str | None = None,
    redo: bool = False,
    machine: str = "",
    progress: Progress = _noop,
) -> SolveResult:
    """문항 풀이·해설을 만든다 (원본을 보지 않고 풀어 원본 답과 비교). 문항 여러 개는 동시에.

    다른 PC가 같은 문항을 풀고 있으면 그 문항은 막고,
    그새 다른 강사가 문항을 고쳤으면 저장을 거부한다.
    """
    from dawoni import sharing
    from dawoni.parallel import each_in_parallel

    bank = BankStore(svc.workspace)
    items = [bank.get(i) for i in dict.fromkeys(item_ids)]
    res = SolveResult(skipped=[it.id for it in items if it.solution is not None and not redo])
    todo = [it for it in items if it.id not in res.skipped]

    def one(it: BankItem) -> BankItem:
        with sharing.work_mark(bank.item_dir(it.id), "solve", "solve", machine, by):
            scope, mapping = unit_scope(solve_trees(svc, it))
            parts = it.original.parts if it.original else []
            sol, cmp = solve_bank_question(svc, it.question, scope, parts)
            return bank.save(with_solution(it, sol, cmp, mapping), by=by, base_revision=it.revision)

    res.solved = each_in_parallel(
        todo, one, name=lambda it: f"{it.source.display()}", what="풀이",
        workers=svc.config.parallel_questions, progress=progress,
    )  # fmt: skip
    progress(len(todo), len(todo), f"{len(res.solved)}문항 풀이 완료")
    return res


def safe_message(e: BaseException) -> str:
    return (str(e).strip() or type(e).__name__)[:300]
