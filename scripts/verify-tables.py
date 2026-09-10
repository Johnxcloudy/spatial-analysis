"""Verify table/point workflows through the real persistent engine and workers."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import sqlite3
from datetime import date
from pathlib import Path

from openpyxl import Workbook

Rpc = importlib.import_module("verify-vectors").Rpc


def fixtures(directory: Path) -> list[dict]:
    directory.mkdir()
    rows = [
        ["001", "114.0", "27.1", "Chinese \u571f\u5730"],
        ["002", "bad", "27.2", "NULL"],
        ["003", "", "27.3", ""],
        ["004", "114.2", "27.3", "9007199254740993"],
        ["005", "NaN", "27.4", "nonfinite"],
        ["006", "114.3", "999", "range"],
    ]
    result = []
    for encoding, delimiter in [("utf-8-sig", ","), ("gb18030", ";")]:
        source = directory / f"coordinates-{encoding}.csv"
        with source.open("x", encoding=encoding, newline="") as handle:
            writer = csv.writer(handle, delimiter=delimiter)
            writer.writerow(["code", "x", "y", "label"])
            writer.writerows(rows)
        result.append({"sourcePath": str(source), "encoding": encoding, "delimiter": delimiter,
                       "sheet": None, "headerRow": 1})
    workbook = Workbook()
    workbook.active.title = "Overview"
    workbook.active.append(["Synthetic acceptance only"])
    sheet = workbook.create_sheet("Coordinates")
    sheet.append(["Synthetic coordinates"])
    sheet.append(["code", "x", "y", "label"])
    for row in rows:
        sheet.append(row)
    sheet.append(["007", "=114+0.3", 27.4, "formula"])
    sheet.append(["008", True, 27.5, "Boolean"])
    sheet.append(["009", date(2026, 1, 1), 27.6, "date"])
    sheet.append(["010", "#DIV/0!", 27.7, "error"])
    sheet["D6"] = 123
    sheet["D6"].number_format = "00000"
    source = directory / "coordinates.xlsx"
    workbook.save(source)
    workbook.close()
    result.append({"sourcePath": str(source), "encoding": None, "delimiter": None,
                   "sheet": "Coordinates", "headerRow": 2})
    return result


def page_query(path: str, dataset_id: str) -> dict:
    return {"path": path, "datasetId": dataset_id, "offset": 0, "limit": 200,
            "sortField": "code", "descending": False, "filter": None}


def verify(rpc: Rpc, output: Path, checks: list[str], export_directory: Path) -> dict:
    runtime = rpc.call("runtime.info")
    assert runtime["protocolVersion"] == 5 and runtime["engineVersion"] == "0.5.0", runtime
    assert "openpyxl" in runtime["versions"]
    project = rpc.call("project.create", {"directory": str(output / "project"), "name": "Table acceptance"})
    assert project["schemaVersion"] == 5
    path = project["projectPath"]
    inputs = fixtures(output / "sources")
    xlsx_options = inputs[-1]
    sheets = rpc.call("table.inspect", {**xlsx_options, "sheet": None})
    assert sheets["sheets"] == ["Overview", "Coordinates"], sheets
    checks.append("XLSX sheet enumeration uses the real workbook parser.")
    saved_datasets = []
    for index, options in enumerate(inputs):
        source = Path(options["sourcePath"])
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        preview = rpc.call("table.inspect", options)
        assert [column["fieldName"] for column in preview["columns"]] == ["code", "x", "y", "label"], preview
        task = rpc.wait_task(path, rpc.call("table.import", {"path": path, **options}))
        workspace = rpc.call("workspace.get", {"path": path})
        dataset = next(item for item in workspace["datasets"] if item["id"] == task["datasetId"])
        count = 10 if source.suffix == ".xlsx" else 6
        assert dataset["kind"] == "table" and dataset["featureCount"] == count
        assert dataset["crsWkt"] is None and dataset["bounds"] is None
        assert all(layer["datasetId"] != dataset["id"] for layer in workspace["layers"])
        query = page_query(path, dataset["id"])
        page = rpc.call("table.page", query)
        assert page["total"] == count and page["rows"][0]["values"]["code"] == "001"
        assert [row["sourceRow"] for row in page["rows"]] == [str(options["headerRow"] + index + 1) for index in range(count)]
        assert page["rows"][0]["values"]["label"] == "Chinese \u571f\u5730"
        assert page["rows"][1]["values"]["label"] == "NULL"
        if source.suffix == ".csv":
            assert page["rows"][2]["values"]["label"] == ""
            assert page["rows"][3]["values"]["label"] == "9007199254740993"
        else:
            assert page["rows"][6]["values"]["x"] == "=114+0.3"
        filtered = rpc.call("table.page", {**query, "filter": {"field": "code", "operator": "equals", "value": "002"}})
        assert filtered["total"] == 1
        checks.append(f"{source.name}: table import preserves text, source rows and bounded attribute querying without a map layer.")
        export = export_directory / f"table-{index}.gpkg"
        rpc.wait_task(path, rpc.call("table.export", {"path": path, "datasetId": dataset["id"], "destination": str(export)}))
        assert hashlib.sha256(export.read_bytes()).hexdigest() == dataset["version"]
        connection = sqlite3.connect(export)
        try:
            content = dict(connection.execute("SELECT table_name, data_type FROM gpkg_contents"))
            assert content["records"] == "attributes"
            if source.suffix == ".xlsx":
                assert content[dataset["cellMetadataLayer"]] == "attributes"
                assert connection.execute('SELECT COUNT(*) FROM "cell_metadata"').fetchone()[0] > 0
        finally:
            connection.close()
        checks.append(f"{source.name}: export preserves the exact GPKG hash and registered attributes/cell metadata.")
        point_params = {"path": path, "datasetId": dataset["id"], "xField": "x", "yField": "y", "declaredCrs": "EPSG:4326"}
        point_task = rpc.wait_task(path, rpc.call("table.points", point_params))
        point_page = rpc.call("vector.page", page_query(path, point_task["datasetId"]))
        assert point_page["total"] == count
        assert [row["values"]["code"] for row in point_page["rows"]] == [row["values"]["code"] for row in page["rows"]]
        assert [row["sourceRow"] for row in point_page["rows"]] == [row["sourceRow"] for row in page["rows"]]
        viewport = rpc.call("vector.viewport", {"path": path, "datasetId": point_task["datasetId"],
                                                "bbox": [113, 26, 115, 28], "limit": 2000, "propertyFields": ["code"]})
        assert viewport["returnedCount"] == 2 and not viewport["truncated"], viewport
        for row in point_page["rows"]:
            result = rpc.call("vector.feature", {"path": path, "datasetId": point_task["datasetId"], "featureId": row["id"]})
            valid = row["values"]["code"] in {"001", "004"}
            geometry = result["feature"] and result["feature"]["geometry"]
            assert bool(geometry) == valid, result
        assert rpc.call("table.page", query) == page
        checks.append(f"{source.name}: explicit CRS point generation retains every row; invalid/formula/type/range rows have no geometry.")
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        source.rename(source.with_name("moved-" + source.name))
        assert rpc.call("table.page", query) == page
        saved_datasets.append(dataset)
    cancelled = rpc.call("table.points", point_params)
    cancelled = rpc.call("task.cancel", {"path": path, "taskId": cancelled["id"]})
    assert cancelled["status"] == "cancelled", cancelled
    state = rpc.call("workspace.get", {"path": path})
    assert len(state["datasets"]) == 6 and len(state["layers"]) == 3
    checks.append("Cancelling point derivation does not publish a partial dataset.")
    rpc.call("project.close")
    rpc.call("project.open", {"path": path})
    restored = rpc.call("workspace.get", {"path": path})
    assert restored["datasets"] == state["datasets"] and restored["layers"] == state["layers"]
    for dataset in saved_datasets:
        assert rpc.call("table.page", page_query(path, dataset["id"]))["total"] == dataset["featureCount"]
    # The source has moved: derivation must read the managed table snapshot.
    rpc.wait_task(path, rpc.call("table.points", point_params))
    rpc.call("project.close")
    checks.append("Project reopen and point derivation remain available after original sources move.")
    return {"runtime": runtime, "projectPath": path, "sources": inputs, "exportDirectory": str(export_directory)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--executable")
    parser.add_argument("--export-directory")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    export_directory = Path(args.export_directory).resolve() if args.export_directory else output / "exports"
    export_directory.mkdir(parents=True, exist_ok=False)
    report = {"ok": False, "checks": []}
    rpc = Rpc(Path(__file__).resolve().parents[1], output, args.executable)
    try:
        report.update(verify(rpc, output, report["checks"], export_directory))
        if args.executable:
            assert report["runtime"]["packaged"] is True
        report["ok"] = True
    except Exception as error:
        report["error"] = repr(error)
        raise
    finally:
        rpc.close()
        (output / "table-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"ok": report["ok"], "checks": len(report["checks"]), "output": str(output)}))


if __name__ == "__main__":
    main()
