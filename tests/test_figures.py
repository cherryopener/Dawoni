"""M10 파라미터 그림: 종류별 골든 이미지, 작도 계산, 변수 연동, 오류 안내, 스펙 변수 검사, 캐시.

골든 이미지 다시 만들기: `DAWONI_UPDATE_GOLDEN=1 uv run pytest tests/test_figures.py -k golden`
(폰트·matplotlib 판이 기록과 다르면 비교를 건너뛴다 — 글꼴 차이로 픽셀이 달라지므로)
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import pytest
from PIL import Image
from pydantic import TypeAdapter

from dawoni.figures.common import Ctx, FigureError, korean_font
from dawoni.figures.geometry import compute_points
from dawoni.figures.render import render_figure, render_spec
from dawoni.models.figure import FIGURE_KINDS, FigureSpec, GeometrySpec
from dawoni.models.item import ItemContent

FIX = Path(__file__).parent / "fixtures" / "figures"
GOLDEN = FIX / "golden"
SAMPLES: dict[str, dict[str, Any]] = json.loads((FIX / "samples.json").read_text(encoding="utf-8"))
TA: TypeAdapter[Any] = TypeAdapter(FigureSpec)
META = GOLDEN / "meta.json"


def spec_of(kind: str, **over: Any) -> Any:
    data = json.loads(json.dumps(SAMPLES[kind]["spec"]))
    data.update(over)
    return TA.validate_python(data)


def _gray(p: Path) -> np.ndarray:
    return np.asarray(Image.open(p).convert("L"), dtype=np.int16)


def test_every_kind_has_sample() -> None:
    assert set(SAMPLES) == set(FIGURE_KINDS)


@pytest.mark.parametrize("kind", FIGURE_KINDS)
def test_golden(kind: str, tmp_path: Path) -> None:
    out = render_spec(spec_of(kind), SAMPLES[kind]["values"], tmp_path / f"{kind}.png")
    golden = GOLDEN / f"{kind}.png"
    env = {"font": korean_font(), "matplotlib": ".".join(matplotlib.__version__.split(".")[:2])}
    if os.environ.get("DAWONI_UPDATE_GOLDEN"):
        GOLDEN.mkdir(parents=True, exist_ok=True)
        golden.write_bytes(out.read_bytes())
        META.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
        return
    if not golden.exists():
        pytest.fail(f"골든 이미지 없음: {golden} (DAWONI_UPDATE_GOLDEN=1로 만들기)")
    if json.loads(META.read_text(encoding="utf-8")) != env:
        pytest.skip(f"폰트·matplotlib 판이 골든과 다름 ({env})")
    a, b = _gray(out), _gray(golden)
    assert abs(a.shape[0] - b.shape[0]) <= 2 and abs(a.shape[1] - b.shape[1]) <= 2, "크기가 달라짐"
    h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
    differ = float(np.mean(np.abs(a[:h, :w] - b[:h, :w]) > 60))
    assert differ < 0.01, f"{kind}: 골든과 픽셀 {differ:.1%} 다름"


def test_geometry_constructions() -> None:
    spec = GeometrySpec.model_validate({
        "kind": "geometry",
        "points": [
            {"name": "A", "at": [0, 0]}, {"name": "B", "at": [6, 0]}, {"name": "C", "at": [2, 4]},
            {"name": "M", "midpoint": ["A", "B"]}, {"name": "H", "foot": ["C", "A", "B"]},
            {"name": "D", "divide": {"a": "A", "b": "C", "m": 1, "n": 2}},
            {"name": "G", "intersect": ["A", "C", "B", "D"]},
            {"name": "P", "on_circle": {"center": "M", "radius": 3, "angle_deg": 90}},
            {"name": "Q", "polar": {"origin": "A", "length": "k", "angle_deg": 45}},
        ],
    })  # fmt: skip
    p = compute_points(spec, Ctx({"k": math.sqrt(2)}))
    assert p["M"] == (3, 0) and p["H"] == pytest.approx((2, 0))
    assert p["D"] == pytest.approx((2 / 3, 4 / 3)), "A에서 1:2 내분"
    assert p["G"] == pytest.approx(p["D"]), "D는 AC 위의 점이므로 AC와 BD의 교점은 D"
    assert p["P"] == pytest.approx((3, 3)) and p["Q"] == pytest.approx((1, 1))


def test_geometry_validation() -> None:
    with pytest.raises(ValueError, match="앞에서 정의하지 않은 점"):
        GeometrySpec.model_validate({"kind": "geometry", "points": [
            {"name": "M", "midpoint": ["A", "B"]}, {"name": "A", "at": [0, 0]}, {"name": "B", "at": [1, 0]}]})  # fmt: skip
    with pytest.raises(ValueError, match="하나만"):
        GeometrySpec.model_validate({"kind": "geometry", "points": [
            {"name": "A", "at": [0, 0], "midpoint": ["A", "A"]}]})  # fmt: skip
    with pytest.raises(ValueError, match="정의되지 않은 점"):
        GeometrySpec.model_validate({"kind": "geometry", "points": [{"name": "A", "at": [0, 0]}],
                                     "segments": [{"a": "A", "b": "Z"}]})  # fmt: skip
    parallel = GeometrySpec.model_validate({"kind": "geometry", "points": [
        {"name": "A", "at": [0, 0]}, {"name": "B", "at": [1, 0]}, {"name": "C", "at": [0, 1]},
        {"name": "D", "at": [1, 1]}, {"name": "X", "intersect": ["A", "B", "C", "D"]}]})  # fmt: skip
    with pytest.raises(FigureError, match="평행"):
        compute_points(parallel, Ctx())


def test_variables_change_the_figure(tmp_path: Path) -> None:
    a = render_spec(spec_of("incline"), {"theta": 30, "m": 2}, tmp_path / "a.png")
    b = render_spec(spec_of("incline"), {"theta": 45, "m": 2}, tmp_path / "b.png")
    assert a.read_bytes() != b.read_bytes()
    with pytest.raises(FigureError, match="80"):
        render_spec(spec_of("incline"), {"theta": 85, "m": 2}, tmp_path / "c.png")
    with pytest.raises(FigureError, match="계산 불가"):
        render_spec(spec_of("incline"), {"m": 2}, tmp_path / "d.png")


def test_label_formatting() -> None:
    ctx = Ctx({"m": 2.5, "k": 3})
    assert ctx.label(r"\var{m} kg") == "2.5 kg"
    assert ctx.label(r"\(\theta\) = \var{2*k}°") == r"$\theta$ = 6°"
    assert ctx.label(None) == ""


def test_item_rejects_undefined_variables_in_figure_spec() -> None:
    base: dict[str, Any] = {
        "item_type": "short_answer",
        "stem": [{"type": "figure", "figure_id": "F1"}],
        "figures": [{"id": "F1", "mode": "parametric", "spec": SAMPLES["incline"]["spec"]}],
    }
    with pytest.raises(ValueError, match=r"정의되지 않은 변수: \['m', 'theta'\]"):
        ItemContent.model_validate(base)
    ok = ItemContent.model_validate(
        {**base, "variables": {"theta": {"value": 30}, "m": {"value": 2}}}
    )
    assert ok.figures[0].spec is not None
    graph = {**base, "figures": [{"id": "F1", "mode": "parametric", "spec": SAMPLES["function_graph"]["spec"]}],
             "variables": {"a": {"value": 1}, "b": {"value": 4}}}  # fmt: skip
    assert ItemContent.model_validate(graph), "함수식의 x는 변수가 아님"


def test_render_figure_cache(tmp_path: Path) -> None:
    item = ItemContent.model_validate({
        "item_type": "short_answer",
        "stem": [{"type": "figure", "figure_id": "F1"}],
        "variables": {"theta": {"value": 30}, "m": {"value": 2}},
        "figures": [{"id": "F1", "mode": "parametric", "spec": SAMPLES["incline"]["spec"]}],
    })  # fmt: skip
    p1 = render_figure(item.figures[0], item, tmp_path)
    mtime = p1.stat().st_mtime_ns
    assert render_figure(item.figures[0], item, tmp_path) == p1 and p1.stat().st_mtime_ns == mtime
    changed = item.model_copy(deep=True)
    changed.variables["theta"].value = 40
    p2 = render_figure(changed.figures[0], changed, tmp_path)
    assert p2 != p1 and p2.exists(), "값이 바뀌면 새로 그림"


def test_docx_uses_rendered_parametric_figure(tmp_path: Path) -> None:
    from dawoni.export.docx.builder import DocWriter, FigureContext
    from dawoni.export.docx.check import check_docx

    item = ItemContent.model_validate({
        "item_type": "short_answer",
        "stem": [{"type": "paragraph", "runs": [{"t": "text", "v": "그림에서 가속도를 구하시오."}]},
                 {"type": "figure", "figure_id": "F1"}],
        "variables": {"theta": {"value": 30}, "m": {"value": 2}},
        "figures": [{"id": "F1", "mode": "parametric", "spec": SAMPLES["incline"]["spec"]}],
    })  # fmt: skip
    w = DocWriter()
    w.question("1", 3, item, FigureContext.of(item, tmp_path))
    out = w.save(tmp_path / "p.docx")
    rep = check_docx(out)
    assert rep.ok and rep.images == 1 and w.warnings == []
    assert list((tmp_path / "figures" / "_render").glob("F1_*.png")), "다시 그린 그림이 캐시에"


def _engine(app_paths: Any, make_fake: Any, script: list[dict[str, Any]]) -> tuple[Any, Path]:
    from dawoni.agents import AgentClient
    from dawoni.config import AppConfig
    from dawoni.context import ContextEngine

    exe = make_fake(
        "script", script=[{"response": json.dumps(r, ensure_ascii=False)} for r in script]
    )
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.agents["claude"].enabled = False
    cfg.agents["codex"].enabled = False
    return ContextEngine(AgentClient(cfg, app_paths)), exe


def test_extract_loop_repairs_and_revises(app_paths: Any, make_fake: Any, tmp_path: Path) -> None:
    from tests_support import fake_prompts

    from dawoni.figures.extract import extract_figure

    orig = tmp_path / "orig.png"
    Image.new("RGB", (200, 120), "white").save(orig)
    good = SAMPLES["incline"]["spec"]
    steep = {**good, "angle_deg": 85}  # 그릴 수 없음 → 수리
    script: list[dict[str, Any]] = [
        {"verdict": "spec", "reason": "빗면", "spec": steep},
        {"verdict": "spec", "reason": "빗면", "spec": {**good, "angle_deg": 30}},
        {"match": False, "differences": ["물체 위치가 더 위"], "revised": {**good, "angle_deg": 30, "block_position": 0.7}},
        {"match": True, "differences": []},
    ]  # fmt: skip
    engine, exe = _engine(app_paths, make_fake, script)
    ex = extract_figure(engine, orig, tmp_path / "work", item_text="빗면 문항", values={"m": 2})
    assert ex.verdict == "spec" and ex.matched and ex.rounds == 2
    from dawoni.models.figure import InclineSpec

    assert isinstance(ex.spec, InclineSpec) and ex.spec.block_position == 0.7
    prompts = fake_prompts(exe)
    assert "80" in prompts[1] and "그릴 수 없다" in prompts[1], "그릴 수 없는 스펙은 수리"
    assert '"block_position"' not in prompts[2] or "0.7" not in prompts[2], "첫 비교는 원래 스펙"
    assert "0.7" in prompts[3], "두 번째 비교는 고친 스펙"


def test_extract_raster_verdict(app_paths: Any, make_fake: Any, tmp_path: Path) -> None:
    from dawoni.figures.extract import extract_figure

    orig = tmp_path / "o.png"
    Image.new("RGB", (50, 50), "white").save(orig)
    engine, _ = _engine(app_paths, make_fake, [{"verdict": "raster", "reason": "세포 사진"}])
    ex = extract_figure(engine, orig, tmp_path / "w")
    assert ex.verdict == "raster" and ex.spec is None and "세포" in ex.reason


def test_triangle_centers() -> None:
    spec = GeometrySpec.model_validate({
        "kind": "geometry",
        "points": [{"name": "A", "at": [0, 0]}, {"name": "B", "at": [4, 0]}, {"name": "C", "at": [0, 3]},
                   {"name": "I", "incenter": ["A", "B", "C"]}, {"name": "O", "circumcenter": ["A", "B", "C"]},
                   {"name": "G", "centroid": ["A", "B", "C"]}],
        "circles": [{"incircle": ["A", "B", "C"]}, {"circumcircle": ["A", "B", "C"], "style": "dashed"}],
    })  # fmt: skip
    p = compute_points(spec, Ctx())
    assert p["I"] == pytest.approx((1, 1)), "3-4-5 직각삼각형의 내접원 반지름 1"
    assert p["O"] == pytest.approx((2, 1.5)), "직각삼각형의 외심은 빗변의 중점"
    assert p["G"] == pytest.approx((4 / 3, 1))
    with pytest.raises(ValueError, match="incircle"):
        GeometrySpec.model_validate({"kind": "geometry", "points": [{"name": "A", "at": [0, 0]}],
                                     "circles": [{"center": "A"}]})  # fmt: skip


def test_extract_keeps_spec_when_compare_fails(
    app_paths: Any, make_fake: Any, tmp_path: Path
) -> None:
    from dawoni.figures.extract import extract_figure

    orig = tmp_path / "o.png"
    Image.new("RGB", (50, 50), "white").save(orig)
    good = {
        "verdict": "spec",
        "reason": "빗면",
        "spec": {**SAMPLES["incline"]["spec"], "angle_deg": 30},
    }
    bad_cmp = {"match": False, "differences": ["x"]}  # revised 없음 → 수리 → 끝내 실패
    engine, _ = _engine(app_paths, make_fake, [good, bad_cmp, bad_cmp, bad_cmp])
    ex = extract_figure(engine, orig, tmp_path / "w", values={"m": 2})
    assert ex.spec is not None and ex.render is not None and not ex.matched
    assert "원본 비교 실패" in ex.differences[0]
