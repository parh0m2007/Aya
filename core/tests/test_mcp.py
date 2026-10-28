import json
import os
import subprocess
import sys
import time

import pytest

from core import mcp
from core.safety import gateway

MOCK_SERVER = """\
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method = msg.get("method")
    if method == "initialize":
        reply = {"jsonrpc": "2.0", "id": msg["id"],
                 "result": {"protocolVersion": "2025-06-18",
                            "serverInfo": {"name": "echo", "version": "1.0"}}}
    elif method == "tools/list":
        reply = {"jsonrpc": "2.0", "id": msg["id"],
                 "result": {"tools": [
                     {"name": "hello", "description": "поздоровайся",
                      "inputSchema": {"type": "object",
                                      "properties": {"who": {"type": "string"}}, "required": ["who"]}},
                     {"name": "calc", "description": "сложи два числа",
                      "inputSchema": {"type": "object",
                                      "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}}},
                 ]}}
    elif method == "tools/call":
        t = (msg.get("params") or {}).get("name")
        a = (msg.get("params") or {}).get("arguments") or {}
        if t == "hello":
            text = "привет, " + str(a.get("who", "?"))
        elif t == "calc":
            try:
                text = str(int(a["a"]) + int(a["b"]))
            except Exception:
                text = "нужны целые a и b"
        else:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "no tool"}}
            sys.stdout.write(json.dumps(reply) + "\\n")
            sys.stdout.flush()
            continue
        reply = {"jsonrpc": "2.0", "id": msg["id"],
                 "result": {"content": [{"type": "text", "text": text}]}}
    else:
        reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": method}}
    sys.stdout.write(json.dumps(reply) + "\\n")
    sys.stdout.flush()
"""


def _write_mock(tmp_path) -> str:
    path = tmp_path / "mock_mcp_server.py"
    path.write_text(MOCK_SERVER, encoding="utf-8")
    return str(path)


def _echo_cmd(script: str) -> dict:
    return {"command": sys.executable, "args": ["-u", script], "enabled": True}


@pytest.fixture()
def echo_registry(tmp_path, monkeypatch, conftest_ws):
    script = _write_mock(tmp_path)
    cfg = {"servers": {"echo": _echo_cmd(script)}}
    monkeypatch.setattr(mcp.Registry, "config", staticmethod(lambda: cfg))
    reg = mcp.Registry()
    monkeypatch.setattr(mcp, "REGISTRY", reg)
    yield reg
    reg.stop_all()


def config_registry(tmp_path, monkeypatch):
    script = _write_mock(tmp_path)
    cfg = {"servers": {"echo": _echo_cmd(script)}}
    monkeypatch.setattr(mcp.Registry, "config", staticmethod(lambda: cfg))
    reg = mcp.Registry()
    monkeypatch.setattr(mcp, "REGISTRY", reg)
    return reg


def test_handshake_and_tool_map(echo_registry):
    echo_registry.start_all(enabled_only=True, silent=True)
    assert echo_registry.connected["echo"]
    tm = echo_registry.tool_map()
    key = "mcp_echo_hello"
    assert key in tm
    server, tool, schema = tm[key]
    assert (server, tool) == ("echo", "hello")
    assert schema.get("required") == ["who"]


def test_call_tool(echo_registry):
    echo_registry.start_all(enabled_only=True, silent=True)
    assert echo_registry.call_tool("mcp_echo_hello", {"who": "Ая"}) == "привет, Ая"
    assert echo_registry.call_tool("mcp_echo_calc", {"a": 2, "b": 5}) == "7"


def test_call_unknown_tool(echo_registry):
    echo_registry.start_all(enabled_only=True, silent=True)
    with pytest.raises(mcp.McpError):
        echo_registry.call_tool("mcp_echo_nope", {})


def test_tool_instructions_contains_keys(echo_registry):
    echo_registry.start_all(enabled_only=True, silent=True)
    txt = mcp.tool_instructions()
    assert "mcp_echo_hello" in txt
    assert "who*: string" in txt or "who*: string" in txt.replace(" ", "")


def test_dispatch_mcp_requires_permission(echo_registry):
    echo_registry.start_all(enabled_only=True, silent=True)
    mcp.set_permitted("echo", False)
    res = gateway._dispatch_mcp("mcp_echo_hello", {"who": "x"})
    assert "permission" in res


def test_dispatch_mcp_calls(echo_registry):
    echo_registry.start_all(enabled_only=True, silent=True)
    mcp.set_permitted("echo", True)
    res = gateway._dispatch_mcp("mcp_echo_hello", {"who": "Ая"})
    assert res == "привет, Ая"


def test_disabled_server_not_spawned(tmp_path, monkeypatch):
    script = _write_mock(tmp_path)
    cfg = {"servers": {"off": {"command": sys.executable, "args": [script], "enabled": False}}}
    monkeypatch.setattr(mcp.Registry, "config", staticmethod(lambda: cfg))
    reg = mcp.Registry()
    reg.start_all(enabled_only=True, silent=True)
    assert not reg.servers
    tm = reg.tool_map()
    assert not tm
    reg.stop_all()


def test_validate_spec_rejects_bad_name():
    for name in ("", "Bad", "a b", "a" * 49):
        with pytest.raises(ValueError):
            mcp.validate_spec(name, {"command": "x"})


def test_validate_spec_normalizes(tmp_path):
    spec = mcp.validate_spec(
        "echo",
        {"command": " python ", "args": ["-u", "srv.py"], "env": {"K": "V"}, "cwd": str(tmp_path), "enabled": 1},
    )
    assert spec == {
        "command": "python",
        "args": ["-u", "srv.py"],
        "env": {"K": "V"},
        "cwd": str(tmp_path),
        "enabled": True,
    }


def test_save_rename_and_delete(tmp_path, monkeypatch, db_reset):
    cfg_path = tmp_path / "mcp.json"
    cfg_path.write_text('{"other": 1, "servers": {}}', encoding="utf-8")
    monkeypatch.setattr(mcp.Registry, "config_path", staticmethod(lambda: str(cfg_path)))

    spec = mcp.validate_spec("echo", {"command": sys.executable, "args": ["-u", "x.py"], "enabled": False})
    mcp.save_server("echo", spec)
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["other"] == 1 and "echo" in data["servers"]
    assert not (tmp_path / "mcp.json.tmp").exists()

    mcp.set_permitted("echo", True)
    mcp.save_server("echo2", spec, old_name="echo")
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "echo2" in data["servers"] and "echo" not in data["servers"]
    assert mcp.permitted("echo2") is True
    assert mcp.permitted("echo") is False

    assert mcp.delete_server("echo2") is True
    assert mcp.delete_server("echo2") is False
    assert json.loads(cfg_path.read_text(encoding="utf-8"))["servers"] == {}
    assert mcp.permitted("echo2") is False


def test_save_rename_requires_old_server(tmp_path, monkeypatch, db_reset):
    cfg_path = tmp_path / "mcp.json"
    cfg_path.write_text('{"servers": {}}', encoding="utf-8")
    monkeypatch.setattr(mcp.Registry, "config_path", staticmethod(lambda: str(cfg_path)))
    spec = mcp.validate_spec("new", {"command": "x", "enabled": False})
    with pytest.raises(ValueError):
        mcp.save_server("new", spec, old_name="missing")


def test_test_server_reports_tools(tmp_path):
    script = _write_mock(tmp_path)
    res = mcp.test_server("echo", {"command": sys.executable, "args": ["-u", script], "enabled": False})
    assert res["ok"] is True
    assert {t["name"] for t in res["tools"]} == {"hello", "calc"}


def test_test_server_reports_bad_command(tmp_path):
    res = mcp.test_server("bad", {"command": str(tmp_path / "nope.exe"), "enabled": False})
    assert res["ok"] is False
    assert res["error"]


def test_start_and_stop_registered(tmp_path, monkeypatch):
    script = _write_mock(tmp_path)
    reg = mcp.Registry()
    monkeypatch.setattr(mcp, "REGISTRY", reg)
    spec = mcp.validate_spec("echo", {"command": sys.executable, "args": ["-u", script], "enabled": True})

    assert mcp.start_registered("echo", spec) == ""
    assert reg.connected["echo"] is True
    assert "mcp_echo_hello" in reg.tool_map()

    mcp.stop_registered("echo")
    assert reg.connected["echo"] is False
    assert reg.servers == {}


def test_start_registered_reports_error(tmp_path, monkeypatch):
    reg = mcp.Registry()
    monkeypatch.setattr(mcp, "REGISTRY", reg)
    spec = mcp.validate_spec("bad", {"command": str(tmp_path / "nope.exe"), "enabled": True})

    err = mcp.start_registered("bad", spec)
    assert err
    assert reg.connected["bad"] is False
    assert reg.errors["bad"] == err
    assert reg.servers == {}


@pytest.fixture
def mcp_cfg(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    path.write_text('{"servers": {}}', encoding="utf-8")
    monkeypatch.setattr(mcp.Registry, "config_path", staticmethod(lambda: str(path)))
    mcp.REGISTRY.stop_all()
    mcp.REGISTRY.errors.clear()
    yield path
    mcp.REGISTRY.stop_all()
    mcp.REGISTRY.errors.clear()


def test_api_get_includes_env_cwd(client, mcp_cfg, tmp_path):
    mcp_cfg.write_text(json.dumps({
        "servers": {"echo": {"command": "python", "args": [], "env": {"K": "V"}, "cwd": str(tmp_path), "enabled": False}}
    }), encoding="utf-8")
    d = client.get("/api/mcp").json()
    assert d["echo"]["env"] == {"K": "V"}
    assert d["echo"]["cwd"] == str(tmp_path)


def test_api_save_rename_delete(client, mcp_cfg, db_reset):
    body = {"name": "echo", "command": sys.executable, "args": ["-u", "x.py"], "env": {}, "cwd": "", "enabled": False}
    assert client.post("/api/mcp/save", json=body).json()["ok"] is True
    assert "echo" in json.loads(mcp_cfg.read_text(encoding="utf-8"))["servers"]

    mcp.set_permitted("echo", True)
    renamed = {**body, "name": "echo2", "old_name": "echo"}
    assert client.post("/api/mcp/save", json=renamed).json()["ok"] is True
    data = json.loads(mcp_cfg.read_text(encoding="utf-8"))["servers"]
    assert "echo2" in data and "echo" not in data
    assert mcp.permitted("echo2") is True
    assert mcp.permitted("echo") is False

    assert client.post("/api/mcp/delete", json={"name": "echo2"}).json()["ok"] is True
    assert json.loads(mcp_cfg.read_text(encoding="utf-8"))["servers"] == {}


def test_api_save_invalid_name_400(client, mcp_cfg):
    r = client.post("/api/mcp/save", json={"name": "Bad Name", "command": "x", "enabled": False})
    assert r.status_code == 400


def test_api_normalizes_names(client, mcp_cfg, monkeypatch, db_reset):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        mcp,
        "start_registered",
        lambda name, spec: (calls.append(("start", name)) or ""),
    )
    monkeypatch.setattr(mcp, "stop_registered", lambda name: calls.append(("stop", name)))

    body = {"name": " echo ", "command": "x", "enabled": True}
    assert client.post("/api/mcp/save", json=body).json()["ok"] is True
    assert "echo" in json.loads(mcp_cfg.read_text(encoding="utf-8"))["servers"]
    assert calls == [("start", "echo")]

    calls.clear()
    renamed = {**body, "name": " echo2 ", "old_name": " echo "}
    assert client.post("/api/mcp/save", json=renamed).json()["ok"] is True
    data = json.loads(mcp_cfg.read_text(encoding="utf-8"))["servers"]
    assert "echo2" in data and "echo" not in data
    assert calls == [("stop", "echo"), ("start", "echo2")]

    calls.clear()
    assert client.post("/api/mcp/delete", json={"name": " echo2 "}).json()["ok"] is True
    assert calls == [("stop", "echo2")]


def test_api_save_enabled_starts_server(client, mcp_cfg, tmp_path, db_reset):
    script = _write_mock(tmp_path)
    body = {"name": "echo", "command": sys.executable, "args": ["-u", script], "env": {}, "cwd": "", "enabled": True}
    res = client.post("/api/mcp/save", json=body).json()
    assert res["ok"] is True and res["error"] == ""
    assert mcp.REGISTRY.connected["echo"] is True
    assert client.post("/api/mcp/delete", json={"name": "echo"}).json()["ok"] is True
    assert mcp.REGISTRY.servers == {}


def test_api_test_endpoint(client, mcp_cfg, tmp_path):
    script = _write_mock(tmp_path)
    body = {"name": "echo", "command": sys.executable, "args": ["-u", script], "env": {}, "cwd": "", "enabled": False}
    res = client.post("/api/mcp/test", json=body).json()
    assert res["ok"] is True
    assert {t["name"] for t in res["tools"]} == {"hello", "calc"}


def test_api_save_start_error_keeps_config(client, mcp_cfg, tmp_path, db_reset):
    body = {"name": "bad", "command": str(tmp_path / "nope.exe"), "args": [], "env": {}, "cwd": "", "enabled": True}
    res = client.post("/api/mcp/save", json=body).json()
    assert res["ok"] is True
    assert res["error"]
    data = json.loads(mcp_cfg.read_text(encoding="utf-8"))["servers"]
    assert data["bad"]["enabled"] is True
    status = client.get("/api/mcp").json()
    assert status["bad"]["error"]
