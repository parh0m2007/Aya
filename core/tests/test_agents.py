import os

import pytest

from core import agents
from core.safety import gateway, tools


@pytest.fixture()
def agents_home(tmp_path, monkeypatch, conftest_ws):
    root = tmp_path / "agents"
    root.mkdir()
    soul = tmp_path / "SOUL.md"
    monkeypatch.setattr(agents, "agents_root", lambda: str(root))
    monkeypatch.setattr(agents, "soul_path", lambda: str(soul))
    return root


def _agent(root, name: str, soul: str):
    d = root / name
    d.mkdir()
    (d / "SOUL.md").write_text(soul, encoding="utf-8")


def test_list_and_cre(agents_home):
    assert agents.list_agents() == []
    ok, msg = agents.create("Соня")
    assert ok, msg
    _agent(agents_home, "Игривая", "# Игривая\n(шуткует)")
    names = [a["name"] for a in agents.list_agents()]
    assert "Соня" in names and "Игривая" in names


def test_safe_name(agents_home):
    assert agents.safe_name("good-name_1") == "good-name_1"
    assert agents.safe_name("bad/name") is None
    assert agents.safe_name("") is None
    assert agents.safe_name("x" * 50) is None


def test_activate_and_persona_block(agents_home):
    _agent(agents_home, "Игривая", "Ты — Игривая: шутки, лёгкость.")
    ok, _ = agents.set_active("Игривая")
    assert ok
    assert agents.active() == "Игривая"
    block = agents.persona_block()
    assert "Игривая" in block
    agents.set_active("")
    assert agents.active() == ""
    assert "ТЫ СЕЙЧАС" not in agents.persona_block()


def test_activate_unknown(agents_home):
    ok, msg = agents.set_active("НетТакой")
    assert not ok and "нет" in msg


def test_switch_agent_tool(agents_home):
    _agent(agents_home, "Игривая", "Ты — Игривая.")
    res = tools.run_action("switch_agent", {"agent": "Игривая"})
    assert "Игривая" in res
    assert agents.active() == "Игривая"
    res = tools.run_action("switch_agent", {"agent": ""})
    assert "основной" in res
    agents.set_active("")


def test_gateway_dispatch_switch_agent(agents_home):
    _agent(agents_home, "Соня", "Ты — Соня.")
    agents.set_active("")
    res = gateway._dispatch_mcp("mcp_nope_x", {})  # не влияет; проверим прямой путь
    assert "MCP" in res or "mcp" in res

    import asyncio

    out = asyncio.run(gateway.dispatch({"name": "switch_agent", "args": {"agent": "Соня"}}))
    assert "Соня" in str(out)
    assert agents.active() == "Соня"
    out = asyncio.run(gateway.dispatch({"name": "switch_agent", "args": {}}))
    assert "основной" in str(out)
    agents.set_active("")


def test_prompting_includes_agent(agents_home):
    from core.prompting import build_system_prompt

    _agent(agents_home, "Игривая", "Ты — Игривая: шутки, лёгкость.")
    agents.set_active("Игривая")
    prompt = build_system_prompt({"mood": "calm"})
    assert "Игривая" in prompt
    agents.set_active("")


def test_main_soul_in_prompt(agents_home):
    from core.prompting import build_system_prompt

    (agents_home.parent / "SOUL.md").write_text("Люблю короткие ответы.", encoding="utf-8") if False else None
    import pathlib

    pathlib.Path(agents.soul_path()).write_text("Люблю короткие ответы.", encoding="utf-8")
    prompt = build_system_prompt({"mood": "calm"})
    assert "короткие ответы" in prompt
