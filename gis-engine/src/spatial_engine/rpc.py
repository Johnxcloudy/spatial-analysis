from __future__ import annotations

import logging
from typing import Any

from .diagnostics import run_diagnostics
from .errors import DomainError, EngineError, InvalidParamsError, InvalidRequestError, MethodNotFoundError
from .logging_config import configure_logging
from .projects import ProjectStore
from .resources import configure_native_data_paths
from .runtime import runtime_info
from .validation import require_exact_keys, require_object

ALLOWED_METHODS = {
    "runtime.info",
    "project.create",
    "project.open",
    "project.save",
    "project.close",
    "diagnostics.run",
}


class Engine:
    def __init__(self) -> None:
        configure_native_data_paths()
        self.logger = configure_logging()
        self.projects = ProjectStore()
        self.logger.info("Engine started")

    def dispatch(self, method: str, params: Any) -> Any:
        self.logger.info("RPC request: %s", method if isinstance(method, str) else "<invalid>")
        if method not in ALLOWED_METHODS:
            raise MethodNotFoundError()
        values = require_object(params)
        if method == "runtime.info":
            require_exact_keys(values, set())
            return runtime_info()
        if method == "project.create":
            return self.projects.create(values)
        if method == "project.open":
            return self.projects.open(values)
        if method == "project.save":
            return self.projects.save(values)
        if method == "project.close":
            require_exact_keys(values, set())
            return self.projects.close()
        if method == "diagnostics.run":
            return run_diagnostics(values)
        raise MethodNotFoundError()

    def close(self) -> None:
        self.projects.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def handle_request(engine: Engine, request: Any) -> dict:
    raw_request_id = request.get("id") if isinstance(request, dict) else None
    request_id = raw_request_id if isinstance(raw_request_id, (str, int)) and not isinstance(raw_request_id, bool) else None
    try:
        if not isinstance(request, dict):
            raise InvalidRequestError("request must be an object")
        if set(request.keys()) != {"jsonrpc", "id", "method", "params"}:
            raise InvalidRequestError("request must contain exactly jsonrpc, id, method, and params")
        if request["jsonrpc"] != "2.0":
            raise InvalidRequestError("jsonrpc must be 2.0")
        if isinstance(request["id"], bool) or not isinstance(request["id"], (str, int)):
            raise InvalidRequestError("id must be a string or integer")
        if not isinstance(request["method"], str):
            raise InvalidRequestError("method must be a string")
        result = engine.dispatch(request["method"], request["params"])
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except EngineError as exc:
        return {"jsonrpc": "2.0", "id": request_id, "error": exc.as_rpc_error()}
    except Exception:
        logging.getLogger("spatial_engine").exception("Unhandled RPC failure")
        error = DomainError("Internal engine error", kind="internal_error")
        return {"jsonrpc": "2.0", "id": request_id, "error": error.as_rpc_error()}
