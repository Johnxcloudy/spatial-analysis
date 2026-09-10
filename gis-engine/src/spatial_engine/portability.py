from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import uuid
from pathlib import Path
from typing import Any

from .errors import DomainError, InvalidParamsError
from .projects import (
    OWNED_DIRECTORIES, PROJECT_FILENAME, _connect_readonly, _read_project, _utc_now,
)
from .validation import (
    MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH, MAX_PATH_LENGTH, require_crs,
    require_exact_keys, require_path, require_string, require_view_state,
)

MAX_COPY_BYTES = 32 * 1024**3
MAX_SOURCE_FILES = 100_000
COPY_MARKER = ".spa-copy-pending.json"


def _cancel(cancelled) -> None:
    if cancelled():
        raise DomainError("Task was cancelled", kind="task_cancelled")


def _no_links(path: Path) -> None:
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise DomainError("Links and reparse points are not valid portability inputs", kind="unsafe_path", detail=str(candidate))


def _path(value: Any, label: str) -> Path:
    raw = require_string(value, label, maximum=MAX_PATH_LENGTH)
    _no_links(Path(raw).expanduser().absolute())
    return require_path(raw, label)


def validate_save_as(params: dict, source: Path) -> dict:
    require_exact_keys(params, {"path", "directory", "name", "description", "analysisCrs", "displayCrs", "viewState"})
    destination = _path(params["directory"], "directory")
    _no_links(source)
    root = source.parent.resolve()
    if destination == root or destination.is_relative_to(root):
        raise DomainError("Save As destination must be outside the source project", kind="invalid_destination")
    if destination.exists():
        raise DomainError("Save As destination already exists", kind="destination_exists", detail=str(destination))
    if not destination.parent.is_dir():
        raise DomainError("Save As destination parent does not exist", kind="invalid_destination")
    return {
        "directory": str(destination),
        "name": require_string(params["name"], "name", maximum=MAX_NAME_LENGTH),
        "description": require_string(params["description"], "description", maximum=MAX_DESCRIPTION_LENGTH, allow_empty=True),
        "analysisCrs": require_crs(params["analysisCrs"], "analysisCrs", nullable=True),
        "displayCrs": require_crs(params["displayCrs"], "displayCrs"),
        "viewState": require_view_state(params["viewState"]),
    }


def _registry(connection: sqlite3.Connection) -> list[dict]:
    from .workspace import MAX_DATASETS, _validate_dataset

    rows = connection.execute("SELECT dataset_id, version, relative_path, dataset_json FROM datasets ORDER BY dataset_id").fetchall()
    if len(rows) > MAX_DATASETS:
        raise DomainError("Project dataset limit exceeded", kind="invalid_project")
    datasets = []
    for row in rows:
        try:
            dataset = json.loads(row[3])
            _validate_dataset(dataset)
        except (TypeError, ValueError, DomainError) as exc:
            raise DomainError("Dataset registry is invalid", kind="invalid_project") from exc
        if (dataset["id"], dataset["version"], dataset["relativePath"]) != tuple(row[:3]):
            raise DomainError("Dataset registry identity is inconsistent", kind="invalid_project")
        datasets.append(dataset)
    by_id = {dataset["id"]: dataset for dataset in datasets}
    for dataset in datasets:
        if dataset["source"]["driver"] == "TablePoints":
            metadata = dataset["source"]["metadata"]
            parent = by_id.get(metadata.get("parentDatasetId"))
            if parent is None or parent["kind"] != "table" or parent["version"] != metadata.get("parentVersion"):
                raise DomainError("Derived dataset parent is missing or changed", kind="invalid_project")
    return datasets


def prepare_copy(workspace, task_id: str, project_id: str, options: dict) -> dict:
    path = workspace.projects._session.path
    destination = Path(options["directory"])
    temporary = destination.with_name(f".{destination.name}.{task_id}.pending")
    _no_links(temporary)
    if temporary.exists():
        raise DomainError("Copy temporary directory already exists", kind="destination_exists")
    with workspace._connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for table in ("pending_publications", "pending_exports", "pending_copies"):
            if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                raise DomainError("Resolve pending publications before Save As", kind="publication_pending")
        datasets = _registry(connection)
        plan = {dataset["relativePath"]: dataset["version"] for dataset in datasets}
        total = path.stat().st_size
        for relative in plan:
            snapshot = path.parent / relative
            _no_links(snapshot)
            if not snapshot.is_file():
                raise DomainError("Managed dataset file is missing", kind="dataset_missing", detail=relative)
            total += snapshot.stat().st_size
        if total > MAX_COPY_BYTES:
            raise DomainError("Project exceeds the 32 GiB copy budget", kind="copy_limit")
        timestamp = _utc_now()
        connection.execute(
            "INSERT INTO tasks VALUES (?, 'save_as', 'running', 'queued', NULL, NULL, ?, ?, NULL, ?, NULL)",
            (task_id, timestamp, timestamp, str(destination / PROJECT_FILENAME)),
        )
        connection.execute("INSERT INTO pending_copies VALUES (?, ?, ?, ?, ?, NULL)",
                           (task_id, str(temporary), str(destination), project_id, json.dumps(plan)))
    return {"sourceProject": str(path), "taskId": task_id, "projectId": project_id,
            "temporaryPath": str(temporary), "options": options, "plan": plan}


def _hash(path: Path, *, cancelled=lambda: False) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    _no_links(path)
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            _cancel(cancelled)
            size += len(chunk)
            if size > MAX_COPY_BYTES:
                raise DomainError("File exceeds the copy budget", kind="copy_limit")
            digest.update(chunk)
    return size, digest.hexdigest()


def _marker(task_id: str, project_id: str) -> dict:
    return {"taskId": task_id, "projectId": project_id}


def copy_project(payload: dict, work_dir: Path, *, progress, cancelled) -> dict:
    require_exact_keys(payload, {"sourceProject", "taskId", "projectId", "temporaryPath", "options", "plan"})
    source = _path(payload["sourceProject"], "sourceProject")
    options = validate_save_as({"path": str(source), **payload["options"]}, source)
    destination = Path(options["directory"])
    task_id = str(uuid.UUID(payload["taskId"]))
    project_id = str(uuid.UUID(payload["projectId"]))
    temporary = _path(payload["temporaryPath"], "temporaryPath")
    if temporary != destination.with_name(f".{destination.name}.{task_id}.pending") or task_id != work_dir.name:
        raise DomainError("Copy worker destination is invalid", kind="invalid_worker_request")
    _cancel(cancelled)
    progress("backing_up", None, None)
    temporary.mkdir(exist_ok=False)
    with (temporary / COPY_MARKER).open("x", encoding="ascii") as marker:
        json.dump(_marker(task_id, project_id), marker)
        marker.flush()
        os.fsync(marker.fileno())
    target_path = temporary / PROJECT_FILENAME
    source_db = _connect_readonly(source)
    target_db = sqlite3.connect(target_path)
    try:
        def backup_progress(status, remaining, total):
            _cancel(cancelled)
            if total * 4096 > MAX_COPY_BYTES:
                raise DomainError("Project database exceeds the copy budget", kind="copy_limit")
        source_db.backup(target_db, pages=256, progress=backup_progress)
        for table in ("pending_publications", "pending_exports"):
            if target_db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                raise DomainError("Project has unresolved publications", kind="publication_pending")
        copies = target_db.execute("SELECT task_id FROM pending_copies").fetchall()
        if copies != [(task_id,)]:
            raise DomainError("Project copy journal changed", kind="invalid_project")
        datasets = _registry(target_db)
        if {d["relativePath"]: d["version"] for d in datasets} != payload["plan"]:
            raise DomainError("Dataset registry changed before copying", kind="invalid_project")
        timestamp = _utc_now()
        with target_db:
            target_db.execute("DELETE FROM pending_copies")
            target_db.execute("DELETE FROM tasks WHERE status = 'running'")
            target_db.execute(
                "UPDATE project_metadata SET project_id = ?, name = ?, description = ?, created_at = ?, updated_at = ?, "
                "analysis_crs = ?, display_crs = ?, view_state = ? WHERE singleton = 1",
                (project_id, options["name"], options["description"], timestamp, timestamp, options["analysisCrs"],
                 options["displayCrs"], json.dumps(options["viewState"])),
            )
        if target_db.execute("PRAGMA quick_check").fetchone() != ("ok",) or target_db.execute("PRAGMA foreign_key_check").fetchall():
            raise DomainError("Project backup is inconsistent", kind="invalid_project")
    finally:
        target_db.close()
        source_db.close()
    _read_project(target_path, allow_copy_pending=True)
    total = target_path.stat().st_size
    for dataset in datasets:
        snapshot = source.parent / dataset["relativePath"]
        _no_links(snapshot)
        if not snapshot.is_file():
            raise DomainError("Managed dataset file is missing", kind="dataset_missing")
        total += snapshot.stat().st_size
    if total > MAX_COPY_BYTES:
        raise DomainError("Project exceeds the 32 GiB copy budget", kind="copy_limit")
    completed = target_path.stat().st_size
    manifest = {PROJECT_FILENAME: list(_hash(target_path, cancelled=cancelled))}
    for directory in OWNED_DIRECTORIES:
        (temporary / directory).mkdir()
    for dataset in datasets:
        _cancel(cancelled)
        relative = dataset["relativePath"]
        original = source.parent / relative
        snapshot = temporary / relative
        digest = hashlib.sha256()
        size = 0
        with original.open("rb") as input_handle, snapshot.open("xb") as output_handle:
            while chunk := input_handle.read(1024 * 1024):
                _cancel(cancelled)
                size += len(chunk)
                completed += len(chunk)
                if completed > MAX_COPY_BYTES or completed > total:
                    raise DomainError("Snapshot grew beyond the copy budget", kind="snapshot_changed")
                output_handle.write(chunk)
                digest.update(chunk)
                progress("copying", completed, total)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        if digest.hexdigest() != dataset["version"]:
            raise DomainError("Managed snapshot differs from its registered SHA-256", kind="snapshot_changed", detail=relative)
        progress("verifying_copy", completed, total)
        if _hash(snapshot, cancelled=cancelled) != (size, dataset["version"]):
            raise DomainError("Copied snapshot failed verification", kind="copy_failed")
        manifest[relative] = [size, dataset["version"]]
    _cancel(cancelled)
    return {"manifest": manifest, "temporaryPath": str(temporary), "projectId": project_id}


def _journal(workspace, task_id: str):
    with workspace._connect(workspace.projects._session.path) as connection:
        row = connection.execute("SELECT * FROM pending_copies WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        raise DomainError("Copy publication journal is missing", kind="invalid_project")
    task = workspace.task(task_id)
    destination = _path(row["destination_path"], "destination")
    temporary = _path(row["temporary_path"], "temporary")
    root = workspace.projects._session.path.parent.resolve()
    if (task["kind"] != "save_as" or task["destination"] != str(destination / PROJECT_FILENAME)
            or destination == root or destination.is_relative_to(root)
            or temporary != destination.with_name(f".{destination.name}.{task_id}.pending")):
        raise DomainError("Copy publication journal is invalid", kind="invalid_project")
    return row, temporary, destination


def _validate_manifest(manifest, plan):
    if not isinstance(manifest, dict) or set(manifest) != {PROJECT_FILENAME, *plan}:
        raise DomainError("Copy manifest files do not match the registry", kind="invalid_worker_result")
    total = 0
    for relative, entry in manifest.items():
        if (not isinstance(entry, list) or len(entry) != 2 or isinstance(entry[0], bool)
                or not isinstance(entry[0], int) or entry[0] < 0 or not isinstance(entry[1], str)
                or re.fullmatch(r"[0-9a-f]{64}", entry[1]) is None
                or (relative in plan and entry[1] != plan[relative])):
            raise DomainError("Copy manifest identity is invalid", kind="invalid_worker_result")
        total += entry[0]
    if total > MAX_COPY_BYTES:
        raise DomainError("Copy exceeds the 32 GiB budget", kind="copy_limit")


def _validate_copy(root: Path, row, manifest: dict, *, recovering: bool):
    _no_links(root)
    if not root.is_dir():
        raise DomainError("Pending project copy is missing", kind="copy_failed")
    expected_files = set(manifest)
    marker = root / COPY_MARKER
    if marker.exists():
        if marker.stat().st_size > 1024 or json.loads(marker.read_text(encoding="ascii")) != _marker(row["task_id"], row["project_id"]):
            raise DomainError("Copy ownership marker changed", kind="publication_conflict")
        expected_files.add(COPY_MARKER)
    elif root == Path(row["temporary_path"]):
        raise DomainError("Copy ownership marker is missing", kind="publication_conflict")
    actual_files = set()
    for path in root.rglob("*"):
        _no_links(path)
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            if relative not in OWNED_DIRECTORIES:
                raise DomainError("Unexpected directory in pending copy", kind="publication_conflict", detail=relative)
        elif path.is_file():
            actual_files.add(relative)
        else:
            raise DomainError("Unexpected entry in pending copy", kind="publication_conflict")
    if actual_files != expected_files:
        raise DomainError("Unexpected files in pending copy", kind="publication_conflict")
    for relative, entry in manifest.items():
        path = root / relative
        if path.stat().st_size != entry[0] or (recovering and _hash(path) != tuple(entry)):
            raise DomainError("Pending project copy changed", kind="publication_conflict", detail=relative)
    project = _read_project(root / PROJECT_FILENAME, allow_copy_pending=True)
    if project["id"] != row["project_id"]:
        raise DomainError("Copy project identity changed", kind="publication_conflict")


def _rename_directory_no_replace(source: Path, destination: Path) -> None:
    if os.name == "nt":
        os.rename(source, destination)
    elif sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        if not hasattr(libc, "renameat2"):
            raise OSError("Atomic directory no-replace rename is not available")
        result = libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
        if result != 0:
            errno = ctypes.get_errno()
            raise OSError(errno, os.strerror(errno))
    else:
        raise OSError("Atomic directory no-replace rename is not supported on this platform")


def prepare_copy_publication(workspace, task_id: str, result: dict) -> None:
    row, temporary, destination = _journal(workspace, task_id)
    if not isinstance(result, dict) or set(result) != {"manifest", "temporaryPath", "projectId"}:
        raise DomainError("Copy worker result is invalid", kind="invalid_worker_result")
    if result["temporaryPath"] != str(temporary) or result["projectId"] != row["project_id"]:
        raise DomainError("Copy worker identity is invalid", kind="invalid_worker_result")
    plan = json.loads(row["plan_json"])
    _validate_manifest(result["manifest"], plan)
    _validate_copy(temporary, row, result["manifest"], recovering=False)
    with workspace._connect(workspace.projects._session.path) as connection:
        connection.execute("UPDATE pending_copies SET manifest_json = ? WHERE task_id = ?",
                           (json.dumps(result["manifest"]), task_id))


def complete_copy_publication(workspace, task_id: str, *, recovering=False) -> dict:
    row, temporary, destination = _journal(workspace, task_id)
    if row["manifest_json"] is None:
        raise DomainError("Project copy was interrupted before validation", kind="copy_incomplete")
    manifest = json.loads(row["manifest_json"])
    _validate_manifest(manifest, json.loads(row["plan_json"]))
    if destination.exists():
        if not recovering or temporary.exists():
            raise DomainError("Save As destination already exists", kind="destination_exists")
        _validate_copy(destination, row, manifest, recovering=True)
    else:
        _validate_copy(temporary, row, manifest, recovering=recovering)
        try:
            _rename_directory_no_replace(temporary, destination)
        except OSError as exc:
            raise DomainError("Could not publish project copy", kind="publication_failed", detail=str(exc)) from exc
    marker = destination / COPY_MARKER
    if marker.exists():
        marker.unlink()
    with workspace._connect(workspace.projects._session.path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE tasks SET status = 'completed', stage = 'completed', completed = total, updated_at = ?, error = NULL WHERE task_id = ?",
                           (_utc_now(), task_id))
        connection.execute("DELETE FROM pending_copies WHERE task_id = ?", (task_id,))
    return workspace.task(task_id)


def abandon_copy(workspace, task_id: str) -> bool:
    try:
        row, temporary, destination = _journal(workspace, task_id)
    except (DomainError, OSError, ValueError):
        return False
    if destination.exists():
        return False
    if temporary.exists():
        marker = temporary / COPY_MARKER
        try:
            if marker.stat().st_size > 1024 or json.loads(marker.read_text(encoding="ascii")) != _marker(task_id, row["project_id"]):
                return False
            plan = json.loads(row["plan_json"])
            known_files = {COPY_MARKER, PROJECT_FILENAME, f"{PROJECT_FILENAME}-journal", *plan}
            files, directories = [], []
            for path in temporary.rglob("*"):
                _no_links(path)
                relative = path.relative_to(temporary).as_posix()
                if path.is_file() and relative in known_files:
                    files.append(path)
                elif path.is_dir() and relative in OWNED_DIRECTORIES:
                    directories.append(path)
                else:
                    return False
            for path in files:
                if path != marker:
                    path.unlink()
            for path in directories:
                path.rmdir()
            marker.unlink()
            temporary.rmdir()
        except (OSError, ValueError, DomainError):
            return False
    with workspace._connect(workspace.projects._session.path) as connection:
        connection.execute("DELETE FROM pending_copies WHERE task_id = ?", (task_id,))
    return True


def recover_copies(workspace) -> None:
    with workspace._connect(workspace.projects._session.path) as connection:
        rows = connection.execute("SELECT task_id, manifest_json FROM pending_copies").fetchall()
    for row in rows:
        task_id = row["task_id"]
        task = workspace.task(task_id)
        try:
            if task["status"] not in {"cancelled", "failed"} and row["manifest_json"] is None:
                from .tasks import _bounded_json, MAX_RESULT_BYTES
                result_path = workspace._project_root() / "staging" / "tasks" / task_id / "result.json"
                if result_path.is_file():
                    result = _bounded_json(result_path, MAX_RESULT_BYTES)
                    if isinstance(result, dict) and result.get("ok") is True:
                        prepare_copy_publication(workspace, task_id, result.get("result"))
                        complete_copy_publication(workspace, task_id, recovering=True)
                        continue
            if task["status"] in {"cancelled", "failed"} or row["manifest_json"] is None:
                cleaned = abandon_copy(workspace, task_id)
                if task["status"] == "running":
                    workspace.update_task(task_id, status="interrupted", stage="interrupted", error="Project copy was interrupted before validation")
                if not cleaned:
                    workspace.update_task(task_id, stage="publication_pending", error="Pending copy retained because ownership or contents need inspection")
            else:
                complete_copy_publication(workspace, task_id, recovering=True)
        except (DomainError, OSError, ValueError) as exc:
            workspace.update_task(task_id, status="interrupted", stage="publication_pending", error=str(exc))


def source_status(workspace, params: dict) -> dict:
    require_exact_keys(params, {"path", "datasetId"})
    dataset = workspace.dataset(params["path"], params["datasetId"])
    original = dataset["source"]["path"]
    result = {"datasetId": dataset["id"], "originalPath": original, "resolvedPath": original,
              "availability": "unavailable", "relocated": False, "verifiedAt": None}
    if dataset["source"]["driver"] == "TablePoints":
        metadata = dataset["source"]["metadata"]
        try:
            parent = workspace.dataset(params["path"], metadata.get("parentDatasetId"))
            if parent["kind"] == "table" and parent["version"] == metadata.get("parentVersion"):
                result["resolvedPath"] = str(workspace._project_root() / parent["relativePath"])
                result["availability"] = "internal" if workspace.managed_path(parent).is_file() else "unavailable"
        except (DomainError, InvalidParamsError, ValueError, TypeError):
            pass
        return result
    with workspace._connect(workspace.projects._session.path) as connection:
        row = connection.execute("SELECT source_path, verified_at, fingerprint FROM source_locations WHERE dataset_id = ? ORDER BY location_id DESC LIMIT 1",
                                 (dataset["id"],)).fetchone()
    if row is not None:
        from .projects import _parse_project_timestamp
        try:
            if (not isinstance(row[0], str) or not row[0] or len(row[0]) > MAX_PATH_LENGTH
                    or not isinstance(row[1], str) or len(row[1]) > 64 or row[2] != dataset["source"]["fingerprint"]):
                raise ValueError("source history fields are invalid")
            _parse_project_timestamp(row[1], "verifiedAt")
        except (ValueError, TypeError) as exc:
            raise DomainError("Source location history is corrupt", kind="invalid_project") from exc
        result.update(resolvedPath=row[0], relocated=True, verifiedAt=row[1])
    try:
        candidate = _path(result["resolvedPath"], "sourcePath")
        exists = candidate.is_dir() if dataset["source"]["driver"] == "OpenFileGDB" else candidate.is_file()
        result["availability"] = "present" if exists else "missing"
    except (DomainError, InvalidParamsError, OSError, ValueError):
        pass
    return result


def relocation_candidate(dataset: dict, value: Any) -> Path:
    if dataset["source"]["driver"] == "TablePoints":
        raise DomainError("Derived point sources are internal project datasets", kind="internal_source")
    path = _path(value, "sourcePath")
    driver = dataset["source"]["driver"]
    suffixes = {"CSV": {".csv"}, "XLSX": {".xlsx"}, "GTiff": {".tif", ".tiff"},
                "GPKG": {".gpkg"}, "GeoJSON": {".geojson", ".json"}, "ESRI Shapefile": {".shp"}, "OpenFileGDB": {".gdb"}}
    if driver not in suffixes or path.suffix.lower() not in suffixes[driver]:
        raise DomainError("Relocation candidate format differs from the original source", kind="unsupported_source")
    if driver == "OpenFileGDB":
        exists = path.is_dir()
    else:
        exists = path.is_file()
    if not exists:
        raise DomainError("Relocation source does not exist", kind="source_not_found")
    return path


def _candidate_fingerprint(dataset: dict, candidate: Path, *, cancelled, progress) -> str:
    driver = dataset["source"]["driver"]
    if driver == "GTiff":
        from .rasters import _sidecars
        _sidecars(candidate)
        progress("fingerprinting", None, None)
        return _hash(candidate, cancelled=cancelled)[1]
    original = Path(dataset["source"]["path"])
    if driver == "OpenFileGDB":
        files = []
        for item in candidate.rglob("*"):
            _cancel(cancelled)
            _no_links(item)
            if item.is_file():
                files.append((item.relative_to(candidate).as_posix(), item))
            if len(files) > MAX_SOURCE_FILES:
                raise DomainError("Source bundle exceeds the file budget", kind="source_limit")
        files.sort(key=lambda entry: entry[0])
    elif driver == "ESRI Shapefile":
        from .vectors import _source_path
        _source_path(str(candidate))
        files = [(original.stem + item.suffix, item) for item in candidate.parent.iterdir()
                 if item.is_file() and item.stem.casefold() == candidate.stem.casefold()]
        files.sort(key=lambda entry: entry[0].casefold())
    else:
        files = [(original.name, candidate)]
    if not files or len(files) > MAX_SOURCE_FILES:
        raise DomainError("Source bundle file count is invalid", kind="source_limit")
    total = 0
    for _, item in files:
        _no_links(item)
        total += item.stat().st_size
    if total > MAX_COPY_BYTES:
        raise DomainError("Source bundle exceeds the 32 GiB verification budget", kind="source_limit")
    completed = 0
    digest = hashlib.sha256()
    exact_digest = hashlib.sha256()
    for name, item in files:
        _cancel(cancelled)
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        exact_name = name if driver == "OpenFileGDB" else item.name
        exact_digest.update(exact_name.encode("utf-8"))
        exact_digest.update(b"\0")
        with item.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                _cancel(cancelled)
                completed += len(chunk)
                if completed > total:
                    raise DomainError("Source changed while verifying", kind="source_changed")
                digest.update(chunk)
                exact_digest.update(chunk)
                progress("fingerprinting", completed, total)
        digest.update(b"\0")
        exact_digest.update(b"\0")
    # Existing bundles can legitimately use different casing for each component.
    exact = exact_digest.hexdigest()
    return exact if exact == dataset["source"]["fingerprint"] else digest.hexdigest()


def relocate_source(payload: dict, work_dir: Path, *, progress, cancelled) -> dict:
    require_exact_keys(payload, {"dataset", "sourcePath"})
    dataset = payload["dataset"]
    candidate = relocation_candidate(dataset, payload["sourcePath"])
    digest = _candidate_fingerprint(dataset, candidate, cancelled=cancelled, progress=progress)
    if digest != dataset["source"]["fingerprint"]:
        raise DomainError("Source content differs from the imported identity; import it as a new dataset", kind="source_mismatch")
    _cancel(cancelled)
    return {"datasetId": dataset["id"], "sourcePath": str(candidate), "fingerprint": digest, "verifiedAt": _utc_now()}


def complete_relocation(workspace, task_id: str, result: dict) -> dict:
    require_exact_keys(result, {"datasetId", "sourcePath", "fingerprint", "verifiedAt"})
    task = workspace.task(task_id)
    dataset = workspace.dataset(str(workspace.projects._session.path), task["datasetId"])
    from .projects import _parse_project_timestamp
    _parse_project_timestamp(result["verifiedAt"], "verifiedAt")
    if (task["kind"] != "relocate" or task["status"] != "running" or result["datasetId"] != dataset["id"]
            or result["sourcePath"] != task["destination"] or result["fingerprint"] != dataset["source"]["fingerprint"]
            or dataset["source"]["driver"] == "TablePoints"):
        raise DomainError("Source relocation result is invalid", kind="invalid_worker_result")
    with workspace._connect(workspace.projects._session.path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO source_locations (dataset_id, source_path, fingerprint, verified_at, task_id) VALUES (?, ?, ?, ?, ?)",
                           (dataset["id"], result["sourcePath"], result["fingerprint"], result["verifiedAt"], task_id))
        connection.execute("UPDATE tasks SET status = 'completed', stage = 'completed', completed = total, updated_at = ?, error = NULL WHERE task_id = ?",
                           (_utc_now(), task_id))
    return workspace.task(task_id)
