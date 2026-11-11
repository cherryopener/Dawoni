"""`dawoni scope …`, `dawoni exam …` — 채점 파이프라인 명령줄 (M3, UI 전 단계).

    dawoni scope import scope.json                 # 범위 프로필 등록
    dawoni exam create --title 1학기 중간 --scope <범위ID> --questions questions.json
    dawoni exam solve <시험ID> [-q 3] [--verify]    # 범위 해설 (+ 독립 재풀이)
    dawoni exam rubric <시험ID> [-q 3] [--ai-solve]  # 해설지 넓히기 → 루브릭 초안
                                                   # (--ai-solve: AI가 풀어 해설지 검산)
    dawoni exam show <시험ID> -q 3                 # 해설·루브릭 보기
    dawoni exam key <시험ID> 해설지.pdf [--pages 6]  # 강사 해설지(모범 답안·부분 점수) 붙이기
    dawoni exam key-confirm <시험ID> [-q 3] --by 김선생  # 해설지 판독을 원본과 비교해 확인
    dawoni exam candidates <시험ID> -q 3   # 정답 후보 정하기 전에: 학생 답이 어느 쪽인지 (판독 후)
    dawoni exam approve <시험ID> -q 3 --by 김선생 [--accept A1 E1] [--reject A2]  # 루브릭 승인
    dawoni exam answer <시험ID> --student 홍길동 -q 3 answer.txt   # 입력한 답안(확인된 판독문)
    dawoni exam scan <시험ID> --student 홍길동 사진1.jpg 사진2.jpg [--cross]  # 손글씨 판독(미확인)
    dawoni exam scan-batch <시험ID> 답안지.pdf --per-student 3 [--classes TSS1,MSS1-J]  # 여러 학생
    dawoni exam answer-pages <시험ID> 1=2 2=3 3=4 4=5   # 문항마다 답안 쪽 (표지가 1쪽)
    dawoni exam scan-batch <시험ID> 답안지.pdf --per-student 5 -q 2   # 2번 쪽만 (강사별 분담)
    dawoni exam compare <시험ID>                   # AI 채점 ↔ 표지의 강사 점수
    dawoni exam transcript <시험ID> --student 홍길동 -q 3 [--confirm] [--replace 고친답안.txt]
    dawoni exam confirm <시험ID> [--student 홍길동] [-q 3] --by 김선생  # 확인 완료: 한글에서 고친
                                                   # 판독본 HWPX 다시 읽기 → 확정 판독문
    dawoni exam students <시험ID>                 # 표지에서 읽은 학생 (명부 연결·이름 확인 대기)
    dawoni exam student <시험ID> 김다원#5 --name 김다원B [--class 2반] [--roster S0012]
                                                 [--off-roster]   # 명부에 없는 학생으로 확정
    dawoni exam export <시험ID>                 # 채점 HWPX·점수표 다시 쓰기 (보통은 채점 때 저절로)
    dawoni exam scores <시험ID> [--by-class | --one-sheet]   # 엑셀 점수표만 (반별 시트 켜고 끄기)
    dawoni exam grade <시험ID> [--student 홍길동] [-q 3] [--by 김선생] [--double] [--regrade]
    dawoni exam assign <시험ID> -q 3 --by 김선생 [--take | --release]  # 문항 맡기 (§8)
    dawoni exam board <시험ID>                   # 진행판: 문항마다 맡은 강사·채점 현황
    dawoni exam report <시험ID>

답안 파일: 한 줄 = 답안 한 줄. `식:`으로 시작하면 수식(LaTeX) 줄, `~~`로 시작하면 지운 흔적.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from dawoni.agents import AgentError
from dawoni.config import AGENT_NAMES
from dawoni.context import TaskFailed
from dawoni.export.exam_files import ExportResult, export_scores, write_all
from dawoni.fsio import read_json
from dawoni.grading import workflow
from dawoni.grading.answers import set_answers
from dawoni.grading.pipeline import (
    PipelineError,
    SecondOpinion,
    approve_rubric,
    decide_candidates,
)
from dawoni.grading.render import key_text, rubric_text, solution_text
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.models.grading import (
    UNREADABLE,
    CandidateMatches,
    Exam,
    ExamQuestion,
    GradeResult,
    PartAnswerSides,
    Rubric,
    Transcript,
    TranscriptLine,
)
from dawoni.models.scope import ScopeProfile
from dawoni.ocr.pages import PageError
from dawoni.ocr.review import confirm_problems
from dawoni.services import Services, open_services
from dawoni.workspace import WorkspaceError


def _out(s: str = "") -> None:
    sys.stdout.write(s + "\n")


def _err(s: str) -> int:
    sys.stderr.write(f"오류: {s}\n")
    return 1


def _svc(args: argparse.Namespace) -> Services:
    svc = open_services(Path(args.workspace) if args.workspace else None)
    if getattr(args, "cli", None):
        svc.config.default_agent = args.cli
    return svc


def _progress(msg: str) -> None:
    sys.stderr.write(f"  … {msg}\n")


def _questions(exam: Exam, only: list[str] | None) -> list[ExamQuestion]:
    if not only:
        return exam.questions
    return [exam.question(n) for n in only]


# ---------------------------------------------------------------- scope


def _cmd_scope_import(args: argparse.Namespace) -> int:
    svc = _svc(args)
    scope = ScopeProfile.model_validate(read_json(Path(args.file)))
    ScopeStore(svc.workspace).save(scope)
    counts = f"개념 {len(scope.concepts)}·기법 {len(scope.skills)}"
    _out(f"범위 프로필 등록: {scope.id}  ({scope.title}, {counts})")
    return 0


def _cmd_scope_list(args: argparse.Namespace) -> int:
    for s in ScopeStore(_svc(args).workspace).all():
        _out(f"{s.id}  {s.title}  [{s.level}]  v{s.version}")
    return 0


# ---------------------------------------------------------------- exam


def _cmd_create(args: argparse.Namespace) -> int:
    svc = _svc(args)
    ScopeStore(svc.workspace).get(args.scope)  # 존재 확인
    raw = read_json(Path(args.questions))
    exam = Exam(title=args.title, scope_id=args.scope, questions=raw)
    ExamStore.create(svc.workspace, exam)
    _out(f"시험 등록: {exam.id}  ({exam.title}, {len(exam.questions)}문항)")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    svc = _svc(args)
    for eid in ExamStore.list_ids(svc.workspace):
        e = ExamStore(svc.workspace, eid).exam()
        _out(f"{e.id}  {e.title}  ({len(e.questions)}문항)")
    return 0


def _cli_progress(done: int, total: int, message: str) -> None:
    if done < total:
        _progress(message)


def _cmd_solve(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    verify = SecondOpinion(model=args.verify_model) if args.verify else None
    for sol in workflow.solve_questions(
        svc, store, args.q, verify=verify, by=args.by, progress=_cli_progress
    ):
        mark = (
            "재풀이 일치" if sol.verified else ("확인 필요" if sol.check_notes else "재풀이 안 함")
        )
        _out(
            f"문항 {sol.question_number}: 해설 {len(sol.steps)}단계, "
            f"답 {sol.final_answer.latex}  [{mark}]"
        )
        for n in sol.check_notes:
            _out(f"   ! {n}")
    return 0


VERDICT_TEXT = {
    "same": "해설지와 같음",
    "ai_wrong": "AI 풀이 오답 → 해설지 따름",
    "other_valid": "AI 답도 정답일 수 있음 → 후보",
    "key_doubt": "해설지·문제에 의문",
}
CHECK_TEXT = {
    "valid": "검산 통과",
    "invalid": "검산 실패",
    "unsure": "해석에 따라 다름",
    None: "검산 안 함",
}
STATUS_TEXT = {"proposed": "강사 미확인", "accepted": "인정", "rejected": "제외"}
KEY_CHOICE_TEXT = {
    None: "강사 미확인",
    "question": "문제 기준",
    "key": "해설지 기준",
    "edited": "직접 고침",
}


def _quote(q: str, n: int = 60) -> str:
    q = " ".join(q.split())
    return f"“{q[:n]}…”" if len(q) > n else f"“{q}”"


def sides_lines(p: PartAnswerSides, unconfirmed: list[str] | None = None) -> list[str]:
    """정답 후보를 정할 때 볼 것: 학생 답이 어느 쪽인지 (몇 명, 예시 답안)."""
    groups = [(f"해설지 답 ({p.key_answer or '없음'})", p.of("key"))]
    groups += [(f"후보 {cid} ({ans})", p.of("candidate", cid)) for cid, ans in p.candidates.items()]
    groups.append(("그 외", p.of("other")))
    total = len(p.sides) + len(p.blank)
    out = [f"  학생 답 ({p.part or '전체'}, {total}명 중)"]
    for name, xs in groups:
        out.append(f"    {name}: {len(xs)}명")
        out += [f"      {x.student} {_quote(x.quote)}" for x in xs[:3]]
        if len(xs) > 3:
            out.append(f"      … 외 {len(xs) - 3}명")
    if p.blank:
        out.append(f"    빈 답: {len(p.blank)}명")
    pending = sorted(set(unconfirmed or []) & {x.student for x in p.sides})
    if pending:
        out.append(f"    (판독 확인 전: {', '.join(pending)})")
    return out


def _rubric_review(r: Rubric, matches: CandidateMatches | None = None) -> list[str]:
    out = [f"  {'해설지 기준' if r.based_on_key else '해설지 없음 (AI 풀이 기준)'}"]
    for c in r.checks:
        mark = "" if c.verdict == "same" else " ⚠"
        out.append(f"  {c.part or '전체'}: {VERDICT_TEXT[c.verdict]}{mark}")
        if c.verdict != "same":
            out.append(f"      해설지 {c.key_answer} / AI {c.ai_answer}")
            if c.note:
                out.append(f"      {c.note}")
    for a in r.candidates:
        out.append(
            f"  정답 후보 {a.id} ({a.part or '전체'}): {a.answer} — {CHECK_TEXT[a.check]}, "
            f"{STATUS_TEXT[a.status]}"
        )
    for e in r.accepted_alternatives:
        if e.id:
            where = e.part or "전체"
            out.append(f"  넓힌 표현 {e.id} ({where}): {e.description} — {STATUS_TEXT[e.status]}")
    for k in r.key_conflicts:
        out.append(
            f"  해설지와 다른 기준 {k.id} ({k.item_id or '전체'}): {KEY_CHOICE_TEXT[k.choice]}"
        )
    if matches is not None:
        shown: set[str] = set()
        for a in r.candidates:
            p = matches.for_candidate(a)
            if p is not None and p.part not in shown:
                shown.add(p.part)
                out += sides_lines(p, matches.unconfirmed)
    out += [f"  ! {n}" for n in r.review_notes]
    return out


def _cmd_rubric(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    rubrics = workflow.rubric_questions(
        svc, store, args.q, ai_solve=args.ai_solve, by=args.by, progress=_cli_progress
    )
    for r in rubrics:
        _out(
            f"문항 {r.question_number}: 루브릭 초안 v{r.version} — "
            f"항목 {len(r.items)}개, 합계 {r.total:g}점"
        )
        for line in _rubric_review(r):
            _out(line)
    _out(
        "검토 후 `exam approve`로 승인해야 채점할 수 있습니다"
        " (정답 후보 A…·넓힌 표현 E…는 --accept/--reject)."
    )
    return 0


def _cmd_candidates(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    qs = [
        q
        for q in _questions(store.exam(), args.q)
        if (r := store.rubric(q.number)) and r.candidates
    ]
    if not qs:
        return _err("정답 후보가 있는 문항이 없습니다.")
    for q in qs:
        m = workflow.match_candidates(svc, store, q.number, progress=_cli_progress)
        r = store.rubric(q.number)
        assert r is not None
        _out(f"문항 {q.number}")
        for line in _rubric_review(r, m)[1:]:
            if not line.startswith("  ! "):
                _out(line)
        _out()
    _out("후보를 정하려면 `exam approve -q <문항> --accept <ID>` 또는 `--reject <ID>`.")
    return 0


def _cmd_key(args: argparse.Namespace) -> int:
    from dawoni.cli_ingest import parse_pages
    from dawoni.ingest.workflow import attach_answer_key

    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    res = attach_answer_key(
        svc, store, Path(args.file), parse_pages(args.pages), progress=_cli_progress
    )
    exam = store.exam()
    for k in res.keys:
        _out(key_text(k, exam.question(k.question_number)))
        _out()
    for w in res.warnings:
        _out(f"  ! {w}")
    _out("해설지를 반영하려면 `exam rubric`으로 루브릭을 다시 만드세요.")
    return 0


def _cmd_key_confirm(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    for q in _questions(store.exam(), args.q):
        k = store.answer_key(q.number)
        if k is None:
            _out(f"문항 {q.number}: 해설지 없음")
            continue
        store.save_key(k.model_copy(update={"confirmed": True, "confirmed_by": args.by}))
        _out(f"문항 {q.number}: 해설지 확인")
    _out("해설지를 고쳤다면 `exam rubric`으로 루브릭을 다시 만드세요.")
    return 0


def _cmd_answers(args: argparse.Namespace) -> int:
    """객관식·단답형 정답 고치기: 복수 정답(--answer 여러 번), 전원 정답(--all-correct)."""
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    r = store.rubric(args.q)
    if r is None or not r.code_graded:
        return _err(f"문항 {args.q}: 코드 채점 루브릭이 없습니다 (`exam rubric` 먼저).")
    r = set_answers(r, args.part or "", args.answer, all_correct=args.all_correct, by=args.by)
    store.save_rubric(r)
    _out(f"문항 {args.q}: 정답 고침 — {r.items[0].criterion if len(r.items) == 1 else '저장'}")
    _out("다시 승인해야 채점에 씁니다 (`exam approve`).")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    for q in _questions(store.exam(), args.q):
        _out(f"=== 문항 {q.number} ===")
        key = store.answer_key(q.number)
        if key:
            _out(key_text(key, q) + "\n")
        sol = store.solution(q.number)
        _out(solution_text(sol, "[AI 풀이]" if key else "[모범 해설]") if sol else "(해설 없음)")
        r = store.rubric(q.number)
        if r:
            _out(f"\n{rubric_text(r, q.parts)}\n상태: {r.status} v{r.version}")
            for line in _rubric_review(r, store.candidate_matches(q.number)):
                _out(line)
        _out()
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    for q in _questions(store.exam(), args.q):
        r = store.rubric(q.number)
        if r is None:
            return _err(f"문항 {q.number}: 루브릭이 없습니다.")
        mine = {c.id for c in r.candidates} | {a.id for a in r.accepted_alternatives if a.id}
        r = decide_candidates(
            r, [a for a in args.accept if a in mine], [x for x in args.reject if x in mine]
        )
        r = approve_rubric(r, args.by)
        store.save_rubric(r)
        pending = [c.id for c in r.candidates if c.status == "proposed"]
        left = ""
        if pending:
            left = f" (미확인 정답 후보 {', '.join(pending)} — 학생 답이 같으면 검토 필요)"
        unused = [a.id for a in r.accepted_alternatives if a.id and a.status == "proposed"]
        if unused:
            left += f" (미확인 넓힌 표현 {', '.join(unused)} — 인정 전에는 채점에 안 씀)"
        _out(f"문항 {q.number}: 루브릭 v{r.version} 승인{left}")
    return 0


def parse_answer_text(text: str) -> list[TranscriptLine]:
    lines: list[TranscriptLine] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        struck = s.startswith("~~")
        s = s.removeprefix("~~").removesuffix("~~").strip()
        if s.startswith("식:"):
            lines.append(TranscriptLine(math=s.removeprefix("식:").strip(), struck=struck))
        else:
            lines.append(TranscriptLine(text=s, struck=struck))
    return lines


def _cmd_answer(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    store.exam().question(args.q)
    text = Path(args.file).read_text(encoding="utf-8")
    t = Transcript(
        student_id=args.student,
        question_number=args.q,
        lines=parse_answer_text(text),
        source="typed",
        confirmed=True,  # 강사가 직접 입력한 답안은 확인된 판독문으로 본다
        confirmed_by=args.by,
    )
    store.save_transcript(t)
    _out(f"{args.student} 문항 {args.q}: 답안 {len(t.lines)}줄 등록")
    return 0


def _print_transcript(t: Transcript) -> None:
    head = "확인됨" if t.confirmed else "미확인"
    agree = f", 교차 판독 일치 {t.agreement:.0%}" if t.agreement is not None else ""
    attention = " ⚠ 주의해서 확인" if t.needs_attention and not t.confirmed else ""
    if t.unreadable:
        attention += f" — {UNREADABLE} {t.unreadable}곳: 채워야 확정할 수 있음"
    _out(f"[{t.student_id} 문항 {t.question_number}] {head}{agree}{attention}")
    for n, line in enumerate(t.lines, start=1):
        mark = "~~" if line.struck else ""
        body = f"{line.text} {'식: ' + line.math if line.math else ''}".strip()
        warn = f"   ← {' / '.join(line.uncertain)}" if line.uncertain else ""
        conf = f" ({line.confidence:.0%})" if line.confidence < 0.7 else ""
        _out(f"  {n:2}. {mark}{body}{mark}{conf}{warn}")
    for note in t.notes:
        _out(f"  ! {note}")
    if t.pages:
        _out(f"  원본: {', '.join(t.pages)}")


def _cmd_scan(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    cross: bool | None = True if args.cross else (False if args.no_cross else None)
    res = workflow.scan_student(
        svc,
        store,
        args.student,
        [Path(f) for f in args.files],
        cross=cross,
        cross_model=args.cross_model,
        overwrite=args.overwrite,
        progress=_cli_progress,
    )
    for w in res.warnings:
        _out(f"  ! {w}")
    for q in res.kept_confirmed:
        _out(f"문항 {q}: 이미 확인된 판독문이 있어 덮어쓰지 않음 (--overwrite)")
    for t in res.saved:
        _print_transcript(t)
    for line in res.unassigned:
        _out(f"  ? 문항을 알 수 없는 줄 ({line.page}쪽): {line.joined}")
    for note in res.notes:
        _out(f"  · {note}")
    _print_export(res.export, what="판독본")
    return _grade_ready(svc, store, [args.student] if res.saved else [])


def _cmd_scan_batch(args: argparse.Namespace) -> int:
    from dawoni.cli_ingest import parse_pages

    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    cross: bool | None = True if args.cross else (False if args.no_cross else None)
    classes = [c.strip() for c in args.classes.split(",") if c.strip()] if args.classes else None
    res = workflow.scan_batch(
        svc,
        store,
        Path(args.file),
        per_student=args.per_student,
        classes=classes,
        only_sets=parse_pages(args.sets),
        only_questions=args.q,
        covers_only=args.covers_only,
        cross=cross,
        overwrite=args.overwrite,
        progress=_cli_progress,
    )
    for r in res.students:
        scores = ", ".join(f"{q}번 {v:g}" for q, v in r.teacher_scores.items())
        unsure = f"  ? {' / '.join(r.uncertain)}" if r.uncertain else ""
        _out(
            f"{r.student_id:10} {r.class_name or '-':8} {r.school or '-':12} {r.source}"
            + (f"  [표지 점수 {scores}]" if scores else "")
            + unsure
        )
    for sid in res.skipped:
        _out(f"{sid}: 이미 판독함 — 건너뜀 (--overwrite)")
    for w in res.warnings:
        _out(f"  ! {w}")
    for f in res.failed:
        _out(f"  ✗ {f.student}: {f.reason[:300]}")
    _out(f"{len(res.students)}명 판독.")
    _print_export(res.export, what="판독본")
    if res.needs_rename:
        names = ", ".join(r.student_id for r in res.needs_rename)
        _out(f"\n이름 확인 대기 {len(res.needs_rename)}명 (같은 반 동명이인, HWPX 보류): {names}"
             " — `exam student <시험> <학생> --name 김다원A`")  # fmt: skip
    if res.not_in_roster:
        _out(f"명부와 맞추지 못함 {len(res.not_in_roster)}명 — `exam students`로 확인")
    if res.export.written:
        _out("한글에서 원본과 비교해 고친 뒤 저장하고 `exam confirm`으로 확정하세요.")
    graded = _grade_ready(svc, store, [r.student_id for r in res.students])
    return 1 if res.failed else graded


def _grade_ready(svc: Services, store: ExamStore, students: list[str]) -> int:
    """판독이 끝난 학생은 바로 채점하고, [판독불가]가 남은 답안은 입력해 달라고 알린다."""
    run = workflow.grade_ready(svc, store, students, progress=_cli_progress)
    code = _print_grade_run(run) if run else 0
    if run is None and students:
        _out("루브릭이 승인되면 채점합니다 (`exam approve` 뒤 `exam grade`).")
    waiting = [t for t in workflow.needs_input(store) if t.student_id in students]
    if waiting:
        _out(f"\n입력 필요 {len(waiting)}건 — 원본을 보고 {UNREADABLE}을 채운 뒤 확정하면 채점:")
        for t in waiting:
            where = f"--student {t.student_id} -q {t.question_number}"
            _out(f"  - {t.student_id} 문항 {t.question_number}: {UNREADABLE} {t.unreadable}곳"
                 f" (`exam transcript … {where}`)")  # fmt: skip
    return code


def _cmd_answer_pages(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    exam = store.exam()
    if args.map:
        pages: dict[str, list[int]] = {}
        for spec in list[str](args.map):
            q, _, ps = spec.partition("=")
            if not ps:
                return _err(f"'{spec}': 문항=쪽 형식 (예: 2=3, 여러 쪽이면 3=4,5)")
            pages[q.strip()] = [int(x) for x in ps.split(",") if x.strip()]
        exam = workflow.set_answer_pages(store, pages)
    for q in exam.questions:
        where = ", ".join(map(str, q.answer_pages)) + "쪽" if q.answer_pages else "정하지 않음"
        _out(f"문항 {q.number}: {where}")
    return 0


def cover_compare(args: argparse.Namespace) -> int:
    """AI 채점과 표지에 강사가 적은 문항별 점수 비교 (채점이 끝난 답안지를 넣었을 때)."""
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    exam = store.exam()
    same = total = 0
    diff_sum = 0.0
    for r in store.student_records():
        if not r.teacher_scores:
            continue
        cells: list[str] = []
        for q in exam.questions:
            t = r.teacher_scores.get(q.number)
            g = store.grade(r.student_id, q.number)
            ai = g.total if g else None
            if t is None or ai is None:
                tt = "-" if t is None else f"{t:g}"
                aa = "-" if ai is None else f"{ai:g}"
                cells.append(f"{q.number}번 강사 {tt} / AI {aa}")
                continue
            total += 1
            same += abs(t - ai) < 1e-9
            diff_sum += ai - t
            mark = "" if abs(t - ai) < 1e-9 else " ✗"
            cells.append(f"{q.number}번 {t:g}/{ai:g}{mark}")
        _out(f"{r.student_id:10} {r.class_name or '-':8} " + "  ".join(cells))
    if total:
        avg = diff_sum / total
        _out(f"\n문항 점수 일치 {same}/{total} ({same / total:.0%}), AI − 강사 평균 {avg:+.2f}점")
    return 0


def _cmd_transcript(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    t = store.transcript(args.student, args.q)
    if t is None:
        return _err(f"{args.student} 문항 {args.q}: 판독문이 없습니다.")
    before = t
    if args.replace:
        lines = parse_answer_text(Path(args.replace).read_text(encoding="utf-8"))
        t = t.model_copy(update={"lines": lines, "confirmed": False})
        _out("판독문을 고친 내용으로 바꿨습니다.")
    if args.confirm:
        if problems := confirm_problems(t):
            return _err(" ".join(problems) + " (`--replace 고친답안.txt`)")
        t = t.model_copy(update={"confirmed": True, "confirmed_by": args.by})
    if args.replace or args.confirm:
        store.save_transcript(t)
    _print_transcript(t)
    run = workflow.grade_after_confirm(svc, store, before, t, progress=_cli_progress)
    return _print_grade_run(run) if run else 0


def _cmd_grade(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    run = workflow.grade_exam(
        svc,
        store,
        students=[args.student] if args.student else None,
        only=args.q,
        double=SecondOpinion(model=args.double_model, when="all") if args.double else None,
        regrade=args.regrade,
        use_examples=not args.no_examples,
        by=args.by,
        progress=_cli_progress,
    )
    return _print_grade_run(run)


def _cmd_assign(args: argparse.Namespace) -> int:
    from dawoni.grading import assign

    store = ExamStore(_svc(args).workspace, args.exam)
    try:
        if args.release:
            assign.release(store, args.q, args.by)
            _out(f"문항 {args.q}: 맡은 강사 없음")
            return 0
        a = assign.assign(store, args.q, args.by, take=args.take)
    except assign.AssignError as e:
        return _err(str(e))
    _out(f"문항 {args.q}: {a.teacher} 선생님이 맡음")
    return 0


def _cmd_board(args: argparse.Namespace) -> int:
    from dawoni.grading import assign

    store = ExamStore(_svc(args).workspace, args.exam)
    _out("문항  맡은 강사      학생  완료  검토 필요  입력 대기  채점 전")
    for b in assign.board(store):
        _out(f"{b.question:<5} {b.teacher or '-':<12} {b.students:>4}  {b.graded:>4}  "
             f"{b.needs_review:>9}  {b.waiting_input:>9}  {b.ungraded:>7}")  # fmt: skip
    return 0


def _print_grade_run(run: workflow.GradeRun) -> int:
    for sk in run.skipped:
        hint = " (`exam transcript … --confirm`)" if sk.reason.startswith(UNREADABLE) else ""
        _out(f"{sk.student} 문항 {sk.question}: {sk.reason} — 건너뜀{hint}")
    for g in run.graded:
        mark = "검토 필요" if g.status == "needs_review" else "자동"
        if g.examples_used:
            mark += f", 기준 예시 {len(g.examples_used)}개"
        _out(f"{g.student_id} 문항 {g.question_number}: {g.total:g}/{g.max_points:g}  [{mark}]")
        for f in g.flags:
            _out(f"   ! {f}")
    if run.graded:
        code = sum(1 for g in run.graded if g.calls == 0)
        _out(
            f"AI 호출 {run.calls}번 (수리 {run.repairs}번), 토큰 {run.tokens:,}, "
            f"{run.wall_s:.0f}초 · 동시 {run.workers}개, AI 없이 {code}건"
            + (f" · 한도 신호 {run.rate_signals}번" if run.rate_signals else "")
        )
    _print_export(run.export)
    if run.failed:
        _out(f"\n실패 {len(run.failed)}건 (다시 실행하면 이 답안만 다시 채점합니다):")
        for f in run.failed:
            _out(f"  - {f.student} 문항 {f.question}: {f.reason}")
        return 1
    return 0


def _print_export(res: ExportResult, *, what: str = "HWPX") -> None:
    if res.written:
        _out(f"{what} {len(res.written)}개: {res.written[0].parent}")
        for p in res.written:
            _out(f"  · {p.name}")
    for w in res.warnings:
        _out(f"  ! {w}")


def _cmd_scores(args: argparse.Namespace) -> int:
    svc = _svc(args)
    res = export_scores(svc.workspace, ExamStore(svc.workspace, args.exam), by_class=args.by_class,
                        force=args.force)  # fmt: skip
    _print_export(res, what="점수표")
    return 1 if res.warnings else 0


def _cmd_export(args: argparse.Namespace) -> int:
    svc = _svc(args)
    res = write_all(svc.workspace, ExamStore(svc.workspace, args.exam), force=args.force)
    _print_export(res)
    if not res.written:
        _out("쓸 파일이 없습니다 (판독한 답안이 없음).")
    if res.edited:
        _out("강사가 고친 파일은 두었습니다 — `exam confirm`으로 읽어 들이거나, 고친 것을 버리려면"
             " `--force`")  # fmt: skip
    return 1 if res.warnings else 0


def _cmd_confirm(args: argparse.Namespace) -> int:
    """확인 완료: 강사가 한글에서 고친 판독본 HWPX를 다시 읽어 확정 판독문으로."""
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    run = workflow.confirm_from_files(
        svc, store, students=[args.student] if args.student else None, only=args.q,
        by=args.by, progress=_cli_progress,
    )  # fmt: skip
    for c in run.confirmed:
        what = f"{c.changed}줄 고침" if c.changed else "고친 것 없음"
        _out(f"✓ {c.transcript.student_id} 문항 {c.transcript.question_number}: 확정 ({what})")
    for c in run.waiting:
        t = c.transcript
        _out(f"… {t.student_id} 문항 {t.question_number}: {c.changed}줄 받음, {UNREADABLE}"
             f" {t.unreadable}곳이 남아 확정하지 않음 — 채워 저장한 뒤 다시")  # fmt: skip
    for p in run.problems:
        _out(f"  ✗ {p.student} 문항 {p.question}: {p.reason}")
    if run.unchanged:
        _out(f"이미 확정, 그 뒤 고치지 않음 {len(run.unchanged)}건")
    for g in run.graded:
        _out(f"채점 {g.student_id} 문항 {g.question_number}: {g.total:g}/{g.max_points:g}")
    for f in run.failed:
        _out(f"  ✗ 채점 실패 {f.student} 문항 {f.question}: {f.reason[:300]}")
    _print_export(run.export)
    return 1 if run.problems or run.failed else 0


def _cmd_students(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    exam = store.exam()
    records = {r.student_id: r for r in store.student_records()}
    for sid in sorted(set(records) | set(store.students())):
        r = records.get(sid)
        ts = [store.transcript(sid, q.number) for q in exam.questions]
        read = [t for t in ts if t is not None]
        done = [t for t in read if t.confirmed]
        marks: list[str] = []
        if r and r.needs_rename:
            marks.append("이름 확인 대기(같은 반 동명이인)")
        if r and r.roster_note:
            marks.append(r.roster_note)
        if r and r.cover_name and r.name != r.cover_name:
            marks.append(f"표지 '{r.cover_name}'")
        roster = r.roster_id if r and r.roster_id else "-"
        klass = (r.class_name if r else None) or "-"
        line = f"{sid:10} {klass:8} {roster:6} 판독 {len(read)}/{len(ts)} 확정 {len(done)}"
        _out(line + (f"  ! {'; '.join(marks)}" if marks else ""))
    return 0


def _cmd_student(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    if not (args.name or args.klass or args.roster or args.off_roster):
        return _err("--name, --class, --roster, --off-roster 중 하나는 주세요")
    res = workflow.rename_student(
        svc, store, args.student, name=args.name, class_name=args.klass, roster_id=args.roster,
        off_roster=args.off_roster,
    )  # fmt: skip
    r = res.record
    _out(f"{args.student} → {r.student_id} ({r.class_name or '-'}, 명부 {r.roster_id or '-'})")
    for w in res.warnings:
        _out(f"  ! {w}")
    _print_export(res.export, what="판독본")
    return 0


def _part_totals(store: ExamStore, q: ExamQuestion, g: GradeResult) -> str:
    r = store.rubric(q.number)
    if not q.parts or r is None:
        return ""
    got = {j.rubric_item_id: j.awarded for j in g.judgements}
    cells = [
        f"{p.label} {sum(got.get(i.id, 0) for i in r.part_items(p.label)):g}/{p.points:g}"
        for p in q.parts
    ]
    return "  (" + ", ".join(cells) + ")"


def _cmd_report(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    exam = store.exam()
    _out(f"# {exam.title}")
    for st in store.students():
        total = maximum = 0.0
        _out(f"\n## {st}")
        for q in exam.questions:
            g = store.grade(st, q.number)
            if g is None:
                _out(f"- 문항 {q.number}: (미채점)")
                continue
            total += g.total
            maximum += g.max_points
            review = " ⚠ 검토 필요" if g.status == "needs_review" else ""
            parts = _part_totals(store, q, g)
            _out(f"- 문항 {q.number}: {g.total:g}/{g.max_points:g}{review}{parts}")
            for j in g.judgements:
                q_text = " / ".join(j.evidence_quote.split("\n")) if j.evidence_quote else ""
                quote = f" — “{q_text}”" if q_text else ""
                why = f" (감점: {j.deduction_reason})" if j.deduction_reason else ""
                _out(f"    {j.rubric_item_id} {j.awarded:g}점{quote}{why}")
            _out(f"    첨삭: {g.comment}")
        _out(f"합계: {total:g}/{maximum:g}")
    return 0


def _wrap(fn: Any) -> Any:
    def run(args: argparse.Namespace) -> int:
        try:
            return int(fn(args))
        except (WorkspaceError, PipelineError, PageError, KeyError, FileNotFoundError) as e:
            return _err(str(e).strip("'\""))
        except TaskFailed as e:
            return _err(f"{e}\n  마지막 응답 일부: {e.last_response[:300]}")
        except AgentError as e:
            return _err(f"{e.user_message} ({e.code}: {e.detail[:300]})")

    return run


def register(sub: Any) -> None:
    sp = sub.add_parser("scope", help="풀이 범위 프로필")
    sp.add_argument("--workspace")
    ss = sp.add_subparsers(dest="scope_command", required=True)
    si = ss.add_parser("import", help="JSON 파일에서 범위 프로필 등록")
    si.add_argument("file")
    si.set_defaults(func=_wrap(_cmd_scope_import))
    ss.add_parser("list", help="범위 프로필 목록").set_defaults(func=_wrap(_cmd_scope_list))

    ep = sub.add_parser("exam", help="시험 채점 파이프라인")
    ep.add_argument("--workspace")
    ep.add_argument("--cli", choices=AGENT_NAMES, help="이번에 쓸 AI")
    es = ep.add_subparsers(dest="exam_command", required=True)

    c = es.add_parser("create", help="시험 등록")
    c.add_argument("--title", required=True)
    c.add_argument("--scope", required=True)
    c.add_argument("--questions", required=True, help="문항 JSON 파일 [{number, points, item}]")
    c.set_defaults(func=_wrap(_cmd_create))

    es.add_parser("list", help="시험 목록").set_defaults(func=_wrap(_cmd_list))

    def with_q(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("exam")
        p.add_argument("-q", action="append", help="문항 번호 (여러 번 가능, 없으면 전체)")
        return p

    s = with_q(es.add_parser("solve", help="범위 해설"))
    s.add_argument("--verify", action="store_true", help="독립 재풀이로 답 확인")
    s.add_argument("--verify-model", help="재풀이에 쓸 모델 (예: claude-sonnet-5-5-low)")
    s.add_argument("--by", help="만든 사람 (팀원에게 보임)")
    s.set_defaults(func=_wrap(_cmd_solve))
    rb = with_q(es.add_parser("rubric", help="루브릭 초안 (해설지가 있으면 해설지 넓히기)"))
    rb.add_argument(
        "--ai-solve",
        action="store_true",
        help="AI 풀기 (해설지 검산): AI가 해설지를 보지 않고 풀어 비교·검토·검산 (호출 여러 번)",
    )
    rb.add_argument("--by", help="만든 사람 (팀원에게 보임)")
    rb.set_defaults(func=_wrap(_cmd_rubric))
    with_q(es.add_parser("show", help="해설·루브릭 보기")).set_defaults(func=_wrap(_cmd_show))
    a = with_q(es.add_parser("approve", help="루브릭 승인"))
    a.add_argument("--by")
    a.add_argument("--accept", action="append", default=[], help="인정할 정답 후보 ID (예: A1)")
    a.add_argument("--reject", action="append", default=[], help="제외할 정답 후보 ID")
    a.set_defaults(func=_wrap(_cmd_approve))
    c = with_q(
        es.add_parser("candidates", help="정답 후보 정하기 전에: 학생 답이 어느 쪽인지 (AI)")
    )
    c.set_defaults(func=_wrap(_cmd_candidates))
    k = es.add_parser("key", help="강사 해설지(모범 답안·부분 점수) 붙이기")
    k.add_argument("exam")
    k.add_argument("file", help="해설지 PDF·DOCX·HWPX·HWP (시험지 파일의 해설 부분이어도 됨)")
    k.add_argument("--pages", help="해설 쪽 (예: 6 또는 5-6). 없으면 '부분 점수' 표시로 찾음")
    k.set_defaults(func=_wrap(_cmd_key))
    aw = es.add_parser("answers", help="객관식·단답형 정답 고치기 (복수 정답·전원 정답)")
    aw.add_argument("exam")
    aw.add_argument("-q", required=True, help="문항 번호")
    aw.add_argument("--part", help="소문항 표시 (예: (1)). 없으면 문항 전체")
    aw.add_argument("--answer", action="append", default=[], help="인정할 답 (여러 번 = 복수 정답)")
    aw.add_argument("--all-correct", action="store_true", help="전원 정답")
    aw.add_argument("--by")
    aw.set_defaults(func=_wrap(_cmd_answers))
    kc = with_q(es.add_parser("key-confirm", help="해설지 판독 확인 (원본과 비교한 뒤)"))
    kc.add_argument("--by")
    kc.set_defaults(func=_wrap(_cmd_key_confirm))

    an = es.add_parser("answer", help="답안 입력 (확인된 판독문으로 저장)")
    an.add_argument("exam")
    an.add_argument("--student", required=True)
    an.add_argument("-q", required=True)
    an.add_argument("--by")
    an.add_argument("file")
    an.set_defaults(func=_wrap(_cmd_answer))

    sc = es.add_parser("scan", help="손글씨 답안 사진·PDF 판독 (강사 확인 전 상태로 저장)")
    sc.add_argument("exam")
    sc.add_argument("--student", required=True)
    sc.add_argument("files", nargs="+")
    sc.add_argument("--cross", action="store_true", help="교차 판독 (설정이 켜져 있으면 기본)")
    sc.add_argument("--no-cross", action="store_true", help="교차 판독 끄기")
    sc.add_argument("--cross-model", help="교차 판독에 쓸 모델 (예: claude-sonnet-5-5-low)")
    sc.add_argument("--overwrite", action="store_true", help="이미 확인된 판독문도 덮어쓰기")
    sc.set_defaults(func=_wrap(_cmd_scan))

    sb = es.add_parser("scan-batch", help="여러 학생 답안지 PDF 한 번에 (표지로 학생 구분)")
    sb.add_argument("exam")
    sb.add_argument("file", help="스캔 PDF (학생마다 같은 쪽 수, 첫 쪽은 표지)")
    sb.add_argument("--per-student", type=int, required=True, help="학생 한 명의 쪽 수 (표지 포함)")
    sb.add_argument("--classes", help="반 이름 목록 (예: TSS1,TSS2,MSS1-J,MSS1-K)")
    sb.add_argument("--sets", help="일부 학생만 (몇 번째 묶음, 예: 1-8)")
    sb.add_argument("--cross", action="store_true", help="교차 판독")
    sb.add_argument("--no-cross", action="store_true", help="교차 판독 끄기")
    sb.add_argument("--overwrite", action="store_true", help="이미 판독한 학생도 다시")
    sb.add_argument(
        "-q", action="append", help="이 문항만 (그 문항의 답안 쪽만 판독 — 강사별 분담)"
    )
    sb.add_argument("--covers-only", action="store_true", help="표지(이름·반·학교)만 먼저 읽기")
    sb.set_defaults(func=_wrap(_cmd_scan_batch))
    ap = es.add_parser("answer-pages", help="문항마다 답안지의 몇 쪽에 답이 있는지 (표지가 1쪽)")
    ap.add_argument("exam")
    ap.add_argument("map", nargs="*", help="문항=쪽 (예: 1=2 2=3 3=4 4=5)")
    ap.set_defaults(func=_wrap(_cmd_answer_pages))
    tr = es.add_parser("transcript", help="판독문 보기·고치기·확정")
    tr.add_argument("exam")
    tr.add_argument("--student", required=True)
    tr.add_argument("-q", required=True)
    tr.add_argument("--replace", help="고친 답안 텍스트 파일 (answer 형식)")
    tr.add_argument("--confirm", action="store_true", help="원본과 대조 확인 완료")
    tr.add_argument("--by")
    tr.set_defaults(func=_wrap(_cmd_transcript))

    g = with_q(es.add_parser("grade", help="채점"))
    g.add_argument("--student")
    g.add_argument("--by", help="채점하는 강사 — 다른 강사가 맡은 문항은 건너뜀")
    g.add_argument(
        "--double",
        action="store_true",
        help="모든 답안 이중 채점 (기본은 확신도 낮은 것만 — 설정 second_opinion)",
    )
    g.add_argument("--double-model", help="두 번째 채점에 쓸 모델")
    g.add_argument("--regrade", action="store_true", help="이미 채점한 답안도 다시 채점")
    g.add_argument("--no-examples", action="store_true", help="강사 기준 예시 없이 채점")
    g.set_defaults(func=_wrap(_cmd_grade))

    asg = es.add_parser("assign", help="문항 맡기 (다른 강사가 맡은 문항은 채점이 잠김)")
    asg.add_argument("exam")
    asg.add_argument("-q", required=True, help="문항 번호")
    asg.add_argument("--by", required=True, help="맡을 강사")
    mode = asg.add_mutually_exclusive_group()
    mode.add_argument("--take", action="store_true", help="다른 강사가 맡은 문항 가져오기")
    mode.add_argument("--release", action="store_true", help="맡은 문항 놓기")
    asg.set_defaults(func=_wrap(_cmd_assign))
    bd = es.add_parser("board", help="진행판: 문항마다 맡은 강사·채점 현황")
    bd.add_argument("exam")
    bd.set_defaults(func=_wrap(_cmd_board))

    r = es.add_parser("report", help="채점 결과")
    r.add_argument("exam")
    r.set_defaults(func=_wrap(_cmd_report))

    ex = es.add_parser("export", help="판독본·채점 HWPX 모두 다시 쓰기 (보통은 저절로 씀)")
    ex.add_argument("exam")
    ex.add_argument("--force", action="store_true", help="강사가 한글에서 고친 파일도 덮어쓰기")
    ex.set_defaults(func=_wrap(_cmd_export))

    sc = es.add_parser("scores", help="엑셀 점수표만 다시 쓰기 (반 | 이름 | 학교 | 소문항… | 총점)")
    sc.add_argument("exam")
    way = sc.add_mutually_exclusive_group()
    way.add_argument("--by-class", dest="by_class", action="store_const", const=True,
                     help="반마다 시트 하나 (시험에 저장 — 다음부터 저절로 쓸 때도)")  # fmt: skip
    way.add_argument("--one-sheet", dest="by_class", action="store_const", const=False,
                     help="한 시트에 모두 (기본)")  # fmt: skip
    sc.add_argument("--force", action="store_true", help="엑셀에서 고친 점수표도 덮어쓰기")
    sc.set_defaults(func=_wrap(_cmd_scores), by_class=None)

    cf = es.add_parser("confirm", help="확인 완료: 한글에서 고친 판독본 HWPX → 확정 판독문")
    cf.add_argument("exam")
    cf.add_argument("--student")
    cf.add_argument("-q", action="append", help="문항 번호 (여러 번 가능, 없으면 전체)")
    cf.add_argument("--by", help="확인한 강사")
    cf.set_defaults(func=_wrap(_cmd_confirm))
    sts = es.add_parser("students", help="이 시험의 학생 (명부 연결·이름 확인 대기)")
    sts.add_argument("exam")
    sts.set_defaults(func=_wrap(_cmd_students))
    stu = es.add_parser("student", help="학생 이름·반 고치기, 명부와 잇기 (동명이인 가르기)")
    stu.add_argument("exam")
    stu.add_argument("student", help="지금 학생 ID (예: 김다원#5)")
    stu.add_argument("--name", help="새 이름 (예: 김다원B)")
    stu.add_argument("--class", dest="klass", help="새 반")
    stu.add_argument("--roster", help="명부 학생 ID (S0012) — 명부의 이름·반·학교를 쓴다")
    stu.add_argument(
        "--off-roster", action="store_true", help="명부에 없는 학생으로 확정 (이름 확인 끝)"
    )
    stu.set_defaults(func=_wrap(_cmd_student))

    from dawoni import cli_calibration

    cli_calibration.register(es, with_q, _wrap, _svc)
