from __future__ import annotations

import json
import hashlib
import math
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from .errors import DomainError

MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 1024 * 1024
MAX_PROGRESS_BYTES = 64 * 1024
REPLACE_RETRY_SECONDS = 1.0


def _replace_with_retry(source: Path, destination: Path) -> None:
    deadline = time.monotonic() + REPLACE_RETRY_SECONDS
    delay = 0.005
    while True:
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            sharing_violation = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {5, 32, 33}
            if not sharing_violation or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.05)


def _atomic_json(path: Path, value: Any, maximum: int) -> None:
    serialized = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(serialized) > maximum:
        raise DomainError("Worker output exceeds its size limit", kind="worker_output_too_large")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_request(path: Path) -> dict[str, Any]:
    if path.name != "request.json" or not path.is_file():
        raise DomainError("Worker request path is invalid", kind="invalid_worker_request")
    raw = path.read_bytes()
    if len(raw) > MAX_REQUEST_BYTES:
        raise DomainError("Worker request exceeds the size limit", kind="invalid_worker_request")
    try:
        request = json.loads(
            raw.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non-finite number {value}")),
            parse_float=_finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise DomainError("Worker request is malformed", kind="invalid_worker_request", detail=str(exc)) from exc
    if not isinstance(request, dict) or set(request) != {
        "protocolVersion", "taskId", "kind", "payload", "workDir", "publishPath"
    }:
        raise DomainError("Worker request shape is invalid", kind="invalid_worker_request")
    if request["protocolVersion"] != 1 or request["kind"] not in {"import", "export"}:
        raise DomainError("Worker request version or kind is invalid", kind="invalid_worker_request")
    if not isinstance(request["taskId"], str):
        raise DomainError("Worker task id is invalid", kind="invalid_worker_request")
    try:
        uuid.UUID(request["taskId"])
    except ValueError as exc:
        raise DomainError("Worker task id is invalid", kind="invalid_worker_request") from exc
    if not isinstance(request["payload"], dict):
        raise DomainError("Worker payload is invalid", kind="invalid_worker_request")
    work_dir = Path(request["workDir"]).resolve(strict=False) if isinstance(request["workDir"], str) else None
    if work_dir is None or work_dir != path.parent.resolve(strict=False) or work_dir.name != request["taskId"]:
        raise DomainError("Worker directory is invalid", kind="invalid_worker_request")
    publish_path = request["publishPath"]
    if request["kind"] == "import" and publish_path is not None:
        raise DomainError("Import publication path must be null", kind="invalid_worker_request")
    if request["kind"] == "export":
        if not isinstance(publish_path, str):
            raise DomainError("Export publication path is invalid", kind="invalid_worker_request")
        publication = Path(publish_path).resolve(strict=False)
        suffix = f".{request['taskId']}.pending"
        if not publication.parent.is_dir() or not publication.name.startswith(".") or not publication.name.endswith(suffix):
            raise DomainError("Export publication path is invalid", kind="invalid_worker_request")
    return request


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("number is outside the finite float range")
    return result


def _validate_result(kind: str, result: Any, work_dir: Path) -> dict[str, Any]:
    expected = {"dataset", "artifactPath"} if kind == "import" else {"artifactPath"}
    if not isinstance(result, dict) or set(result) != expected:
        raise DomainError("Vector operation returned an invalid result", kind="invalid_worker_result")
    artifact_path = result.get("artifactPath")
    expected_name = "snapshot.gpkg" if kind == "import" else "export.gpkg"
    expected_path = (work_dir / expected_name).resolve(strict=False)
    if not isinstance(artifact_path, str) or Path(artifact_path).resolve(strict=False) != expected_path:
        raise DomainError("Vector operation returned an invalid artifact path", kind="invalid_worker_result")
    if not expected_path.is_file():
        raise DomainError("Vector operation did not create its artifact", kind="invalid_worker_result")
    return result


def _copy_export_for_publication(
    source: Path, destination: Path, ownership_marker: Path, progress, cancelled
) -> tuple[int, str]:
    total = source.stat().st_size
    completed = 0
    digest = hashlib.sha256()
    created = False
    try:
        with source.open("rb") as input_handle, destination.open("xb") as output_handle:
            created = True
            _atomic_json(ownership_marker, {"path": str(destination)}, MAX_PROGRESS_BYTES)
            while chunk := input_handle.read(1024 * 1024):
                if cancelled():
                    raise DomainError("Task was cancelled", kind="task_cancelled")
                output_handle.write(chunk)
                digest.update(chunk)
                completed += len(chunk)
                progress("publishing", completed, total)
            output_handle.flush()
            os.fsync(output_handle.fileno())
    except Exception:
        try:
            if created and destination.is_file():
                destination.unlink()
        except OSError:
            pass
        raise
    return completed, digest.hexdigest()


def run_worker(request_path: Path) -> int:
    work_dir = request_path.resolve(strict=False).parent
    publication_path: Path | None = None
    publication_owned = False
    try:
        request = _read_request(request_path.resolve(strict=False))
        work_dir = Path(request["workDir"]).resolve(strict=False)
        cancel_path = work_dir / "cancel.flag"

        def progress(stage: str, completed: int | None, total: int | None) -> None:
            if not isinstance(stage, str) or not stage or len(stage) > 200:
                raise DomainError("Progress stage is invalid", kind="invalid_worker_progress")
            for value in (completed, total):
                if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                    raise DomainError("Progress count is invalid", kind="invalid_worker_progress")
            if completed is not None and total is not None and completed > total:
                raise DomainError("Progress exceeds total", kind="invalid_worker_progress")
            _atomic_json(
                work_dir / "progress.json",
                {"stage": stage, "completed": completed, "total": total},
                MAX_PROGRESS_BYTES,
            )

        def cancelled() -> bool:
            return cancel_path.is_file()

        if cancelled():
            raise DomainError("Task was cancelled", kind="task_cancelled")
        from . import vectors

        operation = vectors.import_vector if request["kind"] == "import" else vectors.export_vector
        result = operation(request["payload"], work_dir, progress, cancelled)
        if cancelled():
            raise DomainError("Task was cancelled", kind="task_cancelled")
        validated = _validate_result(request["kind"], result, work_dir)
        if request["kind"] == "export":
            publication_path = Path(request["publishPath"]).resolve(strict=False)
            artifact_size, artifact_digest = _copy_export_for_publication(
                Path(validated["artifactPath"]), publication_path,
                work_dir / "publication-owned.json", progress, cancelled
            )
            publication_owned = True
            if cancelled():
                raise DomainError("Task was cancelled", kind="task_cancelled")
            validated = {
                **validated,
                "publicationPath": str(publication_path),
                "artifactSize": artifact_size,
                "artifactSha256": artifact_digest,
            }
        _atomic_json(work_dir / "result.json", {"ok": True, "result": validated}, MAX_RESULT_BYTES)
        return 0
    except DomainError as exc:
        if publication_owned and publication_path is not None:
            try:
                if publication_path.is_file():
                    publication_path.unlink()
            except OSError:
                pass
        error = {"kind": exc.kind, "message": exc.message, "detail": exc.detail}
        try:
            work_dir.mkdir(parents=True, exist_ok=True)
            _atomic_json(work_dir / "result.json", {"ok": False, "error": error}, MAX_RESULT_BYTES)
        except Exception:
            pass
        return 2
    except Exception as exc:
        if publication_owned and publication_path is not None:
            try:
                if publication_path.is_file():
                    publication_path.unlink()
            except OSError:
                pass
        error = {"kind": "worker_failed", "message": "GIS worker failed", "detail": str(exc)[:2048]}
        try:
            work_dir.mkdir(parents=True, exist_ok=True)
            _atomic_json(work_dir / "result.json", {"ok": False, "error": error}, MAX_RESULT_BYTES)
        except Exception:
            pass
        print(f"GIS worker failed: {exc}", file=sys.stderr)
        return 3
