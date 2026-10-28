"""Оркестрация 2.0 (пер-агентные локи, бюджет, фоновые задачи + отчёты) и Discord-мост."""
import json
import time

from core import db, tasks
from core import agents as agents_mod
from core.safety import gateway, tools


def test_delegate_budget_raised():
    assert agents_mod.DELEGATE_BUDGET_STEPS >= 5


def test_per_agent_locks():
    assert gateway._agent_lock("A") is gateway._agent_lock("A")
    assert gateway._agent_lock("A") is not gateway._agent_lock("B")


def test_delegate_blocked_for_subagent():
    assert "delegate_bg" in agents_mod.DELEGATE_BLOCKED_TOOLS
    assert "screen_look" in agents_mod.DELEGATE_BLOCKED_TOOLS


def test_delegate_bg_flow(monkeypatch, tmp_path, conftest_ws):
    """Фоновая задача: следующая за dispatch тред пишет 'done' + результат в tasks."""
    monkeypatch.setattr(agents_mod, "agents_root", lambda: str(tmp_path / "agents"))
    agents_mod.create("ТестыБГ")

    async def fake_run(name, task, instr, collect, dispatch_fn):
        return "итог фоновой задачи: всё собрано"

    monkeypatch.setattr(agents_mod, "run_agent", fake_run)
    # чтобы agents_adapters не смотрел в реальный конфиг — вернём пусто, но fake_run всё равно вернёт
    monkeypatch.setattr(gateway, "agents_adapters", lambda cfg: ["fake-adapter"])

    res = gateway._dispatch_delegate_bg("ТестыБГ", "собрать итоги", 1)
    assert "взяла" in res

    deadline = time.time() + 8
    status, result = "", ""
    while time.time() < deadline:
        rows = tools.db.query("SELECT status,result FROM tasks WHERE kind='report' ORDER BY id DESC LIMIT 1")
        if rows:
            status, result = rows[0][0], rows[0][1]
            if status in ("done", "error"):
                break
        time.sleep(0.2)
    assert status == "done", result
    assert "итог фоновой задачи" in result


def test_delegate_bg_unknown_agent(conftest_ws):
    res = gateway._dispatch_delegate_bg("нет-такой", "задача", 1)
    assert "нет такой личности" in res


def test_report_task_runner_initiative(conftest_ws):
    tid = db.add_task("report", {"agent": "Писец"}, time.time() + 5)
    db.set_task_status(tid, "done", "всё собрано: 3 файла, аномалий нет")
    tasks._run_task(tid, "report", {"agent": "Писец"})
    force = db.setting("initiative_force", "")
    assert force.startswith("report:")
    assert "всё собрано" in force
    rows = db.query("SELECT status FROM tasks WHERE id=?", (tid,))
    assert rows[0][0] == "reported"


def test_report_task_runner_still_running(conftest_ws):
    tid = db.add_task("report", {"agent": "Писец"}, time.time() + 5)
    db.set_task_status(tid, "running", "выполняется")
    tasks._run_task(tid, "report", {"agent": "Писец"})
    rows = db.query("SELECT status, due_at FROM tasks WHERE id=?", (tid,))
    status = rows[0][0]
    assert status == "pending", "задача должна остаться в очереди"
    assert rows[0][1] > time.time() - 1, "из-за повторного срока"


def test_discord_config_roundtrip(monkeypatch, tmp_path):
    from core import bridge_discord

    monkeypatch.setattr(bridge_discord, "config_path", lambda: str(tmp_path / "config.json"))
    bridge_discord.save_config("tok123", "555", True)
    cfg = bridge_discord.load_config()
    assert cfg == {"token": "tok123", "channel_id": "555", "enabled": True}
    st = bridge_discord.BRIDGE.status()
    assert st["enabled"] is True and st["has_token"] is True
    assert st["running"] is False  # в тестах не стартует реальный клиент


def test_discord_api_endpoints(client, monkeypatch, tmp_path):
    from core import bridge_discord

    monkeypatch.setattr(bridge_discord, "config_path", lambda: str(tmp_path / "config.json"))
    r = client.post("/api/discord", json={"token": "abc", "channel_id": "77", "enabled": False})
    assert r.json()["channel_id"] == "77"
    r2 = client.get("/api/discord")
    assert r2.json()["has_token"] is True and r2.json()["enabled"] is False


def test_llm_vision_roundtrip(client, monkeypatch, tmp_path):
    import core.main as main

    monkeypatch.setattr(main, "CONFIG_FILE", tmp_path / "config.json")
    r = client.post(
        "/api/llm",
        json={"llm_vision": {"type": "openai_compatible", "base_url": "https://api.y/v1", "model": "vlm", "api_key": "ky"}},
    )
    assert r.json()["ok"] is True
    d = client.get("/api/llm").json()
    assert d["llm_vision"]["model"] == "vlm"
    assert d["llm_vision"]["api_key_set"] is True
