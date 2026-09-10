"""Exercise Phase 4A1 cartography through the real source/frozen stdio service.

Run with --output NEW_DIRECTORY [--executable FROZEN_ENGINE]. All GeoPackages,
projects and reports are newly generated synthetic artifacts. This verifies
display configuration persistence and data identity, not rendered pixels, category
coverage, formal map exports, large-data performance or OS cold-cache behavior.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import math
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyogrio
import shapely

Rpc = importlib.import_module("verify-vectors").Rpc
CODES = [None, "", "001", "null", "constructor", "toString", "0", "false"]
AMOUNTS = [None, 0.0, 1.0, -1.0, 1.5, 2.0, 0.0, 2.0]
FLAGS = [None, False, True, False, True, False, True, False]
COLORS = ["#123456", "#234567", "#345678", "#456789", "#56789a", "#6789ab", "#789abc"]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def exact(value):
    # JSON encoding retains boolean/number/text distinctions lost by Python ==.
    # Integer-valued floats and integers are the same public JSON number type.
    def normalize(item):
        if isinstance(item, float) and math.isfinite(item) and item.is_integer():
            return int(item)
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        return item

    return json.dumps(normalize(value), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def checkpoint(output, report):
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


@contextmanager
def step(output, report, name):
    event = {"name": name, "ok": False}
    report["steps"].append(event)
    started = time.monotonic()
    try:
        yield event
        event["ok"] = True
    finally:
        event["durationSeconds"] = time.monotonic() - started
        checkpoint(output, report)
        print(json.dumps(event, ensure_ascii=True), flush=True)


def fixtures(directory):
    directory.mkdir()
    parcels, boundary = directory / "parcels.gpkg", directory / "boundary.gpkg"
    geometries = [shapely.box(500000 + i * 20, 3000000, 500010 + i * 20, 3000010) for i in range(len(CODES))]
    table = pa.table({"code": pa.array(CODES, type=pa.string()),
                      "amount": pa.array(AMOUNTS, type=pa.float64()),
                      "flag": pa.array(FLAGS, type=pa.bool_()),
                      "geometry": pa.array(shapely.to_wkb(geometries), type=pa.binary())})
    pyogrio.write_arrow(table, parcels, layer="land", driver="GPKG", geometry_name="geometry",
                        geometry_type="Polygon", crs="EPSG:32650", layer_options={"SPATIAL_INDEX": "YES"})
    mask = pa.table({"code": pa.array(["boundary"], type=pa.string()),
                     "geometry": pa.array([shapely.to_wkb(shapely.box(499995, 2999995, 500155, 3000015))], type=pa.binary())})
    pyogrio.write_arrow(mask, boundary, layer="land", driver="GPKG", geometry_name="geometry",
                        geometry_type="Polygon", crs="EPSG:32650", layer_options={"SPATIAL_INDEX": "YES"})
    return parcels, boundary


def import_dataset(rpc, path, source):
    inspection = rpc.call("source.inspect", {"sourcePath": str(source), "encoding": None})
    require(inspection["driver"] == "GPKG" and len(inspection["layers"]) == 1, "Fixture is not a single-layer real GeoPackage")
    task = rpc.wait_task(path, rpc.call("vector.import", {"path": path, "sourcePath": str(source),
        "sourceLayer": "land", "encoding": None, "assignedCrs": None}))
    workspace = rpc.call("workspace.get", {"path": path})
    return next(item for item in workspace["datasets"] if item["id"] == task["datasetId"])


def layer_for(rpc, path, dataset):
    return next(item for item in rpc.call("workspace.get", {"path": path})["layers"] if item["datasetId"] == dataset["id"])


def spec_for(dataset, revision, field="code", values=None, preset="planning"):
    values = CODES[1:] if values is None else values
    return {"specVersion": 1, "kind": "vector-layer", "revision": revision,
            "input": {"datasetId": dataset["id"], "version": dataset["version"]},
            "basePreset": preset, "presetVersion": 1,
            "symbol": {"fillColor": "#89abcd", "strokeColor": "#102030", "strokeWidthPt": 1.5, "pointRadiusPt": 4},
            "renderer": {"kind": "categorized", "field": field,
                "categories": [{"value": value, "label": f"Configured {index}", "color": COLORS[index % len(COLORS)]}
                               for index, value in enumerate(values)],
                "nullColor": "#aabbcc", "nullLabel": "NULL", "otherColor": "#ddeeff", "otherLabel": "Other"},
            "legend": {"visible": True, "title": "Synthetic configured symbols"}}


def update(rpc, path, layer_id, spec, expected):
    changed = rpc.call("layer.update", {"path": path, "layerId": layer_id,
        "changes": {"cartography": spec, "expectedCartographyRevision": expected}})
    require(exact(changed.get("cartography")) == exact(spec), "Accepted spec differs from the submitted configuration")
    require(changed.get("cartographyRevision") == expected + 1, "Accepted change did not advance the edit revision")
    return changed


def reject(rpc, path, layer_id, changes, name, kind="invalid_params"):
    before = rpc.call("workspace.get", {"path": path})
    response = rpc._request("layer.update", {"path": path, "layerId": layer_id, "changes": changes})
    require("result" not in response and "error" in response, f"Invalid edit was accepted: {name}")
    error = response["error"]
    require(error.get("data", {}).get("kind") == kind, f"Wrong rejection kind for {name}: {error}")
    require(error["code"] == (-32602 if kind == "invalid_params" else -32000), f"Wrong rejection code for {name}")
    require(exact(rpc.call("workspace.get", {"path": path})) == exact(before), f"Rejected edit partially changed the workspace: {name}")
    return {"name": name, "kind": kind, "code": error["code"], "unchanged": True}


def invalid_specs(base):
    cases = []

    def change(name, keys, value):
        edited = copy.deepcopy(base)
        current = edited
        for key in keys[:-1]:
            current = current[key]
        current[keys[-1]] = value
        cases.append((name, edited))

    change("future_spec_version", ["specVersion"], 2)
    change("unknown_root_field", ["arbitraryCode"], "disallowed")
    change("unknown_symbol_field", ["symbol", "opacity"], 0.5)
    change("unknown_legend_field", ["legend", "counts"], True)
    change("wrong_dataset", ["input", "datasetId"], "missing-dataset")
    change("wrong_snapshot_version", ["input", "version"], "0" * 64)
    change("unknown_field", ["renderer", "field"], "absent_field")
    change("unknown_renderer", ["renderer", "kind"], "graduated")
    change("unknown_preset", ["basePreset"], "journal-certified")
    change("future_preset_version", ["presetVersion"], 2)
    change("invalid_color", ["symbol", "fillColor"], "red")
    change("invalid_null_color", ["renderer", "nullColor"], "#ffff")
    change("negative_stroke_width", ["symbol", "strokeWidthPt"], -0.1)
    change("stroke_width_over_budget", ["symbol", "strokeWidthPt"], 6.1)
    change("boolean_stroke_width", ["symbol", "strokeWidthPt"], True)
    change("point_radius_below_budget", ["symbol", "pointRadiusPt"], 0.9)
    change("point_radius_over_budget", ["symbol", "pointRadiusPt"], 16.1)
    change("non_boolean_legend_visibility", ["legend", "visible"], 1)
    change("legend_title_over_budget", ["legend", "title"], "a" * 121)
    change("category_label_over_budget", ["renderer", "categories", 0, "label"], "a" * 121)
    change("category_value_over_budget", ["renderer", "categories", 0, "value"], "a" * 1025)
    change("null_in_non_null_categories", ["renderer", "categories", 0, "value"], None)
    change("unsafe_numeric_category", ["renderer", "categories", 0, "value"], 9007199254740992)
    change("duplicate_typed_category", ["renderer", "categories"], [base["renderer"]["categories"][0]] * 2)
    change("category_count_over_budget", ["renderer", "categories"],
           [{"value": str(i), "label": "Value", "color": "#123456"} for i in range(65)])
    # Every individual string/count is valid; only the compact UTF-8 byte budget fails.
    change("encoded_spec_over_budget", ["renderer", "categories"],
           [{"value": str(i) + "x" * 1000, "label": "Value", "color": "#123456"} for i in range(40)])
    change("revision_not_next", ["revision"], base["revision"] + 1)
    change("boolean_revision", ["revision"], True)
    return cases


def page(rpc, path, dataset):
    return rpc.call("vector.page", {"path": path, "datasetId": dataset["id"], "offset": 0,
        "limit": 200, "sortField": None, "descending": False, "filter": None})


def statistics(rpc, path, dataset):
    result = rpc.call("analysis.result", {"path": path, "datasetId": dataset["id"], "offset": 0, "limit": 100})
    require(not result["hasMore"] and result["total"] == len(CODES), "Synthetic statistics are incomplete")
    return result


def assert_identity(rpc, path, datasets, sources, baseline_page, input_dataset, result_dataset, baseline_statistics):
    current = rpc.call("workspace.get", {"path": path})
    require(exact(current["datasets"]) == exact(datasets), "Cartography changed dataset metadata/provenance")
    hashes = {}
    for dataset in datasets:
        snapshot = Path(path).parent / dataset["relativePath"]
        hashes[dataset["id"]] = digest(snapshot)
        require(hashes[dataset["id"]] == dataset["version"], "Cartography changed snapshot bytes")
    for source, before in sources.items():
        require(digest(source) == before, "Cartography changed original source bytes")
    require(exact(page(rpc, path, input_dataset)) == exact(baseline_page), "Cartography changed original attributes")
    require(exact(statistics(rpc, path, result_dataset)) == exact(baseline_statistics), "Cartography changed analysis record/statistics/units")
    return hashes


def verify(rpc, output, report):
    with step(output, report, "runtime_real_geopackage_import_and_typed_values") as event:
        runtime = rpc.call("runtime.info")
        require(runtime["protocolVersion"] == 7 and runtime["engineVersion"] == "0.7.0", "Unexpected cartography runtime version")
        report["runtime"] = runtime
        parcels, boundary = fixtures(output / "fixtures")
        sources = {parcels: digest(parcels), boundary: digest(boundary)}
        report["sourceSha256Before"] = {str(path): sha for path, sha in sources.items()}
        project = rpc.call("project.create", {"directory": str(output / "project"), "name": "Synthetic cartography acceptance"})
        require(project["schemaVersion"] == 7, "New project schema is not 7")
        path = project["projectPath"]
        report["projectPath"] = path
        input_dataset, overlay_dataset = (import_dataset(rpc, path, source) for source in (parcels, boundary))
        baseline_page = page(rpc, path, input_dataset)
        require(baseline_page["total"] == len(CODES) and not baseline_page["hasMore"], "Incomplete synthetic input page")
        for field, expected in (("code", CODES), ("amount", AMOUNTS), ("flag", FLAGS)):
            actual = [row["values"][field] for row in baseline_page["rows"]]
            require(exact(actual) == exact(expected), f"Synthetic {field} types/values were not preserved")
        viewport_params = {"path": path, "datasetId": input_dataset["id"], "bbox": input_dataset["boundsWgs84"],
                           "limit": 2000, "propertyFields": ["code", "amount", "flag"]}
        baseline_viewport = rpc.call("vector.viewport", viewport_params)
        require(baseline_viewport["returnedCount"] == len(CODES) and not baseline_viewport["truncated"], "Incomplete synthetic viewport")
        event["features"] = len(CODES)
        report["inputDatasetId"] = input_dataset["id"]

    with step(output, report, "tiny_analytic_clip_baseline") as event:
        task = rpc.wait_task(path, rpc.call("analysis.run", {"path": path, "operation": "clip", "name": "Synthetic 8-parcel clip",
            "inputDatasetId": input_dataset["id"], "overlayDatasetId": overlay_dataset["id"], "inputClassField": "code",
            "overlayClassField": None, "classificationStandard": "Synthetic literal codes only; no business classification",
            "analysisCrs": "EPSG:32650", "crsReason": "Synthetic 10 m rectangles in UTM zone 50N near 117E, 27N"}))
        workspace = rpc.call("workspace.get", {"path": path})
        datasets = workspace["datasets"]
        result_dataset = next(item for item in datasets if item["id"] == task["datasetId"])
        baseline_statistics = statistics(rpc, path, result_dataset)
        record = baseline_statistics["record"]
        require(record["outputFeatureCount"] == len(CODES), "Synthetic clip lost parcel geometries")
        for field, expected in (("recordAreaM2", 800), ("coveredAreaM2", 800), ("studyAreaM2", 3200), ("uncoveredAreaM2", 2400)):
            require(math.isclose(record[field], expected, rel_tol=1e-12, abs_tol=1e-6), f"Analytic area differs: {field}")
        require({row["inputClass"] for row in baseline_statistics["rows"]} == set(CODES), "Analysis merged distinct text/NULL categories")
        require(all(row["featureCount"] == 1 and row["areaM2"] == 100 for row in baseline_statistics["rows"]), "Synthetic category areas differ")
        report["resultDatasetId"] = result_dataset["id"]
        report["analysisBaseline"] = baseline_statistics
        event["features"], event["coveredAreaM2"] = len(CODES), record["coveredAreaM2"]

    with step(output, report, "explicit_adoption_and_atomic_invalid_edit_rejection") as event:
        layer = layer_for(rpc, path, input_dataset)
        require(layer.get("cartography") is None and layer.get("cartographyRevision", 0) == 0, "New layer silently adopted a style")
        legacy_changes = {"color": "#335577", "categoryField": "code", "categoryColors": {"001": "#778899", "constructor": "#99aabb"}}
        legacy = rpc.call("layer.update", {"path": path, "layerId": layer["id"], "changes": legacy_changes})
        current_spec = spec_for(input_dataset, 1)
        layer = update(rpc, path, layer["id"], current_spec, 0)
        report["rejections"] = []
        for name, invalid in invalid_specs(spec_for(input_dataset, 2)):
            report["rejections"].append(reject(rpc, path, layer["id"], {"name": "MUST NOT BE SAVED", "cartography": invalid,
                "expectedCartographyRevision": 1}, name))
        for name, changes, kind in (
            ("missing_expected_revision", {"cartography": spec_for(input_dataset, 2)}, "invalid_params"),
            ("boolean_expected_revision", {"cartography": spec_for(input_dataset, 2), "expectedCartographyRevision": True}, "invalid_params"),
            ("stale_adoption", {"cartography": current_spec, "expectedCartographyRevision": 0}, "cartography_conflict"),
            ("active_legacy_style_edit", {"color": "#ffffff"}, "invalid_params"),
            ("mixed_legacy_and_new_style", {"color": "#ffffff", "cartography": spec_for(input_dataset, 2), "expectedCartographyRevision": 1}, "invalid_params"),
        ):
            report["rejections"].append(reject(rpc, path, layer["id"], changes, name, kind))
        event["rejectedEdits"] = len(report["rejections"])

    with step(output, report, "typed_categories_symbol_limits_and_result_style"):
        # Include type-distinct values together; a configured value need not occur.
        typed_values = [0, False, "0", "false", 1, True, "1", "", "null", 9007199254740991, -9007199254740991]
        for revision, field, values in ((2, "amount", typed_values), (3, "flag", [False, True, 0, 1, "false", "true"])):
            current_spec = spec_for(input_dataset, revision, field, values, "publication")
            current_spec["symbol"].update(strokeWidthPt=0, pointRadiusPt=16)
            layer = update(rpc, path, layer["id"], current_spec, revision - 1)
        single = spec_for(result_dataset, 1, preset="publication")
        single["renderer"] = {"kind": "single"}
        single["symbol"].update(strokeWidthPt=6, pointRadiusPt=1)
        result_layer = layer_for(rpc, path, result_dataset)
        update(rpc, path, result_layer["id"], single, 0)
        # Name, visibility and opacity remain independent from active cartography.
        layer = rpc.call("layer.update", {"path": path, "layerId": layer["id"],
            "changes": {"name": "Styled synthetic parcels", "visible": False, "opacity": 0.6}})
        require(exact(layer["cartography"]) == exact(current_spec) and layer["cartographyRevision"] == 3,
                "Independent layer properties altered the cartography revision/spec")

    with step(output, report, "restore_tombstone_reopen_stale_rejection_and_readoption") as event:
        layer = update(rpc, path, layer["id"], None, 3)
        require(all(layer[key] == legacy[key] for key in legacy_changes), "Restore did not retain the original legacy style")
        rpc.call("project.close")
        rpc.call("project.open", {"path": path})
        require(exact(layer_for(rpc, path, input_dataset)) == exact(layer), "Restore revision/tombstone did not survive reopening")
        # A tombstone is real stored state, not merely an absent UI field.
        with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as connection:
            row = connection.execute("SELECT revision, spec_json FROM vector_cartography WHERE layer_id = ?", (layer["id"],)).fetchone()
        require(row == (4, None), "Restore did not retain a revision-4 SQL NULL tombstone")
        report["rejections"].append(reject(rpc, path, layer["id"], {"cartography": current_spec, "expectedCartographyRevision": 2},
                                          "stale_after_restore", "cartography_conflict"))
        current_spec = spec_for(input_dataset, 5)
        layer = update(rpc, path, layer["id"], current_spec, 4)
        require(exact(rpc.call("vector.viewport", viewport_params)) == exact(baseline_viewport), "Styling changed viewport geometry/properties")
        event["restoredRevision"], event["readoptedRevision"] = 4, 5
        report["snapshotSha256BeforeCopy"] = assert_identity(rpc, path, datasets, sources, baseline_page, input_dataset, result_dataset, baseline_statistics)

    with step(output, report, "save_close_reopen_save_as_and_exact_data_identity") as event:
        saved = rpc.call("project.save", {"path": path, "name": project["name"], "description": "Cartography synthetic acceptance",
            "analysisCrs": "EPSG:32650", "displayCrs": project["displayCrs"], "viewState": project["viewState"]})
        expected_layers = rpc.call("workspace.get", {"path": path})["layers"]
        rpc.call("project.close")
        rpc.call("project.open", {"path": path})
        require(exact(rpc.call("workspace.get", {"path": path})["layers"]) == exact(expected_layers), "Saved styles differ after close/reopen")
        report["snapshotSha256AfterReopen"] = assert_identity(rpc, path, datasets, sources, baseline_page, input_dataset, result_dataset, baseline_statistics)
        copied = rpc.wait_task(path, rpc.call("project.saveAs", {"path": path, "directory": str(output / "copy"),
            "name": "Synthetic cartography copy", "description": saved["description"], "analysisCrs": saved["analysisCrs"],
            "displayCrs": saved["displayCrs"], "viewState": saved["viewState"]}))
        copy_path = copied["destination"]
        report["copyProjectPath"] = copy_path
        rpc.call("project.close")
        copy_project = rpc.call("project.open", {"path": copy_path})
        require(copy_project["id"] != project["id"], "Save As reused original project identity")
        require(exact(rpc.call("workspace.get", {"path": copy_path})["layers"]) == exact(expected_layers), "Save As changed exact layer specs/revisions")
        report["snapshotSha256Copy"] = assert_identity(rpc, copy_path, datasets, sources, baseline_page, input_dataset, result_dataset, baseline_statistics)
        rpc.call("project.close")
        report["sourceSha256After"] = {str(source): digest(source) for source in sources}
        require(report["sourceSha256Before"] == report["sourceSha256After"], "Source fingerprints differ at completion")
        event["preservedSnapshots"] = len(datasets)
        event["preservedAnalysisCategories"] = baseline_statistics["total"]
        report["finalLayers"] = expected_layers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New directory; existing outputs are never overwritten")
    parser.add_argument("--executable", help="Frozen engine executable; omitted uses the current source engine")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    executable = str(Path(args.executable).resolve()) if args.executable else None
    if executable and not Path(executable).is_file():
        parser.error("--executable must identify an existing file")
    output.mkdir(parents=True, exist_ok=False)
    report = {"ok": False, "startedAt": datetime.now(UTC).isoformat(), "steps": [], "executable": executable,
              "scope": "New eight-parcel synthetic GeoPackages through real stdio service and GIS workers",
              "limitations": ["No rendered-pixel, native UI or geometry-refetch verification in this script.",
                  "Configured categories do not establish actual category presence or complete coverage.",
                  "No formal business classification, formal map export, ArcGIS comparison or independent-machine acceptance.",
                  "No large-data performance or OS cold-cache evidence; source/frozen modes are separate runs."]}
    rpc = None
    try:
        if executable:
            report["executableSha256"] = digest(executable)
        rpc = Rpc(Path(__file__).resolve().parents[1], output, executable)
        verify(rpc, output, report)
        require(report["runtime"]["packaged"] == bool(executable), "Runtime packaged flag does not match the selected mode")
        report["ok"] = True
    except Exception as exc:
        report["error"] = repr(exc)
    finally:
        if rpc is not None:
            try:
                rpc.close()
                report["engineExitCode"] = rpc.process.returncode
                require(rpc.process.returncode == 0, "Engine did not shut down cleanly")
            except Exception as exc:
                report["ok"] = False
                report["cleanupError"] = repr(exc)
        if "sourceSha256Before" in report:
            try:
                report["sourceSha256After"] = {source: digest(source) for source in report["sourceSha256Before"]}
                report["sourceUnchanged"] = report["sourceSha256Before"] == report["sourceSha256After"]
                require(report["sourceUnchanged"], "Original source bytes changed")
            except Exception as exc:
                report["ok"] = False
                report["sourceIntegrityError"] = repr(exc)
        report["finishedAt"] = datetime.now(UTC).isoformat()
        checkpoint(output, report)
        print(json.dumps({"ok": report["ok"], "report": str(output / "report.json"), "error": report.get("error"),
                          "cleanupError": report.get("cleanupError")}), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
