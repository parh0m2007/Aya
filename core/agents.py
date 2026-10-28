"""Агенты (M4): суб-личности из ~/.aya/agents/<name>/SOUL.md.

Структура:
  ~/.aya/agents/<имя>/SOUL.md     — инструкция личности (кратко, до 2500 символов)
  ~/.aya/agents/<имя>/skills.json — {"delegate": true, "tools": [...]} (опционально)

Активная личность: db setting agent_persona ('' = основная Ая).
Главная душа: ~/.aya/SOUL.md подмешивается всегда.

Оркестратор (M4+): основная Ая вызывает суб-агента инструментом
delegate {agent, task} — отдельная LLM-сессия со своим SOUL; суб-агент
может пользоваться инструментами через общий гатвей (без write_allowed_file
и без вложенного delegate). Результат — короткая сводка.
"""
import contextvars
import json as _json
import os
import re
import threading


SOUL_MAX = 2500
AGENT_FILE = "SOUL.md"
SKILLS_FILE = "skills.json"


def agents_root() -> str:
    return os.path.join(os.path.expanduser("~"), ".aya", "agents")


def soul_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".aya", "SOUL.md")


def _read_head(path: str, limit: int = SOUL_MAX) -> str:
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read(limit).strip()
    except OSError:
        return ""


def list_agents() -> list[dict]:
    root = agents_root()
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    for name in names:
        soul = os.path.join(root, name, AGENT_FILE)
        if not os.path.isfile(soul) or not os.path.isdir(os.path.join(root, name)):
            continue
        head = _read_head(soul, 200)
        out.append({"name": name, "preview": head[:120], "has_skills": os.path.isfile(os.path.join(root, name, SKILLS_FILE))})
    return out


def safe_name(name: str) -> str | None:
    name = str(name).strip()
    if not name or len(name) > 40:
        return None
    if not re.fullmatch(r"[A-Za-zА-Яа-я0-9_ -]+", name):
        return None
    return name


def agent_soul(name: str) -> str:
    if not safe_name(name):
        return ""
    return _read_head(os.path.join(agents_root(), name, AGENT_FILE))


def main_soul() -> str:
    return _read_head(soul_path())


def active() -> str:
    from . import db

    return db.setting("agent_persona", "")


def set_active(name: str) -> tuple[bool, str]:
    from . import db

    if not name:
        db.set_setting("agent_persona", "")
        return True, "вернулась к основной личности"
    if agent_soul(name):
        db.set_setting("agent_persona", name)
        return True, f"переключилась на {name}"
    return False, f"нет такой личности: {name}"


def create(name: str, soul_text: str = "") -> tuple[bool, str]:
    from . import db

    name = safe_name(name) or ""
    if not name:
        return False, "недопустимое имя"
    folder = os.path.join(agents_root(), name)
    os.makedirs(folder, exist_ok=True)
    soul = os.path.join(folder, AGENT_FILE)
    if not os.path.exists(soul):
        default = soul_text.strip() or (
            f"# {name}\n\nТы — {name}, альтернативная личность Аи.\n"
            "Сохрани общий стиль: русская речь, краткость, тёплость, без наигранности.\n"
            "Добавь сюда свой характер и манеру.\n"
        )
        with open(soul, "w", encoding="utf-8", newline="\n") as f:
            f.write(default[:SOUL_MAX] + "\n")
    db.log_event("agent", f"создана личность {name}")
    return True, f"создана папка ~/.aya/agents/{name} с SOUL.md"


def skills_for(name: str) -> dict:
    """~/.aya/agents/<name>/skills.json → dict (или {})."""
    if not safe_name(name):
        return {}
    try:
        with open(os.path.join(agents_root(), name, SKILLS_FILE), encoding="utf-8-sig") as f:
            data = _json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_soul(name: str, soul_text: str) -> tuple[bool, str]:
    """Перезаписывает SOUL.md созданной личности (или создаёт её)."""
    name = safe_name(name) or ""
    if not name:
        return False, "недопустимое имя"
    folder = os.path.join(agents_root(), name)
    os.makedirs(folder, exist_ok=True)
    if not os.path.exists(os.path.join(folder, AGENT_FILE)) and not soul_text.strip():
        return False, "личности нет, а текст пустой"
    with open(os.path.join(folder, AGENT_FILE), "w", encoding="utf-8", newline="\n") as f:
        f.write(soul_text[:SOUL_MAX])
    from . import db

    db.log_event("agent", f"обновлён SOUL.md {name}")
    return True, "сохранила"


def persona_block() -> str:
    """Кусок для системного промпта: главная душа + активная личность."""
    blocks = []
    main = main_soul()
    if main:
        blocks.append("Завет пользователя (если противоречит базовому стилю — приоритет у него):\n" + main)
    act = active()
    if act:
        soul = agent_soul(act)
        if soul:
            blocks.append(f"ТЫ СЕЙЧАС В ЛИЧНОСТИ «{act}». Следуй инструкции:\n" + soul)
    return "\n\n".join(blocks)


__all__ = [
    "list_agents",
    "agent_soul",
    "main_soul",
    "active",
    "set_active",
    "create",
    "persona_block",
    "safe_name",
    "agents_root",
    "soul_path",
    "AGENT_FILE",
    "SOUL_MAX",
    "skills_for",
    "run_agent",
]


# ── суб-агенты: delegate ──────────────────────────────────────
IN_DELEGATE = contextvars.ContextVar("aya_in_delegate", default=False)
DELEGATE_BLOCKED_TOOLS = {
    "write_allowed_file", "run_cmd", "ssh_run", "delegate", "delegate_bg", "screen_look",
    "recall", "tg_send", "tg_read", "tg_inbox", "focus_mode",
}
DELEGATE_BUDGET_STEPS = 5


def _agent_system(soul: str, tool_instructions: str) -> str:
    return (
        f"{persona_base()}"
        f"Ты — суб-агент. Твоя личность и инструкция:\n{soul}\n\n"
        "Ты часть большую миссию: главный ассистент делегировал тебе задачу. "
        "Работай этой задачей, не общайся «за жизнь». Пользователь тебя не видит."
        "Сначала, если нужно подействовать инструментом, ответь строго JSON-строкой одной строкой "
        '{"action":{"name":"<инструмент>","args":{...}}}.\n'
        "Когда задача решена — верни короткий итог текстом (1–5 предложений), БЕЗ JSON.\n"
        "Вложенный delegate и delegate_bg тебе недоступны; write_allowed_file, run_cmd, ssh_run и screen_look недоступны.\n\n"
        "Инструменты:\n" + tool_instructions
    )


def persona_base() -> str:
    from . import db

    style = db.setting("persona_style", "")
    if not style:
        from .prompting import DEFAULT_SYSTEM

        style = DEFAULT_SYSTEM
    return style.split("Текущее состояние:")[0].strip() + "\n\n"


async def run_agent(name: str, task: str, tool_instructions: str, stream_collect, dispatch_fn) -> str:
    """Отдельная LLM-сессия суб-агента: до DELEGATE_BUDGET_STEPS шагов.

    stream_collect(system, messages) -> str  (полный текст ответа)
    dispatch_fn(action_dict) -> str|dict     (gateway.dispatch — те же права/rate/audit)
    Итог: текст суб-агента (без JSON-экшенов).
    """
    from . import db

    if IN_DELEGATE.get():
        return "вложенный delegate запрещён"
    soul = agent_soul(name)
    if not soul:
        return f"нет такой личности: {name}"
    state = {"system": _agent_system(soul, tool_instructions), "messages": [{"role": "user", "content": task[:4000]}]}
    token = IN_DELEGATE.set(True)
    try:
        for _step in range(DELEGATE_BUDGET_STEPS):
            db.log_event("agent", f"{name}: шаг {_step + 1}")
            raw = await stream_collect(state["system"], state["messages"])
            raw = raw.strip()
            if raw.lstrip().startswith('{"action"'):
                try:
                    parsed = _json.loads(raw.lstrip())
                    action = parsed.get("action") or {}
                except _json.JSONDecodeError:
                    action = None
                if action and action.get("name") in DELEGATE_BLOCKED_TOOLS:
                    tool_result = "этот инструмент суб-агенту недоступен, верни текстовый итог"
                elif action:
                    tool_result = await dispatch_fn(action)
                    if isinstance(tool_result, dict) and "confirm_id" in tool_result:
                        tool_result = "действие требует подтверждения пользователя в основном окне; скажи об этом в итоговом тексте"
                else:
                    tool_result = "не разобрал JSON-экшен; ответь обычным текстом"
                state["messages"].append({"role": "assistant", "content": raw[:1000]})
                state["messages"].append({"role": "user", "content": f"Результат инструмента: {str(tool_result)[:3000]}\nПродолжай или дай финальный текстовый итог."})
                continue
            return raw or "(суб-агент вернул пусто)"
        return "(суб-агент исчерпал бюджет шагов, верни его частичный итог)"
    finally:
        IN_DELEGATE.reset(token)
