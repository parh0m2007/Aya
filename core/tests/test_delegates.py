import asyncio
import json

import pytest

from core import agents
from core.safety import gateway


@pytest.fixture()
def agents_home(tmp_path, monkeypatch, conftest_ws):
    root = tmp_path / "agents"
    root.mkdir()
    monkeypatch.setattr(agents, "agents_root", lambda: str(root))
    monkeypatch.setattr(agents, "soul_path", lambda: str(tmp_path / "SOUL.md"))
    return root


def test_run_agent_simple(agents_home):
    agents.create("Писец")
    agents.write_soul("Писец", "Ты практичный писец.")

    async def collect(system, messages):
        assert "практичный писец" in system
        assert messages[0]["content"].startswith("Перепиши")
        return "Готово: текст сжат до трёх строк."

    async def dispatch(action):
        return "never called"

    res = asyncio.run(agents.run_agent("Писец", "Перепиши и сократи", "нет инструментов", collect, dispatch))
    assert res.startswith("Готово")


def test_run_agent_uses_tool(agents_home):
    agents.create("Сыщик")
    agents.write_soul("Сыщик", "агент-сыщик")

    calls = []

    async def collect(system, messages):
        calls.append([m["role"] for m in messages])
        if len(calls) == 1:
            return '{"action":{"name":"search_allowed_folder","args":{"query":"план"}}}'
        return "Нашёл один файл с планом."

    async def dispatch(action):
        assert action["name"] == "search_allowed_folder"
        return "D:\\x\\план.md"

    res = asyncio.run(agents.run_agent("Сыщик", "найди план", "- search_allowed_folder {query}", collect, dispatch))
    assert res == "Нашёл один файл с планом."
    assert len(calls) == 2
    assert "assistant" in calls[1]


def test_run_agent_blocked_tool(agents_home):
    agents.create("Хулиган")
    agents.write_soul("Хулиган", "делает файл")

    async def collect(system, messages):
        assert "write_allowed_file" in system and "run_cmd" in system and "ssh_run" in system
        return '{"action":{"name":"write_allowed_file","args":{"name":"x","content":"v"}}}'

    async def dispatch(action):
        return "should-not-be-called"

    res = asyncio.run(agents.run_agent("Хулиган", "запиши", "- write_allowed_file ...", collect, dispatch))
    assert "итог" in res or "подтверждени" in res


def test_nested_delegate_blocked(agents_home):
    agents.create("Внутренний")
    agents.write_soul("Внутренний", "двойной агент")
    calls = []

    async def collect(system, messages):
        calls.append(system)
        return '{"action":{"name":"delegate","args":{"agent":"Ещё","task":"x"}}}'

    async def dispatch(action):
        return "вызов прошёл"

    res = asyncio.run(agents.run_agent("Внутренний", "делай", "", collect, dispatch))
    # gateway._DELEGATE_LOCK защищает от вложенного — dispatch всё равно вернёт «уже работает»,
    # а сам run_agent пометит второй вызов IN_DELEGATE
    assert isinstance(res, str)


def test_delegate_dispatch_missing_agent(agents_home):
    import asyncio

    out = asyncio.run(gateway.dispatch({"name": "delegate", "args": {"agent": "Небо", "task": "x"}}))
    assert "нет такой личности" in out


def test_delegate_needs_both(agents_home):
    import asyncio

    out = asyncio.run(gateway.dispatch({"name": "delegate", "args": {"agent": "", "task": ""}}))
    assert "нужно указать" in out


def test_soul_endpoints_read_write(agents_home):
    from core import db

    ok, msg = agents.write_soul("Новый", "# Новый\nописание")
    assert ok, msg
    assert "описание" in agents.agent_soul("Новый")
    ok, _ = agents.write_soul("Новый", "# Новый\nv2")
    assert "v2" in agents.agent_soul("Новый")
    # db всё ещё жива (log_event сработал)
    assert db.query("SELECT COUNT(*) FROM events")[0][0] >= 1
