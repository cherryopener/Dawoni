"""문항 제작 흐름: 시험의 문항 하나 → 제작물 세트(초안) 저장 → 강사 승인 → 새 시험으로 묶기.

제작물은 작업공간 `bank/generated/<ID>.json`에 하나씩 저장한다 (항목 1개 = 파일 1개).
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from dawoni.fsio import read_json, write_model_atomic
from dawoni.generate import checks as gc
from dawoni.generate import pipeline as gp
from dawoni.grading.pipeline import PipelineError
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.models.base import new_id
from dawoni.models.figure import Figure
from dawoni.models.generation import ExtensionProposals, GeneratedSet, SourceRef, VariantKind
from dawoni.models.grading import Exam, ExamQuestion, Rubric, SolutionDraft
from dawoni.models.item import ItemContent
from dawoni.models.scope import ExtensionConcept, ScopeProfile
from dawoni.paths import WorkspacePaths
from dawoni.services import Services

Progress = Callable[[int, int, str], None]


def _noop(done: int, total: int, message: str) -> None:
    pass


class GenStore:
    def __init__(self, ws: WorkspacePaths) -> None:
        self.ws = ws
        self.dir = ws.bank / "generated"

    def _path(self, gen_id: str) -> Path:
        return self.dir / f"{gen_id}.json"

    def save(self, s: GeneratedSet) -> GeneratedSet:
        write_model_atomic(self._path(s.id), s)
        return s

    def get(self, gen_id: str) -> GeneratedSet:
        p = self._path(gen_id)
        if not p.exists():
            raise KeyError(f"제작물 {gen_id} 없음")
        return GeneratedSet.model_validate(read_json(p))

    def all(self) -> list[GeneratedSet]:
        if not self.dir.is_dir():
            return []
        out = [GeneratedSet.model_validate(read_json(p)) for p in self.dir.glob("*.json")]
        return sorted(out, key=lambda s: s.created_at, reverse=True)

    def for_exam(self, exam_id: str) -> list[GeneratedSet]:
        return [s for s in self.all() if s.source.exam_id == exam_id]

    def for_bank_item(self, item_id: str) -> list[GeneratedSet]:
        return [s for s in self.all() if s.source.bank_item_id == item_id]


def same_scope_items(
    ws: WorkspacePaths, scope_id: str, *, exclude: tuple[str, str] | None = None
) -> list[tuple[str, ItemContent, set[str]]]:
    """같은 범위 프로필을 쓰는 시험 문항·제작물 (신규 A의 중복 검사 DB)."""
    out: list[tuple[str, ItemContent, set[str]]] = []
    for eid in ExamStore.list_ids(ws):
        store = ExamStore(ws, eid)
        exam = store.exam()
        if exam.scope_id != scope_id:
            continue
        for q in exam.questions:
            if exclude == (eid, q.number):
                continue
            sol = store.solution(q.number)
            out.append((f"{exam.title} {q.number}번", q.item, gc.skill_ids(sol) if sol else set()))
    for s in GenStore(ws).all():
        if s.scope_id == scope_id and s.status != "rejected":
            out.append((f"제작물 {s.id[-6:]}", s.item, set(s.skill_ids)))
    return out


def generate(
    svc: Services,
    store: ExamStore,
    q_number: str,
    kind: VariantKind,
    *,
    opts: gp.Options | None = None,
    extension: ExtensionConcept | None = None,
    progress: Progress = _noop,
) -> list[GeneratedSet]:
    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    others = (
        same_scope_items(svc.workspace, scope.id, exclude=(exam.id, q_number))
        if kind == "novel_a" else []
    )  # fmt: skip
    return generate_from(
        svc, exam.question(q_number), store.solution(q_number), scope,
        SourceRef(exam_id=exam.id, question_number=q_number), kind,
        opts=opts, extension=extension, others=others, progress=progress,
    )  # fmt: skip


def generate_from(
    svc: Services,
    src: ExamQuestion,
    src_sol: SolutionDraft | None,
    scope: ScopeProfile,
    source: SourceRef,
    kind: VariantKind,
    *,
    opts: gp.Options | None = None,
    extension: ExtensionConcept | None = None,
    others: list[tuple[str, ItemContent, set[str]]] | None = None,
    progress: Progress = _noop,
) -> list[GeneratedSet]:
    """기준 문항 하나(시험 문항이든 문제은행 문항이든) → 제작물 초안들."""
    opts = opts or gp.Options()
    if kind == "novel_b" and extension is None:
        raise PipelineError("신규 B는 확장 개념이 필요합니다 (직접 입력하거나 AI 후보에서 고르기).")
    if kind == "numeric" and src.item.has_figure_choices:
        raise PipelineError("그림 선지 문항은 수치형으로 만들 수 없습니다. '조건 변형'을 쓰세요.")

    progress(0, 1, "AI가 문항 틀·초안 작성")
    warnings: list[str] = []
    match kind:
        case "numeric":
            drafts, warnings = gp.numeric_drafts(svc.engine, src, src_sol, scope, opts)
        case "symbolic":
            drafts = [gp.symbolic_draft(svc.engine, src, src_sol, scope)]
        case "condition":
            drafts = [gp.condition_draft(svc.engine, src, src_sol, scope)]
        case "novel_a":
            drafts = [gp.novel_a_draft(svc.engine, src, src_sol, scope, others or [], opts)]
        case "novel_b":
            assert extension is not None
            drafts = [gp.novel_b_draft(svc.engine, src, src_sol, scope, extension)]
    total = 1 + len(drafts)
    out: list[GeneratedSet] = []
    gs = GenStore(svc.workspace)
    for k, d in enumerate(drafts, start=1):
        progress(k, total, f"제작물 {k}/{len(drafts)}: 독립 풀이로 확인 · 루브릭")
        s = gp.finish(svc.engine, d, kind, src, scope, source)
        s.warnings = [*warnings, *s.warnings]
        out.append(gs.save(s))
    progress(total, total, f"제작 {len(out)}개 완료 — 검토 후 승인하세요")
    return out


def propose(svc: Services, store: ExamStore, q_number: str) -> ExtensionProposals:
    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    return gp.propose_extensions(
        svc.engine, exam.question(q_number), store.solution(q_number), scope
    )


def review(gs: GenStore, gen_id: str, status: str, by: str | None) -> GeneratedSet:
    s = gs.get(gen_id)
    if status not in ("approved", "rejected", "draft"):
        raise ValueError(f"상태 {status}?")
    if status == "approved" and s.rubric is None:
        raise PipelineError("루브릭이 없는 제작물은 승인할 수 없습니다 (답이 확인되지 않음).")
    return gs.save(s.model_copy(update={"status": status, "reviewed_by": by}))


def exam_from_sets(ws: WorkspacePaths, gen_ids: list[str], title: str) -> Exam:
    """승인한 제작물들 → 새 시험 (해설 + 루브릭 초안 포함, 루브릭은 시험에서 다시 승인)."""
    gs = GenStore(ws)
    sets = [gs.get(i) for i in gen_ids]
    if not sets:
        raise ValueError("제작물을 하나 이상 고르세요")
    not_ok = [s.id for s in sets if s.status != "approved"]
    if not_ok:
        raise PipelineError(f"승인하지 않은 제작물이 있습니다: {not_ok}")
    scopes = {s.scope_id for s in sets}
    if len(scopes) != 1:
        raise PipelineError("범위 프로필이 다른 제작물은 한 시험으로 묶을 수 없습니다")
    exam_id = new_id("exam")
    root = ws.exams / exam_id
    questions: list[ExamQuestion] = []
    for n, s in enumerate(sets, start=1):
        # 그림은 원래 시험 폴더 기준 경로 → 새 시험 폴더로 복사하고 경로를 바꾼다
        src_root = ws.exams / s.source.exam_id if s.source.exam_id else ws.bank
        figures: list[Figure] = []
        for f in s.item.figures:
            if f.orig:
                rel = f"figures/{n}/{f.id}{Path(f.orig).suffix or '.png'}"
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                src = src_root / f.orig
                if src.is_file():
                    shutil.copy2(src, root / rel)
                f = f.model_copy(update={"orig": rel})
            figures.append(f)
        item = s.item.model_copy(update={"figures": figures})
        questions.append(ExamQuestion(number=str(n), points=s.points, item=item))
    exam = Exam(id=exam_id, title=title, scope_id=scopes.pop(), questions=questions)
    store = ExamStore.create(ws, exam)
    for n, s in enumerate(sets, start=1):
        store.save_solution(s.solution.model_copy(update={"question_number": str(n)}))
        if s.rubric is not None:
            store.save_rubric(Rubric(**s.rubric.model_dump(), question_number=str(n)))
    return exam
