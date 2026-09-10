from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .errors import DomainError, InvalidParamsError
from .projects import ProjectStore
from .validation import MAX_PATH_LENGTH, require_crs, require_exact_keys, require_path, require_string
from .workspace import WorkspaceStore

MAX_PROGRESS_BYTES = 64 * 1024
MAX_RESULT_BYTES = 1024 * 1024
CANCEL_GRACE_SECONDS = 2.0


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    serialized = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _bounded_json(path: Path, maximum: int) -> Any:
    try:
        with path.open("rb") as handle:
            raw = handle.read(maximum + 1)
    except OSError as exc:
        raise DomainError("Worker result is missing", kind="invalid_worker_result", detail=str(path)) from exc
    if len(raw) > maximum:
        raise DomainError("Worker result exceeds the size limit", kind="invalid_worker_result")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise DomainError("Worker result is malformed", kind="invalid_worker_result", detail=str(exc)) from exc


def _default_command(request_path: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker", str(request_path)]
    return [sys.executable, "-m", "spatial_engine", "--worker", str(request_path)]


class TaskManager:
    def __init__(
        self,
        projects: ProjectStore,
        workspace: WorkspaceStore,
        *,
        command_factory: Callable[[Path], list[str]] | None = None,
    ) -> None:
        self.projects = projects
        self.workspace = workspace
        self._command_factory = command_factory or _default_command
        self._process: subprocess.Popen[bytes] | None = None
        self._task_id: str | None = None
        self._work_dir: Path | None = None
        self._operation: str | None = None
        self._stderr_handle: Any = None

    def require_mutation_allowed(self) -> None:
        self.harvest()
        if self._operation == "save_as":
            raise DomainError("Project copy is running", kind="task_busy")

    def start_save_as(self, params: dict[str, Any]) -> dict[str, Any]:
        from . import portability
        require_exact_keys(params, {"path", "directory", "name", "description", "analysisCrs", "displayCrs", "viewState"})
        source = self.projects.active_path(params["path"])
        self.workspace.recover()
        self.harvest()
        self._require_idle()
        options = portability.validate_save_as(params, source)
        task_id = str(uuid.uuid4())
        payload = portability.prepare_copy(self.workspace, task_id, str(uuid.uuid4()), options)
        try:
            self._start(task_id, "save_as", payload, publish_path=None)
        except Exception:
            portability.abandon_copy(self.workspace, task_id)
            raise
        return self.workspace.task(task_id)

    def start_relocation(self, params: dict[str, Any]) -> dict[str, Any]:
        from . import portability
        require_exact_keys(params, {"path", "datasetId", "sourcePath"})
        self.projects.active_path(params["path"])
        self.workspace.recover()
        self.harvest()
        self._require_idle()
        dataset = self.workspace.dataset(params["path"], params["datasetId"])
        source = portability.relocation_candidate(dataset, params["sourcePath"])
        task_id = str(uuid.uuid4())
        task = self.workspace.create_task("relocate", task_id=task_id, dataset_id=dataset["id"], destination=str(source))
        self._start(task_id, "relocate", {"dataset": dataset, "sourcePath": str(source)}, publish_path=None)
        return task

    def start_import(self, params: dict[str, Any]) -> dict[str, Any]:
        require_exact_keys(params, {"path", "sourcePath", "sourceLayer", "encoding", "assignedCrs"})
        self.projects.active_path(params["path"])
        self.workspace.recover()
        self.harvest()
        self._require_idle()
        source_path = require_path(params["sourcePath"], "sourcePath")
        is_gdb_directory = source_path.is_dir() and source_path.suffix.lower() == ".gdb"
        if not source_path.is_file() and not is_gdb_directory:
            raise DomainError("Vector source does not exist", kind="source_not_found", detail=str(source_path))
        source_layer = require_string(params["sourceLayer"], "sourceLayer", maximum=1024)
        encoding = self._nullable_string(params["encoding"], "encoding", 128)
        assigned_crs = require_crs(params["assignedCrs"], "assignedCrs", nullable=True)
        task_id = str(uuid.uuid4())
        dataset_id = str(uuid.uuid4())
        task = self.workspace.create_task("import", task_id=task_id, dataset_id=dataset_id)
        payload = {
            "sourcePath": str(source_path), "sourceLayer": source_layer, "encoding": encoding,
            "assignedCrs": assigned_crs, "datasetId": dataset_id,
        }
        self._start(task_id, "import", payload, publish_path=None)
        return task

    def start_export(self, params: dict[str, Any]) -> dict[str, Any]:
        require_exact_keys(params, {"path", "datasetId", "destination"})
        self.projects.active_path(params["path"])
        self.workspace.recover()
        self.harvest()
        self._require_idle()
        dataset_id = require_string(params["datasetId"], "datasetId", maximum=64)
        dataset = self.workspace.dataset(params["path"], dataset_id)
        managed_path = self.workspace.managed_path(dataset)
        destination = require_path(params["destination"], "destination")
        raster_export = dataset["kind"] == "raster"
        allowed_extensions = {".tif", ".tiff"} if raster_export else {".gpkg"}
        if destination.suffix.lower() not in allowed_extensions:
            expected = ".tif or .tiff" if raster_export else ".gpkg"
            raise InvalidParamsError(f"destination must use the {expected} extension")
        if destination.exists():
            raise DomainError("Export destination already exists", kind="destination_exists", detail=str(destination))
        if not destination.parent.is_dir():
            raise DomainError("Export destination directory does not exist", kind="invalid_destination", detail=str(destination.parent))
        if raster_export:
            from . import rasters

            rasters._sidecars(destination)
        task_id = str(uuid.uuid4())
        task = self.workspace.create_task(
            "export", task_id=task_id, dataset_id=dataset_id, destination=str(destination)
        )
        publish_path = destination.with_name(f".{destination.name}.{task_id}.pending")
        if publish_path.exists():
            self.workspace.update_task(task_id, status="failed", stage="failed", error="Export temporary path exists")
            raise DomainError("Export temporary path already exists", kind="destination_exists", detail=str(publish_path))
        self._start(
            task_id,
            "raster_export" if raster_export else "export",
            {"dataset": dataset, "managedPath": str(managed_path)},
            publish_path=publish_path,
        )
        return task

    def start_raster_import(self, params: dict[str, Any]) -> dict[str, Any]:
        require_exact_keys(params, {"path", "sourcePath"})
        self.projects.active_path(params["path"])
        self.workspace.recover()
        self.harvest()
        self._require_idle()
        source_path = require_path(params["sourcePath"], "sourcePath")
        if not source_path.is_file() or source_path.suffix.lower() not in {".tif", ".tiff"}:
            raise DomainError("Raster source must be an existing GeoTIFF file", kind="unsupported_source")
        task_id = str(uuid.uuid4())
        dataset_id = str(uuid.uuid4())
        task = self.workspace.create_task("import", task_id=task_id, dataset_id=dataset_id)
        self._start(
            task_id,
            "raster_import",
            {"sourcePath": str(source_path), "datasetId": dataset_id},
            publish_path=None,
        )
        return task

    def start_table_import(self, params: dict[str, Any]) -> dict[str, Any]:
        require_exact_keys(params, {"path", "sourcePath", "encoding", "delimiter", "sheet", "headerRow"})
        self.projects.active_path(params["path"])
        self.workspace.recover()
        self.harvest()
        self._require_idle()
        source_path = require_path(params["sourcePath"], "sourcePath")
        if not source_path.is_file() or source_path.suffix.lower() not in {".csv", ".xlsx"}:
            raise DomainError("Table source must be an existing CSV or XLSX file", kind="unsupported_source")
        from . import tables

        options = tables.validate_options(
            {
                "sourcePath": str(source_path), "encoding": params["encoding"], "delimiter": params["delimiter"],
                "sheet": params["sheet"], "headerRow": params["headerRow"],
            },
            for_import=True,
        )
        task_id = str(uuid.uuid4())
        dataset_id = str(uuid.uuid4())
        task = self.workspace.create_task("import", task_id=task_id, dataset_id=dataset_id)
        self._start(
            task_id,
            "table_import",
            {**options, "datasetId": dataset_id},
            publish_path=None,
        )
        return task

    def start_table_points(self, params: dict[str, Any]) -> dict[str, Any]:
        require_exact_keys(params, {"path", "datasetId", "xField", "yField", "declaredCrs"})
        self.projects.active_path(params["path"])
        self.workspace.recover()
        self.harvest()
        self._require_idle()
        source_id = require_string(params["datasetId"], "datasetId", maximum=64)
        dataset = self.workspace.dataset(params["path"], source_id)
        if dataset["kind"] != "table":
            raise DomainError("Point generation requires a table dataset", kind="invalid_dataset")
        x_field = require_string(params["xField"], "xField", maximum=256)
        y_field = require_string(params["yField"], "yField", maximum=256)
        if x_field == y_field:
            raise InvalidParamsError("xField and yField must be different")
        field_names = {field["name"] for field in dataset["fields"]}
        if x_field not in field_names or y_field not in field_names:
            raise InvalidParamsError("xField and yField must name table fields")
        declared_crs = require_crs(params["declaredCrs"], "declaredCrs")
        managed_path = self.workspace.managed_path(dataset)
        task_id = str(uuid.uuid4())
        output_dataset_id = str(uuid.uuid4())
        task = self.workspace.create_task("points", task_id=task_id, dataset_id=output_dataset_id)
        self._start(
            task_id,
            "points",
            {
                "dataset": dataset, "managedPath": str(managed_path), "datasetId": output_dataset_id,
                "xField": x_field, "yField": y_field, "declaredCrs": declared_crs,
            },
            publish_path=None,
        )
        return task

    def get(self, params: dict[str, Any]) -> dict[str, Any]:
        require_exact_keys(params, {"path", "taskId"})
        self.projects.active_path(params["path"])
        self.harvest()
        return self.workspace.task(params["taskId"])

    def cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        require_exact_keys(params, {"path", "taskId"})
        self.projects.active_path(params["path"])
        task_id = require_string(params["taskId"], "taskId", maximum=64)
        task = self.workspace.task(task_id)
        if task["status"] != "running":
            return task
        work_dir: Path | None = None
        if self._task_id == task_id and self._process is not None and self._work_dir is not None:
            work_dir = self._work_dir
            self._write_cancel_marker(self._work_dir / "cancel.flag")
            deadline = time.monotonic() + CANCEL_GRACE_SECONDS
            while self._process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=1.0)
            else:
                self._process.wait()
            self._clear_process()
        self.workspace.discard_pending(task_id)
        if task["kind"] == "save_as":
            from .portability import abandon_copy
            abandon_copy(self.workspace, task_id)
        self._cleanup_export_pending(task)
        if work_dir is not None:
            self._cleanup_work_dir(work_dir)
        return self.workspace.update_task(
            task_id, status="cancelled", stage="cancelled", error="Task was cancelled"
        )

    def harvest(self) -> None:
        if self._process is None or self._task_id is None or self._work_dir is None:
            return
        self._harvest_progress()
        if self._process.poll() is None:
            return
        task_id = self._task_id
        work_dir = self._work_dir
        operation = self._operation
        return_code = self._process.wait()
        self._clear_process()
        try:
            result = _bounded_json(work_dir / "result.json", MAX_RESULT_BYTES)
            if not isinstance(result, dict) or set(result) != {"ok", "result"} and set(result) != {"ok", "error"}:
                raise DomainError("Worker result shape is invalid", kind="invalid_worker_result")
            if result.get("ok") is not True:
                error = result.get("error")
                if not isinstance(error, dict):
                    raise DomainError("Worker error is invalid", kind="invalid_worker_result")
                message = error.get("message")
                kind = error.get("kind")
                if not isinstance(message, str) or not isinstance(kind, str):
                    raise DomainError("Worker error is invalid", kind="invalid_worker_result")
                status = "cancelled" if kind == "task_cancelled" else "failed"
                detail = error.get("detail")
                error_text = message if not isinstance(detail, str) or not detail else f"{message}: {detail}"
                self.workspace.update_task(task_id, status=status, stage=status, error=error_text[:8192])
                if self.workspace.task(task_id)["kind"] == "save_as":
                    from .portability import abandon_copy
                    abandon_copy(self.workspace, task_id)
                self._cleanup_work_dir(work_dir)
                return
            if return_code != 0:
                raise DomainError("Worker exited unsuccessfully", kind="worker_failed", detail=str(return_code))
            payload = result["result"]
            task = self.workspace.task(task_id)
            if task["kind"] == "save_as":
                from .portability import prepare_copy_publication, complete_copy_publication
                prepare_copy_publication(self.workspace, task_id, payload)
                complete_copy_publication(self.workspace, task_id)
            elif task["kind"] == "relocate":
                from .portability import complete_relocation
                complete_relocation(self.workspace, task_id, payload)
            elif task["kind"] != "export":
                self._complete_import(task_id, work_dir, payload, operation)
            else:
                self._complete_export(task_id, work_dir, payload, task, operation)
            self._cleanup_work_dir(work_dir)
        except Exception as exc:
            message = self._exception_text(exc)
            if self.workspace.has_pending(task_id):
                self.workspace.update_task(
                    task_id, status="interrupted", stage="publication_pending", error=message or "Publication was interrupted"
                )
            else:
                self.workspace.update_task(task_id, status="failed", stage="failed", error=message or "Worker failed")
                self._cleanup_export_pending(self.workspace.task(task_id))
                self._cleanup_work_dir(work_dir)

    def close(self) -> None:
        if self._task_id is not None:
            try:
                self.cancel({
                    "path": str(self.projects._session.path) if self.projects._session else "",
                    "taskId": self._task_id,
                })
            except Exception:
                if self._process is not None and self._process.poll() is None:
                    self._process.kill()
                    self._process.wait()
                self._clear_process()
        self.workspace.interrupt_running_tasks()

    def _start(self, task_id: str, kind: str, payload: dict[str, Any], *, publish_path: Path | None) -> None:
        root = self.projects._session.path.parent
        work_dir = root / "staging" / "tasks" / task_id
        try:
            work_dir.mkdir(parents=True, exist_ok=False)
            request_path = work_dir / "request.json"
            _atomic_json(request_path, {
                "protocolVersion": 4, "taskId": task_id, "kind": kind,
                "payload": payload, "workDir": str(work_dir.resolve()),
                "publishPath": str(publish_path.resolve()) if publish_path is not None else None,
            })
            stderr_handle = (work_dir / "stderr.log").open("ab")
            try:
                process = subprocess.Popen(
                    self._command_factory(request_path),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=stderr_handle,
                    cwd=str(root),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
                )
            except Exception:
                stderr_handle.close()
                raise
        except Exception as exc:
            self.workspace.update_task(task_id, status="failed", stage="failed", error=str(exc))
            if 'work_dir' in locals():
                self._cleanup_work_dir(work_dir)
            if publish_path is not None:
                task = self.workspace.task(task_id)
                self._cleanup_export_pending(task)
            raise DomainError("Could not start GIS worker", kind="worker_start_failed", detail=str(exc)) from exc
        self._process = process
        self._task_id = task_id
        self._work_dir = work_dir
        self._operation = kind
        self._stderr_handle = stderr_handle

    def _require_idle(self) -> None:
        running = self.workspace.running_task()
        if self._process is not None or running is not None:
            raise DomainError("Another GIS task is already running", kind="task_busy")

    def _harvest_progress(self) -> None:
        if self._work_dir is None or self._task_id is None:
            return
        progress_path = self._work_dir / "progress.json"
        if not progress_path.is_file():
            return
        try:
            progress = _bounded_json(progress_path, MAX_PROGRESS_BYTES)
            if not isinstance(progress, dict) or set(progress) != {"stage", "completed", "total"}:
                return
            stage = progress["stage"]
            completed = progress["completed"]
            total = progress["total"]
            if not isinstance(stage, str) or len(stage) > 200:
                return
            if any(value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0)
                   for value in (completed, total)):
                return
            if completed is not None and total is not None and completed > total:
                return
            self.workspace.update_task(self._task_id, stage=stage, completed=completed, total=total)
        except (DomainError, OSError, ValueError):
            return

    def _complete_import(self, task_id: str, work_dir: Path, payload: Any, operation: str | None = None) -> None:
        if not isinstance(payload, dict) or set(payload) != {"dataset", "artifactPath"}:
            raise DomainError("Import result shape is invalid", kind="invalid_worker_result")
        if operation is None and isinstance(payload["dataset"], dict) and payload["dataset"].get("kind") == "raster":
            operation = "raster_import"
        filename = "snapshot.tif" if operation == "raster_import" else "snapshot.gpkg"
        artifact = self._worker_artifact(work_dir, payload["artifactPath"], filename)
        self.workspace.prepare_import_publication(task_id, payload["dataset"], artifact)
        self.workspace.complete_import_publication(task_id)

    def _complete_export(
        self, task_id: str, work_dir: Path, payload: Any, task: dict[str, Any], operation: str | None = None
    ) -> None:
        if not isinstance(payload, dict) or set(payload) != {
            "artifactPath", "publicationPath", "artifactSize", "artifactSha256"
        }:
            raise DomainError("Export result shape is invalid", kind="invalid_worker_result")
        if operation is None and task["datasetId"] is not None:
            dataset = self.workspace.dataset(str(self.projects._session.path), task["datasetId"])
            operation = "raster_export" if dataset["kind"] == "raster" else "export"
        filename = "export.tif" if operation == "raster_export" else "export.gpkg"
        self._worker_artifact(work_dir, payload["artifactPath"], filename)
        publication = Path(payload["publicationPath"]).resolve(strict=False) if isinstance(payload["publicationPath"], str) else None
        destination = Path(task["destination"]).resolve(strict=False)
        expected = destination.with_name(f".{destination.name}.{task_id}.pending")
        if publication != expected or not publication.is_file():
            raise DomainError("Export publication path is invalid", kind="invalid_worker_result")
        self.workspace.prepare_export_publication(
            task_id, publication, payload["artifactSize"], payload["artifactSha256"]
        )
        self.workspace.complete_export_publication(task_id)

    @staticmethod
    def _worker_artifact(work_dir: Path, value: Any, filename: str) -> Path:
        if not isinstance(value, str) or len(value) > MAX_PATH_LENGTH:
            raise DomainError("Worker artifact path is invalid", kind="invalid_worker_result")
        artifact = Path(value).resolve(strict=False)
        expected = (work_dir / filename).resolve(strict=False)
        if artifact != expected or not artifact.is_file():
            raise DomainError("Worker artifact path is invalid", kind="invalid_worker_result")
        return artifact

    @staticmethod
    def _nullable_string(value: Any, label: str, maximum: int) -> str | None:
        if value is None:
            return None
        return require_string(value, label, maximum=maximum)

    @staticmethod
    def _write_cancel_marker(path: Path) -> None:
        try:
            with path.open("x", encoding="ascii") as handle:
                handle.write("cancel\n")
        except FileExistsError:
            pass

    def _clear_process(self) -> None:
        if self._stderr_handle is not None:
            self._stderr_handle.close()
        self._process = None
        self._task_id = None
        self._work_dir = None
        self._operation = None
        self._stderr_handle = None

    def _cleanup_work_dir(self, work_dir: Path) -> None:
        session = self.projects._session
        if session is None:
            return
        tasks_root = (session.path.parent / "staging" / "tasks").resolve(strict=False)
        candidate = work_dir.resolve(strict=False)
        if candidate.parent != tasks_root:
            return
        try:
            uuid.UUID(candidate.name)
        except ValueError:
            return
        shutil.rmtree(candidate, ignore_errors=True)

    def _cleanup_export_pending(self, task: dict[str, Any]) -> None:
        if task["kind"] != "export" or task["destination"] is None:
            return
        self.workspace._remove_owned_export_pending(task["id"], task["destination"])

    @staticmethod
    def _exception_text(exc: Exception) -> str:
        if isinstance(exc, DomainError):
            return (exc.message if not exc.detail else f"{exc.message}: {exc.detail}")[:8192]
        return str(exc)[:8192]

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
