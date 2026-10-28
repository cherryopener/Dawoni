"""`dawoni units …`, `dawoni bank …` — 공통 단원표와 문제은행.

dawoni units list
dawoni units show phy1
dawoni units add gift --subject "영재 물리" --group 물리 --toc 목차.txt

dawoni bank add 문제집.pdf --kind problem_bank --title "OO 물리 문제집" --units phy1 \\
    [--publisher OO학원] [--year 2026] [--pages 3-40 --key-pages 41-60 --unit phy1.1.1]
dawoni bank chunk <자료ID> --pages 61-90 --key-pages 91-99 [--unit phy1.2]
dawoni bank ingest <자료ID> [--chunk 2] [--by 김선생]      # AI 호출
dawoni bank sources
dawoni bank list [--unit phy1.1] [--check]
dawoni bank show <문항ID>
dawoni bank export <문항ID> … --kind paper|key --title "단원 평가"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from dawoni.agents import AgentError
from dawoni.bank import ingest as bi
from dawoni.bank.store import BankStore
from dawoni.bank.units import UnitStore, parse_toc
from dawoni.cli_ingest import parse_pages
from dawoni.context import TaskFailed
from dawoni.grading.pipeline import PipelineError
from dawoni.grading.render import exam_question_text, solution_text
from dawoni.ingest.sources import all_sources
from dawoni.models.bank import ORIGINAL_LEVEL_LABELS
from dawoni.models.source import SourceChunk
from dawoni.models.units import UnitTree
from dawoni.services import Services, open_services
from dawoni.sharing import machine_id
from dawoni.workspace import WorkspaceError

KINDS = {
    "problem_bank": "문제집·강사 교재",
    "textbook": "교과서·개념서",
    "mock_exam": "기출·모의고사",
}


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


# ---------------------------------------------------------------- 단원표


def _print_tree(t: UnitTree) -> None:
    for u, path in t.walk():
        _out(f"{'  ' * len(path)}{u.id:<16} {u.title}")


def _cmd_units_list(args: argparse.Namespace) -> int:
    for t in UnitStore(_svc(args).workspace).all():
        n = sum(1 for _ in t.walk())
        mark = "" if t.reviewed else "  (기본 초안 — 검토 필요)"
        _out(f"{t.id:<8} {t.subject} [{t.group}] 단원 {n}개 · 판 {t.version}{mark}")
    return 0


def _cmd_units_show(args: argparse.Namespace) -> int:
    _print_tree(UnitStore(_svc(args).workspace).get(args.id))
    return 0


def _cmd_units_add(args: argparse.Namespace) -> int:
    toc = Path(args.toc).read_text(encoding="utf-8") if args.toc else ""
    units = parse_toc(toc, args.id) if toc.strip() else []
    tree = UnitTree(id=args.id, subject=args.subject, group=args.group, units=units, reviewed=True)
    t = UnitStore(_svc(args).workspace).create(tree, args.by)
    _out(f"단원표 {t.id} ({t.subject}) 만듦")
    _print_tree(t)
    return 0


# ---------------------------------------------------------------- 자료 · DB화


def _chunk(args: argparse.Namespace) -> SourceChunk | None:
    pages = parse_pages(args.pages)
    if not pages:
        return None
    return SourceChunk(index=1, pages=pages, key_pages=parse_pages(args.key_pages) or [],
                       unit_hint=args.unit, label=args.label)  # fmt: skip


def _cmd_add(args: argparse.Namespace) -> int:
    svc = _svc(args)
    src = bi.add_source(svc, args.pdf, args.kind, title=args.title, publisher=args.publisher,
                        year=args.year, unit_trees=args.units.split(","))  # fmt: skip
    chunk = _chunk(args) or bi.suggest_chunk(svc, src)
    if not src.chunks:
        src = bi.add_chunks(svc, src, [chunk])
    _out(f"자료 등록: {src.title} ({KINDS.get(src.kind, src.kind)}, {src.pages}쪽)")
    _out(f"  ID {src.source_id[:12]}")
    for c in src.chunks:
        _out(f"  구간 {c.index}: 문제 {c.pages[0]}~{c.pages[-1]}쪽, "
             f"해설 {', '.join(map(str, c.key_pages)) or '없음'}")  # fmt: skip
    _out("DB화: dawoni bank ingest " + src.source_id[:12])
    return 0


def _source_id(svc: Services, prefix: str) -> str:
    hits = [s.source_id for s in all_sources(svc.workspace) if s.source_id.startswith(prefix)]
    if len(hits) != 1:
        raise KeyError(f"자료 {prefix}를 찾을 수 없거나 여러 개입니다")
    return hits[0]


def _cmd_chunk(args: argparse.Namespace) -> int:
    svc = _svc(args)
    sid = _source_id(svc, args.source)
    c = _chunk(args)
    if c is None:
        return _err("--pages를 주세요")
    src = bi.add_chunks(svc, bi.get_source(svc.workspace, sid), [c])
    _out(f"구간 {src.chunks[-1].index} 추가")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    svc = _svc(args)
    sid = _source_id(svc, args.source)
    src = bi.get_source(svc.workspace, sid)
    todo = [c.index for c in src.chunks if args.chunk in (None, c.index)
            and (args.chunk is not None or c.status != "ingested")]  # fmt: skip
    if not todo:
        _out("DB화할 구간이 없습니다 (모두 완료).")
        return 0

    def progress(done: int, total: int, msg: str) -> None:
        _out(f"  [{done}/{total}] {msg}")

    for i in todo:
        _out(f"구간 {i}")
        res = bi.ingest_chunk(svc, sid, i, by=args.by, machine=machine_id(svc.paths),
                              progress=progress)  # fmt: skip
        check = sum(it.needs_check for it in res.items)
        _out(f"  → {len(res.items)}문항 추가, 이미 있음 {len(res.skipped)}, 답 확인 필요 {check}")
        for w in res.warnings:
            _out(f"  ! {w}")
    return 0


def _cmd_sources(args: argparse.Namespace) -> int:
    for s in all_sources(_svc(args).workspace):
        if s.kind not in KINDS:
            continue
        done = sum(c.status == "ingested" for c in s.chunks)
        _out(f"{s.source_id[:12]}  {s.title}  [{KINDS[s.kind]}]  구간 {done}/{len(s.chunks)} "
             f"· {s.ingest.item_count}문항")  # fmt: skip
    return 0


# ---------------------------------------------------------------- 문항


def _resolve(units: UnitStore, unit_id: str) -> str:
    t = units.find(unit_id)
    return (t.resolve(unit_id) if t else None) or unit_id


def _cmd_list(args: argparse.Namespace) -> int:
    svc = _svc(args)
    units = UnitStore(svc.workspace)
    want: set[str] | None = None
    if args.unit:
        t = units.find(args.unit)
        want = t.descendants(args.unit) if t else set()
    for s in BankStore(svc.workspace, svc.paths.cache).summaries():
        if want is not None and not want & {_resolve(units, u) for u in s.unit_ids}:
            continue
        if args.check and not s.needs_check:
            continue
        flag = " ⚠답 확인" if s.needs_check else ""
        unit = units.label(s.unit_ids[0]).split(" > ")[-1] if s.unit_ids else "단원 없음"
        _out(f"{s.id}  [{unit}]  {s.source}{flag}")
        _out(f"    {s.preview}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    svc = _svc(args)
    it = BankStore(svc.workspace).get(args.id)
    units = UnitStore(svc.workspace)
    _out(f"{it.id}  출처: {it.source.display()}")
    _out("단원: " + (" / ".join(units.label(u) for u in it.unit_ids) or "없음"))
    _out(exam_question_text(it.question))
    if it.original:
        _out(f"\n[{ORIGINAL_LEVEL_LABELS[it.original.level]}]")
        for p in it.original.parts:
            _out(f"  {p.label} 답 {p.answer or p.answer_expr or '-'}  {p.solution}".rstrip())
    if it.answer_check:
        c = it.answer_check
        _out(f"\n원본 비교: {c.verdict}" + (f" (강사: {c.resolved})" if c.resolved else ""))
        for n in c.notes:
            _out(f"  {n}")
    if it.solution:
        _out("\n" + solution_text(it.solution, "[AI 해설]"))
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    from dawoni.bank.export import export_bank
    from dawoni.export.docx.fonts import FontSpec

    svc = _svc(args)
    d = svc.config.docx
    items = BankStore(svc.workspace).items(args.ids)
    out = export_bank(svc.workspace, items, args.kind, args.title,
                      FontSpec(body=d.body_font, math=d.math_font, size_pt=d.body_size_pt),
                      tmp_dir=svc.paths.cache / "bank_export")  # fmt: skip
    _out(f"저장: {out.path}  ({out.check.summary()})")
    for w in out.warnings:
        _out(f"  ! {w}")
    return 0


def register(sub: Any) -> None:
    from dawoni.config import AGENT_NAMES

    up = sub.add_parser("units", help="공통 단원표 (문제은행 분류 기준)")
    up.add_argument("--workspace")
    us = up.add_subparsers(dest="units_command", required=True)
    us.add_parser("list", help="단원표 목록").set_defaults(func=_wrap(_cmd_units_list))
    sh = us.add_parser("show", help="단원표 보기")
    sh.add_argument("id")
    sh.set_defaults(func=_wrap(_cmd_units_show))
    ad = us.add_parser("add", help="새 단원표 (목차 글 파일로)")
    ad.add_argument("id", help="영문 소문자·숫자 (예: gift)")
    ad.add_argument("--subject", required=True)
    ad.add_argument("--group", required=True, help="과목군 (예: 물리)")
    ad.add_argument("--toc", help="목차를 붙여 넣은 글 파일")
    ad.add_argument("--by")
    ad.set_defaults(func=_wrap(_cmd_units_add))

    bp = sub.add_parser("bank", help="문제은행: 교재·문제집·기출 DB화, 문항, DOCX")
    bp.add_argument("--workspace")
    bp.add_argument("--cli", choices=AGENT_NAMES)
    bs = bp.add_subparsers(dest="bank_command", required=True)

    def chunk_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--pages", help="문제 쪽 (예: 3-40)")
        p.add_argument("--key-pages", help="정답·해설 쪽 (예: 41-60)")
        p.add_argument("--unit", help="이 구간의 단원 ID (예: phy1.1.1)")
        p.add_argument("--label", help="구간 이름 (예: 1단원)")

    a = bs.add_parser("add", help="자료 등록 (AI 호출 없음)")
    a.add_argument("pdf")
    a.add_argument("--kind", required=True, choices=list(KINDS))
    a.add_argument("--title", required=True)
    a.add_argument("--units", required=True, help="분류할 단원표 ID (쉼표로 여러 개)")
    a.add_argument("--publisher")
    a.add_argument("--year", type=int)
    chunk_args(a)
    a.set_defaults(func=_wrap(_cmd_add))

    ch = bs.add_parser("chunk", help="구간 추가")
    ch.add_argument("source")
    chunk_args(ch)
    ch.set_defaults(func=_wrap(_cmd_chunk))

    ig = bs.add_parser("ingest", help="구간 DB화 (AI 호출)")
    ig.add_argument("source")
    ig.add_argument("--chunk", type=int, help="이 구간만 (다시 하기 포함)")
    ig.add_argument("--by")
    ig.set_defaults(func=_wrap(_cmd_ingest))

    bs.add_parser("sources", help="자료 목록").set_defaults(func=_wrap(_cmd_sources))

    ls = bs.add_parser("list", help="문항 목록")
    ls.add_argument("--unit", help="이 단원과 아래 단원")
    ls.add_argument("--check", action="store_true", help="답 확인 필요만")
    ls.set_defaults(func=_wrap(_cmd_list))

    sw = bs.add_parser("show", help="문항 보기")
    sw.add_argument("id")
    sw.set_defaults(func=_wrap(_cmd_show))

    ex = bs.add_parser("export", help="DOCX 내보내기 (출처 없음)")
    ex.add_argument("ids", nargs="+")
    ex.add_argument("--kind", choices=["paper", "key"], default="paper")
    ex.add_argument("--title", default="문제")
    ex.set_defaults(func=_wrap(_cmd_export))
