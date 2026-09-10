"""Exercise the real stdio service, including its child workers, without UI mocks."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path


def seed_source(path: Path) -> None:
    features = []
    for index, code in enumerate(["0101", "0201", "0301"]):
        x, y = 114.0 + index * 0.02, 27.1
        ring = [[x, y], [x + 0.015, y], [x + 0.015, y + 0.015], [x, y + 0.015], [x, y]]
        features.append({
            "type": "Feature",
            "properties": {"code": code, "name": "\u5408\u6210\u7528\u5730" + str(index),
                           "business_id": 9007199254740993 + index if index != 1 else None},
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        })
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False), encoding="utf-8")


class Rpc:
    def __init__(self, root: Path, output: Path, executable: str | None):
        env = os.environ.copy()
        for name in ["PYTHONHOME", "PYTHONPATH", "GDAL_DATA", "PROJ_LIB", "PROJ_DATA"]:
            env.pop(name, None)
        env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PROJ_NETWORK="OFF")
        if executable:
            command = [str(Path(executable).resolve())]
            if os.name == "nt":
                system = Path(os.environ["SystemRoot"])
                env["PATH"] = os.pathsep.join([str(system / "System32"), str(system), str(Path(executable).resolve().parent)])
        else:
            command = [sys.executable, "-u", "-m", "spatial_engine"]
            env["PYTHONPATH"] = str(root / "gis-engine" / "src")
        self.log = (output / "engine-stderr.log").open("wb")
        self.process = subprocess.Popen(command, cwd=output, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.log,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.responses: queue.Queue[bytes] = queue.Queue()
        self.next_id = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        assert self.process.stdout
        while line := self.process.stdout.readline(8 * 1024 * 1024 + 1):
            self.responses.put(line)
        self.responses.put(b"")

    def call(self, method: str, params: dict | None = None):
        deadline = time.monotonic() + 30
        while True:
            response = self._request(method, params)
            if response.get("error", {}).get("data", {}).get("kind") != "query_unready":
                break
            if time.monotonic() >= deadline:
                raise AssertionError(f"Query warmup deadline: {response}")
            time.sleep(0.1)
        if "error" in response:
            raise AssertionError(f"{method}: {response['error']}")
        return response["result"]

    def _request(self, method: str, params: dict | None = None):
        self.next_id += 1
        assert self.process.stdin
        request = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}
        self.process.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        self.process.stdin.flush()
        line = self.responses.get(timeout=90)
        if not line or len(line) > 8 * 1024 * 1024:
            raise AssertionError(f"Unusable RPC frame for {method}")
        response = json.loads(line)
        assert response["jsonrpc"] == "2.0" and response["id"] == self.next_id, response
        return response

    def wait_task(self, path: str, task: dict):
        deadline = time.monotonic() + 930
        while task["status"] == "running":
            if time.monotonic() > deadline:
                raise AssertionError(f"Task deadline: {task}")
            time.sleep(0.1)
            task = self.call("task.get", {"path": path, "taskId": task["id"]})
        assert task["status"] == "completed", task
        return task

    def close(self):
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)
        self.reader.join(timeout=5)
        if self.process.stdout:
            self.process.stdout.close()
        self.log.close()


def verify(rpc: Rpc, output: Path, checks: list[str], export_directory: Path | None = None) -> dict:
    runtime = rpc.call("runtime.info")
    assert runtime["protocolVersion"] == 6 and runtime["engineVersion"] == "0.6.0", runtime
    assert "pyarrow" in runtime["versions"], runtime
    project = rpc.call("project.create", {"directory": str(output / "\u7528\u5730\u9879\u76ee"), "name": "\u7528\u5730\u9a8c\u6536"})
    path = project["projectPath"]
    assert project["schemaVersion"] == 6
    source = output / "\u5408\u6210\u7528\u5730.geojson"
    seed_source(source)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    inspection = rpc.call("source.inspect", {"sourcePath": str(source), "encoding": None})
    assert len(inspection["layers"]) == 1, inspection
    layer_name = inspection["layers"][0]["name"]
    import_params = {"path": path, "sourcePath": str(source), "sourceLayer": layer_name,
                     "encoding": None, "assignedCrs": None}
    task = rpc.call("vector.import", import_params)
    assert task["status"] == "running", task
    assert rpc.call("runtime.info")["protocolVersion"] == 6
    task = rpc.wait_task(path, task)
    workspace = rpc.call("workspace.get", {"path": path})
    assert workspace["projectId"] == project["id"] and len(workspace["datasets"]) == 1, workspace
    dataset = workspace["datasets"][0]
    assert dataset["id"] == task["datasetId"] and dataset["featureCount"] == 3
    assert dataset["source"]["fingerprint"] and not Path(dataset["relativePath"]).is_absolute()
    checks.append("Real asynchronous import worker completed and registered its verified snapshot.")
    query = {"path": path, "datasetId": dataset["id"], "offset": 0, "limit": 2,
             "sortField": "code", "descending": False, "filter": None}
    first = rpc.call("vector.page", query)
    second = rpc.call("vector.page", {**query, "offset": 2})
    rows = first["rows"] + second["rows"]
    assert first["total"] == 3 and first["hasMore"] and not second["hasMore"]
    assert len({row["id"] for row in rows}) == 3
    assert [row["values"]["code"] for row in rows] == ["0101", "0201", "0301"]
    assert [row["values"]["business_id"] for row in rows] == ["9007199254740993", None, "9007199254740995"], rows
    filtered = rpc.call("vector.page", {**query, "filter": {"field": "code", "operator": "equals", "value": "0101"}})
    assert filtered["total"] == 1 and filtered["rows"][0]["id"] == rows[0]["id"]
    checks.append("Deterministic pages/filter preserve leading zeros, NULL and integers above JavaScript's safe range.")
    viewport = rpc.call("vector.viewport", {"path": path, "datasetId": dataset["id"],
                                            "bbox": [113.9, 27.0, 114.2, 27.3], "limit": 2000, "propertyFields": ["code"]})
    assert viewport["returnedCount"] == 3 and not viewport["truncated"], viewport
    feature = rpc.call("vector.feature", {"path": path, "datasetId": dataset["id"], "featureId": rows[0]["id"]})
    assert feature["row"] == rows[0] and feature["feature"]["id"] == rows[0]["id"], feature
    assert {item["id"] for item in viewport["collection"]["features"]} == {row["id"] for row in rows}
    checks.append("Viewport, feature identification and attribute table use the same stable IDs.")
    layer = workspace["layers"][0]
    changed = rpc.call("layer.update", {"path": path, "layerId": layer["id"], "changes": {
        "name": "\u7528\u5730\u5feb\u7167", "opacity": 0.65, "color": "#008577", "categoryField": "code",
        "categoryColors": {"0101": "#008577", "0201": "#cc6470"}}})
    export_path = (export_directory or output) / "\u5bfc\u51fa\u7528\u5730.gpkg"
    exported = rpc.wait_task(path, rpc.call("vector.export", {"path": path, "datasetId": dataset["id"], "destination": str(export_path)}))
    assert exported["destination"] == str(export_path) and export_path.is_file()
    export_info = rpc.call("source.inspect", {"sourcePath": str(export_path), "encoding": None})
    assert export_info["layers"][0]["featureCount"] == 3, export_info
    checks.append("Verified GPKG export is readable through the public source inspection service.")
    cancel_task = rpc.call("vector.import", import_params)
    cancelled = rpc.call("task.cancel", {"path": path, "taskId": cancel_task["id"]})
    assert cancelled["status"] == "cancelled", cancelled
    assert len(rpc.call("workspace.get", {"path": path})["datasets"]) == 1
    checks.append("Cancelling an active import reaps its child and does not register a partial dataset.")
    rpc.call("project.save", {"path": path, "name": project["name"], "description": "Phase 1A verified",
                              "analysisCrs": "EPSG:4547", "displayCrs": "EPSG:3857",
                              "viewState": {"center": [114.02, 27.11], "zoom": 13}})
    rpc.call("project.close")
    moved = source.with_name("moved-source.geojson")
    source.rename(moved)
    assert hashlib.sha256(moved.read_bytes()).hexdigest() == original_hash
    reopened = rpc.call("project.open", {"path": path})
    restored = rpc.call("workspace.get", {"path": path})
    assert reopened["viewState"] == {"center": [114.02, 27.11], "zoom": 13}
    assert restored["layers"] == [changed]
    assert restored["datasets"][0]["id"] == dataset["id"]
    assert rpc.call("vector.page", query)["rows"] == first["rows"]
    checks.append("Project reopen restores view/style/IDs and works after the untouched original source is moved.")
    rpc.call("project.close")
    return {"runtime": runtime, "projectPath": path, "datasetId": dataset["id"], "exportPath": str(export_path)}


def verify_formats(rpc: Rpc, output: Path, manifest: Path, checks: list[str]):
    fixtures = json.loads(manifest.read_text(encoding="utf-8"))
    project = rpc.call("project.create", {"directory": str(output / "formats"), "name": "Format acceptance"})
    path = project["projectPath"]
    for kind in ("gpkg", "shp", "geojson", "gdb"):
        source_path = fixtures[kind]
        encoding = "GBK" if kind == "shp" else None
        inspection = rpc.call("source.inspect", {"sourcePath": source_path, "encoding": encoding})
        if kind in ("gpkg", "gdb"):
            assert {layer["name"] for layer in inspection["layers"]} == {"land", "controls"}, inspection
        task = rpc.wait_task(path, rpc.call("vector.import", {
            "path": path, "sourcePath": source_path, "sourceLayer": "land", "encoding": encoding, "assignedCrs": None,
        }))
        workspace = rpc.call("workspace.get", {"path": path})
        dataset = next(item for item in workspace["datasets"] if item["id"] == task["datasetId"])
        query = {"path": path, "datasetId": dataset["id"], "offset": 0, "limit": 200,
                 "sortField": "code", "descending": False, "filter": None}
        page = rpc.call("vector.page", query)
        assert page["total"] == 4 and [row["values"]["code"] for row in page["rows"]] == ["001", "002", "003", "004"], page
        assert page["rows"][0]["values"]["label"] == "Chinese \u571f\u5730"
        if kind != "shp":
            assert [row["values"]["large_id"] for row in page["rows"]] == ["9007199254740993", None, "9007199254740995", 4], page
        viewport = rpc.call("vector.viewport", {"path": path, "datasetId": dataset["id"], "bbox": dataset["boundsWgs84"],
                                                "limit": 2000, "propertyFields": ["code"]})
        assert viewport["returnedCount"] == 4 and not viewport["truncated"], viewport
        rpc.wait_task(path, rpc.call("vector.export", {"path": path, "datasetId": dataset["id"],
                                                       "destination": str(output / f"export-{kind}.gpkg")}))
        checks.append(f"{kind}: real driver inspection/import, nullable fields, transformed viewport and verified export passed.")
    rpc.call("project.close")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable")
    parser.add_argument("--output", required=True)
    parser.add_argument("--fixtures", help="Manifest from generate-vector-fixtures.py")
    parser.add_argument("--export-directory", help="New destination directory, optionally on another volume")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    export_directory = Path(args.export_directory).resolve() if args.export_directory else None
    if export_directory:
        export_directory.mkdir(parents=True, exist_ok=False)
    report: dict = {"ok": False, "checks": []}
    rpc = Rpc(Path(__file__).resolve().parents[1], output, args.executable)
    try:
        report.update(verify(rpc, output, report["checks"], export_directory))
        if args.fixtures:
            verify_formats(rpc, output, Path(args.fixtures), report["checks"])
        if args.executable:
            assert report["runtime"]["packaged"] is True
        report["ok"] = True
    except Exception as error:
        report["error"] = repr(error)
        raise
    finally:
        rpc.close()
        (output / "vector-verification.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"ok": report["ok"], "report": str(output / "vector-verification.json"), "checks": report["checks"]}))


if __name__ == "__main__":
    main()
