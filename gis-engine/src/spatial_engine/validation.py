from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from pyproj import CRS
from pyproj.exceptions import CRSError

from .errors import InvalidParamsError

MAX_PATH_LENGTH = 32_767
MAX_NAME_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 20_000
MAX_CRS_LENGTH = 8_192
MIN_ZOOM = 0.0
MAX_ZOOM = 30.0


def require_object(value: Any, label: str = "params") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidParamsError(f"{label} must be an object")
    return value


def require_exact_keys(params: dict[str, Any], required: set[str]) -> None:
    missing = sorted(required - params.keys())
    extra = sorted(params.keys() - required)
    if missing:
        raise InvalidParamsError(f"missing parameter(s): {', '.join(missing)}")
    if extra:
        raise InvalidParamsError(f"unknown parameter(s): {', '.join(extra)}")


def require_string(value: Any, label: str, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise InvalidParamsError(f"{label} must be a string")
    result = value.strip()
    if not allow_empty and not result:
        raise InvalidParamsError(f"{label} must not be empty")
    if len(result) > maximum:
        raise InvalidParamsError(f"{label} exceeds the {maximum} character limit")
    return result


def require_path(value: Any, label: str) -> Path:
    raw = require_string(value, label, maximum=MAX_PATH_LENGTH)
    try:
        return Path(raw).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise InvalidParamsError(f"{label} is not a valid path") from exc


def require_crs(value: Any, label: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    raw = require_string(value, label, maximum=MAX_CRS_LENGTH)
    try:
        CRS.from_user_input(raw)
    except (CRSError, ValueError, TypeError) as exc:
        raise InvalidParamsError(f"{label} is not a recognized coordinate reference system") from exc
    return raw


def require_finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidParamsError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise InvalidParamsError(f"{label} must be finite")
    return result


def require_view_state(value: Any) -> dict[str, Any]:
    state = require_object(value, "viewState")
    require_exact_keys(state, {"center", "zoom"})
    center = state["center"]
    if not isinstance(center, (list, tuple)) or len(center) != 2:
        raise InvalidParamsError("viewState.center must contain exactly two numbers")
    x = require_finite_number(center[0], "viewState.center[0]")
    y = require_finite_number(center[1], "viewState.center[1]")
    zoom = require_finite_number(state["zoom"], "viewState.zoom")
    if not MIN_ZOOM <= zoom <= MAX_ZOOM:
        raise InvalidParamsError(f"viewState.zoom must be between {MIN_ZOOM:g} and {MAX_ZOOM:g}")
    return {"center": [x, y], "zoom": zoom}
