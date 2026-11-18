"""`dawoni prep …` — 0단계 해설 다듬기 명령줄 (화면의 "0단계"와 같다, `dawoni.keyprep`).

dawoni prep run 시험지.hwp [--level "고1 물리"] [--title …]   # AI가 부분 점수를 촘촘하게
dawoni prep list                                           # 초안 목록
dawoni prep show <ID> [-q 2]                               # 다듬은 기준 (해설지 · AI 추천 · 질문)
dawoni prep decide <ID> -q 2 --yes L5 --no L7               # 질문에 답하기 / 추천 빼기
dawoni prep redo <ID> [-q 2]                                # 다시 다듬기 (AI)
dawoni prep export <ID> [--out 파일.hwpx]                    # 다듬은 HWPX → 1단계(exam auto)
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

from dawoni import keyprep
from dawoni.cli_exam import _cli_progress, _svc, _wrap  # pyright: ignore[reportPrivateUsage]
from dawoni.config import AGENT_NAMES
from dawoni.models.keyprep import KeyPrep, PrepLine, PrepQuestion

SOURCE_LABEL = {
    "key": "해설지",
    "ai": "AI 추천",
    "ask": "질문",
    "fill": "강사가 적기",
    "teacher": "강사",
}


def _out(s: str = "") -> None:
    sys.stdout.write(s + "\n")


def _show(q: PrepQuestion) -> None:
    pts = f" ({keyprep._fmt(q.points)}점)" if q.points else ""  # pyright: ignore[reportPrivateUsage]
    _out(f"\n■ {q.number}번{pts} — {q.status}{f': {q.error}' if q.error else ''}")
    for c in q.checks:
        if c.agrees != "same":
            _out(f"  ! {c.label or '답'} 풀이 확인 {c.agrees}: {c.answer} {c.note or ''}")
    if q.explanation:
        _out(f"  [AI 해설{'' if q.explanation_in else ' — 넣지 않음'}]")
        for s in q.explanation.splitlines():
            _out(f"    {s}")
    for p in q.parts:
        head = f"  {p.label or '문항'} {keyprep._fmt(p.points)}점"  # pyright: ignore[reportPrivateUsage]
        _out(head + (" [부분점수 없음]" if p.no_partial else ""))
        for ln in p.lines:
            mark = "□" if ln.blank else "✓" if ln.included else ("?" if ln.pending else "✗")
            pts = f"{keyprep._fmt(ln.points)}점 " if ln.points is not None else ""  # pyright: ignore[reportPrivateUsage]
            _out(f"   {mark} {ln.id} [{SOURCE_LABEL[ln.source]}] {ln.kind} {pts}{ln.text}")
            if ln.question:
                _out(f"        {'적을 것' if ln.source == 'fill' else '질문'}: {ln.question}")
    for problem in keyprep.question_problems(q):
        _out(f"  ! {problem}")


def _summary(p: KeyPrep) -> str:
    done = sum(q.status == "done" for q in p.questions)
    pending = sum(q.pending for q in p.questions)
    blanks = sum(q.blanks for q in p.questions)
    tail = f" · 적을 곳 {blanks}개" if blanks else ""
    return f"{p.id}  {p.title}  {p.state} · 문항 {done}/{len(p.questions)} · 질문 {pending}개{tail}"


def _cmd_run(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = keyprep.create(svc, Path(args.file), level=args.level, title=args.title)
    _out(f"초안 {store.id}: 문항 {len(store.load().questions)}개 — 다듬는 중")
    prep = keyprep.refine(svc, store, progress=_cli_progress)
    for q in prep.questions:
        _show(q)
    _out(f"\n{_summary(prep)}")
    _out(f"질문에 답한 뒤: dawoni prep export {store.id}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    for p in keyprep.list_preps(_svc(args).workspace):
        _out(_summary(p))
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    prep = keyprep.PrepStore(_svc(args).workspace, args.prep).load()
    _out(_summary(prep))
    for q in prep.questions:
        if not args.q or q.number in args.q:
            _show(q)
    return 0


def _cmd_decide(args: argparse.Namespace) -> int:
    store = keyprep.PrepStore(_svc(args).workspace, args.prep)
    q = store.load().question(args.q)
    yes, no = set(args.yes or []), set(args.no or [])
    fills: dict[str, str] = dict(args.fill or [])
    known = {ln.id for p in q.parts for ln in p.lines}
    if unknown := (yes | no | set(fills)) - known:
        _out(f"없는 줄: {', '.join(sorted(unknown))}")
        return 1

    def decided(ln: PrepLine) -> PrepLine:
        if ln.id in fills:
            ln = ln.model_copy(update={"text": fills[ln.id]})
        if ln.id in yes | no:
            ln = ln.model_copy(update={"decision": "in" if ln.id in yes else "out"})
        return ln

    parts = [p.model_copy(update={"lines": [decided(ln) for ln in p.lines]}) for p in q.parts]
    q = keyprep.edit_question(store, q.number, parts, explanation=q.explanation,
                              explanation_in=q.explanation_in, by=args.by)  # fmt: skip
    _show(q)
    return 0


def _cmd_redo(args: argparse.Namespace) -> int:
    svc = _svc(args)
    store = keyprep.PrepStore(svc.workspace, args.prep)
    prep = keyprep.refine(svc, store, args.q, progress=_cli_progress)
    for q in prep.questions:
        if not args.q or q.number in args.q:
            _show(q)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    svc = _svc(args)
    res = keyprep.export(svc, keyprep.PrepStore(svc.workspace, args.prep))
    path = res.path
    if args.out:
        path = Path(args.out)
        shutil.copyfile(res.path, path)
    _out(f"다듬은 해설지: {path}")
    if res.skipped:
        _out(f"  ! 다듬지 못해 원래 해설 그대로: {', '.join(res.skipped)}번")
    if res.pending:
        _out(f"  ! 답하지 않은 질문 {res.pending}개는 넣지 않았습니다")
    for w in res.warnings:
        _out(f"  ! {w}")
    _out(f'1단계: dawoni exam auto "{path}" 답안지.pdf …')
    return 0


def register(sub: Any) -> None:
    pp = sub.add_parser("prep", help="0단계 해설 다듬기: 부분 점수 기준을 촘촘하게 → 다듬은 HWPX")
    pp.add_argument("--workspace")
    pp.add_argument("--cli", choices=AGENT_NAMES, help="이번에 쓸 AI")
    ps = pp.add_subparsers(dest="prep_command", required=True)

    r = ps.add_parser("run", help="시험지·해설 HWP/HWPX를 AI로 다듬기")
    r.add_argument("file")
    r.add_argument("--level", default="물리", help="수준·과목 (예: 고1 물리)")
    r.add_argument("--title")
    r.set_defaults(func=_wrap(_cmd_run))

    ps.add_parser("list", help="초안 목록").set_defaults(func=_wrap(_cmd_list))

    s = ps.add_parser("show", help="다듬은 기준 보기")
    s.add_argument("prep")
    s.add_argument("-q", action="append", help="문항 번호 (여러 번 가능)")
    s.set_defaults(func=_wrap(_cmd_show))

    d = ps.add_parser(
        "decide", help="질문에 답하기(--yes 넣기) · 줄 빼기(--no) · 빈칸 채우기(--fill)"
    )
    d.add_argument("prep")
    d.add_argument("-q", required=True, help="문항 번호")
    d.add_argument("--yes", action="append", help="넣을 줄 ID (예: L5)")
    d.add_argument("--no", action="append", help="뺄 줄 ID")
    d.add_argument(
        "--fill", nargs=2, action="append", metavar=("ID", "글"),
        help="강사가 적을 줄의 글을 바꾼다 (예: --fill L4 \"y절편 3 cm를 표시\")",
    )  # fmt: skip
    d.add_argument("--by", help="강사")
    d.set_defaults(func=_wrap(_cmd_decide))

    rd = ps.add_parser("redo", help="다시 다듬기 (강사가 고친 것은 사라진다)")
    rd.add_argument("prep")
    rd.add_argument("-q", action="append", help="문항 번호 (없으면 전체)")
    rd.set_defaults(func=_wrap(_cmd_redo))

    e = ps.add_parser("export", help="다듬은 HWPX 쓰기")
    e.add_argument("prep")
    e.add_argument("--out", help="여기에도 복사")
    e.set_defaults(func=_wrap(_cmd_export))
