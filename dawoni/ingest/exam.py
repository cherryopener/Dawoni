"""시험지 PDF → 시험(문항 + 그림). 레이아웃은 코드가, 문장 구조화는 AI가.

    레이아웃 분석(결정적) → 문항 영역 이미지·그림 크롭 → 쪽 단위 AI 구조화 → 코드 검증 → Exam

코드 검증 (AI 출력을 그대로 믿지 않음):
- 주어진 문항 번호를 빠짐없이, 지어낸 번호 없이
- 그림 ID는 레이아웃이 찾은 것만, 모두 역할을 받아야 함
- 레이아웃이 ①~⑤ 옆에서 찾은 그림은 그 번호의 선지여야 함
- 본문의 한글이 PDF 글자에 있어야 함 (지어낸 문장 차단)
- 최종 문항은 `ItemContent` 검증 통과 (그림 선지 규칙 등)
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from dawoni.context import ContextEngine, TaskDef, TaskInput
from dawoni.context.extract import describe_validation_error
from dawoni.grading.render import blocks_text, runs_text
from dawoni.grading.store import safe_name
from dawoni.ingest.grounding import hangul_outside
from dawoni.ingest.layout import DocumentLayout, Region, render_clip
from dawoni.models.base import DawoniModel
from dawoni.models.content import Block
from dawoni.models.grading import ExamQuestion
from dawoni.models.item import Choice, ItemContent, ItemType

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
    stem: list[Block] = Field(min_length=1)
    choices: list[Choice] = Field(default_factory=lambda: list[Choice]())
    choices_figure_id: str | None = None
    figures: list[FigureUse] = Field(default_factory=lambda: list[FigureUse]())


class PageExtraction(DawoniModel):
    questions: list[ExtractedQuestion] = Field(min_length=1)


EXTRACT: TaskDef[PageExtraction] = TaskDef(
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


def prepare_regions(layout: DocumentLayout, out_dir: Path) -> list[PreparedRegion]:
    out: list[PreparedRegion] = []
    for r in layout.regions:
        d = out_dir / safe_name(r.label)
        d.mkdir(parents=True, exist_ok=True)
        images = [
            render_clip(layout.path, page, box, d / f"region_{k}.png", dpi=170)
            for k, (page, box) in enumerate(r.boxes, start=1)
        ]
        figs = {
            f.id: render_clip(layout.path, f.page, f.rect, d / f"{f.id}.png", dpi=300)
            for f in r.figures
        }
        out.append(PreparedRegion(r, images, figs))
    return out


def _region_material(r: Region) -> str:
    lines = [f"[문항 {r.label}]", "PDF 글자:", r.text]
    if r.points:
        lines.append(f"(배점 표시: {r.points:g}점)")
    if r.textboxes:
        lines.append(f"(테두리 글 상자 {len(r.textboxes)}개 — <보기>·조건 등, 그림 아님)")
    if r.figures:
        lines.append("그림:")
        for f in r.figures:
            pos = f" — 레이아웃상 {f.near_choice}번 선지 기호 옆" if f.near_choice else ""
            kind = "이미지" if f.kind == "image" else "선 그림"
            lines.append(f"- {f.id} ({kind}, {f.page}쪽){pos}")
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


def check_page(out: PageExtraction, regions: list[Region], subject: str | None) -> list[str]:
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
        produced = blocks_text(q.stem) + " " + " ".join(runs_text(c.runs) for c in q.choices)
        outside = hangul_outside(produced, r.text)
        if outside > 0.1:
            problems.append(
                f"문항 {q.label}: 본문에 PDF 글자에 없는 한글이 {outside:.0%} 들어갔다. "
                "PDF 글자를 기준으로 그대로 옮길 것"
            )
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
) -> IngestResult:
    prepared = prepare_regions(layout, work_dir)
    by_page: dict[int, list[PreparedRegion]] = {}
    for pr in prepared:
        by_page.setdefault(pr.region.boxes[0][0], []).append(pr)

    questions: list[ExamQuestion] = []
    warnings: list[str] = list(layout.warnings)
    for page in sorted(by_page):
        group = by_page[page]
        regions = [pr.region for pr in group]

        def check(out: PageExtraction, regions: list[Region] = regions) -> list[str]:
            return check_page(out, regions, subject)

        res = engine.run(
            dataclasses.replace(EXTRACT, name=f"extract_questions_p{page}", validator=check),
            TaskInput(
                variables={},
                material="\n\n".join(_region_material(r) for r in regions)
                + (f"\n\n학생 수준: {level}" if level else ""),
                attachments=tuple(img for pr in group for img in pr.images),
            ),
        )
        by_label = {q.label: q for q in res.value.questions}
        for pr in group:
            q = by_label[pr.region.label]
            item = to_item(q, pr.region, subject)
            points = item.points
            if points is None:
                points = DEFAULT_POINTS
                warnings.append(
                    f"문항 {pr.region.label}: 배점 표시가 없어 {points:g}점으로 둠 — 확인 필요"
                )
            questions.append(ExamQuestion(number=pr.region.label, points=points, item=item))
    return IngestResult(
        questions=questions,
        figure_files={pr.region.label: pr.figure_files for pr in prepared},
        warnings=warnings,
    )
