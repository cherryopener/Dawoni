# pyright: basic, reportMissingTypeStubs=false
"""0단계 해설 다듬기 (2026-11-18 사용자 요청).

    시험지·해설 HWP/HWPX → [다듬기] → AI(높은 등급)가 해설지를 따라 풀고 부분 점수 기준을 촘촘하게
    → 강사가 추천을 보고 질문에 답하고 고침 → 다듬은 HWPX → 1단계(한 번에 채점)에 넣는다

0단계는 건너뛸 수 있다 — 다른 강사는 다른 AI 채팅으로 해설지를 다듬어 바로 1단계에 넣는다.
그래서 결과는 앱 안의 루브릭이 아니라 **해설지 파일**이다: 원본 HWPX에서 문항마다 원래 부분 점수
문단만 새 기준으로 바꾼다 (`export.key_hwpx`). 문항·그림·풀이는 그대로.

- AI 호출은 문항마다 한 번 (`refine_key`, 역할 `key_refiner` = strong 등급). 해설지 없이 처음부터
  풀지 않고 해설지 풀이를 따라가며 확인한다 — 토큰·시간을 아낀다. 해설이 없는 문항만 직접 푼다.
- 줄마다 출처: 해설지(key) · AI 추천(ai, 바로 들어감) · 질문(ask, 강사가 '넣기'를 골라야 들어감).
  "부분점수 없음"인 소문항에는 점수를 나누는 줄이 들어갈 수 없다 (검증·수리 요청).
- 초안은 작업공간 `keyprep/<ID>/draft.json`, 다듬은 파일은 `exports/해설 다듬기/<이름>_다듬음.hwpx`.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from rapidfuzz import fuzz

from dawoni.casebook import CaseStore
from dawoni.casebook import prompt_text as case_prompt
from dawoni.context import TaskDef, TaskInput
from dawoni.fsio import read_json, write_model_atomic
from dawoni.grading.render import scope_layer
from dawoni.grading.store import ScopeStore, safe_name
from dawoni.ingest.workflow import IngestRefused
from dawoni.models.base import new_id, utcnow
from dawoni.models.keyprep import (
    BLANK,
    KIND_LABELS,
    POINTED,
    KeyPrep,
    KeyRefineOutput,
    LineKind,
    PrepLine,
    PrepPart,
    PrepQuestion,
    PrepState,
)
from dawoni.models.scope import ScopeProfile
from dawoni.parallel import ParallelError, each_in_parallel
from dawoni.paths import WorkspacePaths
from dawoni.services import Services

log = logging.getLogger(__name__)
Progress = Callable[[int, int, str], None]

SUFFIXES = (".hwp", ".hwpx")
EXPORT_FOLDER = "해설 다듬기"
SUFFIX = "_다듬음"
"""다듬은 HWPX 이름 꼬리 — 1단계가 시험 이름을 정할 때 뗀다."""

REFINE_KEY: TaskDef[KeyRefineOutput] = TaskDef(
    role="key_refiner",
    name="refine_key",
    template="refine_key.md.j2",
    output=KeyRefineOutput,
    rule_target="rubric",
    timeout_s=900,
)
"""높은 등급 + 그림이라 오래 걸릴 수 있다 (15분)."""


def _noop(done: int, total: int, message: str) -> None:
    pass


# ---------------------------------------------------------------- 저장


def prep_root(ws: WorkspacePaths) -> Path:
    return ws.root / "keyprep"


class PrepStore:
    """초안 하나: `keyprep/<ID>/` — draft.json, source.hwpx(읽고 고쳐 쓰는 원본), 원래 파일,
    그림."""

    _locks: ClassVar[dict[str, threading.Lock]] = {}
    _guard = threading.Lock()

    def __init__(self, ws: WorkspacePaths, prep_id: str) -> None:
        if not re.fullmatch(r"kp-[0-9a-f]{32}", prep_id):
            raise KeyError(f"해설 다듬기 {prep_id} 없음")
        self.ws = ws
        self.id = prep_id
        self.root = prep_root(ws) / prep_id
        with PrepStore._guard:
            self.lock = PrepStore._locks.setdefault(str(self.root), threading.Lock())

    @property
    def file(self) -> Path:
        return self.root / "draft.json"

    @property
    def source(self) -> Path:
        return self.root / "source.hwpx"

    def exists(self) -> bool:
        return self.file.is_file()

    def load(self) -> KeyPrep:
        if not self.exists():
            raise KeyError(f"해설 다듬기 {self.id} 없음")
        return KeyPrep.model_validate(read_json(self.file))

    def save(self, prep: KeyPrep) -> KeyPrep:
        prep = prep.model_copy(update={"updated_at": utcnow()})
        write_model_atomic(self.file, prep)
        return prep

    def update(self, change: Callable[[KeyPrep], None]) -> KeyPrep:
        """읽고 고쳐 저장 (문항마다 동시에 끝나는 AI 결과가 서로 덮지 않게)."""
        with self.lock:
            prep = self.load()
            change(prep)
            return self.save(prep)

    def put_question(self, q: PrepQuestion) -> KeyPrep:
        def change(p: KeyPrep) -> None:
            p.questions = [q if x.number == q.number else x for x in p.questions]

        return self.update(change)


def list_preps(ws: WorkspacePaths) -> list[KeyPrep]:
    root = prep_root(ws)
    if not root.is_dir():
        return []
    out: list[KeyPrep] = []
    for d in root.iterdir():
        try:
            out.append(PrepStore(ws, d.name).load())
        except (KeyError, ValueError, OSError):
            continue
    return sorted(out, key=lambda p: p.created_at, reverse=True)


# ---------------------------------------------------------------- 만들기 (AI 없음)


def create(svc: Services, src: Path, *, level: str, title: str | None = None) -> PrepStore:
    """초안 폴더를 만들고 문서를 문항·해설 조각으로 나눈다. HWP는 HWPX로 바꿔 둔다."""
    from dawoni.ingest.convert import ConvertUnavailable, hwp_to_hwpx
    from dawoni.ingest.document import analyze_document
    from dawoni.ingest.exam import suggested_points

    suffix = src.suffix.lower()
    if suffix not in SUFFIXES:
        raise IngestRefused(
            f"해설 다듬기는 한글 파일(HWP·HWPX)만 받습니다: {src.name} — 다듬은 해설지를 HWPX로 "
            "돌려주기 때문입니다"
        )
    store = PrepStore(svc.workspace, new_id("kp"))
    store.root.mkdir(parents=True, exist_ok=True)
    original = store.root / f"original{suffix}"
    shutil.copyfile(src, original)
    try:
        if suffix == ".hwp":
            hwp_to_hwpx(original, store.source)
        else:
            shutil.copyfile(original, store.source)
        analysis = analyze_document(store.source, store.root)
    except ConvertUnavailable as e:
        shutil.rmtree(store.root, ignore_errors=True)
        raise IngestRefused(str(e)) from e
    except Exception:
        shutil.rmtree(store.root, ignore_errors=True)
        raise
    regions = analysis.layout.regions
    if not regions:
        shutil.rmtree(store.root, ignore_errors=True)
        raise IngestRefused(f"{src.name}에서 문항 번호(1. 2. …)를 찾지 못했습니다.")
    doc = analysis.doc
    assert doc is not None

    def rel(p: Path | None) -> str | None:
        return p.relative_to(store.root).as_posix() if p is not None else None

    # 배점 표시가 없는 문항은 1단계(시험 읽기)와 같은 규칙으로 — 다듬은 기준의 배점이 1단계와 맞게
    per = suggested_points(len(regions))
    questions: list[PrepQuestion] = []
    for r in regions:
        idx = analysis.key_index.get(r.label, [])
        piece = analysis.keys.get(r.label)
        questions.append(
            PrepQuestion(
                number=r.label,
                points=r.points or per,
                points_guessed=not r.points,
                text=r.text,
                figures=[x for f in r.figures if (x := rel(f.file))],
                key_paragraphs=[doc.paragraphs[i].text for i in idx],
                key_figures=[x for f in (piece.images if piece else []) if (x := rel(f))],
            )
        )
    prep = KeyPrep(
        id=store.id,
        title=title or src.stem,
        source_name=src.name,
        level=level,
        questions=questions,
        warnings=list(analysis.layout.warnings),
    )
    if not any(q.has_key for q in questions):
        prep.warnings.append(
            "해설을 찾지 못했습니다 — AI가 문항마다 직접 풀어 해설과 부분 점수를 씁니다"
        )
    store.save(prep)
    return store


# ---------------------------------------------------------------- AI


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def _numbered_figures(text: str, prefix: str) -> str:
    k = 0

    def sub(_: re.Match[str]) -> str:
        nonlocal k
        k += 1
        return f"[그림 {prefix}{k}]"

    return re.sub(r"\[그림\]", sub, text)


def material(q: PrepQuestion) -> str:
    """AI에게 보낼 자료: 문항 글, 해설 문단(¶ 번호), 첨부 그림 차례."""
    head = f"[문항 {q.number}]" + (f" 배점 {_fmt(q.points)}점" if q.points else "")
    parts = [head, _numbered_figures(q.text, "F")]
    if q.has_key:
        parts += ["", "[강사 해설지] (문단 번호 ¶)"]
        k = 0
        for n, text in enumerate(q.key_paragraphs, start=1):
            line = text
            while "[그림]" in line:
                k += 1
                line = line.replace("[그림]", f"[해설 그림 K{k}]", 1)
            parts.append(f"¶{n} {line}")
    else:
        parts += ["", "[강사 해설지] 없음"]
    n_f, n_k = len(q.figures), len(q.key_figures)
    if n_f or n_k:
        order = [f"F{i}" for i in range(1, n_f + 1)] + [f"K{i}" for i in range(1, n_k + 1)]
        parts += ["", f"첨부 그림 차례: {', '.join(order)} (F = 문항 그림, K = 해설 그림)"]
    return "\n".join(parts)


def labels_of(q: PrepQuestion) -> list[str]:
    from dawoni.ingest.exam import sub_labels

    return sub_labels(q.text)


def guessed_parts(q: PrepQuestion, labels: list[str]) -> list[float] | None:
    """배점 표시가 없는 문항의 소문항 배점 — 1단계처럼 고르게 (`split_points`)."""
    from dawoni.ingest.exam import split_points

    if not (q.points_guessed and q.points and labels):
        return None
    return split_points(q.points, len(labels))


def _close(a: float, b: float) -> bool:
    return abs(a - b) < 1e-6


def points_problems(
    where: str, points: float, no_partial: bool, lines: list[tuple[LineKind, float | None]]
) -> list[str]:
    """한 소문항의 줄 구조: 채점 요소 합 = 배점, 낮은 단계 < 그 요소, '부분점수 없음' 지키기."""
    probs: list[str] = []
    items = [p for k, p in lines if k == "item"]
    prev: float | None = None
    for k, p in lines:
        if k in POINTED and (p is None or p <= 0):
            probs.append(f"{where}: {KIND_LABELS[k]} 줄에 점수가 없음")
        if k == "item":
            prev = p
        elif k == "level":
            if prev is None:
                probs.append(f"{where}: 낮은 단계 줄은 채점 요소 줄 뒤에 둔다")
            elif p is not None and p >= prev:
                probs.append(
                    f"{where}: 낮은 단계 {_fmt(p)}점은 앞 채점 요소({_fmt(prev)}점)보다 작아야 함"
                )
    if no_partial:
        if any(k in ("level", "deduct") for k, _ in lines):
            probs.append(f"{where}: 부분점수 없음 — 낮은 단계·감점 줄을 넣을 수 없음")
        if len(items) > 1 or any(p is not None and not _close(p, points) for p in items):
            probs.append(f"{where}: 부분점수 없음 — 채점 요소는 만점({_fmt(points)}점) 하나만")
    elif not items:
        probs.append(f"{where}: 채점 요소가 없음")
    elif not _close(total := sum(p or 0 for p in items), points):
        probs.append(f"{where}: 채점 요소 점수 합 {_fmt(total)}점 ≠ 배점 {_fmt(points)}점")
    return probs


def _where(label: str) -> str:
    return f"소문항 {label}" if label else "문항"


def refine_problems(out: KeyRefineOutput, q: PrepQuestion, labels: list[str]) -> list[str]:
    """AI 출력 검사 (수리 요청에 그대로 쓰인다)."""
    probs: list[str] = []
    got = [p.label for p in out.parts]
    if len(set(got)) != len(got):
        probs.append(f"소문항 표시가 겹침: {got}")
    if labels and got != labels:
        probs.append(f"소문항은 {labels} 그대로 차례로 (받은 것: {got})")
    if q.points and not _close(sum(p.points for p in out.parts), q.points):
        total = _fmt(sum(p.points for p in out.parts))
        probs.append(f"소문항 배점 합 {total}점 ≠ 문항 배점 {_fmt(q.points)}점")
    split = guessed_parts(q, labels)
    if split and got == labels and [p.points for p in out.parts] != split:
        want = ", ".join(f"{lb} {_fmt(x)}점" for lb, x in zip(labels, split, strict=True))
        probs.append(f"배점 표시가 없는 문항 — 소문항 배점은 {want} (1단계와 같은 규칙)")
    n = len(q.key_paragraphs)
    for k in out.replace:
        if not 1 <= k <= n:
            probs.append(f"replace의 ¶{k}는 없는 문단 (¶1~¶{n})")
        elif "[그림]" in q.key_paragraphs[k - 1]:
            probs.append(
                f"¶{k}에는 해설 그림이 있어 바꿀 수 없음 — 부분 점수 기준 문단만 replace에"
            )
    if not q.has_key and not (out.explanation or "").strip():
        probs.append("해설지가 없으므로 explanation에 직접 푼 해설을 쓴다")
    for part in out.parts:
        where = _where(part.label)
        counted: list[tuple[LineKind, float | None]] = [
            (ln.kind, ln.points) for ln in part.lines if ln.source != "ask"
        ]
        probs += points_problems(where, part.points, part.no_partial, counted)
        for ln in part.lines:
            if ln.kind not in POINTED and ln.points is not None:
                probs.append(f"{where}: {KIND_LABELS[ln.kind]} 줄의 points는 null")
            if ln.source in ("ai", "ask", "fill") and not (ln.why or "").strip():
                probs.append(f"{where}: '{ln.text[:20]}' — {ln.source} 줄에는 why를 쓴다")
            if ln.source == "fill":
                if BLANK not in ln.text:
                    probs.append(
                        f"{where}: '{ln.text[:20]}' — fill 줄의 text에는 강사가 채울 빈칸 {BLANK}"
                    )
                if not (ln.question or "").strip():
                    probs.append(f"{where}: '{ln.text[:20]}' — fill 줄에는 무엇을 적을지 question")
            elif BLANK in ln.text:
                probs.append(f"{where}: 빈칸 {BLANK}은 fill 줄에만 — 아는 값은 그대로 쓴다")
            if ln.source == "ask":
                if ln.kind == "item":
                    probs.append(
                        f"{where}: 질문(ask) 줄은 채점 요소가 될 수 없음 — "
                        "level·accept·deduct·note로"
                    )
                if not (ln.question or "").strip():
                    probs.append(f"{where}: '{ln.text[:20]}' — ask 줄에는 question을 쓴다")
                if part.no_partial and ln.kind in ("level", "deduct"):
                    probs.append(f"{where}: 부분점수 없음 — 점수를 나누는 질문도 넣지 않는다")
            if ln.source == "key" and not q.has_key:
                probs.append(f"{where}: 해설지가 없으므로 key 줄이 있을 수 없음 — ai 또는 ask")
    return probs


def _norm(s: str) -> str:
    return re.sub(r"\s+|\\[()]", "", s)


def grounded(text: str, key: str) -> bool:
    """해설지에 있던 기준인가 (조금 고쳐 옮긴 것도)."""
    a, b = _norm(text), _norm(key)
    return bool(a) and (a in b or fuzz.partial_ratio(a, b) >= 75)


def to_question(q: PrepQuestion, out: KeyRefineOutput) -> PrepQuestion:
    key = "\n".join(q.key_paragraphs)
    parts: list[PrepPart] = []
    k = 0
    for p in out.parts:
        lines: list[PrepLine] = []
        for ln in p.lines:
            k += 1
            source, why = ln.source, ln.why
            if source == "key" and not grounded(ln.text, key):
                source, why = "ai", "해설지 문장을 고쳐 씀 — 원래 기준과 같은 뜻인지 확인"
            lines.append(
                PrepLine(id=f"L{k}", kind=ln.kind, text=ln.text.strip(),
                         points=ln.points if ln.kind in POINTED else None, source=source,
                         why=why, question=ln.question if source in ("ask", "fill") else None)
            )  # fmt: skip
        parts.append(
            PrepPart(label=p.label, points=p.points, answer=p.answer, no_partial=p.no_partial,
                     lines=lines)
        )  # fmt: skip
    return q.model_copy(
        update={
            "status": "done",
            "error": None,
            "checks": out.checks,
            "explanation": (out.explanation or "").strip() or None,
            "explanation_in": True,
            "replace": sorted(set(out.replace)),
            "parts": parts,
            "edited_by": None,
        }
    )


def scope_for(svc: Services, level: str) -> ScopeProfile:
    from dawoni.cli_ingest import resolve_scope

    return ScopeStore(svc.workspace).get(resolve_scope(svc, None, level))


def refine_question(
    svc: Services, store: PrepStore, number: str, scope: ScopeProfile | None = None
) -> PrepQuestion:
    """문항 하나를 AI로 다듬어 저장한다. 실패하면 그 문항을 '실패'로 저장하고 오류를 올린다."""
    prep = store.load()
    q = prep.question(number)
    scope = scope or scope_for(svc, prep.level)
    labels = labels_of(q)
    task = dataclasses.replace(REFINE_KEY, validator=lambda o: refine_problems(o, q, labels))
    files = [store.root / f for f in [*q.figures, *q.key_figures]]
    # 채점 사례집: 이 문항 글·해설과 열쇠말이 겹치는 지난 사례만 (상한 PROMPT_CHARS)
    cases = CaseStore(svc.workspace).relevant("\n".join([q.text, *q.key_paragraphs]))
    inp = TaskInput(
        variables={
            "level": scope.level,
            "points": _fmt(q.points) if q.points else None,
            "guessed": q.points_guessed,
            "part_points": [
                f"{lb} {_fmt(x)}점"
                for lb, x in zip(labels, guessed_parts(q, labels) or [], strict=False)
            ],
            "labels": labels,
            "has_key": q.has_key,
            "cases": bool(cases),
        },
        scope=scope_layer(scope).variants[-1],
        material=material(q),
        examples=case_prompt(cases),
        attachments=tuple(f for f in files if f.is_file()),
    )
    try:
        res = svc.engine.run(task, inp)
    except Exception as e:
        failed = q.model_copy(update={"status": "failed", "error": str(e)[:500]})
        store.put_question(failed)
        raise
    done = to_question(q, res.value).model_copy(
        update={"agent": res.agent, "model": res.model, "cases": [c.title for c in cases]}
    )
    store.put_question(done)
    return done


def refine(
    svc: Services,
    store: PrepStore,
    numbers: list[str] | None = None,
    *,
    progress: Progress = _noop,
) -> KeyPrep:
    """문항마다 AI로 (동시에 `parallel_questions`개). 한 문항이 실패해도 나머지는 끝까지."""
    prep = store.load()
    todo = [q.number for q in prep.questions if numbers is None or q.number in numbers]

    def mark(state: PrepState, error: str | None = None, start: bool = False) -> None:
        def change(p: KeyPrep) -> None:
            p.state = state
            p.error = error
            for q in p.questions:
                if q.number not in todo:
                    continue
                if start:  # 다시 다듬는 문항은 '다듬는 중'으로 (화면이 기다린다)
                    q.status, q.error = "waiting", None
                elif q.status == "waiting":  # 멈춰서 시작하지 못한 문항
                    q.status, q.error = "failed", error or "다듬지 못함"

        store.update(change)

    mark("running", start=True)
    try:
        scope = scope_for(svc, prep.level)
        each_in_parallel(
            todo,
            lambda n: refine_question(svc, store, n, scope),
            name=lambda n: f"{n}번",
            what="해설 다듬는 중",
            workers=svc.config.parallel_questions,
            progress=progress,
        )
    except ParallelError as e:
        log.warning("해설 다듬기 일부 실패: %s", e)
    except Exception as e:
        mark("failed", str(e)[:500])
        raise
    mark("done")
    return store.load()


def stale(
    prep: KeyPrep, error: str = "다듬기가 끝나지 않고 멈췄습니다 (앱을 닫았거나 다시 켬)"
) -> KeyPrep:
    """도는 작업이 없는데 '다듬는 중'으로 남은 초안을 멈춘 것으로 보인다 (저장하지 않음 — 화면용).
    [다시]를 누르면 그 문항부터 다시 다듬는다."""
    if prep.state != "running":
        return prep
    questions = [
        q.model_copy(update={"status": "failed", "error": error}) if q.status == "waiting" else q
        for q in prep.questions
    ]
    return prep.model_copy(update={"state": "failed", "error": error, "questions": questions})


# ---------------------------------------------------------------- 강사가 고침


def question_problems(q: PrepQuestion) -> list[str]:
    """강사가 고친 기준의 점검 (막지 않고 화면에 알린다): 넣는 줄로만 센다."""
    if q.status != "done":
        return []
    probs: list[str] = []
    if q.points and not _close(sum(p.points for p in q.parts), q.points):
        total = _fmt(sum(p.points for p in q.parts))
        probs.append(f"소문항 배점 합 {total}점 ≠ 문항 배점 {_fmt(q.points)}점")
    for p in q.parts:
        lines: list[tuple[LineKind, float | None]] = [
            (ln.kind, ln.points) for ln in p.lines if ln.included
        ]
        probs += points_problems(_where(p.label), p.points, p.no_partial, lines)
    if q.pending:
        probs.append(f"답하지 않은 질문 {q.pending}개 — 넣기를 고르기 전에는 넣지 않습니다")
    if q.blanks:
        probs.append(
            f"강사가 적을 곳 {q.blanks}개 — 빈칸({BLANK})을 채우거나 빼야 HWPX를 받을 수 있습니다"
        )
    return probs


def edit_question(
    store: PrepStore,
    number: str,
    parts: list[PrepPart],
    *,
    explanation: str | None,
    explanation_in: bool,
    by: str | None,
) -> PrepQuestion:
    """화면에서 고친 기준을 저장한다. 새 줄(강사가 더함)은 ID를 새로 붙이고 출처는 teacher."""
    q = store.load().question(number)
    if q.status != "done":
        raise ValueError(f"{number}번은 아직 다듬지 않았습니다")
    known = {ln.id for p in q.parts for ln in p.lines}
    seen: set[str] = set()
    k = 0
    fixed: list[PrepPart] = []
    for p in parts:
        lines: list[PrepLine] = []
        for ln in p.lines:
            if ln.id not in known or ln.id in seen:
                k += 1
                while f"T{k}" in known | seen:
                    k += 1
                ln = ln.model_copy(update={"id": f"T{k}", "source": "teacher", "why": None,
                                           "question": None})  # fmt: skip
            seen.add(ln.id)
            text = ln.text.strip()
            if not text:
                continue
            pts = ln.points if ln.kind in POINTED else None
            lines.append(ln.model_copy(update={"text": text, "points": pts}))
        fixed.append(p.model_copy(update={"lines": lines}))
    done = q.model_copy(
        update={
            "parts": fixed,
            "explanation": (explanation or "").strip() or None,
            "explanation_in": explanation_in,
            "edited_by": by or "강사",
        }
    )
    store.put_question(done)
    return done


# ---------------------------------------------------------------- HWPX


def _line(kind: LineKind, text: str, points: float | None) -> str:
    pts = f"{_fmt(points)}점: " if points is not None else ""
    return {
        "item": f"◆ {pts}{text}",
        "level": f"　└ {pts}{text}",
        "accept": f"※ {text}",
        "deduct": f"(감점) {text}",
        "note": f"※ {text}",
    }[kind]


def block_lines(q: PrepQuestion) -> list[str]:
    """해설지에 넣을 문단들: (해설이 없으면 정답·AI 해설) + 부분 점수 기준 (넣는 줄만)."""
    out: list[str] = []
    if not q.has_key:
        answers = [f"{p.label} {p.answer}".strip() for p in q.parts if p.answer]
        if answers:
            out.append("[정답] " + " / ".join(answers))
    if q.explanation and q.explanation_in:
        out.append("[해설]")
        out += [s.strip() for s in q.explanation.splitlines() if s.strip()]
    single = len(q.parts) == 1 and not q.parts[0].label
    if single and q.parts[0].no_partial:
        out.append("[부분점수 없음]")
    else:
        out.append("[부분점수]")
    for p in q.parts:
        if not single:
            head = f"{p.label} {_fmt(p.points)}점".strip()
            out.append(head + (" [부분점수 없음]" if p.no_partial else ""))
        out += [_line(ln.kind, ln.text, ln.points) for ln in p.lines if ln.included]
    return out


def _marker(lines: list[str]) -> str | None:
    plain = [s for s in lines if "\\(" not in s]
    return max(plain, key=len) if plain else None


def export_name(prep: KeyPrep) -> str:
    return f"{safe_name(prep.title)}{SUFFIX}.hwpx"


@dataclasses.dataclass
class Exported:
    path: Path
    """작업공간 안의 파일."""
    warnings: list[str]
    skipped: list[str]
    """다듬지 못해 원래 해설 그대로 둔 문항."""
    pending: int
    """답하지 않아 넣지 않은 질문 수."""


def export(svc: Services, store: PrepStore) -> Exported:
    """다듬은 HWPX를 `exports/해설 다듬기/`에 쓴다. 다듬지 못한 문항은 원래 해설 그대로."""
    from dawoni.export.key_hwpx import KeyBlock, write_key_hwpx
    from dawoni.ingest.document import analyze_document, read_hwpx

    prep = store.load()
    if blanks := [q.number for q in prep.questions if q.status == "done" and q.blanks]:
        raise ValueError(
            f"강사가 적을 곳이 남았습니다 ({', '.join(blanks)}번) — 빈칸({BLANK})을 채우거나 "
            "그 줄을 빼 주세요. AI가 그림에서도 풀이로도 정할 수 없던 값입니다"
        )
    doc = read_hwpx(store.source)
    analysis = analyze_document(store.source, store.root / "check", doc=doc, images=False)
    blocks: list[KeyBlock] = []
    skipped: list[str] = []
    for q in prep.questions:
        if q.status != "done":
            skipped.append(q.number)
            continue
        now = [doc.paragraphs[i].text for i in analysis.key_index.get(q.number, [])]
        if now != q.key_paragraphs:
            raise ValueError(f"{q.number}번 해설 문단이 처음 읽은 것과 다릅니다 — 다시 넣어 주세요")
        lines = block_lines(q)
        blocks.append(KeyBlock(q.number, [k - 1 for k in q.replace], lines, marker=_marker(lines)))
    if not blocks:
        raise ValueError("다듬은 문항이 없습니다 — 먼저 다듬기를 끝내 주세요")
    dest = svc.workspace.exports / EXPORT_FOLDER / export_name(prep)
    try:
        res = write_key_hwpx(store.source, dest, blocks)
    except PermissionError as e:
        raise ValueError(f"{dest.name}을 쓸 수 없습니다 — 한글에서 열어 두었으면 닫고 다시") from e
    rel = dest.relative_to(svc.workspace.root).as_posix()

    def change(p: KeyPrep) -> None:
        p.exported = rel
        p.exported_at = utcnow()

    store.update(change)
    pending = sum(q.pending for q in prep.questions if q.status == "done")
    return Exported(dest, res.warnings, skipped, pending)
