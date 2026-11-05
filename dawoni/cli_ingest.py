"""`dawoni ingest …` — 시험지 PDF·DOCX·HWPX·HWP 읽기.

dawoni ingest layout 시험지.pdf [--overlay 폴더]   # AI 없이 레이아웃만 (영역·그림·문서 미주 표시)
dawoni ingest exam 시험지.hwpx --title 중간고사 --level "고1 물리" [--force] [--key-pages 6|none]
dawoni ingest exam 시험지.pdf --title 중간고사 --scope <범위ID>   # 범위 프로필을 직접 고를 때
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from dawoni.agents import AgentError
from dawoni.config import AGENT_NAMES
from dawoni.context import TaskFailed
from dawoni.ingest.document import analyze_document, is_document, region_call
from dawoni.ingest.layout import DocumentLayout, analyze
from dawoni.ingest.workflow import ingest_exam
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
            at = f", {f.page}쪽" if f.file is None else ""
            figs.append(f"{f.id}({'이미지' if f.kind == 'image' else '선'}{at}{near})")
        pts = f" [{r.points:g}점]" if r.points else ""
        boxes = f", 글 상자 {len(r.textboxes)}" if r.textboxes else ""
        where = f"{'·'.join(str(p) for p in r.pages)}쪽" if r.boxes else f"묶음 {region_call(r)}"
        _out(f"- 문항 {r.label}{pts}: {where}, 그림 {len(r.figures)}{boxes}  {' '.join(figs)}")
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
    path = Path(args.file)
    if is_document(path):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            doc = analyze_document(path, Path(tmp))
        print_layout(doc.layout)
        for label, piece in doc.keys.items():
            first = piece.text.splitlines()[0] if piece.text else ""
            _out(f"  해설 {label}: {len(piece.text)}자, 그림 {len(piece.images)}  {first[:60]}")
        if not doc.keys:
            _out("  해설: 미주·'정답 및 해설' 구간 없음")
        return 0
    layout = analyze(path)
    print_layout(layout)
    if args.overlay:
        for p in save_overlay(layout, Path(args.overlay)):
            _out(f"  표시 이미지: {p}")
    return 0


def _cli_progress(done: int, total: int, message: str) -> None:
    if done < total:
        sys.stderr.write(f"  … {message}\n")


def parse_key_pages(spec: str | None) -> list[int] | None:
    if spec is None:
        return None  # 자동
    if spec.strip().lower() in ("none", "no", "없음", ""):
        return []
    return parse_pages(spec)


def resolve_scope(svc: Services, scope_id: str | None, level: str | None) -> str:
    """범위 프로필: `scope_id`가 있으면 그것, 없으면 수준 프리셋(`level`, 없으면 만든다)."""
    from dawoni.grading.store import ScopeStore
    from dawoni.levels import ensure_preset, parse_level

    store = ScopeStore(svc.workspace)
    if scope_id:
        return store.get(scope_id).id
    if not level:
        raise ValueError("범위를 정해 주세요: 수준(예: '고1 물리') 또는 범위 프로필 ID")
    return ensure_preset(store, parse_level(level)).id


def _cmd_exam(args: argparse.Namespace) -> int:
    svc = _svc(args)
    res = ingest_exam(
        svc,
        Path(args.file),
        title=args.title,
        scope_id=resolve_scope(svc, args.scope, args.level),
        subject=args.subject,
        force=args.force,
        key_pages=parse_key_pages(args.key_pages),
        progress=_cli_progress,
    )
    print_layout(res.layout)
    exam = res.exam
    _out(f"\n시험 등록: {exam.id}  ({exam.title}, {len(exam.questions)}문항)")
    for q in exam.questions:
        kind = {"multiple_choice": "선택형", "short_answer": "단답형", "essay": "서술형"}[
            q.item.item_type
        ]
        parts = (
            "  소문항 " + ", ".join(f"{p.label} {p.points:g}점" for p in q.parts) if q.parts else ""
        )
        _out(f"- {q.number} [{kind}, {q.points:g}점] 그림 {len(q.item.figures)}개{parts}")
    if res.key_source:
        _out(
            f"해설지: {res.key_source} → {len(res.keys)}문항 "
            "(모범 답안·부분 점수·개념, `exam show`로 확인)"
        )
    else:
        _out(
            "해설지: 없음 — 루브릭이 AI 풀이만으로 만들어집니다 (`exam key`로 나중에 붙일 수 있음)"
        )
    for w in res.warnings:
        _out(f"  ! {w}")
    _out("문항 내용은 `exam show`로 확인하고, 다음은 `exam solve`입니다.")
    return 0


def _wrap(fn: Any) -> Any:
    def run(args: argparse.Namespace) -> int:
        try:
            return int(fn(args))
        except (WorkspaceError, KeyError, FileNotFoundError, ValueError) as e:  # IngestRefused 포함
            return _err(str(e).strip("'\""))
        except TaskFailed as e:
            return _err(f"{e}\n  마지막 응답 일부: {e.last_response[:300]}")
        except AgentError as e:
            return _err(f"{e.user_message} ({e.code}: {e.detail[:300]})")

    return run


def register(sub: Any) -> None:
    ip = sub.add_parser("ingest", help="시험지 PDF·DOCX·HWPX·HWP 읽기")
    ip.add_argument("--workspace")
    ip.add_argument("--cli", choices=AGENT_NAMES)
    isub = ip.add_subparsers(dest="ingest_command", required=True)

    lay = isub.add_parser("layout", help="레이아웃만 분석 (AI 없음)")
    lay.add_argument("file")
    lay.add_argument("--overlay", help="영역·그림을 표시한 페이지 이미지를 저장할 폴더")
    lay.set_defaults(func=_wrap(_cmd_layout))

    ex = isub.add_parser("exam", help="시험지 PDF·DOCX·HWPX·HWP → 시험")
    ex.add_argument("file")
    ex.add_argument("--title", required=True)
    rng = ex.add_mutually_exclusive_group(required=True)
    rng.add_argument(
        "--level", help="수준 프리셋 (예: '고1 물리', '중3 수학') — 범위 프로필을 만든다"
    )
    rng.add_argument("--scope", help="범위 프로필 ID")
    ex.add_argument("--subject")
    ex.add_argument("--force", action="store_true", help="이미 DB화한 자료도 다시")
    ex.add_argument(
        "--key-pages",
        help="PDF 해설지 쪽 (예: 6). 없으면 '부분 점수' 표시로 자동으로 찾고, 'none'이면 해설지 "
        "없음. DOCX·HWPX는 미주·'정답 및 해설' 구간을 읽는다 ('none'이면 읽지 않음)",
    )
    ex.set_defaults(func=_wrap(_cmd_exam))
