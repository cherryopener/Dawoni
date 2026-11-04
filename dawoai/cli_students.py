"""`dawoai students …` — 학생 명부 (PLAN.md §7). 전화번호는 받지 않는다.

dawoai students import 명부.xlsx       # 엑셀·CSV (머리 줄: 이름/성명, 반, 학교)
dawoai students list [--class 2반]
dawoai students add 김다원A --class 2반 --school ○○고
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from dawoai.services import open_services
from dawoai.students import Roster
from dawoai.students.roster import same_class
from dawoai.workspace import WorkspaceError


def _out(s: str = "") -> None:
    sys.stdout.write(s + "\n")


def _err(s: str) -> int:
    sys.stderr.write(f"오류: {s}\n")
    return 1


def _roster(args: argparse.Namespace) -> Roster:
    svc = open_services(Path(args.workspace) if args.workspace else None)
    return Roster(svc.workspace)


def _cmd_import(args: argparse.Namespace) -> int:
    from dawoai.students import read_table

    rows, notes = read_table(Path(args.file))
    res = _roster(args).import_rows(rows)
    for n in notes + res.notes:
        _out(f"  · {n}")
    for s in res.added:
        _out(f"+ {s.id} {s.class_name or '-'} {s.name} {s.school or ''}".rstrip())
    for s in res.updated:
        _out(f"~ {s.id} {s.class_name or '-'} {s.name} {s.school or ''}".rstrip())
    for p in res.problems:
        _out(f"  ! {p}")
    _out(f"추가 {len(res.added)}명, 학교 고침 {len(res.updated)}명, 그대로 {res.unchanged}명")
    return 1 if res.problems else 0


def _cmd_list(args: argparse.Namespace) -> int:
    everyone = _roster(args).all()
    shown = [s for s in everyone if not args.klass or same_class(s.class_name, args.klass)]
    for s in shown:
        _out(f"{s.id}  {s.class_name or '-':8} {s.name:8} {s.school or ''}".rstrip())
    _out(f"{len(shown)}명")
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    try:
        s = _roster(args).add(args.name, args.klass, args.school)
    except ValueError as e:
        return _err(str(e))
    _out(f"{s.id} {s.class_name or '-'} {s.name} {s.school or ''}".rstrip())
    return 0


def _wrap(fn: Any) -> Any:
    def run(args: argparse.Namespace) -> int:
        try:
            return int(fn(args))
        except (WorkspaceError, FileNotFoundError, ValueError) as e:
            return _err(str(e).strip("'\""))

    return run


def register(sub: Any) -> None:
    sp = sub.add_parser("students", help="학생 명부 (이름·반·학교)")
    sp.add_argument("--workspace")
    ss = sp.add_subparsers(dest="students_command", required=True)
    si = ss.add_parser("import", help="엑셀(xlsx)·CSV에서 한 번에 넣기")
    si.add_argument("file")
    si.set_defaults(func=_wrap(_cmd_import))
    sl = ss.add_parser("list", help="명부 보기")
    sl.add_argument("--class", dest="klass")
    sl.set_defaults(func=_wrap(_cmd_list))
    sa = ss.add_parser("add", help="학생 한 명 넣기 (동명이인은 이름 뒤 알파벳)")
    sa.add_argument("name")
    sa.add_argument("--class", dest="klass")
    sa.add_argument("--school")
    sa.set_defaults(func=_wrap(_cmd_add))
