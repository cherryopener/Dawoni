"""문제은행 문항 → 시험(문제지·정답 및 해설 DOCX, 채점용 시험).

학생에게 줄 문서라 출처(교재·쪽·원래 번호)는 넣지 않고 번호를 1부터 다시 매긴다.
정답은 `BankItem.answer_side()`: 강사가 고른 쪽 → 원본 답 → AI 답.
해설은 원본 해설이 충분하고 AI 답과 같으면 원본, 아니면 AI 해설.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from dawoni.bank import BankError
from dawoni.bank.ingest import choice_numbers
from dawoni.export.docx.documents import KeyOptions, PaperOptions
from dawoni.export.docx.export import ExportKind, ExportOutcome, export_exam
from dawoni.export.docx.fonts import FontSpec
from dawoni.grading.store import ExamStore
from dawoni.models.bank import BankItem
from dawoni.models.content import ParagraphBlock, paragraph, runs_from_text
from dawoni.models.figure import Figure
from dawoni.models.grading import Exam, ExamQuestion, ScopedSolution
from dawoni.models.item import Answer, ChoiceAnswer, TextAnswer
from dawoni.paths import WorkspacePaths


def _original_text(it: BankItem) -> tuple[str, str]:
    """(답, 해설) — 소문항이 있으면 표시를 붙여 잇는다."""
    parts = it.original.parts if it.original else []
    ans = ", ".join(f"{p.label} {p.answer or p.answer_expr or ''}".strip() for p in parts)
    sol = "\n".join(f"{p.label} {p.solution}".strip() for p in parts if p.solution.strip())
    return ans, sol


def bank_answer(it: BankItem) -> Answer | None:
    q = it.question
    side = it.answer_side()
    text = _original_text(it)[0] if side == "original" else (
        it.solution.final_answer.latex if it.solution else ""
    )  # fmt: skip
    if q.item.item_type == "multiple_choice":
        nums = sorted(choice_numbers(text))
        return ChoiceAnswer(kind="choice", labels=nums) if nums else None
    if side == "original" and text:
        return TextAnswer(kind="text", runs=runs_from_text(text))
    return None  # AI 풀이의 최종 답을 쓴다


def _solution(it: BankItem, number: str) -> tuple[ScopedSolution | None, list[ParagraphBlock]]:
    """(AI 해설, 원본 해설 문단). 원본 해설을 쓸 때는 AI 해설을 넣지 않는다."""
    same = it.answer_check is not None and it.answer_check.verdict == "same"
    ai_ok = it.solution is not None and (it.answer_side() == "ai" or same)
    _, orig = _original_text(it)
    paras = [paragraph(*runs_from_text(line)) for line in orig.splitlines() if line.strip()]
    if not ai_ok:
        return None, paras  # 정답과 다른 답을 내는 AI 해설은 넣지 않는다
    if it.solution_by == "source" and paras:
        return None, paras
    if it.solution is None:
        return None, []
    return it.solution.model_copy(update={"question_number": number}), []


def bank_exam(
    items: list[BankItem], title: str, scope_id: str, fig_root: Path, out_root: Path
) -> tuple[Exam, dict[str, ScopedSolution], list[str]]:
    """번호를 1부터 다시 매긴 시험 + 해설 + 경고. 그림은 `out_root/figs/<문항ID>/`로 복사."""
    if not items:
        raise BankError("문항을 하나 이상 고르세요")
    questions: list[ExamQuestion] = []
    sols: dict[str, ScopedSolution] = {}
    warnings: list[str] = []
    for n, it in enumerate(items, start=1):
        number = str(n)
        figs: list[Figure] = []
        for f in it.question.item.figures:
            if f.orig and (fig_root / f.orig).is_file():
                dest = out_root / "figs" / it.id / Path(f.orig).name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(fig_root / f.orig, dest)
                f = f.model_copy(update={"orig": dest.relative_to(out_root).as_posix()})
            figs.append(f)
        sol, paras = _solution(it, number)
        item = it.question.item.model_copy(
            update={"number_label": number, "figures": figs, "answer": bank_answer(it),
                    "solution": paras or it.question.item.solution}
        )  # fmt: skip
        questions.append(it.question.model_copy(update={"number": number, "item": item}))
        if sol:
            sols[number] = sol
        if it.needs_check:
            warnings.append(f"{number}번: 원본 답과 AI 답이 달라 확인이 필요한 문항입니다")
    return Exam(title=title, scope_id=scope_id, questions=questions), sols, warnings


def export_bank(
    ws: WorkspacePaths,
    items: list[BankItem],
    kind: ExportKind,
    title: str,
    fonts: FontSpec,
    *,
    tmp_dir: Path,
    paper: PaperOptions | None = None,
    key: KeyOptions | None = None,
) -> ExportOutcome:
    if kind not in ("paper", "key"):
        raise BankError("문제은행에서는 문제지·정답 및 해설만 내보냅니다")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=tmp_dir))
    try:
        tws = WorkspacePaths(tmp)
        exam, sols, warnings = bank_exam(items, title, "bank", ws.bank, tmp / "exams" / "x")
        exam = exam.model_copy(update={"id": "x"})
        store = ExamStore.create(tws, exam)
        for s in sols.values():
            store.save_solution(s)
        ws.exports.mkdir(parents=True, exist_ok=True)
        out = export_exam(store, kind, fonts, ws.exports, paper=paper, key=key)
        out.warnings[:0] = warnings
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
