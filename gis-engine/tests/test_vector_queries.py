from __future__ import annotations

import importlib
import json
from pathlib import Path

import pandas as pd
import pyogrio
import pytest

from spatial_engine.errors import DomainError, InvalidParamsError
from test_vectors import import_path
from vector_fixtures import create_fixture_bundle, ordinary_frame


def queries():
    return importlib.import_module("spatial_engine.vector_queries")


@pytest.fixture
def imported(tmp_path):
    sources = create_fixture_bundle(tmp_path / "source")
    result = import_path(sources["gpkg"], tmp_path / "work")
    return result["dataset"], Path(result["artifactPath"])


@pytest.fixture
def imported_booleans(tmp_path):
    frame = ordinary_frame()
    frame["active"] = pd.array([True, False, None, True], dtype="boolean")
    source = tmp_path / "booleans.gpkg"
    pyogrio.write_dataframe(frame, source, layer="land", driver="GPKG", use_arrow=True)
    result = import_path(str(source), tmp_path / "work")
    assert next(field for field in result["dataset"]["fields"] if field["name"] == "active")["storageType"] == "bool"
    return result["dataset"], Path(result["artifactPath"])


def page_params(dataset, **overrides):
    result = {"path": "parent-validated", "datasetId": dataset["id"], "offset": 0, "limit": 2,
              "sortField": "label", "descending": False, "filter": None}
    result.update(overrides)
    return result


def test_queries_module_is_available():
    assert importlib.util.find_spec("spatial_engine.vector_queries") is not None


def test_attribute_pages_stable_sort_nulls_and_large_integers(imported):
    dataset, path = imported
    first = queries().attribute_page(dataset, path, page_params(dataset))
    second = queries().attribute_page(dataset, path, page_params(dataset, offset=2))
    assert first["total"] == 4 and first["hasMore"]
    assert not second["hasMore"]
    assert len({row["id"] for row in first["rows"] + second["rows"]}) == 4
    assert [row["values"]["label"] for row in first["rows"] + second["rows"]] == ["Chinese \u571f\u5730", "alpha", "alpha", "beta"]
    assert first["rows"][0]["values"]["large_id"] == "9007199254740993"
    assert second["rows"][-1]["values"]["large_id"] is None
    assert first["datasetId"] == dataset["id"] and first["version"] == dataset["version"]


@pytest.mark.parametrize("filter_value,expected", [
    ({"field": "label", "operator": "equals", "value": "alpha"}, 2),
    ({"field": "label", "operator": "contains", "value": "a"}, 3),
    ({"field": "large_id", "operator": "isNull", "value": ""}, 1),
    ({"field": "large_id", "operator": "equals", "value": "9007199254740993"}, 1),
    ({"field": "label", "operator": "equals", "value": "' OR 1=1 --"}, 0),
])
def test_attribute_filters_are_bounded_and_parameterized(imported, filter_value, expected):
    dataset, path = imported
    result = queries().attribute_page(dataset, path, page_params(dataset, limit=500, filter=filter_value))
    assert result["total"] == expected and len(result["rows"]) == expected


@pytest.mark.parametrize("operator,operand,expected", [
    ("equals", "true", [True, True]),
    ("equals", "false", [False]),
    ("equals", "1", [True, True]),
    ("equals", "0", [False]),
    ("equals", " TRUE ", [True, True]),
    ("contains", "ru", [True, True]),
    ("contains", "als", [False]),
    ("isNull", "", [None]),
])
def test_nullable_boolean_filters_match_displayed_values(imported_booleans, operator, operand, expected):
    dataset, path = imported_booleans
    result = queries().attribute_page(dataset, path, page_params(
        dataset, limit=500, filter={"field": "active", "operator": operator, "value": operand}
    ))
    assert result["total"] == len(expected)
    assert [row["values"]["active"] for row in result["rows"]] == expected


@pytest.mark.parametrize("operand", ["yes", "2", "null", "", "true' OR 1=1 --"])
def test_boolean_equality_rejects_invalid_operands(imported_booleans, operand):
    dataset, path = imported_booleans
    with pytest.raises(InvalidParamsError) as error:
        queries().attribute_page(dataset, path, page_params(
            dataset, filter={"field": "active", "operator": "equals", "value": operand}
        ))
    assert "Boolean equality" in error.value.detail


def test_reject_invalid_query_parameters(imported):
    dataset, path = imported
    with pytest.raises(InvalidParamsError):
        queries().attribute_page(dataset, path, page_params(dataset, sortField="label;DROP TABLE features"))
    with pytest.raises(InvalidParamsError):
        queries().attribute_page(dataset, path, page_params(dataset, limit=501))


def test_projected_viewport_exact_hole_filter_and_feature_selection(imported):
    from pyproj import Transformer
    dataset, path = imported
    transform = Transformer.from_crs(4547, 4326, always_xy=True)
    x1, y1 = transform.transform(500270, 3000070)
    x2, y2 = transform.transform(500330, 3000130)
    empty = queries().viewport(dataset, path, {"datasetId": dataset["id"], "bbox": [x1, y1, x2, y2], "limit": 2000, "propertyFields": ["code"]})
    assert empty["returnedCount"] == 0
    all_features = queries().viewport(dataset, path, {"bbox": dataset["boundsWgs84"], "limit": 2, "propertyFields": ["code"]})
    assert all_features["returnedCount"] == 2 and all_features["truncated"]
    assert all_features["dataCrs"] == "EPSG:4326"
    identifier = all_features["collection"]["features"][0]["id"]
    found = queries().feature(dataset, path, {"featureId": identifier})
    assert found["row"]["id"] == identifier
    assert found["feature"]["id"] == identifier and found["boundsWgs84"] is not None


def test_unknown_crs_attributes_available_geometry_restricted(imported):
    dataset, path = imported
    dataset = dict(dataset, crsWkt=None, boundsWgs84=None)
    assert queries().attribute_page(dataset, path, page_params(dataset))["total"] == 4
    assert queries().feature(dataset, path, {"featureId": "1"})["feature"] is None
    with pytest.raises(DomainError) as error:
        queries().viewport(dataset, path, {"bbox": [113, 26, 115, 28], "limit": 10, "propertyFields": []})
    assert error.value.kind == "crs_required"


def test_ascii_byte_budget_is_a_hard_limit(imported, monkeypatch):
    dataset, path = imported
    monkeypatch.setattr(queries(), "MAX_QUERY_BYTES", 100)
    with pytest.raises(DomainError) as error:
        queries().attribute_page(dataset, path, page_params(dataset))
    assert error.value.kind == "query_limit"


def test_viewport_vertex_budget_is_explicitly_truncated(imported, monkeypatch):
    dataset, path = imported
    monkeypatch.setattr(queries(), "MAX_DISPLAY_VERTICES", 3)
    result = queries().viewport(dataset, path, {"bbox": dataset["boundsWgs84"], "limit": 2000, "propertyFields": []})
    assert result["truncated"] and result["returnedCount"] == 0
    with pytest.raises(DomainError) as error:
        queries().feature(dataset, path, {"featureId": "1"})
    assert error.value.kind == "query_limit"


def test_viewport_ascii_budget_is_explicitly_truncated(imported, monkeypatch):
    dataset, path = imported
    monkeypatch.setattr(queries(), "MAX_QUERY_BYTES", 500)
    result = queries().viewport(dataset, path, {"bbox": dataset["boundsWgs84"], "limit": 2000, "propertyFields": ["label"]})
    assert result["truncated"]
    assert len(json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode("ascii")) <= 500
