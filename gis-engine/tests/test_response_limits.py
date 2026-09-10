import json

from spatial_engine import __main__ as entry


def test_oversize_response_returns_bounded_error_and_next_response_survives(monkeypatch, capsys):
    monkeypatch.setattr(entry, "MAX_RESPONSE_BYTES", 512, raising=False)
    entry._write_response({"jsonrpc": "2.0", "id": 8, "result": {"text": "x" * 4096}})
    entry._write_response({"jsonrpc": "2.0", "id": 9, "result": {"ok": True}})
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2 and len(lines[0].encode("utf-8")) <= 512
    error = json.loads(lines[0])
    assert error["id"] == 8 and error["error"]["data"]["kind"] == "response_too_large"
    assert json.loads(lines[1]) == {"jsonrpc": "2.0", "id": 9, "result": {"ok": True}}
