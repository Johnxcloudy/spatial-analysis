"""Real-RPC complexity success and resource-guard acceptance (source or frozen).

Candidate/geometry guard rejections are reported as protection checks, never as
successful large-overlay capacity. No runtime constants are monkeypatched.
"""
from __future__ import annotations

import argparse
import importlib
import json
import math
import platform
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import psutil
import shapely

base = importlib.import_module("verify-analysis")


def regular_polygons(path, count, edges, radius):
    angles = np.arange(edges, dtype=np.float64) * (2 * math.pi / edges)
    unit = np.column_stack((np.cos(angles), np.sin(angles))) * radius
    for start in range(0, count, 32):
        indices = np.arange(start, min(count, start + 32))
        centres = np.column_stack((500000 + (indices % 50) * 30, 3000000 + (indices // 50) * 30))
        coordinates = centres[:, None, :] + unit[None, :, :]
        geometries = shapely.polygons(coordinates)
        base.write_rectangles(path, geometries, ["001"] * len(indices), append=start != 0)
    return .5 * edges * radius * radius * math.sin(2 * math.pi / edges) * count


def square_ring(radius):
    outer = shapely.box(500000 - radius, 3000000 - radius, 500000 + radius, 3000000 + radius)
    inner = shapely.box(500000 - radius + .25, 3000000 - radius + .25, 500000 + radius - .25, 3000000 + radius - .25)
    return shapely.Polygon(outer.exterior.coords, [inner.exterior.coords])


def workspace_dataset(rpc, path, task):
    return next(dataset for dataset in rpc.call("workspace.get", {"path": path})["datasets"] if dataset["id"] == task["datasetId"])


def record_task(event, task):
    event["task"] = task
    # Task RPC deliberately exposes terminal error text, not structured worker kind.
    # Expected kinds below are checked through exact unique error messages.
    event["taskErrorEvidence"] = task.get("error")


def verify(rpc, output, report):
    sources = output / "sources"
    sources.mkdir()
    project = rpc.call("project.create", {"directory": str(output / "project"), "name": "Complexity and resource guard acceptance"})
    path = project["projectPath"]
    report["projectPath"] = path
    boundary_path = sources / "boundary.gpkg"
    base.write_rectangles(boundary_path, [shapely.box(495000, 2995000, 505000, 3005000)], ["boundary"])
    boundary = base.import_dataset(rpc, path, boundary_path)

    complexity = {"name": "1000-regular-2000-edge-polygons", "checkType": "successful_complexity", "ok": False, "steps": []}
    report["levels"].append(complexity)
    with base.step(rpc, output, report, complexity, "generate_2001000_vertices"):
        source = sources / "regular.gpkg"
        expected_area = regular_polygons(source, 1000, 2000, 10)
        original_hash = base.digest(source)
        complexity.update(inputFeatures=1000, edgesPerFeature=2000, inputVertices=2_001_000,
                          radius=10, expectedAreaM2=expected_area, areaFormula="0.5 * edges * radius^2 * sin(2*pi/edges) * features",
                          sourceBytes=source.stat().st_size, sourceSha256=original_hash)
    with base.step(rpc, output, report, complexity, "import") as event:
        land = base.import_dataset(rpc, path, source)
        assert land["featureCount"] == 1000 and land["report"]["counts"]["vertices"] == 2_001_000
        event["datasetId"] = land["id"]
    with base.step(rpc, output, report, complexity, "clip_complete") as event:
        task = rpc.wait_task(path, rpc.call("analysis.run", base.options(path, land, boundary)))
        record_task(event, task)
        result = workspace_dataset(rpc, path, task)
        assert result["featureCount"] == 1000
        assert result["report"]["counts"]["vertices"] == 2_001_000
        record, rows = base.statistics(rpc, path, result)
        assert len(rows) == 1 and rows[0]["inputClass"] == "001"
        assert math.isclose(record["coveredAreaM2"], expected_area, rel_tol=1e-9, abs_tol=1e-6), (record["coveredAreaM2"], expected_area)
        assert math.isclose(rows[0]["areaM2"], expected_area, rel_tol=1e-9, abs_tol=1e-6)
        complexity.update(actualAreaM2=record["coveredAreaM2"], absoluteAreaErrorM2=abs(record["coveredAreaM2"] - expected_area),
                          resultDatasetId=result["id"], resultVersion=result["version"], candidatePairs=record["candidatePairs"])
    with base.step(rpc, output, report, complexity, "last_page_and_original_unchanged"):
        base.last_page(rpc, path, result)
        assert base.digest(source) == original_hash
        assert base.digest(Path(path).parent / result["relativePath"]) == result["version"]
    complexity["ok"] = True
    base.checkpoint(output, report)

    candidates = {"name": "3200-nested-disjoint-square-rings", "checkType": "protected_candidate_failure", "ok": False, "steps": [],
                  "inputFeatures": 3200, "inputVertices": 32000, "potentialSameLayerPairs": 3200 * 3199 // 2,
                  "configuredCandidateBudget": 5_000_000, "expectedErrorKind": "analysis_limit",
                  "errorKindEvidenceBoundary": "Task RPC returns error text only; exact unique budget message is asserted, structured worker kind is not returned."}
    report["levels"].append(candidates)
    with base.step(rpc, output, report, candidates, "generate_and_import_disjoint_rings"):
        ring_path = sources / "rings.gpkg"
        rings = [square_ring(4000 - index) for index in range(3200)]
        assert all(geometry.is_valid for geometry in rings)
        # Adjacent radii differ by 1 m; each ring is only 0.25 m thick. Their
        # interiors are mathematically disjoint, while every bounding box nests.
        base.write_rectangles(ring_path, rings, ["001"] * len(rings))
        ring_hash = base.digest(ring_path)
        ring_dataset = base.import_dataset(rpc, path, ring_path)
        assert ring_dataset["featureCount"] == 3200
        candidates["sourceSha256"] = ring_hash
    with base.step(rpc, output, report, candidates, "candidate_budget_rejection") as event:
        before = rpc.call("workspace.get", {"path": path})["datasets"]
        started = time.perf_counter()
        task = rpc.wait_terminal(path, rpc.call("analysis.run", base.options(path, ring_dataset, boundary)))
        event["terminalSeconds"] = time.perf_counter() - started
        record_task(event, task)
        assert task["status"] == "failed", task
        assert task["error"] == "Analysis candidate pair budget exceeded", task
        assert rpc.call("workspace.get", {"path": path})["datasets"] == before
        assert base.digest(ring_path) == ring_hash
    candidates["ok"] = True
    base.checkpoint(output, report)

    single = {"name": "oversized-single-polygon-and-recovery", "checkType": "protected_geometry_failure", "ok": False, "steps": [],
              "edges": 100001, "verticesIncludingClosure": 100002, "configuredSingleGeometryBudget": 100000}
    report["levels"].append(single)
    with base.step(rpc, output, report, single, "generate_oversized_polygon"):
        oversized_path = sources / "oversized.gpkg"
        regular_polygons(oversized_path, 1, 100001, 10)
        oversized_hash = base.digest(oversized_path)
    with base.step(rpc, output, report, single, "import_geometry_budget_rejection") as event:
        before = rpc.call("workspace.get", {"path": path})["datasets"]
        task = rpc.wait_terminal(path, rpc.call("vector.import", {"path": path, "sourcePath": str(oversized_path), "sourceLayer": "land", "encoding": None, "assignedCrs": None}))
        record_task(event, task)
        assert task["status"] == "failed", task
        assert task["error"] == "Source exceeds feature, total vertex or single-geometry budget", task
        assert rpc.call("workspace.get", {"path": path})["datasets"] == before
        assert base.digest(oversized_path) == oversized_hash
    with base.step(rpc, output, report, single, "following_small_import_analysis_success") as event:
        small_path = sources / "small.gpkg"
        base.write_rectangles(small_path, [shapely.box(500000, 3000000, 500010, 3000010)], ["001"])
        small = base.import_dataset(rpc, path, small_path)
        task = rpc.wait_task(path, rpc.call("analysis.run", base.options(path, small, boundary)))
        record_task(event, task)
        result = workspace_dataset(rpc, path, task)
        record, rows = base.statistics(rpc, path, result)
        assert record["coveredAreaM2"] == 100 and rows[0]["areaM2"] == 100
        single["recoveryDatasetId"] = result["id"]
    single["ok"] = True
    rpc.call("project.close")
    base.checkpoint(output, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--executable")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"ok": False, "startedAt": datetime.now(UTC).isoformat(), "levels": [],
              "hardware": {"platform": platform.platform(), "physicalMemoryBytes": psutil.virtual_memory().total},
              "thresholds": {"taskDeadlineSeconds": 930, "workerWatchdogSeconds": 900,
                             "regularPolygonAreaAbsoluteToleranceM2": 1e-6, "regularPolygonAreaRelativeTolerance": 1e-9},
              "evidenceBoundary": "Complex polygon success, candidate-budget rejection and single-geometry rejection are separate outcomes; protection failures are not capacity successes."}
    rpc = base.MeasuredRpc(Path(__file__).resolve().parents[1], output, args.executable)
    sampler = base.MemorySampler(rpc, output)
    try:
        report["runtime"] = rpc.call("runtime.info")
        assert report["runtime"]["protocolVersion"] == 6
        if args.executable:
            assert report["runtime"]["packaged"] is True
        verify(rpc, output, report)
        report["ok"] = True
    except Exception as exc:
        report["error"] = repr(exc)
        raise
    finally:
        report["finishedAt"] = datetime.now(UTC).isoformat()
        report["rpcLatency"] = {method: base.summary(values) for method, values in rpc.timings.items()}
        report["memory"] = sampler.close()
        rpc.close()
        base.checkpoint(output, report)
        print(json.dumps({"ok": report["ok"], "report": str(output / "report.json")}), flush=True)


if __name__ == "__main__":
    main()
