"""`dawoni export …` — Word(DOCX) 출력 (M6).

dawoni export paper  <시험ID> [--columns 2] [--no-points] [--answer-lines 8] [--subtitle …]
dawoni export key    <시험ID> [--with-questions] [--with-rubric]     # 정답 및 해설
dawoni export report <시험ID> [--student S …]                        # 채점 리포트 (강사용)
dawoni export scores <시험ID> [--student S …]                        # 성적표 (학생용)

파일은 작업공간 exports/에 저장하고(`--out`으로 바꿀 수 있음), 저장 직후 자동 검사
(한글 폰트·수식·그림)를 통과하지 못하면 오류로 알린다.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from dawoni.export.docx.builder import ExportError
from dawoni.export.docx.documents import KeyOptions, PaperOptions
from dawoni.export.docx.export import ExportKind, export_exam
from dawoni.export.docx.fonts import FontSpec
from dawoni.export.fill import FillError
from dawoni.grading.store import ExamStore
from dawoni.mathx.omml import PandocMissing
from dawoni.services import Services, open_services
from dawoni.workspace import WorkspaceError


def _out(s: str = "") -> None:
    sys.stdout.write(s + "\n")


def _err(s: str) -> int:
    sys.stderr.write(f"오류: {s}\n")
    return 1


def open_file(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform == "win32":
        os.startfile(path)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def fonts_of(svc: Services) -> FontSpec:
    d = svc.config.docx
    return FontSpec(body=d.body_font, math=d.math_font, size_pt=d.body_size_pt)


def _export(args: argparse.Namespace, kind: ExportKind, **options: Any) -> int:
    svc = open_services(Path(args.workspace) if args.workspace else None)
    store = ExamStore(svc.workspace, args.exam)
    res = export_exam(
        store,
        kind,
        fonts_of(svc),
        svc.workspace.exports,
        out=Path(args.out) if args.out else None,
        **options,
    )
    _out(f"저장: {res.path}")
    _out(f"  {res.check.summary()}")
    for w in res.warnings:
        _out(f"  ! {w}")
    if res.math_fallbacks:
        _out(f"  ! Word 수식으로 못 바꾼 학생 답안 식 {len(res.math_fallbacks)}개는 원문으로 표시")
    if not res.check.ok:
        for e in res.check.errors:
            _out(f"  ✗ {e}")
        return _err("자동 검사를 통과하지 못했습니다 (위 ✗ 항목)")
    if args.open:
        open_file(res.path)
    return 0


def _cmd_paper(args: argparse.Namespace) -> int:
    opts = PaperOptions(
        columns=args.columns,
        show_points=not args.no_points,
        answer_lines=args.answer_lines,
        subtitle=args.subtitle,
    )
    return _export(args, "paper", paper=opts)


def _cmd_key(args: argparse.Namespace) -> int:
    opts = KeyOptions(with_questions=args.with_questions, with_rubric=args.with_rubric)
    return _export(args, "key", key=opts)


def _cmd_report(args: argparse.Namespace) -> int:
    return _export(args, "report", students=args.student)


def _cmd_scores(args: argparse.Namespace) -> int:
    return _export(args, "scores", students=args.student)


def _wrap(fn: Any) -> Any:
    def run(args: argparse.Namespace) -> int:
        try:
            return int(fn(args))
        except (WorkspaceError, ExportError, FillError, PandocMissing, KeyError) as e:
            return _err(str(e).strip("'\""))

    return run


def register(sub: Any) -> None:
    xp = sub.add_parser("export", help="Word(DOCX) 출력: 시험지·해설지·채점 리포트·성적표")
    xp.add_argument("--workspace")
    xs = xp.add_subparsers(dest="export_command", required=True)

    def common(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("exam")
        p.add_argument("--out", help="저장할 파일 (기본: 작업공간 exports/)")
        p.add_argument("--open", action="store_true", help="저장 후 Word로 열기")
        return p

    pp = common(xs.add_parser("paper", help="시험지"))
    pp.add_argument("--columns", type=int, choices=(1, 2), default=2)
    pp.add_argument("--no-points", action="store_true", help="배점 표시 안 함")
    pp.add_argument("--answer-lines", type=int, default=8, help="서술형 풀이 칸 높이(줄), 0=없음")
    pp.add_argument("--subtitle", help="제목 아래 줄 (예: 고등학교 1학년 · 대수)")
    pp.set_defaults(func=_wrap(_cmd_paper))

    kp = common(xs.add_parser("key", help="정답 및 해설"))
    kp.add_argument("--with-questions", action="store_true", help="문항을 함께 싣기")
    kp.add_argument("--with-rubric", action="store_true", help="채점 기준 함께 (강사용)")
    kp.set_defaults(func=_wrap(_cmd_key))

    rp = common(xs.add_parser("report", help="채점 리포트 (강사용: 판독문·근거·불일치)"))
    rp.add_argument("--student", action="append", help="학생 (여러 번 가능, 없으면 전체)")
    rp.set_defaults(func=_wrap(_cmd_report))

    sp = common(xs.add_parser("scores", help="성적표 (학생용: 점수·첨삭·감점 내용)"))
    sp.add_argument("--student", action="append", help="학생 (여러 번 가능, 없으면 전체)")
    sp.set_defaults(func=_wrap(_cmd_scores))
