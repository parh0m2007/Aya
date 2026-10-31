import json
import os
import secrets
import threading
import time

from . import tools, skills

PENDING: dict[str, dict] = {}
CONFIRM_TOOLS = {"write_allowed_file", "run_cmd", "ssh_run"}
CONFIRM_TTL = 600  # сколько секунд ждём кнопку подтверждения
# Делегируемые суб-агентам инструменты (остальное — только основная Ая)
FREE_TOOLS = {"remember_fact", "switch_agent", "list_skills", "load_skill", "recall", "tg_inbox", "tg_read", "focus_mode", "pomodoro", "music"}
# Опасные инструменты, которые суб-агент не может вызывать через delegate
DELEGATE_BLOCKED = {"write_allowed_file", "run_cmd", "ssh_run"}


def _agents_list() -> str:
    try:
        from .. import agents

        return ", ".join(a["name"] for a in agents.list_agents()) or "нет созданных"
    except Exception:
        return "нет созданных"


_DELEGATE_LOCK = threading.Lock()  # охрана карты блокировок
_AGENT_LOCKS: dict[str, threading.Lock] = {}


def _agent_lock(agent: str) -> threading.Lock:
    """Блокировка на личность: разные суб-агенты могут работать параллельно."""
    with _DELEGATE_LOCK:
        if agent not in _AGENT_LOCKS:
            _AGENT_LOCKS[agent] = threading.Lock()
        return _AGENT_LOCKS[agent]


def _read_core_config() -> dict:
    """~/.aya/config.json — единый конфиг с main (llm, llm_agents)."""
    import json as _json
    from pathlib import Path as _Path

    path = _Path.home() / ".aya" / "config.json"
    try:
        with open(path, encoding="utf-8-sig") as f:
            return _json.load(f)
    except (OSError, ValueError):
        return {}


def agents_adapters(cfg: dict):
    """Адаптеры для суб-агентов: llm_agents из config.json, fallback — основная LLM."""
    from ..llm.base import make_adapters, make_adapter

    out = []
    la = cfg.get("llm_agents")
    if isinstance(la, dict) and la:
        try:
            out.append(make_adapter(la))
        except Exception:
            pass
    return out or make_adapters(cfg)


async def _dispatch_delegating(name: str, args: dict) -> str:
    """Инструмент delegate {agent, task}: отдельная сессия суб-агента."""
    from .. import agents as agents_mod
    from ..llm.base import stream_llm

    agent_name = str(args.get("agent", "")).strip()
    task = str(args.get("task", "")).strip()
    if not agent_name or not task:
        return "нужно указать agent (имя личности) и task (что сделать)"
    if agent_name not in {a["name"] for a in agents_mod.list_agents()}:
        return f"нет такой личности: {agent_name}"
    lock = _agent_lock(agent_name)
    if not lock.acquire(blocking=False):
        return f"личность {agent_name} уже работает, подожди"
    try:
        cfg = _read_core_config()
        adapters = agents_adapters(cfg)
        if not adapters:
            return "LLM не сконфигурирована — суб-агент не запущен"
        instr = build_tool_instructions()
        started = time.time()

        async def collect(system, messages):
            return "".join([chunk async for chunk in stream_llm(adapters, system, messages)])

        result = await agents_mod.run_agent(agent_name, task, instr, collect, dispatch)
        tools.db.log_event("agent", f"delegate {agent_name}: {str(result)[:80]} ({time.time() - started:.1f}с)")
        return str(result)[:3000]
    finally:
        lock.release()


def _run_delegate_thread(agent_name: str, task: str, task_id: int):
    """Тело фоновой задачи delegate_bg: отдельный поток, отчёт в tasks."""
    from .. import agents as agents_mod
    from ..llm.base import stream_llm

    lock = _agent_lock(agent_name)
    if not lock.acquire(blocking=False):
        tools.db.set_task_status(task_id, "error", f"личность {agent_name} была занята — задача не выполнена")
        return
    tools.db.set_task_status(task_id, "running", "выполняется")
    try:
        cfg = _read_core_config()
        adapters = agents_adapters(cfg)
        if not adapters:
            tools.db.set_task_status(task_id, "error", "LLM не сконфигурирована — суб-агент не запущен")
            return

        async def collect(system, messages):
            return "".join([chunk async for chunk in stream_llm(adapters, system, messages)])

        import asyncio as _asyncio

        result = _asyncio.run(
            agents_mod.run_agent(agent_name, task, build_tool_instructions(), collect, dispatch)
        )
        tools.db.set_task_status(task_id, "done", str(result)[:400])
        tools.db.log_event("agent", f"delegate_bg {agent_name}: {str(result)[:80]}")
    except Exception as e:  # noqa: BLE001
        tools.db.set_task_status(task_id, "error", f"ошибка суб-агента: {str(e)[:200]}")
    finally:
        lock.release()


def _dispatch_delegate_bg(agent_name: str, task: str, report_minutes: int) -> str:
    """delegate_bg: суб-агент трудится сейчас в фоне, отчёт прилетит инициативой позже."""
    from .. import agents as agents_mod

    agent_name = agent_name.strip()
    task = task.strip()
    if not agent_name or not task:
        return "нужно указать agent (имя личности) и task (что сделать)"
    if agent_name not in {a["name"] for a in agents_mod.list_agents()}:
        return f"нет такой личности: {agent_name}"
    report_minutes = max(1, min(24 * 60, int(report_minutes) if report_minutes else 5))
    task_id = tools.db.add_task("report", {"agent": agent_name}, time.time() + report_minutes * 60)
    thread = threading.Thread(target=_run_delegate_thread, args=(agent_name, task[:4000], task_id), daemon=True)
    thread.start()
    tools.db.log_event("task", f"background delegate {agent_name}: {task[:60]} (отчёт ~{report_minutes} мин)")
    return (
        f"взяла в фоновую работу задачу для «{agent_name}» (задача #{task_id}); "
        f"как выполнится — расскажу тебе лично, отчёт в течение ~{report_minutes} мин. "
        "Можешь пока заниматься своими делами."
    )


def _rate_ok() -> bool:
    limit = int(tools.db.setting("perm_rate_limit", "12") or 12)
    row = tools.db.query(
        "SELECT COUNT(*) FROM audit_log WHERE ts>? AND allowed=1 AND tool!='remember_fact'",
        (time.time() - 60,),
    )
    return row[0][0] < limit


async def _run(name: str, args: dict):
    """Инструменты синхронные (сеть, скриншоты, поиск по файлам) — в поток,
    чтобы не замораживать event loop ядра (стрим, озвучку, поллинг UI)."""
    import asyncio

    return await asyncio.to_thread(tools.run_action, name, args)


async def dispatch(action: dict):  # str | dict(pending)
    name = action.get("name", "")
    args = action.get("args", {})
    if not isinstance(args, dict):
        args = {}
    if name == "delegate":
        return await _dispatch_delegating(name, args)
    if name == "delegate_bg":
        try:
            minutes = int(args.get("report_minutes", 5) or 5)
        except (TypeError, ValueError):
            minutes = 5
        return _dispatch_delegate_bg(
            str(args.get("agent", "")), str(args.get("task", "")), minutes
        )
    if name.startswith("mcp_"):
        import asyncio

        return await asyncio.to_thread(_dispatch_mcp, name, args)
    if name != "remember_fact" and not _rate_ok():
        tools.db.log_audit(name, str(args), "rate limit exceeded", False)
        return "лимит действий исчерпан на минуту, попробуй позже"
    if name == "remember_fact":
        res = tools.run_action(name, args)
        tools.db.log_audit(name, str(args), str(res)[:40], not res.startswith("need"))
        return res
    if name in FREE_TOOLS:
        res = await _run(name, args)
        tools.db.log_audit(name, str(args), str(res)[:40], True)
        return res
    if name not in tools.KNOWN_ACTIONS:
        return await _run(name, args)  # вернёт unknown action
    if not tools.is_tool_enabled(name):
        tools.db.log_audit(name, str(args), "permission not granted", False)
        return f"permission 'perm:{name}' not granted; asked user to enable in settings"
    if name == "tg_send":
        # режим черновиков: отправка идёт только после подтверждения юзером
        from .. import bridge_tguser

        if (
            bridge_tguser.has_session()
            and bridge_tguser.mode() == "draft"
            and tools.confirm_mode() != "auto"
        ):
            pid = secrets.token_urlsafe(8)
            PENDING[pid] = {"tool": name, "args": args, "ts": time.time()}
            tools.db.log_event("confirm", f"pending tg_send -> {str(args)[:100]}")
            return {"confirm_id": pid, "tool": name, "args": args}
    if name in CONFIRM_TOOLS:
        if name == "write_allowed_file":
            path = tools.resolve_allowed_path(args)
            if not (isinstance(path, str) and os.path.isabs(path)):
                tools.db.log_audit(name, str(args), str(path)[:60], False)
                return path  # строка-ошибка: имя или папка не годятся
            if os.path.exists(path) and not os.path.isfile(path):
                return "путь занят не файлом"
            # и новый файл, и перезапись — только после подтверждения (или в доверенном режиме)
        if tools.confirm_mode() == "auto":
            # доверенный режим: пользователь дал полное разрешение — выполняем сразу
            result = await _run(name, args)
            tools.db.log_audit(name, str(args), f"auto-confirmed: {str(result)[:60]}", True)
            return result
        pid = secrets.token_urlsafe(8)
        PENDING[pid] = {"tool": name, "args": args, "ts": time.time()}
        tools.db.log_event("confirm", f"pending {name} -> {str(args)[:100]}")
        return {"confirm_id": pid, "tool": name, "args": args}
    result = await _run(name, args)
    tools.db.log_audit(name, str(args), str(result)[:60], True)
    return result


def _dispatch_mcp(name: str, args: dict) -> str:
    """Вызов MCP-инструмента mcp_<server>_<tool>."""
    from .. import mcp

    server_name = name[4:].split("_", 1)[0]
    if not mcp.permitted(server_name):
        tools.db.log_audit(name, str(args), "permission not granted", False)
        return f"permission 'perm:mcp_{server_name}' not granted; предложи включить сервер {server_name} в настройках"
    try:
        result = mcp.REGISTRY.call_tool(name, args)
    except Exception as e:
        tools.db.log_audit(name, str(args), f"error: {str(e)[:40]}", False)
        return f"ошибка MCP-инструмента: {str(e)[:200]}"
    tools.db.log_audit(name, str(args), str(result).replace("\n", " ")[:60], True)
    tools.db.log_event("mcp", f"вызов {name}")
    return result


def resolve_confirm(pid: str, approve: bool):
    item = PENDING.pop(pid, None)
    if not item:
        return None
    if time.time() - item["ts"] > CONFIRM_TTL:
        tools.db.log_audit(item["tool"], str(item["args"]), "confirm expired", False)
        return {"applied": False, "result": "ожидание подтверждения истекло", "expired": True}
    if not approve:
        tools.db.log_audit(item["tool"], str(item["args"]), "user declined", False)
        return {"applied": False, "result": "действие отменено пользователем"}
    result = tools.run_action(item["tool"], item["args"])
    tools.db.log_audit(item["tool"], str(item["args"]), f"confirmed: {str(result)[:40]}", True)
    tools.db.log_event("confirm", f"confirmed {item['tool']} -> {str(result)[:80]}")
    return {"applied": True, "tool": item["tool"], "result": str(result)}


def pending_get(pid: str):
    item = PENDING.get(pid)
    if item and time.time() - item["ts"] > CONFIRM_TTL:
        PENDING.pop(pid, None)
        return None
    return item


MEMO_TEXT = (
    '{"action":{"name":"<инструмент>","args":{...}}}\n'
    "Инструменты и аргументы:\n"
    "- remember_fact {key: короткий ключ, value: значение до 200 символов} — доступно ВСЕГДА. "
    "Когда пользователь сообщает важное о себе (имя, работа, предпочтения, планы, близкие) — "
    "тихо сохрани это через remember_fact, не спрашивая разрешения и не перечисляя, что запомнила."
)


from .tool_text import build_tool_instructions  # noqa: E402,F401 — тексты инструментов
