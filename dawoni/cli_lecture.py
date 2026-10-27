"""`dawoni lecture …` — 유튜브 강의 요약 (M12).

dawoni lecture summarize <주소> [--scope 범위ID] [--pages 1|2] [--refresh] [--docx]
dawoni lecture list
dawoni lecture show <요약ID>
dawoni lecture export <요약ID> [-o 파일.docx]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from dawoni.agents import AgentError
from dawoni.context import TaskFailed
from dawoni.export.docx.builder import ExportError
from dawoni.export.docx.fonts import FontSpec
from dawoni.grading.store import ScopeStore
from dawoni.lecture import workflow as lw
from dawoni.lecture.summarize import stamp
from dawoni.lecture.youtube import LectureError
from dawoni.models.lecture import LectureSummary
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


def _progress(done: int, total: int, msg: str) -> None:
    if done < total:
        sys.stderr.write(f"  … {msg}\n")


def _fonts(svc: Services) -> FontSpec:
    d = svc.config.docx
    return FontSpec(body=d.body_font, math=d.math_font, size_pt=d.body_size_pt)


def summary_line(s: LectureSummary) -> str:
    pages = "1장" if s.pages == 1 else "양면 1장"
    return f"{s.id}  {s.title} — {s.video_title} ({stamp(s.duration_s)}, {pages})"


def _show(svc: Services, s: LectureSummary) -> None:
    _out(summary_line(s))
    _out(f"  {s.url}")
    if s.scope_id:
        scope = ScopeStore(svc.workspace).get(s.scope_id)
        covered = ", ".join(map(scope.name_of, s.covered)) or "없음"
        _out(f"  교재 범위 {scope.title}: 다룬 개념 {covered}")
        if s.missing:
            _out(f"  강의에 없는 교재 개념: {', '.join(map(scope.name_of, s.missing))}")
        if s.beyond:
            _out(f"  범위 밖 내용: {', '.join(s.beyond)}")
    _out(f"\n{s.overview}")
    for sec in s.sections:
        _out(f"\n[{stamp(sec.start_s)}] {sec.heading}")
        for b in sec.bullets:
            _out(f"  · {b}")
        for f in sec.formulas:
            _out(f"    {f}")
    if s.key_formulas:
        _out("\n핵심 공식: " + " | ".join(s.key_formulas))
    for t in s.tips:
        _out(f"요령: {t}")
    for q in s.review_questions:
        _out(f"확인: {q}")


def _export(svc: Services, s: LectureSummary, out: Path | None) -> int:
    res = lw.export_summary(svc.workspace, s, _fonts(svc), out=out)
    _out(f"Word: {res.path}  ({res.check.summary()})")
    for e in res.check.errors:
        _out(f"  ! {e}")
    return 0 if res.check.ok else 1


def _cmd_summarize(args: argparse.Namespace) -> int:
    svc = _svc(args)
    s = lw.run_summary(
        svc, args.url, scope_id=args.scope, pages=args.pages, refresh=args.refresh,
        progress=_progress,
    )  # fmt: skip
    _show(svc, s)
    return _export(svc, s, None) if args.docx else 0


def _cmd_list(args: argparse.Namespace) -> int:
    for s in lw.LectureStore(_svc(args).workspace).all():
        _out(summary_line(s))
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    svc = _svc(args)
    _show(svc, lw.LectureStore(svc.workspace).get(args.id))
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    svc = _svc(args)
    s = lw.LectureStore(svc.workspace).get(args.id)
    return _export(svc, s, Path(args.output) if args.output else None)


def _wrap(fn: Any) -> Any:
    def run(args: argparse.Namespace) -> int:
        try:
            return int(fn(args))
        except (WorkspaceError, LectureError, ExportError, KeyError, ValueError) as e:
            return _err(str(e).strip("'\""))
        except TaskFailed as e:
            return _err(f"{e}\n  마지막 응답 일부: {e.last_response[:300]}")
        except AgentError as e:
            return _err(f"{e.user_message} ({e.code}: {e.detail[:300]})")

    return run


def register(sub: Any) -> None:
    from dawoni.config import AGENT_NAMES

    lp = sub.add_parser("lecture", help="유튜브 강의 요약 (A4 1장·양면 1장)")
    lp.add_argument("--workspace")
    lp.add_argument("--cli", choices=AGENT_NAMES)
    ls = lp.add_subparsers(dest="lecture_command", required=True)

    sm = ls.add_parser("summarize", help="요약 만들기 (AI 호출)")
    sm.add_argument("url")
    sm.add_argument("--scope", help="비교할 범위 프로필 ID")
    sm.add_argument("--pages", type=int, choices=[1, 2], default=1, help="1=A4 1장, 2=양면 1장")
    sm.add_argument("--refresh", action="store_true", help="저장된 자막 대신 다시 받기")
    sm.add_argument("--docx", action="store_true", help="Word로도 내보내기")
    sm.set_defaults(func=_wrap(_cmd_summarize))

    li = ls.add_parser("list", help="요약 목록")
    li.set_defaults(func=_wrap(_cmd_list))

    sh = ls.add_parser("show", help="요약 보기")
    sh.add_argument("id")
    sh.set_defaults(func=_wrap(_cmd_show))

    ex = ls.add_parser("export", help="Word로 내보내기")
    ex.add_argument("id")
    ex.add_argument("-o", "--output")
    ex.set_defaults(func=_wrap(_cmd_export))
