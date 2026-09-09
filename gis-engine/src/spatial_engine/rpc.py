from __future__ import annotations

import logging
from typing import Any

from .diagnostics import run_diagnostics
from .errors import DomainError, EngineError, InvalidParamsError, InvalidRequestError, MethodNotFoundError
from .logging_config import configure_logging
from .projects import ProjectStore
from .resources import configure_native_data_paths
from .runtime import runtime_info
from .tasks import TaskManager
from .workspace import WorkspaceStore
from . import tables, vector_queries, vectors
from .validation import require_exact_keys, require_object

ALLOWED_METHODS = {
    "runtime.info",
    "project.create",
    "project.open",
    "project.save",
    "project.close",
    "diagnostics.run",
    "source.inspect",
    "workspace.get",
    "vector.import",
    "vector.export",
    "table.inspect",
    "table.import",
    "table.export",
    "table.page",
    "table.points",
    "raster.inspect",
    "raster.import",
    "raster.export",
    "raster.render",
    "raster.sample",
    "task.get",
    "task.cancel",
    "layer.update",
    "layer.reorder",
    "layer.remove",
    "vector.page",
    "vector.viewport",
    "vector.feature",
}


class Engine:
    def __init__(self) -> None:
        configure_native_data_paths()
        self.logger = configure_logging()
        self.projects = ProjectStore()
        self.workspace = WorkspaceStore(self.projects)
        self.tasks = TaskManager(self.projects, self.workspace)
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
            self.tasks.close()
            return self.projects.create(values)
        if method == "project.open":
            self.tasks.close()
            return self.projects.open(values)
        if method == "project.save":
            return self.projects.save(values)
        if method == "project.close":
            require_exact_keys(values, set())
            self.tasks.close()
            return self.projects.close()
        if method == "diagnostics.run":
            return run_diagnostics(values)
        if method == "source.inspect":
            return vectors.inspect_source(values)
        if method == "table.inspect":
            return tables.inspect_table(values)
        if method == "raster.inspect":
            require_exact_keys(values, {"sourcePath"})
            from .rasters import inspect_raster
            return inspect_raster(values)
        if method == "workspace.get":
            self.projects.active_path(values.get("path"))
            self.tasks.harvest()
            return self.workspace.get(values)
        if method == "vector.import":
            return self.tasks.start_import(values)
        if method == "table.import":
            return self.tasks.start_table_import(values)
        if method == "table.points":
            return self.tasks.start_table_points(values)
        if method == "raster.import":
            require_exact_keys(values, {"path", "sourcePath"})
            return self.tasks.start_raster_import(values)
        if method in {"vector.export", "table.export", "raster.export"}:
            require_exact_keys(values, {"path", "datasetId", "destination"})
            dataset = self.workspace.dataset(values.get("path"), values.get("datasetId"))
            self._require_dataset_kind(dataset, method.split(".")[0])
            return self.tasks.start_export(values)
        if method in {"raster.render", "raster.sample"}:
            query_keys = {"bbox", "width", "height", "style"} if method == "raster.render" else {"coordinate"}
            require_exact_keys(values, {"path", "datasetId", *query_keys})
            dataset = self.workspace.dataset(values.get("path"), values.get("datasetId"))
            self._require_dataset_kind(dataset, "raster")
            from . import rasters
            query = rasters.render if method == "raster.render" else rasters.sample
            return query(dataset, self.workspace.managed_path(dataset), values)
        if method == "task.get":
            return self.tasks.get(values)
        if method == "task.cancel":
            return self.tasks.cancel(values)
        if method == "layer.update":
            return self.workspace.update_layer(values)
        if method == "layer.reorder":
            return self.workspace.reorder_layers(values)
        if method == "layer.remove":
            return self.workspace.remove_layer(values)
        queries = {
            "vector.page": vector_queries.attribute_page,
            "table.page": vector_queries.attribute_page,
            "vector.viewport": vector_queries.viewport,
            "vector.feature": vector_queries.feature,
        }
        if method in queries:
            keys = {
                "vector.page": {"path", "datasetId", "offset", "limit", "sortField", "descending", "filter"},
                "table.page": {"path", "datasetId", "offset", "limit", "sortField", "descending", "filter"},
                "vector.viewport": {"path", "datasetId", "bbox", "limit", "propertyFields"},
                "vector.feature": {"path", "datasetId", "featureId"},
            }
            require_exact_keys(values, keys[method])
            dataset = self.workspace.dataset(values.get("path"), values.get("datasetId"))
            self._require_dataset_kind(dataset, method.split(".")[0])
            return queries[method](dataset, self.workspace.managed_path(dataset), values)
        raise MethodNotFoundError()

    @staticmethod
    def _require_dataset_kind(dataset: dict, kind: str) -> None:
        if dataset["kind"] != kind:
            raise DomainError(f"This operation requires a {kind} dataset", kind="invalid_dataset_kind")

    def close(self) -> None:
        self.tasks.close()
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
