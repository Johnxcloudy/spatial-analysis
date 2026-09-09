"""Verify an actual schema-2 project on a new copy, preserving its source tree."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

Rpc = importlib.import_module("verify-vectors").Rpc


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def records(path: Path, registry: str) -> dict:
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        return {
            "schema": connection.execute("PRAGMA user_version").fetchone()[0],
            "datasets": connection.execute(f"SELECT dataset_json FROM {registry} ORDER BY dataset_id").fetchall(),
            "layers": connection.execute("SELECT * FROM map_layers ORDER BY layer_id").fetchall(),
            "metadata": connection.execute("SELECT * FROM project_metadata").fetchall(),
            "tasks": connection.execute("SELECT * FROM tasks ORDER BY task_id").fetchall(),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-project", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--executable")
    args = parser.parse_args()
    source = Path(args.source_project).resolve()
    before = records(source, "vector_datasets")
    assert before["schema"] == 2 and before["datasets"], "Supply an existing schema-2 project with vector data."
    datasets = [json.loads(row[0]) for row in before["datasets"]]
    originals = {str(item): sha256(item) for item in [source, *[source.parent / item["relativePath"] for item in datasets]]}
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    copied = output / "project"
    shutil.copytree(source.parent, copied)
    destination = copied / source.name
    previous_backups = set((copied / "backups").glob("project-v2-*.spa"))
    rpc = Rpc(Path(__file__).resolve().parents[1], output, args.executable)
    report = {"ok": False, "sourceProject": str(source), "copiedProject": str(destination)}
    try:
        opened = rpc.call("project.open", {"path": str(destination)})
        assert opened["schemaVersion"] == 3
        after = records(destination, "datasets")
        assert after["schema"] == 3
        assert all(after[key] == before[key] for key in ("datasets", "layers", "metadata", "tasks"))
        backups = set((copied / "backups").glob("project-v2-*.spa")) - previous_backups
        assert len(backups) == 1
        assert records(next(iter(backups)), "vector_datasets") == before
        workspace = rpc.call("workspace.get", {"path": str(destination)})
        assert {item["id"] for item in workspace["datasets"]} == {item["id"] for item in datasets}
        for dataset in datasets:
            page = rpc.call("vector.page", {"path": str(destination), "datasetId": dataset["id"], "offset": 0,
                                           "limit": 200, "sortField": None, "descending": False, "filter": None})
            assert page["total"] == dataset["featureCount"]
            assert sha256(copied / dataset["relativePath"]) == dataset["version"]
        rpc.call("project.close")
        rpc.call("project.open", {"path": str(destination)})
        assert len(set((copied / "backups").glob("project-v2-*.spa")) - previous_backups) == 1
        rpc.call("project.close")
        assert all(sha256(Path(name)) == digest for name, digest in originals.items())
        report.update(ok=True, datasetCount=len(datasets), backup=str(next(iter(backups))),
                      preserved=["source files", "dataset JSON and bytes", "layers", "project metadata", "task history"])
    except Exception as error:
        report["error"] = repr(error)
        raise
    finally:
        rpc.close()
        (output / "migration-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))


if __name__ == "__main__":
    main()
