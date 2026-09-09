from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from spatial_engine.rpc import Engine, handle_request


def test_runtime_info_matches_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    result = Engine().dispatch("runtime.info", {})

    assert result["protocolVersion"] == 1
    assert result["engineVersion"] == "0.1.0"
    assert result["pythonVersion"].startswith("3.12")
    assert isinstance(result["packaged"], bool)
    assert {"geopandas", "shapely", "pyogrio", "pyproj", "rasterio"} <= result["versions"].keys()
    assert result["drivers"].get("GPKG") in {"r", "rw"}
    assert result["drivers"].get("GTiff") in {"r", "rw"}
    assert Path(result["logPath"]).is_absolute()
    assert "runtime.info" in Path(result["logPath"]).read_text(encoding="utf-8")


def test_json_rpc_errors_are_stable_and_preserve_id() -> None:
    engine = Engine()

    unknown = handle_request(engine, {"jsonrpc": "2.0", "id": 7, "method": "layer.import", "params": {}})
    invalid = handle_request(engine, {"jsonrpc": "2.0", "id": "a", "method": "runtime.info", "params": []})

    assert unknown == {
        "jsonrpc": "2.0",
        "id": 7,
        "error": {"code": -32601, "message": "Method not found", "data": {"kind": "method_not_found"}},
    }
    assert invalid["id"] == "a"
    assert invalid["error"]["code"] == -32602
    assert invalid["error"]["data"]["kind"] == "invalid_params"


def test_request_cli_emits_one_protocol_line_without_log_chatter(tmp_path: Path) -> None:
    request = json.dumps({"jsonrpc": "2.0", "id": 12, "method": "project.close", "params": {}})
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(tmp_path / "local")
    completed = subprocess.run(
        [sys.executable, "-m", "spatial_engine", "--request", request],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )

    assert completed.returncode == 0
    assert completed.stdout.splitlines() == [json.dumps({"jsonrpc": "2.0", "id": 12, "result": {"closed": True}}, separators=(",", ":"))]


def test_request_cli_writes_utf8_when_text_stdio_uses_gbk(tmp_path: Path) -> None:
    project_dir = tmp_path / "中文项目"
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "请求一",
            "method": "project.create",
            "params": {"directory": str(project_dir), "name": "土地分析"},
        },
        ensure_ascii=False,
    )
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(tmp_path / "local")
    env["PYTHONIOENCODING"] = "gbk"
    env["PYTHONUTF8"] = "0"
    completed = subprocess.run(
        [sys.executable, "-m", "spatial_engine", "--request", request],
        check=False,
        capture_output=True,
        text=False,
        env=env,
    )

    response = json.loads(completed.stdout.decode("utf-8"))
    assert completed.returncode == 0
    assert response["id"] == "请求一"
    assert response["result"]["name"] == "土地分析"
    assert response["result"]["projectPath"] == str((project_dir / "project.spa").resolve())


def test_persistent_stdio_handles_parse_error_then_next_request(tmp_path: Path) -> None:
    requests = "{broken\n" + json.dumps({"jsonrpc": "2.0", "id": 3, "method": "project.close", "params": {}}) + "\n"
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(tmp_path / "local")
    completed = subprocess.run(
        [sys.executable, "-m", "spatial_engine"],
        input=requests,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    lines = [json.loads(line) for line in completed.stdout.splitlines()]

    assert completed.returncode == 0
    assert lines[0]["error"]["code"] == -32700
    assert lines[0]["id"] is None
    assert lines[1] == {"jsonrpc": "2.0", "id": 3, "result": {"closed": True}}


def test_persistent_stdio_handles_deep_json_then_next_request(tmp_path: Path) -> None:
    deep_request = "[" * 2_000 + "0" + "]" * 2_000
    valid_request = json.dumps({"jsonrpc": "2.0", "id": 4, "method": "project.close", "params": {}})
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(tmp_path / "local")
    completed = subprocess.run(
        [sys.executable, "-m", "spatial_engine"],
        input=f"{deep_request}\n{valid_request}\n",
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    lines = completed.stdout.splitlines()

    assert completed.returncode == 0
    assert len(lines) == 2
    assert len(lines[0].encode("utf-8")) < 2_048
    assert json.loads(lines[0])["error"]["code"] == -32700
    assert json.loads(lines[0])["id"] is None
    assert json.loads(lines[1]) == {"jsonrpc": "2.0", "id": 4, "result": {"closed": True}}


def test_persistent_stdio_nulls_invalid_ids_without_terminating(tmp_path: Path) -> None:
    overflow_id = '{"jsonrpc":"2.0","id":1e999,"method":"project.close","params":{}}'
    invalid_container_id = '{"jsonrpc":"2.0","id":[],"method":"project.close","params":{}}'
    valid_request = json.dumps({"jsonrpc": "2.0", "id": 5, "method": "project.close", "params": {}})
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(tmp_path / "local")
    completed = subprocess.run(
        [sys.executable, "-m", "spatial_engine"],
        input=f"{overflow_id}\n{invalid_container_id}\n{valid_request}\n",
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    lines = [json.loads(line) for line in completed.stdout.splitlines()]

    assert completed.returncode == 0
    assert len(lines) == 3
    assert lines[0]["id"] is None and "error" in lines[0]
    assert lines[1]["id"] is None and lines[1]["error"]["code"] == -32600
    assert lines[2] == {"jsonrpc": "2.0", "id": 5, "result": {"closed": True}}
