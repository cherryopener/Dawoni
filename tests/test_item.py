from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from dawoni.models import InclineSpec, Item, ItemContent
from dawoni.models.figure import Figure, FunctionGraphSpec


def _bad(data: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValidationError) as ei:
        ItemContent.model_validate(data)
    assert fragment in str(ei.value), str(ei.value)


def test_incline_item_valid(incline_item: dict[str, Any]) -> None:
    item = ItemContent.model_validate(incline_item)
    fig = item.figures[0]
    assert isinstance(fig.spec, InclineSpec)
    assert fig.spec.angle_deg == "theta"
    assert item.variables["theta"].value == 30


def test_item_roundtrip_json(incline_item: dict[str, Any]) -> None:
    item = Item.from_content(ItemContent.model_validate(incline_item), created_by="강사A")
    again = Item.model_validate_json(item.model_dump_json())
    assert again == item
    assert item.id.startswith("it-")
    assert item.revision == 1


def test_ids_are_unique_and_time_ordered(incline_item: dict[str, Any]) -> None:
    content = ItemContent.model_validate(incline_item)
    ids = [Item.from_content(content).id for _ in range(50)]
    assert len(set(ids)) == 50
    assert ids == sorted(ids)


def test_undefined_variable_in_stem(incline_item: dict[str, Any]) -> None:
    d = copy.deepcopy(incline_item)
    d["stem"][0]["runs"][1]["v"] = "{{mass}}\\,\\mathrm{kg}"
    _bad(d, "정의되지 않은 변수: ['mass']")


def test_undefined_variable_in_constraint(incline_item: dict[str, Any]) -> None:
    d = copy.deepcopy(incline_item)
    d["constraints"] = ["k > 0"]
    _bad(d, "정의되지 않은 변수: ['k']")


def test_functions_in_placeholder_are_not_variables(incline_item: dict[str, Any]) -> None:
    d = copy.deepcopy(incline_item)
    d["stem"][0]["runs"][1]["v"] = "{{ sqrt(2)*m*sin(theta*pi/180) }}"
    ItemContent.model_validate(d)


@pytest.mark.parametrize("name", ["E", "pi", "x", "sin"])
def test_reserved_variable_names(incline_item: dict[str, Any], name: str) -> None:
    d = copy.deepcopy(incline_item)
    d["variables"][name] = {"value": 1}
    _bad(d, "예약어")


def test_variable_out_of_range(incline_item: dict[str, Any]) -> None:
    d = copy.deepcopy(incline_item)
    d["variables"]["m"]["value"] = 9
    _bad(d, "value(9.0) > max(5.0)")


def test_variable_not_in_choices(incline_item: dict[str, Any]) -> None:
    d = copy.deepcopy(incline_item)
    d["variables"]["theta"]["value"] = 31
    _bad(d, "choices에 없음")


def test_choice_labels_must_be_sequential(incline_item: dict[str, Any]) -> None:
    d = copy.deepcopy(incline_item)
    d["choices"][2]["label"] = 4
    _bad(d, "선지 번호는 1부터 차례대로")


def test_answer_label_must_exist(incline_item: dict[str, Any]) -> None:
    d = copy.deepcopy(incline_item)
    d["answer"] = {"kind": "choice", "labels": [7]}
    _bad(d, "정답 번호 [7]")


def test_multiple_choice_needs_choices(incline_item: dict[str, Any]) -> None:
    d = copy.deepcopy(incline_item)
    d["choices"] = []
    d["answer"] = None
    _bad(d, "선지가 2개 이상")


def test_short_answer_has_no_choices(incline_item: dict[str, Any]) -> None:
    d = copy.deepcopy(incline_item)
    d["item_type"] = "short_answer"
    d["answer"] = {"kind": "value", "latex": "3.3", "expr": "10*(sin(theta*pi/180))"}
    _bad(d, "short_answer 문항에는 선지가 없어야")
    d["choices"] = []
    ItemContent.model_validate(d)


def test_choice_answer_only_for_multiple_choice(incline_item: dict[str, Any]) -> None:
    d = copy.deepcopy(incline_item)
    d["item_type"] = "essay"
    d["choices"] = []
    _bad(d, "선택형 문항에만")


def test_missing_figure_reference(incline_item: dict[str, Any]) -> None:
    d = copy.deepcopy(incline_item)
    d["stem"].append({"type": "figure", "figure_id": "F2"})
    _bad(d, "정의되지 않은 그림 참조: ['F2']")


def test_choice_figure_reference(incline_item: dict[str, Any]) -> None:
    d = copy.deepcopy(incline_item)
    d["choices"][0] = {"label": 1, "figure_id": "F9"}
    _bad(d, "['F9']")


def test_empty_choice_rejected(incline_item: dict[str, Any]) -> None:
    d = copy.deepcopy(incline_item)
    d["choices"][0] = {"label": 1}
    _bad(d, "내용(runs)이나 그림(figure_id)")


def test_parametric_figure_needs_spec() -> None:
    with pytest.raises(ValidationError, match="spec이 필요"):
        Figure(id="F1", mode="parametric")
    with pytest.raises(ValidationError, match="원본"):
        Figure(id="F1", mode="raster")
    with pytest.raises(ValidationError):
        Figure(id="fig1", mode="raster", orig="a.png")


def test_variant_needs_parent(incline_item: dict[str, Any]) -> None:
    content = ItemContent.model_validate(incline_item)
    with pytest.raises(ValidationError, match="parent_id"):
        Item.from_content(content, variant_level="L1")
    Item.from_content(content, variant_level="L1", parent_id="it-x")


def test_graph_point_function_ref() -> None:
    base: dict[str, Any] = {
        "kind": "function_graph",
        "functions": [{"expr": "x**2 - 4"}],
        "x_range": {"min": -3, "max": 3},
        "y_range": {"min": -5, "max": 5},
    }
    FunctionGraphSpec.model_validate({**base, "points": [{"x": 2, "on_function": 0}]})
    with pytest.raises(ValidationError, match="함수는 1개"):
        FunctionGraphSpec.model_validate({**base, "points": [{"x": 2, "on_function": 1}]})
    with pytest.raises(ValidationError, match="y 또는 on_function"):
        FunctionGraphSpec.model_validate({**base, "points": [{"x": 2}]})
