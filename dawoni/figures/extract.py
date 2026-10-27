"""원본 그림 → 스펙 (PLAN §8-4 재현 품질 보장 루프).

    AI: 종류 판별 + 스펙 → 코드: 검증·렌더 (실패하면 수리 요청)
     → AI: 원본과 재현본 비교 → 차이가 있으면 고친 스펙 (최대 2회) → 강사 검수

결과는 스펙과 재현본 경로. 출력에 쓸지(`mode=parametric`)는 강사가 원본과 나란히 보고 정한다.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from dawoni.agents import AgentError, AgentErrorCode
from dawoni.context import ContextEngine, TaskDef, TaskFailed, TaskInput
from dawoni.figures.common import FigureError
from dawoni.figures.render import render_spec
from dawoni.models.base import DawoniModel
from dawoni.models.figure import FigureSpec


class SpecDraft(DawoniModel):
    verdict: Literal["spec", "raster"]
    reason: str = Field(description="종류를 고른 이유, 또는 다시 그리기 어려운 이유")
    spec: FigureSpec | None = None


class CompareResult(DawoniModel):
    match: bool
    differences: list[str] = Field(default_factory=lambda: list[str]())
    revised: FigureSpec | None = None


SPEC_TASK: TaskDef[SpecDraft] = TaskDef(
    name="figure_spec", template="figure_spec.md.j2", output=SpecDraft, rule_target="ingest",
    timeout_s=600,
)  # fmt: skip
COMPARE_TASK: TaskDef[CompareResult] = TaskDef(
    name="figure_compare", template="figure_compare.md.j2", output=CompareResult,
    rule_target="ingest", timeout_s=600,
)  # fmt: skip


@dataclass
class Extraction:
    verdict: Literal["spec", "raster"]
    reason: str
    spec: FigureSpec | None = None
    render: Path | None = None
    rounds: int = 0
    matched: bool = False
    differences: list[str] = field(default_factory=lambda: list[str]())


def _renderable(spec: FigureSpec | None, values: dict[str, float], out: Path) -> list[str]:
    if spec is None:
        return []
    try:
        render_spec(spec, values, out)
    except FigureError as e:
        return [f"스펙을 그릴 수 없다: {e}"]
    return []


def extract_figure(
    engine: ContextEngine,
    original: Path,
    work_dir: Path,
    *,
    item_text: str = "",
    caption: str | None = None,
    values: dict[str, float] | None = None,
    max_rounds: int = 2,
) -> Extraction:
    values = values or {}
    work_dir.mkdir(parents=True, exist_ok=True)

    def check(d: SpecDraft) -> list[str]:
        if d.verdict == "spec" and d.spec is None:
            return ["verdict가 spec이면 spec을 쓸 것 (다시 그리기 어려우면 raster)"]
        return _renderable(d.spec, values, work_dir / "check.png")

    first = engine.run(
        dataclasses.replace(SPEC_TASK, validator=check),
        TaskInput(
            variables={
                "item_text": item_text,
                "caption": caption or "",
                "variables": ", ".join(f"{k}={v:g}" for k, v in values.items()),
            },
            attachments=(original,),
        ),
    ).value
    if first.verdict == "raster" or first.spec is None:
        return Extraction("raster", first.reason)
    spec = first.spec
    ex = Extraction("spec", first.reason, spec)
    for k in range(1, max_rounds + 1):
        render = render_spec(spec, values, work_dir / f"render_{k}.png")
        ex.render = render

        def check_cmp(r: CompareResult) -> list[str]:
            if not r.match and r.revised is None:
                return ["차이가 있으면 고친 스펙 전체를 revised에 쓸 것"]
            return _renderable(r.revised, values, work_dir / "check.png")

        spec_json: Any = json.dumps(
            spec.model_dump(mode="json", exclude_defaults=True), ensure_ascii=False
        )
        try:
            cmp = engine.run(
                dataclasses.replace(COMPARE_TASK, validator=check_cmp),
                TaskInput(variables={"spec_json": spec_json}, attachments=(original, render)),
            ).value
        except (AgentError, TaskFailed) as e:
            if isinstance(e, AgentError) and e.code == AgentErrorCode.CANCELLED:
                raise
            # 비교가 실패해도 만든 스펙은 버리지 않는다 — 강사가 나란히 보고 판단
            ex.rounds = k
            msg = e.user_message if isinstance(e, AgentError) else str(e)
            ex.differences = [f"원본 비교 실패 ({msg}) — 직접 나란히 보고 확인하세요"]
            break
        ex.rounds = k
        ex.differences = cmp.differences
        if cmp.match or cmp.revised is None:
            ex.matched = cmp.match
            break
        spec = cmp.revised
        ex.spec = spec
        ex.render = render_spec(spec, values, work_dir / f"render_{k + 1}.png")
    return ex
