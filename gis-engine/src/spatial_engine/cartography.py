"""Bounded vector display configuration; never reads or changes GIS snapshots."""
from __future__ import annotations

import json
import re
from typing import Any

from .errors import InvalidParamsError

MAX_SPEC_BYTES = 32 * 1024
MAX_CATEGORIES = 64
MAX_SAFE_INTEGER = 9007199254740991
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")


def _object(value: Any, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise InvalidParamsError(f"cartography {label} must contain exactly: {', '.join(sorted(keys))}")
    return value


def _literal(value: Any, maximum: int, label: str) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise InvalidParamsError(f"cartography {label} must be text of at most {maximum} characters")
    return value


def _color(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _COLOR.fullmatch(value):
        raise InvalidParamsError(f"cartography {label} must be a #RRGGBB color")
    return value


def _number(value: Any, minimum: float, maximum: float, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
        raise InvalidParamsError(f"cartography {label} must be finite and between {minimum:g} and {maximum:g}")
    return value


def validate_revision(value: Any, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= MAX_SAFE_INTEGER:
        raise InvalidParamsError(f"cartography revision must be an integer between {minimum} and {MAX_SAFE_INTEGER}")
    return value


def encode_spec(value: Any) -> str:
    try:
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        size = len(serialized.encode("utf-8"))
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise InvalidParamsError("cartography must be finite JSON data") from exc
    if size > MAX_SPEC_BYTES:
        raise InvalidParamsError("cartography exceeds the 32 KiB encoded configuration limit")
    return serialized


def validate_spec(value: Any, dataset: dict) -> dict:
    """Validate against registered metadata and return an independent JSON value."""
    # Bound the document before walking nested structures or allocating derived maps.
    serialized = encode_spec(value)
    spec = _object(value, {"specVersion", "kind", "revision", "input", "basePreset", "presetVersion",
                           "symbol", "renderer", "legend"}, "spec")
    if type(spec["specVersion"]) is not int or spec["specVersion"] != 1 or spec["kind"] != "vector-layer":
        raise InvalidParamsError("cartography requires specVersion 1 and kind vector-layer")
    validate_revision(spec["revision"], minimum=1)
    source = _object(spec["input"], {"datasetId", "version"}, "input")
    if dataset.get("kind") != "vector" or source["datasetId"] != dataset["id"] or source["version"] != dataset["version"]:
        raise InvalidParamsError("cartography input must match the registered vector dataset and version")
    if spec["basePreset"] not in ("planning", "publication") or type(spec["presetVersion"]) is not int or spec["presetVersion"] != 1:
        raise InvalidParamsError("cartography requires a supported preset and presetVersion 1")
    symbol = _object(spec["symbol"], {"fillColor", "strokeColor", "strokeWidthPt", "pointRadiusPt"}, "symbol")
    _color(symbol["fillColor"], "fillColor")
    _color(symbol["strokeColor"], "strokeColor")
    _number(symbol["strokeWidthPt"], 0, 6, "strokeWidthPt")
    _number(symbol["pointRadiusPt"], 1, 16, "pointRadiusPt")
    renderer = spec["renderer"]
    if not isinstance(renderer, dict):
        raise InvalidParamsError("cartography renderer must be an object")
    if renderer.get("kind") == "single":
        _object(renderer, {"kind"}, "single renderer")
    elif renderer.get("kind") == "categorized":
        _object(renderer, {"kind", "field", "categories", "nullColor", "nullLabel", "otherColor", "otherLabel"}, "categorized renderer")
        field = _literal(renderer["field"], 256, "field")
        if field not in {item["name"] for item in dataset["fields"]}:
            raise InvalidParamsError("cartography field must exist in the registered dataset")
        categories = renderer["categories"]
        if not isinstance(categories, list) or len(categories) > MAX_CATEGORIES:
            raise InvalidParamsError("cartography categories must be an array of at most 64 entries")
        seen = set()
        for category in categories:
            _object(category, {"value", "label", "color"}, "category")
            item = category["value"]
            if isinstance(item, str):
                key = ("text", _literal(item, 1024, "category value"))
            elif isinstance(item, bool):
                key = ("boolean", item)
            else:
                key = ("number", _number(item, -MAX_SAFE_INTEGER, MAX_SAFE_INTEGER, "category value"))
            if key in seen:
                raise InvalidParamsError("cartography categories must have unique typed values")
            seen.add(key)
            _literal(category["label"], 120, "category label")
            _color(category["color"], "category color")
        for name in ("nullColor", "otherColor"):
            _color(renderer[name], name)
        for name in ("nullLabel", "otherLabel"):
            _literal(renderer[name], 120, name)
    else:
        raise InvalidParamsError("cartography renderer must be single or categorized")
    legend = _object(spec["legend"], {"visible", "title"}, "legend")
    if not isinstance(legend["visible"], bool):
        raise InvalidParamsError("cartography legend visible must be a boolean")
    _literal(legend["title"], 120, "legend title")
    return json.loads(serialized)
