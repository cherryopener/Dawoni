"""`dawoni scope …`, `dawoni exam …` — 채점 파이프라인 명령줄 (M3, UI 전 단계).

    dawoni scope import scope.json                 # 범위 프로필 등록
    dawoni exam create --title 1학기 중간 --scope <범위ID> --questions questions.json
    dawoni exam solve <시험ID> [-q 3] [--verify]    # 범위 해설 (+ 독립 재풀이)
    dawoni exam rubric <시험ID> [-q 3]             # 해설 → 루브릭 초안
    dawoni exam show <시험ID> -q 3                 # 해설·루브릭 보기
    dawoni exam key <시험ID> 해설지.pdf [--pages 6]  # 강사 해설지(모범 답안·부분 점수) 붙이기
    dawoni exam key-confirm <시험ID> [-q 3] --by 김선생  # 해설지 판독을 원본과 비교해 확인
    dawoni exam approve <시험ID> -q 3 --by 김선생 [--accept A1] [--reject A2]  # 루브릭 승인
    dawoni exam answer <시험ID> --student 홍길동 -q 3 answer.txt   # 입력한 답안(확인된 판독문)
    dawoni exam scan <시험ID> --student 홍길동 사진1.jpg 사진2.jpg [--cross]  # 손글씨 판독(미확인)
    dawoni exam scan-batch <시험ID> 답안지.pdf --per-student 3 [--classes TSS1,MSS1-J]  # 여러 학생
    dawoni exam compare <시험ID>                   # AI 채점 ↔ 표지의 강사 점수
    dawoni exam transcript <시험ID> --student 홍길동 -q 3 [--confirm] [--replace 고친답안.txt]
    dawoni exam grade <시험ID> [--student 홍길동] [-q 3] [--double] [--regrade]
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
from dawoni.fsio import read_json
from dawoni.grading import workflow
from dawoni.grading.pipeline import (
    PipelineError,
    SecondOpinion,
    approve_rubric,
    decide_candidates,
)
from dawoni.grading.render import key_text, rubric_text, solution_text
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.models.grading import (
    Exam,
    ExamQuestion,
    GradeResult,
    Rubric,
    Transcript,
    TranscriptLine,
)
from dawoni.models.scope import ScopeProfile
from dawoni.ocr.pages import PageError
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
    for sol in workflow.solve_questions(svc, store, args.q, verify=verify, progress=_cli_progress):
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


def _rubric_review(r: Rubric) -> list[str]:
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
    out += [f"  ! {n}" for n in r.review_notes]
    return out


def _cmd_rubric(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    for r in workflow.rubric_questions(svc, store, args.q, progress=_cli_progress):
        _out(
            f"문항 {r.question_number}: 루브릭 초안 v{r.version} — "
            f"항목 {len(r.items)}개, 합계 {r.total:g}점"
        )
        for line in _rubric_review(r):
            _out(line)
    _out("검토 후 `exam approve`로 승인해야 채점할 수 있습니다 (정답 후보는 --accept/--reject).")
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
            for line in _rubric_review(r):
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
        mine = {c.id for c in r.candidates}
        r = decide_candidates(
            r, [a for a in args.accept if a in mine], [x for x in args.reject if x in mine]
        )
        r = approve_rubric(r, args.by)
        store.save_rubric(r)
        pending = [c.id for c in r.candidates if c.status == "proposed"]
        left = ""
        if pending:
            left = f" (미확인 정답 후보 {', '.join(pending)} — 학생 답이 같으면 검토 필요)"
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
    _out("원본과 비교해 확인한 뒤 `exam transcript … --confirm`으로 확정해야 채점할 수 있습니다.")
    return 0


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
    _out(f"{len(res.students)}명 판독. 원본과 비교해 확인한 뒤 `exam transcript … --confirm`")
    return 1 if res.failed else 0


def _cmd_compare(args: argparse.Namespace) -> int:
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
    if args.replace:
        lines = parse_answer_text(Path(args.replace).read_text(encoding="utf-8"))
        t = t.model_copy(update={"lines": lines, "confirmed": False})
        _out("판독문을 고친 내용으로 바꿨습니다.")
    if args.confirm:
        t = t.model_copy(update={"confirmed": True, "confirmed_by": args.by})
    if args.replace or args.confirm:
        store.save_transcript(t)
    _print_transcript(t)
    return 0


def _cmd_grade(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    run = workflow.grade_exam(
        svc,
        store,
        students=[args.student] if args.student else None,
        only=args.q,
        double=SecondOpinion(model=args.double_model) if args.double else None,
        regrade=args.regrade,
        use_examples=not args.no_examples,
        progress=_cli_progress,
    )
    for sk in run.skipped:
        hint = " (`exam transcript … --confirm`)" if sk.reason.startswith("판독문") else ""
        _out(f"{sk.student} 문항 {sk.question}: {sk.reason} — 건너뜀{hint}")
    for g in run.graded:
        mark = "검토 필요" if g.status == "needs_review" else "자동"
        if g.examples_used:
            mark += f", 기준 예시 {len(g.examples_used)}개"
        _out(f"{g.student_id} 문항 {g.question_number}: {g.total:g}/{g.max_points:g}  [{mark}]")
        for f in g.flags:
            _out(f"   ! {f}")
    if run.failed:
        _out(f"\n실패 {len(run.failed)}건 (다시 실행하면 이 답안만 다시 채점합니다):")
        for f in run.failed:
            _out(f"  - {f.student} 문항 {f.question}: {f.reason}")
        return 1
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
    s.set_defaults(func=_wrap(_cmd_solve))
    with_q(es.add_parser("rubric", help="루브릭 초안")).set_defaults(func=_wrap(_cmd_rubric))
    with_q(es.add_parser("show", help="해설·루브릭 보기")).set_defaults(func=_wrap(_cmd_show))
    a = with_q(es.add_parser("approve", help="루브릭 승인"))
    a.add_argument("--by")
    a.add_argument("--accept", action="append", default=[], help="인정할 정답 후보 ID (예: A1)")
    a.add_argument("--reject", action="append", default=[], help="제외할 정답 후보 ID")
    a.set_defaults(func=_wrap(_cmd_approve))
    k = es.add_parser("key", help="강사 해설지(모범 답안·부분 점수) 붙이기")
    k.add_argument("exam")
    k.add_argument("file", help="해설지 PDF (시험지 PDF의 해설 쪽이어도 됨)")
    k.add_argument("--pages", help="해설 쪽 (예: 6 또는 5-6). 없으면 '부분 점수' 표시로 찾음")
    k.set_defaults(func=_wrap(_cmd_key))
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
    sb.set_defaults(func=_wrap(_cmd_scan_batch))
    cp = es.add_parser("compare", help="AI 채점 ↔ 표지의 강사 점수 비교")
    cp.add_argument("exam")
    cp.set_defaults(func=_wrap(_cmd_compare))

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
    g.add_argument("--double", action="store_true", help="이중 채점")
    g.add_argument("--double-model", help="두 번째 채점에 쓸 모델")
    g.add_argument("--regrade", action="store_true", help="이미 채점한 답안도 다시 채점")
    g.add_argument("--no-examples", action="store_true", help="강사 기준 예시 없이 채점")
    g.set_defaults(func=_wrap(_cmd_grade))

    r = es.add_parser("report", help="채점 결과")
    r.add_argument("exam")
    r.set_defaults(func=_wrap(_cmd_report))

    from dawoni import cli_calibration

    cli_calibration.register(es, with_q, _wrap, _svc)
