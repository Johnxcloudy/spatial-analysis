"""Verify an actual schema-2/3/4 project on a new copy, preserving its source tree."""
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


def records(path: Path) -> dict:
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        schema = connection.execute("PRAGMA user_version").fetchone()[0]
        registry = "vector_datasets" if schema == 2 else "datasets"
        result = {
            "schema": schema,
            "datasets": connection.execute(f"SELECT dataset_json FROM {registry} ORDER BY dataset_id").fetchall(),
            "layers": connection.execute("SELECT layer_id,dataset_id,name,visible,opacity,color,category_field,category_colors,display_order FROM map_layers ORDER BY layer_id").fetchall(),
            "metadata": connection.execute("SELECT * FROM project_metadata").fetchall(),
            "tasks": connection.execute("SELECT * FROM tasks ORDER BY task_id").fetchall(),
            "publications": connection.execute("SELECT * FROM pending_publications ORDER BY task_id").fetchall(),
            "exports": connection.execute("SELECT * FROM pending_exports ORDER BY task_id").fetchall(),
        }
        result["rasterStyles"] = connection.execute("SELECT layer_id,raster_style FROM map_layers ORDER BY layer_id").fetchall() if schema >= 4 else None
        return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-project", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--executable")
    args = parser.parse_args()
    source = Path(args.source_project).resolve()
    before = records(source)
    assert before["schema"] in {2, 3, 4} and before["datasets"], "Supply an existing schema-2/3/4 project with data."
    backup_pattern = f"project-v{before['schema']}-*.spa"
    datasets = [json.loads(row[0]) for row in before["datasets"]]
    originals = {str(item): sha256(item) for item in [source, *[source.parent / item["relativePath"] for item in datasets]]}
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    copied = output / "project"
    shutil.copytree(source.parent, copied)
    destination = copied / source.name
    previous_backups = set((copied / "backups").glob(backup_pattern))
    rpc = Rpc(Path(__file__).resolve().parents[1], output, args.executable)
    report = {"ok": False, "sourceProject": str(source), "copiedProject": str(destination)}
    try:
        opened = rpc.call("project.open", {"path": str(destination)})
        assert opened["schemaVersion"] == 5
        after = records(destination)
        assert after["schema"] == 5
        assert all(after[key] == before[key] for key in ("datasets", "layers", "metadata", "tasks", "publications", "exports"))
        with closing(sqlite3.connect(destination)) as connection:
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()
            if before["schema"] >= 4:
                assert after["rasterStyles"] == before["rasterStyles"]
            else:
                assert connection.execute("SELECT COUNT(*) FROM map_layers WHERE raster_style IS NOT NULL").fetchone()[0] == 0
        backups = set((copied / "backups").glob(backup_pattern)) - previous_backups
        assert len(backups) == 1
        assert records(next(iter(backups))) == before
        workspace = rpc.call("workspace.get", {"path": str(destination)})
        assert {item["id"] for item in workspace["datasets"]} == {item["id"] for item in datasets}
        for dataset in datasets:
            if dataset["kind"] == "raster":
                raster = rpc.call("raster.inspect", {"sourcePath": str(copied / dataset["relativePath"])})
                assert raster["raster"] == dataset["raster"]
            else:
                page = rpc.call(f"{dataset['kind']}.page", {"path": str(destination), "datasetId": dataset["id"], "offset": 0,
                                               "limit": 200, "sortField": None, "descending": False, "filter": None})
                assert page["total"] == dataset["featureCount"]
            assert sha256(copied / dataset["relativePath"]) == dataset["version"]
        rpc.call("project.close")
        rpc.call("project.open", {"path": str(destination)})
        assert len(set((copied / "backups").glob(backup_pattern)) - previous_backups) == 1
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
