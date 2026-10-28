"""`dawoni gen …` — 문항 제작 (M9).

dawoni gen make <시험ID> -q 1 --kind numeric [--count 2] [--max-den 20] [--sqrt]
dawoni gen make <시험ID> -q 1 --kind symbolic | condition | novel_a [--max-overlap 0.5]
dawoni gen make <시험ID> -q 1 --kind novel_b --ext-name 이름 --ext-def "정의 (식 포함)"
dawoni gen extensions <시험ID> -q 1             # 신규 B: 확장 개념 후보 제안
dawoni gen list [<시험ID>]
dawoni gen show <제작ID>
dawoni gen approve|reject <제작ID> [--by 김선생]
dawoni gen to-exam <제작ID> … --title "동형 시험"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, get_args

from dawoni.agents import AgentError
from dawoni.context import TaskFailed
from dawoni.generate import pipeline as gp
from dawoni.generate import workflow as gw
from dawoni.grading.pipeline import PipelineError
from dawoni.grading.render import question_text, rubric_text, solution_text
from dawoni.grading.store import ExamStore
from dawoni.models.generation import GEN_KIND_LABELS, GeneratedSet, VariantKind
from dawoni.models.scope import ExtensionConcept
from dawoni.services import Services, open_services
from dawoni.workspace import WorkspaceError

_STATUS = {"draft": "초안", "approved": "승인", "rejected": "버림"}


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


def _progress(done: int, total: int, msg: str) -> None:
    if done < total:
        sys.stderr.write(f"  … {msg}\n")


def summary_line(s: GeneratedSet) -> str:
    ok = "답 확인" if s.solution.verified else "답 미확인"
    rub = "루브릭 있음" if s.rubric else "루브릭 없음"
    return (
        f"{s.id}  [{GEN_KIND_LABELS[s.kind]}] {_STATUS[s.status]} · {ok} · {rub}  "
        f"(원본 {s.source.question_number}번)"
    )


def _print_set(s: GeneratedSet, *, full: bool = False) -> None:
    _out(summary_line(s))
    if s.note:
        _out(f"  {s.note}")
    for c in s.checks:
        _out(f"  ✓ {c}")
    for w in s.warnings:
        _out(f"  ! {w}")
    if full:
        _out("\n" + question_text("새", s.points, s.item))
        _out("\n" + solution_text(s.solution))
        if s.rubric:
            _out("\n" + rubric_text(s.rubric))


def _cmd_make(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = ExamStore(svc.workspace, args.exam)
    ext: ExtensionConcept | None = None
    if args.kind == "novel_b":
        if not (args.ext_name and args.ext_def):
            return _err(
                "신규 B는 --ext-name, --ext-def가 필요합니다 (`gen extensions`로 후보 보기)"
            )
        ext = ExtensionConcept(
            id=args.ext_id, name=args.ext_name, definition=args.ext_def,
            assumptions=args.ext_assume or [], expected_inference=args.ext_infer,
        )  # fmt: skip
    opts = gp.Options(
        count=args.count, max_den=args.max_den, allow_sqrt=args.sqrt, max_overlap=args.max_overlap
    )
    sets = gw.generate(svc, store, args.q, args.kind, opts=opts, extension=ext, progress=_progress)
    for s in sets:
        _print_set(s, full=args.show)
        _out()
    _out("검토 후 `gen approve <제작ID>`로 승인하고, `gen to-exam`으로 시험에 묶으세요.")
    return 0


def _cmd_extensions(args: argparse.Namespace) -> int:
    svc = _svc(args)
    p = gw.propose(svc, ExamStore(svc.workspace, args.exam), args.q)
    for i, c in enumerate(p.candidates):
        _out(f"{c.id} {c.name}: {c.definition}")
        if c.assumptions:
            _out(f"   가정: {'; '.join(c.assumptions)}")
        if c.expected_inference:
            _out(f"   유추: {c.expected_inference}")
        if i < len(p.rationale):
            _out(f"   이유: {p.rationale[i]}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    gs = gw.GenStore(_svc(args).workspace)
    sets = gs.for_exam(args.exam) if args.exam else gs.all()
    for s in sets:
        _out(summary_line(s))
    if not sets:
        _out("제작물이 없습니다.")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    _print_set(gw.GenStore(_svc(args).workspace).get(args.id), full=True)
    return 0


def _cmd_review(args: argparse.Namespace, status: str) -> int:
    s = gw.review(gw.GenStore(_svc(args).workspace), args.id, status, args.by)
    _out(summary_line(s))
    return 0


def _reviewer(status: str) -> Any:
    def run(args: argparse.Namespace) -> int:
        return _cmd_review(args, status)

    return run


def _cmd_to_exam(args: argparse.Namespace) -> int:
    exam = gw.exam_from_sets(_svc(args).workspace, args.ids, args.title)
    _out(f"시험 등록: {exam.id}  ({exam.title}, {len(exam.questions)}문항)")
    _out("루브릭은 이 시험에서 다시 승인해야 채점에 쓰입니다 (`exam approve`).")
    return 0


def _wrap(fn: Any) -> Any:
    def run(args: argparse.Namespace) -> int:
        try:
            return int(fn(args))
        except (WorkspaceError, PipelineError, KeyError, ValueError) as e:
            return _err(str(e).strip("'\""))
        except TaskFailed as e:
            return _err(f"{e}\n  마지막 응답 일부: {e.last_response[:300]}")
        except AgentError as e:
            return _err(f"{e.user_message} ({e.code}: {e.detail[:300]})")

    return run


def register(sub: Any) -> None:
    from dawoni.config import AGENT_NAMES

    gp_ = sub.add_parser("gen", help="문항 제작: 동형문제·신규 문제")
    gp_.add_argument("--workspace")
    gp_.add_argument("--cli", choices=AGENT_NAMES)
    gs = gp_.add_subparsers(dest="gen_command", required=True)

    mk = gs.add_parser("make", help="제작 (AI 호출)")
    mk.add_argument("exam")
    mk.add_argument("-q", required=True, help="기준 문항 번호")
    mk.add_argument("--kind", required=True, choices=list(get_args(VariantKind)))
    mk.add_argument("--count", type=int, default=2, help="수치형: 만들 문항 수")
    mk.add_argument("--max-den", type=int, default=20, help="수치형: 분모 상한")
    mk.add_argument("--sqrt", action="store_true", help="수치형: √n 꼴 답 허용")
    mk.add_argument("--max-overlap", type=float, default=0.5, help="신규 A: 허용 기법 겹침 비율")
    mk.add_argument("--ext-id", default="X1")
    mk.add_argument("--ext-name")
    mk.add_argument("--ext-def", help="확장 개념 정의 (식 포함)")
    mk.add_argument("--ext-assume", action="append", help="가정 (여러 번)")
    mk.add_argument("--ext-infer", help="학생이 유추할 내용")
    mk.add_argument("--show", action="store_true", help="문항·해설·루브릭 전체 출력")
    mk.set_defaults(func=_wrap(_cmd_make))

    ex = gs.add_parser("extensions", help="신규 B: 확장 개념 후보 제안 (AI 호출)")
    ex.add_argument("exam")
    ex.add_argument("-q", required=True)
    ex.set_defaults(func=_wrap(_cmd_extensions))

    ls = gs.add_parser("list", help="제작물 목록")
    ls.add_argument("exam", nargs="?")
    ls.set_defaults(func=_wrap(_cmd_list))

    sh = gs.add_parser("show", help="제작물 보기")
    sh.add_argument("id")
    sh.set_defaults(func=_wrap(_cmd_show))

    for name, status, help_ in (("approve", "approved", "승인"), ("reject", "rejected", "버림")):
        p = gs.add_parser(name, help=help_)
        p.add_argument("id")
        p.add_argument("--by")
        p.set_defaults(func=_wrap(_reviewer(status)))

    te = gs.add_parser("to-exam", help="승인한 제작물 → 새 시험")
    te.add_argument("ids", nargs="+")
    te.add_argument("--title", required=True)
    te.set_defaults(func=_wrap(_cmd_to_exam))
