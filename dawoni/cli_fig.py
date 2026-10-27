"""`dawoni fig …` — 파라미터 그림 (M10).

dawoni fig render 스펙.json 그림.png [--var theta=30 --var m=2]   # 스펙 → PNG (AI 없음)
dawoni fig extract <시험ID> -q 3 --fig F1      # 원본 → 스펙 (AI, 원본 비교 최대 2회)
dawoni fig mode <시험ID> -q 3 --fig F1 parametric|raster
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from dawoni.agents import AgentError
from dawoni.context import TaskFailed
from dawoni.figures.common import FigureError
from dawoni.figures.render import item_values, render_spec
from dawoni.fsio import read_json
from dawoni.grading.render import question_text
from dawoni.grading.store import ExamStore
from dawoni.models.figure import Figure, FigureSpec
from dawoni.models.item import ItemContent
from dawoni.services import open_services
from dawoni.workspace import WorkspaceError


def _out(s: str = "") -> None:
    sys.stdout.write(s + "\n")


def _err(s: str) -> int:
    sys.stderr.write(f"오류: {s}\n")
    return 1


def _cmd_render(args: argparse.Namespace) -> int:
    spec = TypeAdapter[Any](FigureSpec).validate_python(read_json(Path(args.spec)))
    values: dict[str, float] = {}
    pairs: list[str] = args.var or []
    for kv in pairs:
        k, _, v = kv.partition("=")
        values[k.strip()] = float(v)
    out = render_spec(spec, values, Path(args.out))
    _out(f"저장: {out}")
    return 0


def _set_figure(store: ExamStore, q: str, fig: Figure) -> None:
    exam = store.exam()
    qq = exam.question(q)
    figs = [fig if f.id == fig.id else f for f in qq.item.figures]
    item = ItemContent.model_validate(
        {**qq.item.model_dump(), "figures": [f.model_dump() for f in figs]}
    )
    exam = exam.model_copy(
        update={
            "questions": [
                x.model_copy(update={"item": item}) if x.number == q else x for x in exam.questions
            ]
        }
    )
    store.save_exam(exam)


def _find(store: ExamStore, q: str, fid: str) -> tuple[ItemContent, Figure]:
    item = store.exam().question(q).item
    for f in item.figures:
        if f.id == fid:
            return item, f
    raise KeyError(f"그림 {fid} 없음")


def _cmd_extract(args: argparse.Namespace) -> int:
    from dawoni.figures.extract import extract_figure

    svc = open_services(Path(args.workspace) if args.workspace else None)
    store = ExamStore(svc.workspace, args.exam)
    item, fig = _find(store, args.q, args.fig)
    if not fig.orig:
        return _err("원본 이미지가 없는 그림입니다")
    q = store.exam().question(args.q)
    ex = extract_figure(
        svc.engine,
        store.root / fig.orig,
        store.root / "figures" / "_extract" / f"{args.q}_{args.fig}",
        item_text=question_text(q.number, q.points, item),
        caption=fig.caption,
        values=item_values(item),
    )
    if ex.verdict == "raster" or ex.spec is None:
        _out(f"다시 그리기 어려움 → 원본 유지: {ex.reason}")
        return 0
    _set_figure(store, args.q, fig.model_copy(update={"spec": ex.spec}))
    state = "일치" if ex.matched else "차이 남음"
    _out(f"스펙 저장 ({ex.spec.kind}, 원본 비교 {ex.rounds}회, {state})")
    for d in ex.differences:
        _out(f"  - {d}")
    _out(f"재현본: {ex.render}")
    _out("출력에 쓰려면: `dawoni fig mode … parametric`")
    return 0


def _cmd_mode(args: argparse.Namespace) -> int:
    svc = open_services(Path(args.workspace) if args.workspace else None)
    store = ExamStore(svc.workspace, args.exam)
    _, fig = _find(store, args.q, args.fig)
    new = Figure.model_validate({**fig.model_dump(), "mode": args.mode})
    _set_figure(store, args.q, new)
    _out(f"그림 {args.fig}: {args.mode}")
    return 0


def _wrap(fn: Any) -> Any:
    def run(args: argparse.Namespace) -> int:
        try:
            return int(fn(args))
        except (WorkspaceError, FigureError, KeyError, ValueError, ValidationError) as e:
            return _err(str(e).strip("'\""))
        except TaskFailed as e:
            return _err(f"{e}\n  마지막 응답 일부: {e.last_response[:300]}")
        except AgentError as e:
            return _err(f"{e.user_message} ({e.code}: {e.detail[:300]})")

    return run


def register(sub: Any) -> None:
    fp = sub.add_parser("fig", help="파라미터 그림: 그리기·원본에서 스펙 만들기·모드")
    fp.add_argument("--workspace")
    fs = fp.add_subparsers(dest="fig_command", required=True)
    r = fs.add_parser("render", help="스펙 JSON → PNG")
    r.add_argument("spec")
    r.add_argument("out")
    r.add_argument("--var", action="append", help="변수 값 (예: theta=30)")
    r.set_defaults(func=_wrap(_cmd_render))
    e = fs.add_parser("extract", help="원본 그림 → 스펙 (AI)")
    e.add_argument("exam")
    e.add_argument("-q", required=True)
    e.add_argument("--fig", required=True)
    e.set_defaults(func=_wrap(_cmd_extract))
    m = fs.add_parser("mode", help="출력에 쓸 그림: parametric(다시 그린 것) / raster(원본)")
    m.add_argument("exam")
    m.add_argument("-q", required=True)
    m.add_argument("--fig", required=True)
    m.add_argument("mode", choices=["parametric", "raster"])
    m.set_defaults(func=_wrap(_cmd_mode))
