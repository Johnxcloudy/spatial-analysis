from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import EngineError
if TYPE_CHECKING:
    from .rpc import Engine

MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ERROR_DETAIL_LENGTH = 512


def _parse_json(raw: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value}")

    def parse_float(value: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("JSON number is outside the finite float range")
        return result

    return json.loads(raw, parse_constant=reject_constant, parse_float=parse_float)


def _parse_error(detail: str) -> dict:
    bounded_detail = detail[:MAX_ERROR_DETAIL_LENGTH]
    return {
        "jsonrpc": "2.0",
        "id": None,
        "error": {
            "code": -32700,
            "message": "Parse error",
            "data": {"kind": "parse_error", "detail": bounded_detail},
        },
    }


def _write_response(response: dict) -> None:
    try:
        serialized = json.dumps(response, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        logging.getLogger("spatial_engine").exception("Could not serialize RPC response")
        response_id = response.get("id")
        if isinstance(response_id, bool) or not isinstance(response_id, (str, int)):
            response_id = None
        fallback = {
            "jsonrpc": "2.0",
            "id": response_id,
            "error": {
                "code": -32000,
                "message": "Internal engine error",
                "data": {"kind": "response_serialization_failed"},
            },
        }
        serialized = json.dumps(fallback, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    # ASCII serialization makes character length equal to the UTF-8 wire size.
    if len(serialized) + 1 > MAX_RESPONSE_BYTES:
        serialized = json.dumps({
            "jsonrpc": "2.0", "id": response.get("id"),
            "error": {"code": -32000, "message": "Response exceeds size limit",
                      "data": {"kind": "response_too_large"}},
        }, ensure_ascii=True, separators=(",", ":"))
    sys.stdout.buffer.write((serialized + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def _process_raw(engine: Engine, raw: str) -> dict:
    from .rpc import handle_request

    try:
        request = _parse_json(raw)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        return _parse_error(str(exc))
    return handle_request(engine, request)


def _serve_stdio(engine: Engine) -> None:
    while True:
        raw = sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1)
        if not raw:
            return
        if len(raw) > MAX_REQUEST_BYTES:
            while raw and not raw.endswith(b"\n"):
                raw = sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1)
            _write_response(_parse_error(f"request exceeds {MAX_REQUEST_BYTES} byte limit"))
            continue
        try:
            text = raw.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            _write_response(_parse_error(f"request is not UTF-8: {exc}"))
            continue
        if not text:
            _write_response(_parse_error("request line is empty"))
            continue
        _write_response(_process_raw(engine, text))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spatial-engine")
    parser.add_argument("--request", help="process one JSON-RPC request and exit")
    parser.add_argument("--worker", help="execute a managed GIS job request file")
    arguments = parser.parse_args(argv)
    if arguments.worker is not None:
        from .resources import configure_native_data_paths
        from .worker import run_worker

        configure_native_data_paths()
        return run_worker(Path(arguments.worker))
    from .rpc import Engine

    engine = Engine()
    try:
        if arguments.request is not None:
            if len(arguments.request.encode("utf-8")) > MAX_REQUEST_BYTES:
                _write_response(_parse_error(f"request exceeds {MAX_REQUEST_BYTES} byte limit"))
            else:
                _write_response(_process_raw(engine, arguments.request))
        else:
            _serve_stdio(engine)
        return 0
    except EngineError as exc:
        _write_response({"jsonrpc": "2.0", "id": None, "error": exc.as_rpc_error()})
        return 0
    finally:
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
