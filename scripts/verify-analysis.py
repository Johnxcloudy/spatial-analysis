"""Real source/frozen RPC analysis acceptance with analytic pressure fixtures.

All outputs are new synthetic artifacts. An interrupted/failed level stays failed in
report.json; protected rejection is never counted as successful capacity acceptance.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
import os
import platform
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import psutil
import pyarrow as pa
import pyogrio
import shapely

Rpc = importlib.import_module("verify-vectors").Rpc
CODES = ["001", None, "", " ", "NULL"]
SCHEMA = pa.schema([("code", pa.string()), ("geometry", pa.binary())])


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def summary(values):
    return {"count": len(values), "p50Ms": float(np.percentile(values, 50)) if values else None,
            "p95Ms": float(np.percentile(values, 95)) if values else None, "maxMs": max(values) if values else None}


class MeasuredRpc(Rpc):
    def __init__(self, root, output, executable):
        self.timings = {}
        self.scope = "startup"
        self.events = (output / "rpc-timings.jsonl").open("x", encoding="utf-8")
        super().__init__(root, output, executable)

    def call(self, method, params=None):
        started = time.perf_counter()
        ok = False
        try:
            result = super().call(method, params)
            ok = True
            return result
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            self.timings.setdefault(method, []).append(elapsed)
            self.events.write(json.dumps({"scope": self.scope, "method": method, "durationMs": elapsed, "ok": ok}) + "\n")
            self.events.flush()

    def wait_terminal(self, path, task):
        deadline = time.monotonic() + 930
        while task["status"] == "running":
            if time.monotonic() > deadline:
                raise AssertionError(f"930 second task deadline: {task}")
            time.sleep(.1)
            task = self.call("task.get", {"path": path, "taskId": task["id"]})
        return task

    def wait_task(self, path, task):
        task = self.wait_terminal(path, task)
        assert task["status"] == "completed", task
        return task

    def close(self):
        try:
            super().close()
        finally:
            self.events.close()


class MemorySampler:
    def __init__(self, rpc, output):
        self.rpc = rpc
        self.stop_event = threading.Event()
        self.peak = 0
        self.peaks = {}
        self.scopes = {}
        self.samples = 0
        self.errors = []
        self.stream = (output / "memory-samples.jsonl").open("x", encoding="utf-8")
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.thread.start()

    def _sample(self):
        while not self.stop_event.is_set():
            try:
                parent = psutil.Process(self.rpc.process.pid)
                processes = [parent, *parent.children(recursive=True)]
                entries = []
                for process in processes:
                    try:
                        memory = process.memory_info().rss
                        identity = f"{process.pid}:{process.create_time()}"
                        self.peaks[identity] = max(self.peaks.get(identity, 0), memory)
                        entries.append({"pid": process.pid, "rssBytes": memory, "name": process.name()})
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                total = sum(entry["rssBytes"] for entry in entries)
                self.peak = max(self.peak, total)
                self.scopes[self.rpc.scope] = max(self.scopes.get(self.rpc.scope, 0), total)
                self.samples += 1
                self.stream.write(json.dumps({"time": time.monotonic(), "scope": self.rpc.scope, "treeRssBytes": total, "processes": entries}) + "\n")
                self.stream.flush()
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                if len(self.errors) < 10:
                    self.errors.append(str(exc))
            self.stop_event.wait(.1)

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=5)
        self.stream.close()
        return {"sampleIntervalSeconds": .1, "samples": self.samples, "treePeakRssBytes": self.peak,
                "processPeakRssBytes": self.peaks, "scopePeakRssBytes": self.scopes, "samplingErrors": self.errors,
                "limitation": "Sampled RSS is observed memory, not a proof of the exact instantaneous allocation peak."}


def checkpoint(output, report):
    temporary = output / "report.pending.json"
    temporary.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    temporary.replace(output / "report.json")


@contextmanager
def step(rpc, output, report, level, name):
    rpc.scope = f"{level['name']}:{name}"
    event = {"name": name, "ok": False, "startedAt": datetime.now(UTC).isoformat()}
    level["steps"].append(event)
    started = time.perf_counter()
    try:
        yield event
        event["ok"] = True
    except Exception as exc:
        event["error"] = repr(exc)
        raise
    finally:
        event["durationSeconds"] = time.perf_counter() - started
        checkpoint(output, report)
        print(json.dumps({"scope": rpc.scope, **event}, ensure_ascii=False), flush=True)


def write_rectangles(path, geometries, codes, append=False):
    table = pa.Table.from_arrays([pa.array(codes, type=pa.string()), pa.array(shapely.to_wkb(geometries), type=pa.binary())], schema=SCHEMA)
    pyogrio.write_arrow(table, path, layer="land", driver="GPKG", geometry_name="geometry", geometry_type="Polygon",
                        crs="EPSG:32650", append=append, layer_options={"SPATIAL_INDEX": "YES"} if not append else None)


def fixtures(directory, count):
    directory.mkdir()
    left, right = directory / "parcels.gpkg", directory / "boundary.gpkg"
    columns = min(count, 1000)
    rows = math.ceil(count / columns)
    for start in range(0, count, 4096):
        indices = np.arange(start, min(start + 4096, count))
        x = 500000 + (indices % columns) * 20
        y = 3000000 + (indices // columns) * 20
        write_rectangles(left, shapely.box(x, y, x + 10, y + 10), [CODES[int(i % len(CODES))] for i in indices], append=start != 0)
    write_rectangles(right, [shapely.box(499995, 2999995, 499995 + columns * 20, 2999995 + rows * 20)], ["boundary"])
    expected = {"featureCount": count, "vertices": count * 5, "coveredAreaM2": count * 100,
                "studyAreaM2": columns * rows * 400,
                "classes": [{"code": code, "featureCount": (count - index + 4) // 5}
                            for index, code in enumerate(CODES) if count > index]}
    return left, right, expected


def import_dataset(rpc, path, source):
    task = rpc.wait_task(path, rpc.call("vector.import", {"path": path, "sourcePath": str(source), "sourceLayer": "land",
                                                          "encoding": None, "assignedCrs": None}))
    workspace = rpc.call("workspace.get", {"path": path})
    return next(dataset for dataset in workspace["datasets"] if dataset["id"] == task["datasetId"])


def options(path, left, right, operation="clip"):
    return {"path": path, "operation": operation, "name": f"Synthetic {operation}", "inputDatasetId": left["id"],
            "overlayDatasetId": right["id"], "inputClassField": "code", "overlayClassField": "code" if operation == "intersect" else None,
            "classificationStandard": "Synthetic five-code fixture v1", "analysisCrs": "EPSG:32650",
            "crsReason": "Synthetic 10-metre parcels wholly inside UTM zone 50N around 117E, 27N"}


def statistics(rpc, path, dataset):
    rows, offset, record = [], 0, None
    while True:
        page = rpc.call("analysis.result", {"path": path, "datasetId": dataset["id"], "offset": offset, "limit": 2})
        assert page["datasetId"] == dataset["id"] and page["version"] == dataset["version"]
        if record is not None:
            assert record == page["record"]
        record = page["record"]
        rows.extend(page["rows"])
        offset += len(page["rows"])
        if not page["hasMore"]:
            assert len(rows) == page["total"]
            return record, rows


def assert_analytic(record, rows, expected):
    assert record["outputFeatureCount"] == expected["featureCount"], record
    # Fixed absolute/relative tolerance for exact metre rectangles, set before execution.
    for field, value in (("coveredAreaM2", expected["coveredAreaM2"]), ("recordAreaM2", expected["coveredAreaM2"]),
                         ("studyAreaM2", expected["studyAreaM2"]),
                         ("uncoveredAreaM2", expected["studyAreaM2"] - expected["coveredAreaM2"])):
        assert math.isclose(record[field], value, rel_tol=1e-12, abs_tol=1e-6), (field, record[field], value)
    assert len(rows) == len(expected["classes"])
    by_code = {row["inputClass"]: row for row in rows}
    for group in expected["classes"]:
        row = by_code[group["code"]]
        area = group["featureCount"] * 100
        assert row["featureCount"] == group["featureCount"] and row["areaM2"] == area, row
        assert math.isclose(row["studyRatio"], area / expected["studyAreaM2"], rel_tol=1e-12, abs_tol=1e-12)
        assert math.isclose(row["coverageRatio"], area / expected["coveredAreaM2"], rel_tol=1e-12, abs_tol=1e-12)


def last_page(rpc, path, dataset):
    result = rpc.call("vector.page", {"path": path, "datasetId": dataset["id"], "offset": dataset["featureCount"] - 1,
                                      "limit": 1, "sortField": None, "descending": False, "filter": None})
    assert result["total"] == dataset["featureCount"] and len(result["rows"]) == 1 and not result["hasMore"], result
    assert result["rows"][0]["id"] == str(dataset["featureCount"]), result
    return result


def pressure_level(rpc, output, report, count):
    level = {"name": f"clip-{count}", "featureCount": count, "ok": False, "steps": []}
    report["levels"].append(level)
    directory = output / str(count)
    directory.mkdir()
    task_start = len(rpc.timings.get("task.get", []))
    with step(rpc, output, report, level, "generate"):
        left_source, right_source, expected = fixtures(directory / "sources", count)
        hashes = {str(source): digest(source) for source in (left_source, right_source)}
        level["expected"] = expected
        level["sourceBytes"] = {source.name: source.stat().st_size for source in (left_source, right_source)}
    with step(rpc, output, report, level, "create_import"):
        project = rpc.call("project.create", {"directory": str(directory / "project"), "name": f"Pressure {count}"})
        path = project["projectPath"]
        left = import_dataset(rpc, path, left_source)
        right = import_dataset(rpc, path, right_source)
        assert left["featureCount"] == count and right["featureCount"] == 1
        level.update(projectPath=path, inputDatasetId=left["id"], overlayDatasetId=right["id"], features=count,
                     options={key: value for key, value in options(path, left, right).items() if key != "path"})
    with step(rpc, output, report, level, "last_input_page"):
        assert last_page(rpc, path, left)["rows"][0]["values"]["code"] == CODES[(count - 1) % 5]
    with step(rpc, output, report, level, "cancel_analysis") as event:
        before = rpc.call("workspace.get", {"path": path})["datasets"]
        task = rpc.call("analysis.run", options(path, left, right))
        started = time.perf_counter()
        task = rpc.call("task.cancel", {"path": path, "taskId": task["id"]})
        task = rpc.wait_terminal(path, task)
        event["cancelToTerminalSeconds"] = time.perf_counter() - started
        assert task["status"] == "cancelled" and event["cancelToTerminalSeconds"] <= 5, task
        assert rpc.call("workspace.get", {"path": path})["datasets"] == before
    with step(rpc, output, report, level, "clip_after_cancel") as event:
        task = rpc.wait_task(path, rpc.call("analysis.run", options(path, left, right)))
        result = next(item for item in rpc.call("workspace.get", {"path": path})["datasets"] if item["id"] == task["datasetId"])
        event["taskId"] = task["id"]
        level["resultDatasetId"] = result["id"]
        level["resultVersion"] = result["version"]
    with step(rpc, output, report, level, "statistics_and_last_result_page"):
        record, rows = statistics(rpc, path, result)
        assert_analytic(record, rows, expected)
        last_page(rpc, path, result)
        level["analysisRecord"] = record
        level["statistics"] = rows
    with step(rpc, output, report, level, "export_gpkg_csv"):
        gpkg, csv_path = directory / "result.gpkg", directory / "statistics.csv"
        rpc.wait_task(path, rpc.call("vector.export", {"path": path, "datasetId": result["id"], "destination": str(gpkg)}))
        rpc.wait_task(path, rpc.call("analysis.exportCsv", {"path": path, "datasetId": result["id"], "destination": str(csv_path)}))
        assert digest(gpkg) == result["version"]
        assert pyogrio.read_info(gpkg, layer="features")["features"] == count
        layers = {row[0] for row in pyogrio.list_layers(gpkg)}
        assert {"features", "analysis_record", "analysis_statistics"} <= layers
        with csv_path.open(encoding="utf-8-sig", newline="") as stream:
            csv_rows = list(csv.DictReader(stream))
        csv_codes = {None if row["inputClassIsNull"] == "true" else row["inputClass"] for row in csv_rows}
        assert csv_codes == {group["code"] for group in expected["classes"]}
        assert sum(float(row["areaM2"]) for row in csv_rows) == expected["coveredAreaM2"]
        level["resultBytes"] = gpkg.stat().st_size
        level["exports"] = {"gpkg": str(gpkg), "csv": str(csv_path), "csvSha256": digest(csv_path)}
    with step(rpc, output, report, level, "reopen"):
        rpc.call("project.close")
        reopened = rpc.call("project.open", {"path": path})
        assert reopened["id"] == project["id"]
        assert statistics(rpc, path, result) == (record, rows)
    with step(rpc, output, report, level, "save_as_lineage_hash"):
        copy = rpc.wait_task(path, rpc.call("project.saveAs", {"path": path, "directory": str(directory / "copy"), "name": "Pressure copy",
                             "description": "Analysis lineage preserved", "analysisCrs": "EPSG:32650", "displayCrs": "EPSG:3857",
                             "viewState": {"center": [117, 27.12], "zoom": 10}}))
        copied = rpc.call("project.open", {"path": copy["destination"]})
        copied_path = copied["projectPath"]
        assert copied["id"] != project["id"]
        workspace = rpc.call("workspace.get", {"path": copied_path})
        assert {item["id"] for item in workspace["datasets"]} == {left["id"], right["id"], result["id"]}
        for dataset in workspace["datasets"]:
            assert digest(Path(copied_path).parent / dataset["relativePath"]) == dataset["version"]
        assert statistics(rpc, copied_path, result) == (record, rows)
        status = rpc.call("source.status", {"path": copied_path, "datasetId": result["id"]})
        assert status["availability"] == "internal", status
        metadata = result["source"]["metadata"]
        assert metadata["inputVersion"] == left["version"] and metadata["overlayVersion"] == right["version"]
        for source, expected_hash in hashes.items():
            assert digest(source) == expected_hash
        level["projectPath"] = path
        level["copyProjectPath"] = copied_path
        rpc.call("project.close")
    level["taskGetLatency"] = summary(rpc.timings.get("task.get", [])[task_start:])
    assert level["taskGetLatency"]["p95Ms"] <= 1000, level["taskGetLatency"]
    level["ok"] = True
    checkpoint(output, report)


def intersection_probe(rpc, output, report):
    level = {"name": "analytic-intersection-and-overlap-rejection", "ok": False, "steps": []}
    report["levels"].append(level)
    directory = output / "intersection"
    directory.mkdir()
    with step(rpc, output, report, level, "crossing_rectangles"):
        left_path, right_path = directory / "left.gpkg", directory / "right.gpkg"
        write_rectangles(left_path, [shapely.box(500000, 3000000, 500010, 3000020), shapely.box(500010, 3000000, 500020, 3000020)], ["001", None])
        write_rectangles(right_path, [shapely.box(500000, 3000000, 500020, 3000010), shapely.box(500000, 3000010, 500020, 3000020)], ["A", "B"])
        project = rpc.call("project.create", {"directory": str(directory / "project"), "name": "Intersection fixture"})
        path = project["projectPath"]
        left, right = import_dataset(rpc, path, left_path), import_dataset(rpc, path, right_path)
        task = rpc.wait_task(path, rpc.call("analysis.run", options(path, left, right, "intersect")))
        result = next(item for item in rpc.call("workspace.get", {"path": path})["datasets"] if item["id"] == task["datasetId"])
        record, rows = statistics(rpc, path, result)
        assert record["coveredAreaM2"] == record["studyAreaM2"] == 400
        assert record["outputFeatureCount"] == 4
        assert {(row["inputClass"], row["overlayClass"], row["areaM2"]) for row in rows} == {("001", "A", 100), ("001", "B", 100), (None, "A", 100), (None, "B", 100)}
    with step(rpc, output, report, level, "dense_overlap_rejection_then_success"):
        overlap_path = directory / "overlap.gpkg"
        write_rectangles(overlap_path, [shapely.box(500000, 3000000, 500010, 3000010)] * 1000, ["001"] * 1000)
        overlap = import_dataset(rpc, path, overlap_path)
        before = rpc.call("workspace.get", {"path": path})["datasets"]
        failed = rpc.wait_terminal(path, rpc.call("analysis.run", options(path, overlap, right)))
        assert failed["status"] == "failed" and "overlap" in failed["error"].lower(), failed
        assert rpc.call("workspace.get", {"path": path})["datasets"] == before
        succeeded = rpc.wait_task(path, rpc.call("analysis.run", options(path, left, right)))
        assert succeeded["status"] == "completed"
        rpc.call("project.close")
    level["ok"] = True
    checkpoint(output, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New artifact directory")
    parser.add_argument("--executable", help="Frozen engine executable; omitted uses source engine")
    parser.add_argument("--counts", nargs="+", default=["1000", "10000", "100000", "250000", "500000"],
                        help="One or more counts, space- or comma-separated")
    args = parser.parse_args()
    counts = [int(value) for item in args.counts for value in item.split(",")]
    if not counts or len(set(counts)) != len(counts) or any(not 1 <= count <= 500000 for count in counts):
        parser.error("counts must be distinct integers between 1 and 500000")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"ok": False, "startedAt": datetime.now(UTC).isoformat(), "requestedCounts": counts, "levels": [],
              "hardware": {"platform": platform.platform(), "processor": platform.processor(), "logicalCpuCount": os.cpu_count(),
                           "physicalCpuCount": psutil.cpu_count(logical=False), "physicalMemoryBytes": psutil.virtual_memory().total,
                           "artifactDisk": str(output.anchor), "diskFreeBytesAtStart": psutil.disk_usage(str(output)).free},
              "thresholds": {"taskGetP95Ms": 1000, "cancelToTerminalSeconds": 5, "taskDeadlineSeconds": 930,
                             "areaAbsoluteToleranceM2": 1e-6, "areaRelativeTolerance": 1e-12},
              "notMeasured": ["DOM interaction p95 and event-loop pauses require native/UI acceptance.",
                              "Sampled process RSS does not establish an exact allocation peak.",
                              "Simple rectangle capacity is not arbitrary-complexity polygon capacity."]}
    rpc = MeasuredRpc(Path(__file__).resolve().parents[1], output, args.executable)
    memory = MemorySampler(rpc, output)
    try:
        report["runtime"] = rpc.call("runtime.info")
        assert report["runtime"]["protocolVersion"] == 6 and report["runtime"]["engineVersion"] == "0.6.0"
        if args.executable:
            assert report["runtime"]["packaged"] is True
        for count in counts:
            pressure_level(rpc, output, report, count)
        intersection_probe(rpc, output, report)
        report["ok"] = True
    except Exception as exc:
        report["error"] = repr(exc)
        raise
    finally:
        report["finishedAt"] = datetime.now(UTC).isoformat()
        report["rpcLatency"] = {method: summary(values) for method, values in rpc.timings.items()}
        report["memory"] = memory.close()
        rpc.close()
        checkpoint(output, report)
        print(json.dumps({"ok": report["ok"], "report": str(output / "report.json")}), flush=True)


if __name__ == "__main__":
    main()
