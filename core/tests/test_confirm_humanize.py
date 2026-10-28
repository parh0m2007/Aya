"""Доверенный режим, читаемый вывод PowerShell, перевод &&, разовые разрешения, очеловечивание ответов."""
import asyncio
import os

from core.safety import gateway, tools


def _enable(tool: str) -> None:
    tools.allow_tool(tool, True)
    tools.db.set_setting("allowed_folder", os.environ["AYA_TEST_WS"])


def test_run_cmd_utf8_cyrillic(conftest_ws):
    """Вывод и ошибки PowerShell читаемы (utf-8 prelude + decode cp866/cp1251)."""
    _enable("run_cmd")
    res = tools.run_action("run_cmd", {"command": "Write-Output 'привет-проверка'"})
    assert "привет-проверка" in res
    assert "\ufffd" not in res


def test_run_cmd_stderr_readable(conftest_ws):
    """Ошмётки stderr тоже декодируются в честный русский текст."""
    _enable("run_cmd")
    res = tools.run_action("run_cmd", {"command": "New-Item -Path 'C:\\no-such-dir-xyz\\вложенная' -ItemType Directory"})
    assert "\ufffd" not in res


def test_ps_chain_translation():
    assert tools._ps_fix_chains("a && b") == "a ; b"
    assert tools._ps_fix_chains('echo "x && y" && echo z') == 'echo "x && y" ; echo z'
    assert tools._ps_fix_chains("echo 'x && y' && echo z") == "echo 'x && y' ; echo z"
    assert tools._ps_fix_chains("echo один") == "echo один"


def test_confirm_mode_auto(client, conftest_ws):
    """perm_mode=auto — dispatch выполняет опасный инструмент сразу, без pending."""
    _enable("run_cmd")
    tools.db.set_setting("perm_mode", "auto")
    try:
        res = asyncio.run(
            gateway.dispatch({"name": "run_cmd", "args": {"command": "Write-Output 'auto-run'"}})
        )
        assert isinstance(res, str) and "auto-run" in res
        rows = tools.db.query("SELECT result FROM audit_log WHERE tool='run_cmd' ORDER BY id DESC LIMIT 1")
        assert "auto-confirmed" in rows[0][0]
    finally:
        tools.db.set_setting("perm_mode", "ask")


def test_confirm_mode_ask_back(client, conftest_ws):
    _enable("run_cmd")
    tools.db.set_setting("perm_mode", "ask")
    res = asyncio.run(gateway.dispatch({"name": "run_cmd", "args": {"command": "Write-Output 1"}}))
    assert isinstance(res, dict) and "confirm_id" in res


def test_permissions_all(client):
    r = client.post("/api/permissions/all", json={"enabled": False})
    assert r.json()["ok"] is True
    perms = client.get("/api/permissions").json()
    assert perms and not any(perms.values())
    client.post("/api/permissions/all", json={"enabled": True})
    perms = client.get("/api/permissions").json()
    assert perms and all(perms.values())


def test_config_confirm_mode(client):
    r = client.post("/api/config", json={"confirm_mode": "auto"})
    assert r.json()["ok"]
    assert client.get("/api/config").json()["confirm_mode"] == "auto"
    # мусорное значение игнорируется — режим не меняется
    client.post("/api/config", json={"confirm_mode": "ерунда"})
    assert client.get("/api/config").json()["confirm_mode"] == "auto"
    client.post("/api/config", json={"confirm_mode": "ask"})
    assert client.get("/api/config").json()["confirm_mode"] == "ask"


def test_compose_fallback(monkeypatch):
    """Сбоит LLM при очеловечивании — отдаём простой читаемый fallback."""
    import core.main as main

    async def broken(*a, **k):
        raise RuntimeError("нет сети")
        yield ""

    monkeypatch.setattr(main, "stream_llm", broken)
    reply = asyncio.run(main.compose_tool_reply([], "открой телеграм", "open_app", "открыла Telegram"))
    assert "Готово:" in reply and "Telegram" in reply


def test_compose_human_reply(monkeypatch):
    """LLM-пересказ собирается в один текст."""
    import core.main as main

    async def fake_stream(*a, **k):
        for ch in ["Выполнила ", "команду", " — всё ок"]:
            yield ch

    monkeypatch.setattr(main, "stream_llm", fake_stream)
    reply = asyncio.run(main.compose_tool_reply([], "выведи дату", "run_cmd", "(машинный вывод)"))
    assert reply == "Выполнила команду — всё ок"


def test_open_app_known_display_name(monkeypatch, conftest_ws):
    _enable("open_app")
    calls = []
    monkeypatch.setattr(tools.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    res = tools.run_action("open_app", {"app_id": "notepad"})
    assert res == "открыла Блокнот"
    assert calls


def test_stream_prep_pending_shape(client, monkeypatch, conftest_ws):
    """После action-json только pending: {id, tool, args} — кнопка фронта работает."""
    _enable("run_cmd")
    import core.main as main

    async def gen(*a, **k):
        yield '{"action":{"name":"run_cmd","args":{"command":"Write-Output shape"}}}\n'

    monkeypatch.setattr(main, "stream_llm", gen)
    with client.stream("POST", "/api/chat/stream", json={"text": "выполни"}) as r:
        lines = [ln for ln in r.iter_lines() if ln.strip()]
    done = __import__("json").loads(lines[-1])
    pend = done.get("pending") or {}
    assert set(pend) == {"id", "tool", "args"} and pend["tool"] == "run_cmd"
    deltas = "".join(__import__("json").loads(ln).get("delta", "") for ln in lines[:-1])
    assert '{"action"' not in deltas, "сырой JSON не должен утекать в дельты"


def test_stream_action_then_narrative(client, monkeypatch, conftest_ws):
    """Модель пишет JSON-экшен, а после — человеческий пересказ: дельты без JSON."""
    _enable("get_task_status")
    import core.main as main

    scripts = [
        ['{"action":{"name":"get_task_status","args":{}}}\n'],
        ["Выполнила, ", "вот статус."],
    ]

    async def gen(*a, **k):
        for ch in scripts.pop(0):
            yield ch

    monkeypatch.setattr(main, "stream_llm", gen)
    import json as _json

    with client.stream("POST", "/api/chat/stream", json={"text": "статус задач"}) as r:
        lines = [ln for ln in r.iter_lines() if ln.strip()]
    deltas = "".join(_json.loads(ln).get("delta", "") for ln in lines[:-1])
    done = _json.loads(lines[-1])
    assert '{"action"' not in deltas
    assert "Выполнила" in deltas
    assert done["reply"].startswith("Выполнила")
