from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from dawoni.models.content import (
    Block,
    BogiBlock,
    DisplayMathBlock,
    MathRun,
    figure_ids_in,
    iter_runs,
    math,
    paragraph,
    placeholders_in,
    text,
)

BlockList = TypeAdapter(list[Block])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("x^2", "x^2"),
        ("$x^2$", "x^2"),
        ("$ x^2 $", "x^2"),
        ("$$\\frac{a}{b}$$", "\\frac{a}{b}"),
        ("\\(a+b\\)", "a+b"),
        ("\\[a+b\\]", "a+b"),
        ("  v_A - v_B ", "v_A - v_B"),
    ],
)
def test_math_run_strips_delimiters(raw: str, expected: str) -> None:
    assert MathRun(t="math", v=raw).v == expected


@pytest.mark.parametrize("raw", ["", "  ", "$$", "$ $"])
def test_math_run_rejects_empty(raw: str) -> None:
    with pytest.raises(ValidationError):
        MathRun(t="math", v=raw)


def test_display_math_strips_delimiters() -> None:
    assert DisplayMathBlock(type="display_math", latex="$$E=mc^2$$").latex == "E=mc^2"


def test_blocks_parse_by_discriminator() -> None:
    blocks = BlockList.validate_python(
        [
            {"type": "paragraph", "runs": [{"t": "text", "v": "다음 "}, {"t": "math", "v": "x"}]},
            {
                "type": "bogi",
                "items": [
                    {"label": "ㄱ", "runs": [{"t": "text", "v": "A는 B보다 빠르다."}]},
                    {"label": "ㄴ", "runs": [{"t": "math", "v": "v_A > v_B"}]},
                ],
            },
            {"type": "figure", "figure_id": "F1", "width": 0.6},
        ]
    )
    assert isinstance(blocks[1], BogiBlock)
    assert blocks[1].title == "보기"
    assert [r.v for r in iter_runs(blocks)] == ["다음 ", "x", "A는 B보다 빠르다.", "v_A > v_B"]
    assert figure_ids_in(blocks) == ["F1"]


def test_unknown_block_type_rejected() -> None:
    with pytest.raises(ValidationError):
        BlockList.validate_python([{"type": "image", "src": "a.png"}])


def test_extra_fields_rejected() -> None:
    """AI가 지어낸 필드는 받아들이지 않는다 → 수리 루프 대상."""
    with pytest.raises(ValidationError):
        BlockList.validate_python([{"type": "paragraph", "runs": [], "color": "red"}])


def test_placeholders() -> None:
    assert placeholders_in("{{m}} kg, {{ 2*m }}") == ["m", "2*m"]
    assert placeholders_in("없음") == []


def test_helpers_build_valid_models() -> None:
    p = paragraph(text("속력 ", bold=True), math("v"))
    assert p.model_dump()["runs"][1] == {"t": "math", "v": "v"}
