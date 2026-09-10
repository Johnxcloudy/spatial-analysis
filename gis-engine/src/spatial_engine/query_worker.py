"""Private, allowlisted query-only subprocess protocol."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .errors import EngineError, DomainError

MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
OPERATIONS = frozenset({"source.inspect", "table.inspect", "raster.inspect", "vector.page",
    "vector.viewport", "vector.feature", "table.page", "raster.render", "raster.sample", "analysis.result"})


def dispatch(operation, payload):
    if operation not in OPERATIONS or not isinstance(payload, dict):
        raise DomainError("Unsupported query operation", kind="invalid_query")
    from . import vectors, vector_queries, tables, rasters
    params = payload.get("params")
    if not isinstance(params, dict):
        raise DomainError("Query params must be an object", kind="invalid_query")
    inspections = {"source.inspect": vectors.inspect_source, "table.inspect": tables.inspect_table,
                   "raster.inspect": rasters.inspect_raster}
    if operation in inspections:
        if set(payload) != {"params"}:
            raise DomainError("Unexpected inspection fields", kind="invalid_query")
        return inspections[operation](params)
    if set(payload) != {"dataset", "managedPath", "params"}:
        raise DomainError("Unexpected query fields", kind="invalid_query")
    queries = {"vector.page": vector_queries.attribute_page, "table.page": vector_queries.attribute_page,
               "vector.viewport": vector_queries.viewport, "vector.feature": vector_queries.feature,
               "raster.render": rasters.render, "raster.sample": rasters.sample}
    if operation == "analysis.result":
        from .analysis import result_page
        query = result_page
    else:
        query = queries[operation]
    return query(payload["dataset"], Path(payload["managedPath"]), params)


def _write(value):
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
    if len(raw) + 1 > MAX_RESPONSE_BYTES:
        raw = b'{"ok":false,"error":{"kind":"query_limit","message":"Query response exceeds limit"}}'
    sys.stdout.buffer.write(raw + b"\n")
    sys.stdout.buffer.flush()


def run():
    from .resources import configure_native_data_paths
    configure_native_data_paths()
    # Native imports are cold-start work, outside the per-operation deadline.
    from . import vectors, vector_queries, tables, rasters
    _write({"ready": True, "protocolVersion": 1})
    while raw := sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1):
        try:
            if len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
                return 2
            request = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            if not isinstance(request, dict) or set(request) != {"operation", "payload"}:
                raise DomainError("Invalid query frame", kind="invalid_query")
            _write({"ok": True, "result": dispatch(request["operation"], request["payload"])})
        except EngineError as exc:
            _write({"ok": False, "error": {"kind": getattr(exc, "kind", "invalid_query"),
                                            "message": exc.message, "detail": getattr(exc, "detail", None)}})
        except Exception as exc:
            _write({"ok": False, "error": {"kind": "query_failed", "message": "Query failed", "detail": str(exc)[:512]}})
    return 0
