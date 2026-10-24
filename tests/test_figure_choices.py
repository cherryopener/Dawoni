"""그림(그래프) 선지 문항.

동형문제에서 그림 선지는 고정하고 문제 조건만 바꿔 다른 선지가 정답이 되게 한다.
그래서 선지 그림은 원본 그대로 쓰고, 각 선지가 무엇을 그린 것인지(note)를 반드시 남긴다.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from dawoni.models import ItemContent

NOTES = [
    "y=2^x 그래프: (0,1)을 지나고 증가",
    "y=(1/2)^x 그래프: (0,1)을 지나고 감소",
    "y=log_2 x 그래프: (1,0)을 지나고 증가",
    "y=-2^x 그래프: (0,-1)을 지나고 감소",
    "y=2^x+1 그래프: (0,2)를 지나고 점근선 y=1",
]


@pytest.fixture
def graph_choice_item() -> dict[str, Any]:
    return {
        "item_type": "multiple_choice",
        "variables": {"a": {"value": 2, "choices": [2, 3]}},
        "stem": [
            {
                "type": "paragraph",
                "runs": [
                    {"t": "text", "v": "함수 "},
                    {"t": "math", "v": "y=\\left(\\frac{1}{\\var{a}}\\right)^x"},
                    {"t": "text", "v": "의 그래프로 옳은 것은?"},
                ],
            }
        ],
        "choices": [{"label": i, "figure_id": f"F{i}", "note": NOTES[i - 1]} for i in range(1, 6)],
        "choice_layout": "grid_3_2",
        "answer": {"kind": "choice", "labels": [2]},
        "figures": [
            {"id": f"F{i}", "mode": "raster", "orig": f"figures/F{i}.orig.png"} for i in range(1, 6)
        ],
    }


def _bad(data: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValidationError) as ei:
        ItemContent.model_validate(data)
    assert fragment in str(ei.value), str(ei.value)


def test_per_choice_figures_valid(graph_choice_item: dict[str, Any]) -> None:
    item = ItemContent.model_validate(graph_choice_item)
    assert item.has_figure_choices
    assert item.choice_layout == "grid_3_2"
    assert item.choices[1].note == NOTES[1]


def test_text_choices_are_not_figure_choices(incline_item: dict[str, Any]) -> None:
    assert not ItemContent.model_validate(incline_item).has_figure_choices


def test_figure_choice_requires_note(graph_choice_item: dict[str, Any]) -> None:
    d = copy.deepcopy(graph_choice_item)
    del d["choices"][3]["note"]
    _bad(d, "선지 4: 그림 선지에는 내용 설명(note)이 필요")


def test_choice_figure_must_be_raster(graph_choice_item: dict[str, Any]) -> None:
    d = copy.deepcopy(graph_choice_item)
    d["figures"][0] = {
        "id": "F1",
        "mode": "parametric",
        "spec": {
            "kind": "function_graph",
            "functions": [{"expr": "2**x"}],
            "x_range": {"min": -2, "max": 2},
            "y_range": {"min": -1, "max": 5},
        },
    }
    _bad(d, "F1: 선지 그림은 원본 그대로(mode=raster)")


def test_raster_choice_may_keep_spec_for_meaning(graph_choice_item: dict[str, Any]) -> None:
    """출력은 원본, spec은 정답 검증·의미 기록용으로만 보관."""
    d = copy.deepcopy(graph_choice_item)
    d["figures"][0]["spec"] = {
        "kind": "function_graph",
        "functions": [{"expr": "2**x"}],
        "x_range": {"min": -2, "max": 2},
        "y_range": {"min": -1, "max": 5},
    }
    ItemContent.model_validate(d)


def test_choice_runs_cannot_use_variables(graph_choice_item: dict[str, Any]) -> None:
    d = copy.deepcopy(graph_choice_item)
    d["choices"][0] = {"label": 1, "runs": [{"t": "math", "v": "\\var{a}"}]}
    _bad(d, "선지 1: 그림 선지 문항의 선지에는 변수 자리표시 금지")


def test_stem_may_still_use_variables(graph_choice_item: dict[str, Any]) -> None:
    """선지는 고정이어도 문제 조건(a)은 바꿀 수 있어야 한다."""
    item = ItemContent.model_validate(graph_choice_item)
    assert "a" in item.variables


@pytest.fixture
def sheet_item(graph_choice_item: dict[str, Any]) -> dict[str, Any]:
    """①~⑤가 그림 한 장에 함께 들어 있는 경우 — 억지로 자르지 않고 통째로 쓴다."""
    d = copy.deepcopy(graph_choice_item)
    d["choices"] = [{"label": i, "note": NOTES[i - 1]} for i in range(1, 6)]
    d["choices_figure_id"] = "F1"
    d["figures"] = [{"id": "F1", "mode": "raster", "orig": "figures/F1.orig.png"}]
    return d


def test_choice_sheet_valid(sheet_item: dict[str, Any]) -> None:
    item = ItemContent.model_validate(sheet_item)
    assert item.has_figure_choices
    assert all(c.is_label_only for c in item.choices)


def test_choice_sheet_choices_are_label_only(sheet_item: dict[str, Any]) -> None:
    d = copy.deepcopy(sheet_item)
    d["choices"][0]["runs"] = [{"t": "text", "v": "가"}]
    _bad(d, "선지 1: 선지 그림이 한 장")


def test_choice_sheet_needs_notes(sheet_item: dict[str, Any]) -> None:
    d = copy.deepcopy(sheet_item)
    del d["choices"][4]["note"]
    _bad(d, "선지 5: 그림 선지에는 내용 설명(note)이 필요")


def test_choice_sheet_figure_must_exist(sheet_item: dict[str, Any]) -> None:
    d = copy.deepcopy(sheet_item)
    d["choices_figure_id"] = "F7"
    _bad(d, "정의되지 않은 그림 참조: ['F7']")


def test_choice_sheet_only_for_multiple_choice(sheet_item: dict[str, Any]) -> None:
    d = copy.deepcopy(sheet_item)
    d["item_type"] = "short_answer"
    d["choices"] = []
    d["answer"] = None
    _bad(d, "choices_figure_id는 선택형 문항에만")


def test_label_only_choice_without_sheet(graph_choice_item: dict[str, Any]) -> None:
    d = copy.deepcopy(graph_choice_item)
    d["choices"][0] = {"label": 1, "note": "설명만"}
    _bad(d, "선지 1: 내용(runs)이나 그림(figure_id)이 필요")


def test_invalid_layout(graph_choice_item: dict[str, Any]) -> None:
    d = copy.deepcopy(graph_choice_item)
    d["choice_layout"] = "diagonal"
    _bad(d, "choice_layout")
