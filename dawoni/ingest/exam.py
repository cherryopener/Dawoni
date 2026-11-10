"""시험지 PDF·DOCX·HWPX → 시험(문항 + 그림). 레이아웃은 코드가, 문장 구조화는 AI가.

    레이아웃 분석(결정적) → 문항 영역 이미지·그림 크롭 → 쪽 단위 AI 구조화 → 코드 검증 → Exam

코드 검증 (AI 출력을 그대로 믿지 않음):
- 주어진 문항 번호를 빠짐없이, 지어낸 번호 없이
- 그림 ID는 레이아웃이 찾은 것만, 모두 역할을 받아야 함
- 레이아웃이 ①~⑤ 옆에서 찾은 그림은 그 번호의 선지여야 함
- 본문의 한글이 PDF 글자에 있어야 함 (지어낸 문장 차단)
- 모든 식이 Word 수식(OMML)으로 바뀔 것 (출력 시점 실패 방지)
- 최종 문항은 `ItemContent` 검증 통과 (그림 선지 규칙 등)
"""

from __future__ import annotations

import dataclasses
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from dawoni.context import ContextEngine, TaskDef, TaskFailed, TaskInput
from dawoni.context.extract import describe_validation_error
from dawoni.grading.render import blocks_text, runs_text
from dawoni.grading.store import safe_name
from dawoni.ingest.document import IMAGE_MARK, region_call
from dawoni.ingest.grounding import (
    UNGROUNDED,
    accept_ungrounded,
    broken_glyphs,
    hangul_outside,
    review_note,
)
from dawoni.ingest.layout import DocumentLayout, Region, render_clip
from dawoni.ingest.layout import Figure as LayoutFigure
from dawoni.mathx.latex import latex_problems
from dawoni.models.base import DawoniModel
from dawoni.models.content import Block, MathRun, iter_runs
from dawoni.models.grading import ExamPart, ExamQuestion
from dawoni.models.item import Choice, ItemContent, ItemType
from dawoni.parallel import Progress


def _quiet(done: int, total: int, message: str) -> None:
    pass


log = logging.getLogger(__name__)

DEFAULT_POINTS = 4.0


class FigureUse(DawoniModel):
    id: str
    role: Literal["stem", "choice", "choices_sheet", "decoration"]
    choice_label: int | None = Field(default=None, ge=1, le=9)
    caption: str = Field(min_length=1, description="그림이 나타내는 것 (해설·채점에 쓰임)")


class ExtractedQuestion(DawoniModel):
    label: str = Field(description="[자료]에 주어진 문항 번호 그대로")
    item_type: ItemType
    points: float | None = Field(default=None, gt=0)
    parts: list[ExamPart] = Field(
        default_factory=lambda: list[ExamPart](),
        description="소문항 (1)(2)·①②③의 표시·배점·유형. 소문항이 없으면 빈 목록",
    )
    stem: list[Block] = Field(min_length=1)
    choices: list[Choice] = Field(default_factory=lambda: list[Choice]())
    choices_figure_id: str | None = None
    figures: list[FigureUse] = Field(default_factory=lambda: list[FigureUse]())


class PageExtraction(DawoniModel):
    questions: list[ExtractedQuestion] = Field(min_length=1)


EXTRACT: TaskDef[PageExtraction] = TaskDef(
    role="exam_reader",
    name="extract_questions",
    template="extract_questions.md.j2",
    output=PageExtraction,
    rule_target="ingest",
    timeout_s=900,
)


@dataclass
class PreparedRegion:
    region: Region
    images: list[Path]
    figure_files: dict[str, Path]


@dataclass
class IngestResult:
    questions: list[ExamQuestion]
    figure_files: dict[str, dict[str, Path]]
    """문항 번호 → 그림 ID → 크롭 파일."""
    warnings: list[str] = field(default_factory=lambda: list[str]())
    failed_pages: dict[int, list[str]] = field(default_factory=lambda: dict[int, list[str]]())
    """`partial`일 때 구조화에 실패한 쪽 → 그 쪽의 문항 번호 (다시 하면 이 쪽만 읽는다)."""
    notes: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    """문항 번호 → 강사가 원본과 비교할 것 (PDF 글자로 확인하지 못한 본문)."""


def prepare_regions(
    layout: DocumentLayout, out_dir: Path, regions: list[Region] | None = None
) -> list[PreparedRegion]:
    out: list[PreparedRegion] = []
    for r in layout.regions if regions is None else regions:
        d = out_dir / safe_name(r.label)
        d.mkdir(parents=True, exist_ok=True)
        images = [
            render_clip(layout.path, page, box, d / f"region_{k}.png", dpi=170)
            for k, (page, box) in enumerate(r.boxes, start=1)
        ]
        figs = {
            f.id: f.file or render_clip(layout.path, f.page, f.rect, d / f"{f.id}.png", dpi=300)
            for f in r.figures
        }  # 문서(DOCX·HWPX) 그림은 이미 꺼낸 파일, PDF 그림은 쪽에서 잘라 낸다
        out.append(PreparedRegion(r, images, figs))
    return out


def _figure_place(r: Region, f: LayoutFigure) -> str:
    """그림이 문항 영역 그림의 어디에 있나: "4쪽 영역 그림의 위에서 12%, 왼쪽에서 30%"."""
    box = next(
        (b for p, b in r.boxes if p == f.page and b.contains_point(f.rect.cx, f.rect.cy)), None
    )
    if box is None or box.w <= 0 or box.h <= 0:
        return f"{f.page}쪽"
    k = next(i for i, (p, b) in enumerate(r.boxes, start=1) if p == f.page and b is box)
    name = f"{k}번째 영역 그림" if len(r.boxes) > 1 else "영역 그림"
    top = round(100 * (f.rect.y0 - box.y0) / box.h)
    left = round(100 * (f.rect.x0 - box.x0) / box.w)
    return f"{f.page}쪽 {name}의 위에서 {max(0, top)}%, 왼쪽에서 {max(0, left)}%"


def attachments_for(
    group: list[PreparedRegion],
) -> tuple[tuple[Path, ...], dict[str, dict[str, int]]]:
    """첨부 파일 (문항마다 영역 그림, 그 뒤에 그림마다 잘라 낸 것)과 문항 → 그림 ID → 첨부 순번.

    실측(2026-11-06, 공통형3회): 영역 그림만 붙이고 ID를 목록으로만 주면 flash·sonnet 모두 그림과
    ID를 어긋나게 짝지었다 (자석 설명이 워터마크 F4에). flash는 F1.png를 찾아 작업 폴더 밖까지
    읽으려다 차단돼 실패했다 — 그림마다 따로 보여 주고 몇 번째 첨부인지 적는다."""
    files: list[Path] = []
    slots: dict[str, dict[str, int]] = {}
    for pr in group:
        files += pr.images
        mine: dict[str, int] = {}
        for fid, f in pr.figure_files.items():
            files.append(f)
            mine[fid] = len(files)
        slots[pr.region.label] = mine
    return tuple(files), slots


def _region_material(r: Region, *, doc: bool = False, slots: dict[str, int] | None = None) -> str:
    """`slots`: 그림 ID → 첨부 순번 (`attachments_for`)."""
    lines = [f"[문항 {r.label}]", "문서 글자:" if doc else "PDF 글자:", r.text]
    if r.points:
        lines.append(f"(배점 표시: {r.points:g}점)")
    if r.textboxes:
        lines.append(f"(테두리 글 상자 {len(r.textboxes)}개 — <보기>·조건 등, 그림 아님)")
    if r.figures:
        lines.append("그림 (그림마다 따로 첨부함):" if slots else "그림:")
        for k, f in enumerate(r.figures, start=1):
            pos = f" — 레이아웃상 {f.near_choice}번 선지 기호 옆" if f.near_choice else ""
            kind = "이미지" if f.kind == "image" else "선 그림"
            slot = f"첨부 {slots[f.id]}번째 파일, " if slots and f.id in slots else ""
            where = f"글의 {k}번째 {IMAGE_MARK} 자리" if doc else _figure_place(r, f)
            lines.append(f"- {f.id} ({kind}, {slot}{where}){pos}")
    else:
        lines.append("그림: 없음")
    return "\n".join(lines)


def to_item(q: ExtractedQuestion, region: Region, subject: str | None) -> ItemContent:
    """AI 출력 → 문항 (그림은 원본 크롭, raster)."""
    from dawoni.models.figure import Figure

    choices: list[Choice] = []
    notes = {f.choice_label: f.caption for f in q.figures if f.role == "choice" and f.choice_label}
    for c in q.choices:
        if c.figure_id and not c.runs and not c.note:
            c = c.model_copy(update={"note": notes.get(c.label) or c.note})
        choices.append(c)
    figures = [
        Figure(
            id=f.id,
            mode="raster",
            orig=f"figures/{safe_name(region.label)}/{f.id}.png",
            caption=f.caption,
        )
        for f in q.figures
        if f.role != "decoration"
    ]
    return ItemContent(
        item_type=q.item_type,
        subject=subject,
        number_label=region.label,
        points=q.points or region.points,
        stem=q.stem,
        choices=choices,
        choices_figure_id=q.choices_figure_id,
        figures=figures,
    )


PART_POINTS = 2.0
"""배점 표시가 없는 문서 문항의 소문항 하나 배점 (강사 확인 전 자리값)."""
_SUB_LABELS = (
    (re.compile(r"(?m)^\s*(\(\d{1,2}\))"), "({})"),
    (re.compile(r"(?m)^\s*(\d{1,2}\))"), "{})"),  # Word 자동 번호 "1)" (실측 월례 시험지)
)


def sub_labels(text: str) -> list[str]:
    """줄 맨 앞의 (1), (2) … 또는 1), 2) … 가 1부터 이어지면 그 표시들 (둘 이상일 때만)."""
    for pattern, form in _SUB_LABELS:
        found = list(dict.fromkeys(pattern.findall(text)))
        if len(found) >= 2 and found == [form.format(i) for i in range(1, len(found) + 1)]:
            return found
    return []


def unmarked_points(
    q: ExtractedQuestion, r: Region, default: float
) -> tuple[float, list[ExamPart], str]:
    """문서에 배점 표시가 없는 문항: (배점, 소문항, 경고).

    문서 글이 전부이므로 AI가 적은 배점은 지어낸 것이고, 배점을 못 정하면 소문항을 빼먹는다
    (실측 2026-11-04 주말반 시험: 4번은 1+1점을 지어내고, (1)(2)가 있는 5·7번은 소문항 없이 옮김).
    소문항은 AI 것이 없으면 본문의 (1)(2) 줄에서 가져온다."""
    parts = [p.model_copy(update={"points": PART_POINTS}) for p in q.parts] or [
        ExamPart(label=label, points=PART_POINTS) for label in sub_labels(r.text)
    ]
    if not parts:
        return default, [], f"배점 표시가 없어 {default:g}점으로 둠 — 확인 필요"
    total = PART_POINTS * len(parts)
    why = f"배점 표시가 없어 소문항마다 {PART_POINTS:g}점(합 {total:g}점)으로 둠 — 확인 필요"
    return total, parts, why


def _check_parts(q: ExtractedQuestion, r: Region) -> list[str]:
    if not q.parts:
        return []
    labels = [p.label for p in q.parts]
    if len(labels) != len(set(labels)):
        return [
            f"문항 {q.label}: 소문항 표시 중복 {labels} "
            "— 소문항 안의 소문항은 '(1)①'처럼 이어 붙일 것"
        ]
    total = q.points or r.points
    got = sum(p.points for p in q.parts)
    if total is not None and abs(got - total) > 1e-9:
        return [
            f"문항 {q.label}: 소문항 배점 합({got:g})이 문항 배점({total:g})과 다르다. "
            "'각 2점'처럼 묶어 적힌 배점은 소문항마다 나눠 적었는지 확인할 것"
        ]
    return []


def _failing_labels(e: Exception, labels: list[str]) -> set[str]:
    """검증 실패 메시지("문항 18: …")에서 문제가 된 문항 번호. 쪽 전체 문제면 빈 집합."""
    errors: list[str] = list(e.errors) if isinstance(e, TaskFailed) else []
    found = {m.group(1) for err in errors for m in re.finditer(r"문항 ([^\s:]+):", err)}
    return found & set(labels) if found and found < set(labels) else set()


def ungrounded_share(q: ExtractedQuestion, r: Region) -> float | None:
    """본문·선지의 한글 중 PDF 글자에 없는 비율 — 10%를 넘을 때만."""
    produced = blocks_text(q.stem) + " " + " ".join(runs_text(c.runs) for c in q.choices)
    outside = hangul_outside(produced, r.text)
    return outside if outside > 0.1 else None


def text_beyond_pdf(r: Region) -> bool:
    """이 문항의 글 일부가 PDF 글자 밖에 있을 수 있음 — 그림 이미지 속 글, 깨진 글자."""
    return any(f.kind == "image" for f in r.figures) or broken_glyphs(r.text)


def check_page(
    out: PageExtraction,
    regions: list[Region],
    subject: str | None,
    *,
    grounded: bool = True,
    doc: bool = False,
) -> list[str]:
    """`grounded`: 본문 한글이 PDF 글자에 있는지 본다 (붙여 넣은 그림만 있으면 볼 글자가 없다).
    `doc`: 문서(DOCX·HWPX)는 코드가 읽은 배점 표시가 맞다 — AI 배점이 다르면 고치게 한다."""
    problems: list[str] = []
    by_label = {r.label: r for r in regions}
    got = [q.label for q in out.questions]
    missing = [label for label in by_label if label not in got]
    extra = [label for label in got if label not in by_label]
    if missing:
        problems.append(f"문항 {missing}이 빠졌다. [자료]의 문항을 모두 옮길 것")
    if extra:
        problems.append(f"[자료]에 없는 문항 번호 {extra}를 만들었다")
    for q in out.questions:
        r = by_label.get(q.label)
        if r is None:
            continue
        given = {f.id for f in r.figures}
        used = [f.id for f in q.figures]
        unknown = sorted(set(used) - given)
        if unknown:
            problems.append(f"문항 {q.label}: 주어지지 않은 그림 ID {unknown}")
        unassigned = sorted(given - set(used))
        if unassigned:
            problems.append(f"문항 {q.label}: 그림 {unassigned}의 역할(figures)을 적지 않았다")
        for f in r.figures:
            if f.near_choice is None:
                continue
            use = next((u for u in q.figures if u.id == f.id), None)
            if use is not None and (use.role != "choice" or use.choice_label != f.near_choice):
                problems.append(
                    f"문항 {q.label}: 레이아웃상 {f.id}는 {f.near_choice}번 선지 기호 옆에 있다. "
                    f"role 'choice', choice_label {f.near_choice}로 할 것"
                )
        if grounded and (outside := ungrounded_share(q, r)) is not None:
            problems.append(
                f"문항 {q.label}: 본문에 {UNGROUNDED} {outside:.0%} 들어갔다. "
                "PDF 글자를 기준으로 그대로 옮길 것"
            )
        if doc and r.points and q.points is not None and abs(q.points - r.points) > 1e-9:
            problems.append(
                f"문항 {q.label}: 문서에 적힌 배점은 {r.points:g}점이다 (소문항에만 적혀 있으면 "
                f"그 합). points를 {r.points:g}로"
            )
        problems += _check_parts(q, r)
        latex = [x.v for x in iter_runs(q.stem) if isinstance(x, MathRun)]
        latex += [x.v for c in q.choices for x in c.runs if isinstance(x, MathRun)]
        problems += [f"문항 {q.label}: {m}" for m in latex_problems(latex)]
        try:
            to_item(q, r, subject)
        except ValidationError as e:
            problems += [f"문항 {q.label}: {m}" for m in describe_validation_error(e)]
        except ValueError as e:
            problems.append(f"문항 {q.label}: {e}")
    return problems


def extract_exam(
    engine: ContextEngine,
    layout: DocumentLayout,
    work_dir: Path,
    *,
    subject: str | None = None,
    level: str | None = None,
    workers: int = 1,
    progress: Progress | None = None,
    partial: bool = False,
    default_points: float = DEFAULT_POINTS,
) -> IngestResult:
    """`workers`: 쪽 여러 개를 동시에 구조화 (쪽마다 새 세션 — 두꺼운 교재·문제집용).

    `partial`: 어떤 쪽이 끝내 검증을 통과하지 못해도 나머지 쪽 문항은 돌려준다
    (`failed_pages`). 쪽별 결과는 `work_dir/pages/`에 남겨, 다시 하면 그 쪽은 AI 없이 쓴다
    (실측: 17쪽 중 1쪽 실패로 구간 전체를 잃음)."""
    from dawoni.agents import AgentError
    from dawoni.parallel import each_in_parallel

    prepared = prepare_regions(layout, work_dir)
    doc = not layout.pages  # DOCX·HWPX: 쪽 대신 문항 묶음, 영역 그림 대신 문서 속 그림
    by_page: dict[int, list[PreparedRegion]] = {}
    for pr in prepared:
        by_page.setdefault(region_call(pr.region), []).append(pr)

    cache_dir = work_dir / "pages"
    failed: dict[int, list[str]] = {}

    def read_page(page: int) -> dict[str, ExtractedQuestion] | None:
        group = by_page[page]
        regions = [pr.region for pr in group]
        labels = sorted(r.label for r in regions)
        cached = cache_dir / f"p{page}.json"
        if cached.is_file():
            try:
                hit = PageExtraction.model_validate_json(cached.read_text(encoding="utf-8"))
                if sorted(q.label for q in hit.questions) == labels:
                    return {q.label: q for q in hit.questions}
            except ValueError:
                pass
        try:
            return _read_page(page, group, regions, cached)
        except (TaskFailed, AgentError) as e:
            if not partial:
                raise
            # 한두 문항 때문이면 그 문항만 빼고 한 번 더
            # (실측: 그래프 선지 18번 때문에 17·19·20번까지 잃음)
            bad = _failing_labels(e, labels)
            rest = [pr for pr in group if pr.region.label not in bad]
            if bad and rest:
                try:
                    part_cache = cache_dir / f"p{page}-without-{'-'.join(sorted(bad))}.json"
                    got = _read_page(page, rest, [pr.region for pr in rest], part_cache)
                    failed[page] = sorted(bad)
                    log.warning("%d쪽 문항 %s 구조화 실패 — 나머지 문항만 저장", page, sorted(bad))
                    return got
                except (TaskFailed, AgentError):
                    pass
            failed[page] = [r.label for r in regions]
            log.warning("%d쪽 구조화 실패 (나머지 쪽은 계속): %s", page, e)
            return None

    def _read_page(
        page: int, group: list[PreparedRegion], regions: list[Region], cached: Path
    ) -> dict[str, ExtractedQuestion]:
        def check(out: PageExtraction, regions: list[Region] = regions) -> list[str]:
            return check_page(out, regions, subject, doc=doc)

        files, slots = attachments_for(group)
        try:
            value = engine.run(
                dataclasses.replace(EXTRACT, name=f"extract_questions_p{page}", validator=check),
                TaskInput(
                    variables={"doc": doc},
                    material="\n\n".join(
                        _region_material(pr.region, doc=doc, slots=slots[pr.region.label])
                        for pr in group
                    )
                    + (f"\n\n학생 수준: {level}" if level else ""),
                    attachments=files,
                ),
            ).value
        except TaskFailed as e:
            accepted = accept_ungrounded(e, PageExtraction)
            by_label = {r.label: r for r in regions}
            if accepted is None or not all(
                text_beyond_pdf(r)
                for q in accepted.questions
                if (r := by_label.get(q.label)) is not None and ungrounded_share(q, r) is not None
            ):
                raise
            log.warning("%d쪽: 근거 검사만 남아 받아들임 (강사 확인): %s", page, e.errors[:3])
            value = accepted
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(value.model_dump_json(), encoding="utf-8")
        return {q.label: q for q in value.questions}

    pages = sorted(by_page)
    reads = each_in_parallel(
        pages, read_page, name=lambda p: f"{p}쪽", what="문항 구조화", workers=workers,
        progress=progress or _quiet,
    )  # fmt: skip
    questions: list[ExamQuestion] = []
    warnings: list[str] = list(layout.warnings)
    notes: dict[str, str] = {}
    for page, by_label in zip(pages, reads, strict=True):
        if page in failed:
            nums = ", ".join(failed[page])
            warnings.append(f"{page}쪽(문항 {nums}) 구조화 실패 — 다시 하면 이 쪽만 읽습니다")
        if by_label is None:
            continue
        group = by_page[page]
        for pr in group:
            q = by_label.get(pr.region.label)
            if q is None:
                continue
            if (share := ungrounded_share(q, pr.region)) is not None:
                notes[pr.region.label] = f"본문: {review_note(share)}"
                warnings.append(f"문항 {pr.region.label}: {notes[pr.region.label]}")
            item = to_item(q, pr.region, subject)
            if doc and pr.region.points is None:
                points, parts, why = unmarked_points(q, pr.region, default_points)
                item = item.model_copy(update={"points": points})
                warnings.append(f"문항 {pr.region.label}: {why}")
                questions.append(
                    ExamQuestion(
                        number=pr.region.label,
                        points=points,
                        item=item,
                        parts=parts,
                        source_pages=pr.region.pages,
                    )
                )
                continue
            points = item.points
            if points is None and q.parts:
                points = sum(p.points for p in q.parts)
            if points is None:
                points = default_points
                warnings.append(
                    f"문항 {pr.region.label}: 배점 표시가 없어 {points:g}점으로 둠 — 확인 필요"
                )
            parts = q.parts
            if parts and abs(sum(p.points for p in parts) - points) > 1e-9:
                warnings.append(
                    f"문항 {pr.region.label}: 소문항 배점 합이 문항 배점과 달라 소문항 정보를 뺌 "
                    "— 확인 필요"
                )
                parts = []
            questions.append(
                ExamQuestion(
                    number=pr.region.label,
                    points=points,
                    item=item,
                    parts=parts,
                    source_pages=pr.region.pages,
                )
            )
    return IngestResult(
        questions=questions,
        figure_files={pr.region.label: pr.figure_files for pr in prepared},
        warnings=warnings,
        failed_pages=failed,
        notes=notes,
    )


# ---------------------------------------------------------------- 영역 지정해서 다시 읽기


@dataclass
class RegionRead:
    question: ExtractedQuestion
    region: Region
    figure_files: dict[str, Path]
    note: str | None = None
    """강사가 원본과 비교할 것 (PDF 글자로 확인하지 못한 본문)."""


def fixed_parts_problems(q: ExtractedQuestion, fixed: list[ExamPart]) -> list[str]:
    if not fixed:
        return []
    want = [p.label for p in fixed]
    got = [p.label for p in q.parts]
    if got == want:
        return []
    return [f"문항 {q.label}: 소문항은 강사가 정한 {want} 그대로 parts에 적을 것 (지금 {got})"]


def read_region(
    engine: ContextEngine,
    prepared: PreparedRegion,
    *,
    subject: str | None = None,
    level: str | None = None,
    fixed: list[ExamPart] | None = None,
) -> RegionRead:
    """강사가 지정한 영역(원본 쪽의 상자, 또는 붙여 넣은 그림) 하나를 문항으로 읽는다.

    `fixed`: 강사가 정한 소문항 — AI는 그 표시를 그대로 써야 통과하고, 배점·유형은 강사 것을 쓴다.
    같은 쪽을 같은 방법으로 다시 읽히면 같은 결과지만, 영역과 소문항이 새 정보라 결과가 달라진다."""
    r = prepared.region
    grounded = bool(r.text.strip())  # 붙여 넣은 그림만 있으면 근거 글자가 없다

    def with_fixed(out: PageExtraction) -> PageExtraction:
        if not fixed:
            return out
        total = sum(p.points for p in fixed)
        qs = [q.model_copy(update={"parts": fixed, "points": total}) for q in out.questions]
        return out.model_copy(update={"questions": qs})

    def check(out: PageExtraction) -> list[str]:
        problems = [m for q in out.questions for m in fixed_parts_problems(q, fixed or [])]
        return problems + check_page(with_fixed(out), [r], subject, grounded=grounded)

    files, slots = attachments_for([prepared])
    material = _region_material(r, slots=slots[r.label])
    if not grounded:
        material = material.replace("PDF 글자:\n", "PDF 글자: 없음 (붙여 넣은 그림만 있음)\n")
    if fixed:
        material += "\n강사가 정한 소문항 (이 표시 그대로 parts에):\n" + "\n".join(
            f"- {p.label}: {p.points:g}점"
            + {"essay": ", 서술형", "short_answer": ", 단답형", None: ""}[p.kind]
            for p in fixed
        )
    if level:
        material += f"\n\n학생 수준: {level}"
    try:
        value = engine.run(
            dataclasses.replace(EXTRACT, name="extract_questions_region", validator=check),
            TaskInput(
                variables={"doc": False, "image_only": not grounded, "fixed": bool(fixed)},
                material=material,
                attachments=files,
            ),
        ).value
    except TaskFailed as e:
        accepted = accept_ungrounded(e, PageExtraction)
        if accepted is None or not text_beyond_pdf(r):
            raise
        log.warning("영역 읽기 문항 %s: 근거 검사만 남아 받아들임 (강사 확인)", r.label)
        value = accepted
    value = with_fixed(value)
    q = next(x for x in value.questions if x.label == r.label)
    share = ungrounded_share(q, r) if grounded else None
    note = f"본문: {review_note(share)}" if share is not None else None
    if not grounded:
        note = "본문: 붙여 넣은 그림에서 읽음 — 원본과 비교하세요"
    return RegionRead(q, r, prepared.figure_files, note)
