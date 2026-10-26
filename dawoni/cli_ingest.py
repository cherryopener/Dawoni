"""`dawoni ingest …` — 교재·시험지 PDF DB화 (M5).

dawoni ingest layout 시험지.pdf [--overlay 폴더]   # AI 없이 레이아웃만 (영역·그림 표시)
dawoni ingest exam 시험지.pdf --title 중간고사 --scope <범위ID> [--force]
dawoni ingest textbook 교재.pdf --title "이차함수의 최대·최소" --level "고1" [--pages 12-20]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

from dawoni.agents import AgentError
from dawoni.config import AGENT_NAMES
from dawoni.context import TaskFailed
from dawoni.grading.store import ExamStore, ScopeStore, safe_name
from dawoni.ingest.layout import DocumentLayout, analyze
from dawoni.ingest.sources import find_source, register_source, update_source
from dawoni.models.base import new_id
from dawoni.models.grading import Exam
from dawoni.models.source import SourceIngestState
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


def parse_pages(spec: str | None) -> list[int] | None:
    """'1-3,5' → [1, 2, 3, 5]."""
    if not spec:
        return None
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            pages += list(range(int(a), int(b) + 1))
        elif part:
            pages.append(int(part))
    return sorted(set(pages))


def print_layout(layout: DocumentLayout) -> None:
    for p in layout.pages:
        cols = " | ".join(f"{a:.0f}–{b:.0f}" for a, b in p.columns)
        _out(f"{p.number}쪽: 단 {len(p.columns)}개 ({cols}), 머리말 ~{p.header_y:.0f}pt")
    for r in layout.regions:
        figs: list[str] = []
        for f in r.figures:
            near = f" ←{f.near_choice}번 선지" if f.near_choice else ""
            figs.append(f"{f.id}({'이미지' if f.kind == 'image' else '선'}, {f.page}쪽{near})")
        pts = f" [{r.points:g}점]" if r.points else ""
        boxes = f", 글 상자 {len(r.textboxes)}" if r.textboxes else ""
        pages = "·".join(str(p) for p in r.pages)
        _out(f"- 문항 {r.label}{pts}: {pages}쪽, 그림 {len(r.figures)}{boxes}  {' '.join(figs)}")
    for w in layout.warnings:
        _out(f"  ! {w}")


def save_overlay(layout: DocumentLayout, out_dir: Path) -> list[Path]:
    """페이지 이미지 위에 문항 영역(초록)·그림(빨강)·글 상자(파랑)·머리말 경계(회색)."""
    from PIL import Image, ImageDraw

    from dawoni.ingest.layout import render_page

    out_dir.mkdir(parents=True, exist_ok=True)
    dpi = 100
    s = dpi / 72
    saved: list[Path] = []
    for p in layout.pages:
        img_path = render_page(layout.path, p.number, out_dir / f"page_{p.number:02d}.png", dpi=dpi)
        im = Image.open(img_path).convert("RGB")
        d = ImageDraw.Draw(im)
        d.line([(0, p.header_y * s), (im.width, p.header_y * s)], fill=(150, 150, 150), width=2)
        d.line([(0, p.footer_y * s), (im.width, p.footer_y * s)], fill=(150, 150, 150), width=2)
        for r in layout.regions:
            for page, box in r.boxes:
                if page == p.number:
                    xy = [box.x0 * s, box.y0 * s, box.x1 * s, box.y1 * s]
                    d.rectangle(xy, outline=(0, 160, 0), width=3)
                    d.text((box.x0 * s + 3, box.y0 * s + 2), r.label, fill=(0, 120, 0))
            for f in r.figures:
                if f.page == p.number:
                    xy = [f.rect.x0 * s, f.rect.y0 * s, f.rect.x1 * s, f.rect.y1 * s]
                    d.rectangle(xy, outline=(220, 0, 0), width=2)
                    d.text((f.rect.x1 * s - 18, f.rect.y0 * s + 2), f.id, fill=(220, 0, 0))
        for t in p.textboxes:
            xy = [t.rect.x0 * s, t.rect.y0 * s, t.rect.x1 * s, t.rect.y1 * s]
            d.rectangle(xy, outline=(0, 0, 220), width=2)
        im.save(img_path)
        saved.append(img_path)
    return saved


def _cmd_layout(args: argparse.Namespace) -> int:
    layout = analyze(Path(args.file))
    print_layout(layout)
    if args.overlay:
        for p in save_overlay(layout, Path(args.overlay)):
            _out(f"  표시 이미지: {p}")
    return 0


def _cmd_exam(args: argparse.Namespace) -> int:
    from dawoni.ingest.exam import extract_exam

    svc = _svc(args)
    path = Path(args.file)
    scope = ScopeStore(svc.workspace).get(args.scope)
    existing = find_source(svc.workspace, path)
    if existing and existing.ingest.status == "ingested" and not args.force:
        return _err(
            f"이미 DB화한 자료입니다 ({existing.ingest.item_count}문항, "
            f"{existing.ingest.message}). 다시 하려면 --force"
        )
    layout = analyze(path)
    if layout.scanned:
        return _err("글자 정보가 없는 스캔 PDF입니다. 아직 디지털 PDF만 지원합니다.")
    print_layout(layout)
    src = register_source(svc.workspace, path, "exam", title=args.title)
    exam_id = new_id("exam")
    exam_dir = svc.workspace.exams / exam_id
    result = extract_exam(
        svc.engine,
        layout,
        exam_dir / "ingest",
        subject=args.subject or scope.subject,
        level=scope.level,
    )
    for label, figs in result.figure_files.items():
        target = exam_dir / "figures" / safe_name(label)
        target.mkdir(parents=True, exist_ok=True)
        for fid, f in figs.items():
            shutil.copy2(f, target / f"{fid}.png")
    exam = Exam(id=exam_id, title=args.title, scope_id=scope.id, questions=result.questions)
    ExamStore.create(svc.workspace, exam)
    src.ingest = SourceIngestState(
        status="ingested", item_count=len(exam.questions), message=exam_id
    )
    update_source(svc.workspace, src)
    _out(f"\n시험 등록: {exam.id}  ({exam.title}, {len(exam.questions)}문항)")
    for q in exam.questions:
        kind = {"multiple_choice": "선택형", "short_answer": "단답형", "essay": "서술형"}[
            q.item.item_type
        ]
        _out(f"- {q.number} [{kind}, {q.points:g}점] 그림 {len(q.item.figures)}개")
    for w in result.warnings:
        _out(f"  ! {w}")
    _out("문항 내용은 `exam show`로 확인하고, 다음은 `exam solve`입니다.")
    return 0


def _cmd_textbook(args: argparse.Namespace) -> int:
    from dawoni.ingest.textbook import extract_scope

    svc = _svc(args)
    path = Path(args.file)
    src = register_source(svc.workspace, path, "textbook", title=args.title)
    draft = extract_scope(
        svc.engine,
        path,
        title=args.title,
        level=args.level,
        subject=args.subject,
        pages=parse_pages(args.pages),
    )
    ScopeStore(svc.workspace).save(draft.scope)
    src.ingest = SourceIngestState(
        status="ingested", item_count=len(draft.scope.concepts), message=draft.scope.id
    )
    update_source(svc.workspace, src)
    s = draft.scope
    _out(f"범위 프로필 초안: {s.id}  ({s.title}, {s.level})")
    for c in s.concepts:
        _out(f"  {c.id} {c.name} ({c.source}): {c.summary}")
    for k in s.skills:
        _out(f"  {k.id} {k.name} → {', '.join(k.concept_ids)}")
    for f in s.forbidden:
        _out(f"  금지: {f.name} — {f.reason}")
    for c in s.conventions:
        _out(f"  관례: {c}")
    for w in draft.warnings:
        _out(f"  ! {w}")
    path_json = svc.workspace.scopes / f"{s.id}.json"
    _out(f"검토·편집: {path_json}  (특히 '금지' 목록은 채점 공정성의 기준입니다)")
    return 0


def _wrap(fn: Any) -> Any:
    def run(args: argparse.Namespace) -> int:
        try:
            return int(fn(args))
        except (WorkspaceError, KeyError, FileNotFoundError, ValueError) as e:
            return _err(str(e).strip("'\""))
        except TaskFailed as e:
            return _err(f"{e}\n  마지막 응답 일부: {e.last_response[:300]}")
        except AgentError as e:
            return _err(f"{e.user_message} ({e.code}: {e.detail[:300]})")

    return run


def register(sub: Any) -> None:
    ip = sub.add_parser("ingest", help="교재·시험지 PDF DB화")
    ip.add_argument("--workspace")
    ip.add_argument("--cli", choices=AGENT_NAMES)
    isub = ip.add_subparsers(dest="ingest_command", required=True)

    lay = isub.add_parser("layout", help="레이아웃만 분석 (AI 없음)")
    lay.add_argument("file")
    lay.add_argument("--overlay", help="영역·그림을 표시한 페이지 이미지를 저장할 폴더")
    lay.set_defaults(func=_wrap(_cmd_layout))

    ex = isub.add_parser("exam", help="시험지 PDF → 시험")
    ex.add_argument("file")
    ex.add_argument("--title", required=True)
    ex.add_argument("--scope", required=True)
    ex.add_argument("--subject")
    ex.add_argument("--force", action="store_true", help="이미 DB화한 자료도 다시")
    ex.set_defaults(func=_wrap(_cmd_exam))

    tb = isub.add_parser("textbook", help="교재 PDF → 범위 프로필 초안")
    tb.add_argument("file")
    tb.add_argument("--title", required=True)
    tb.add_argument("--level", required=True, help="학년·과정 (예: 고등학교 1학년)")
    tb.add_argument("--subject")
    tb.add_argument("--pages", help="쪽 범위 (예: 12-20,25)")
    tb.set_defaults(func=_wrap(_cmd_textbook))
