"""`dawoni scope …`, `dawoni exam …` — 채점 파이프라인 명령줄 (M3, UI 전 단계).

    dawoni scope import scope.json                 # 범위 프로필 등록
    dawoni exam create --title 1학기 중간 --scope <범위ID> --questions questions.json
    dawoni exam solve <시험ID> [-q 3] [--verify]    # 범위 해설 (+ 독립 재풀이)
    dawoni exam rubric <시험ID> [-q 3]             # 해설 → 루브릭 초안
    dawoni exam show <시험ID> -q 3                 # 해설·루브릭 보기
    dawoni exam approve <시험ID> -q 3 --by 김선생   # 루브릭 승인 (강사 단계)
    dawoni exam answer <시험ID> --student 홍길동 -q 3 answer.txt   # 입력한 답안(확인된 판독문)
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
from dawoni.grading.pipeline import (
    PipelineError,
    SecondOpinion,
    approve_rubric,
    derive_rubric,
    grade_answer,
    solve_question,
)
from dawoni.grading.render import rubric_text, solution_text
from dawoni.grading.store import ExamStore, ScopeStore
from dawoni.models.grading import Exam, ExamQuestion, Transcript, TranscriptLine
from dawoni.models.scope import ScopeProfile
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


def _cmd_solve(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    verify = SecondOpinion(model=args.verify_model) if args.verify else None
    for q in _questions(exam, args.q):
        _progress(f"문항 {q.number} 해설")
        sol = solve_question(svc.engine, q, scope, verify=verify)
        store.save_solution(sol)
        mark = (
            "재풀이 일치" if sol.verified else ("확인 필요" if sol.check_notes else "재풀이 안 함")
        )
        _out(f"문항 {q.number}: 해설 {len(sol.steps)}단계, 답 {sol.final_answer.latex}  [{mark}]")
        for n in sol.check_notes:
            _out(f"   ! {n}")
    return 0


def _cmd_rubric(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    for q in _questions(exam, args.q):
        sol = store.solution(q.number)
        if sol is None:
            return _err(f"문항 {q.number}: 해설이 없습니다. 먼저 `exam solve`를 실행하세요.")
        _progress(f"문항 {q.number} 루브릭")
        r = derive_rubric(svc.engine, q, sol, scope, previous=store.rubric(q.number))
        store.save_rubric(r)
        _out(
            f"문항 {q.number}: 루브릭 초안 v{r.version} — 항목 {len(r.items)}개, 합계 {r.total:g}점"
        )
    _out("검토 후 `exam approve`로 승인해야 채점할 수 있습니다.")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    for q in _questions(store.exam(), args.q):
        _out(f"=== 문항 {q.number} ===")
        sol = store.solution(q.number)
        _out(solution_text(sol) if sol else "(해설 없음)")
        r = store.rubric(q.number)
        if r:
            _out(f"\n{rubric_text(r)}\n상태: {r.status} v{r.version}")
        _out()
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    for q in _questions(store.exam(), args.q):
        r = store.rubric(q.number)
        if r is None:
            return _err(f"문항 {q.number}: 루브릭이 없습니다.")
        store.save_rubric(approve_rubric(r, args.by))
        _out(f"문항 {q.number}: 루브릭 v{r.version} 승인")
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


def _cmd_grade(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    exam = store.exam()
    scope = ScopeStore(svc.workspace).get(exam.scope_id)
    students = [args.student] if args.student else store.students()
    double = SecondOpinion(model=args.double_model) if args.double else None
    failed: list[str] = []
    for q in _questions(exam, args.q):
        rubric = store.rubric(q.number)
        if rubric is None:
            return _err(f"문항 {q.number}: 루브릭이 없습니다.")
        for st in students:
            t = store.transcript(st, q.number)
            if t is None:
                continue
            old = store.grade(st, q.number)
            if old is not None and not args.regrade and old.rubric_version == rubric.version:
                done = f"{old.total:g}/{old.max_points:g}"
                _out(f"{st} 문항 {q.number}: 이미 채점됨 ({done}) — 건너뜀")
                continue
            _progress(f"{st} 문항 {q.number} 채점")
            try:
                g = grade_answer(svc.engine, q, rubric, t, scope, double=double)
            except (AgentError, TaskFailed) as e:
                # 한 학생 실패로 나머지 채점을 멈추지 않는다
                failed.append(f"{st} 문항 {q.number}: {e}")
                _out(f"{st} 문항 {q.number}: 실패 — 다시 실행하면 이 답안만 다시 채점합니다")
                continue
            store.save_grade(g)
            mark = "검토 필요" if g.status == "needs_review" else "자동"
            _out(f"{st} 문항 {q.number}: {g.total:g}/{g.max_points:g}  [{mark}]")
            for f in g.flags:
                _out(f"   ! {f}")
    if failed:
        _out(f"\n실패 {len(failed)}건:")
        for f in failed:
            _out(f"  - {f}")
        return 1
    return 0


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
            _out(f"- 문항 {q.number}: {g.total:g}/{g.max_points:g}{review}")
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
        except (WorkspaceError, PipelineError, KeyError, FileNotFoundError) as e:
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
    a.set_defaults(func=_wrap(_cmd_approve))

    an = es.add_parser("answer", help="답안 입력 (확인된 판독문으로 저장)")
    an.add_argument("exam")
    an.add_argument("--student", required=True)
    an.add_argument("-q", required=True)
    an.add_argument("--by")
    an.add_argument("file")
    an.set_defaults(func=_wrap(_cmd_answer))

    g = with_q(es.add_parser("grade", help="채점"))
    g.add_argument("--student")
    g.add_argument("--double", action="store_true", help="이중 채점")
    g.add_argument("--double-model", help="두 번째 채점에 쓸 모델")
    g.add_argument("--regrade", action="store_true", help="이미 채점한 답안도 다시 채점")
    g.set_defaults(func=_wrap(_cmd_grade))

    r = es.add_parser("report", help="채점 결과")
    r.add_argument("exam")
    r.set_defaults(func=_wrap(_cmd_report))
