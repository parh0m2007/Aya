# Уведомления, трей, MCP-редактор и свободные руки — план реализации

> **Для агентов:** ОБЯЗАТЕЛЬНЫЙ СУБ-СКИЛЛ: `superpowers:subagent-driven-development` (рекомендовано) или `superpowers:executing-plans`. Шаги помечены `- [ ]`.

**Goal:** Сделать Аю удобнее в фоне: показывать тосты об инициативах, когда окно скрыто/свёрнуто или включена «Тишина»; управлять «свободными руками» и тишиной из трея; редактировать stdio MCP-серверы из консоли; после голосового ответа Ая ещё несколько секунд слушает, чтобы продолжить разговор без Ctrl+Space.

**Architecture:** Четыре независимых слоя. (1) Python: настройки follow-up в `/api/config`, гейт legacy-инициатив в `state.initiative_check`, чистые помощники MCP и тонкие API-эндпоинты. (2) Чистые TypeScript-помощники с vitest: решение о тосте, окно follow-up, парсинг MCP-формы. (3) Веб-консоль: форма MCP и настройки follow-up. (4) Rust/Tauri: пункты трея, синхронизация чекбоксов, плагин уведомлений; `main.ts` связывает настройки, VAD, TTS-пампа, тосты и трей.

**Tech Stack:** TypeScript/Vite/vitest, Python/FastAPI/pytest, Rust/Tauri 2.12.4, `tauri-plugin-notification` 2, WebView2, PowerShell 5.1.

**Spec:** `docs/superpowers/specs/2026-10-23-notifications-tray-mcp-followup-design.md` (утверждён 2026-10-23, коммит `53fec68`).

## Global Constraints

- Работаем прямо в `D:\Aya` на master, без worktree. Пользователь тестирует живьём.
- `start-aya.cmd` — dev-лаунчер, `start-aya-prod.cmd` — прод. Не смешивать: общий профиль WebView2 `dev.aya.companion` и один глобальный хоткей; одновременно dev и prod не запускать.
- PowerShell 5.1: не использовать `&&`; вывод pytest не редиректить в файл (UTF-16). Для bash-скриптов — `C:\Program Files\Git\bin\bash.exe`.
- TDD: сначала падающий тест, затем минимальная реализация, затем полный прогон и коммит.
- Русские тексты и комментарии — в стиле проекта.
- Не ломать существующее: PTT/хоткей Ctrl+Space, barge-in, режим `auto`, инициативы, MCP `playwright`, релизный лаунчер.
- Новых зависимостей, кроме `tauri-plugin-notification` и `@tauri-apps/plugin-notification`, нет.
- MCP только stdio; HTTP/SSE не делаем. Секреты в `mcp.json` хранятся локально в открытом виде.
- Уведомления работают только пока приложение запущено; клик по тосту в v1 ничего не открывает.
- Follow-up по умолчанию выключен, 6 с, диапазон 2–30. Включается/выключается, длительность редактируется.
- Тесты до изменений: pytest 178 passed, `cargo test` 1/1. После — все зелёные.

## Review Focus

1. **Тост и речь не дублируются неправильно:** окно видно + тишина выключена → только речь; скрыто/свёрнуто или тишина включена → тост + речь. Отказ в разрешении на уведомления не ломает приложение. Тест `shouldToast`/`toastBody` (Task 5), интеграция — Task 12.
2. **MCP-конфиг не теряется и не ломается:** rename переносит permission, существующий `playwright` остаётся, ошибка старта не откатывает сохранение, `mcp.json` пишется атомарно. Тесты Task 3–4.
3. **Свободные руки не взводятся после текста, `auto`, `pending`, пустого ответа, без озвучки или после `speakAbort`.** Truth-table `shouldArmFollowUp` (Task 6), интеграционные проверки Task 11.
4. **Нет «залипшего» микрофона:** пустая расшифровка/ошибка STT/тишина/скрытие/закрытие панели/«Стоп»/ручной микрофон останавливают цикл; запись follow-up ограничена 30 с. Тесты окна + чек-лист Task 11.
5. **Трей и консоль не расходятся:** чекбоксы синхронизируются после загрузки и после переключений; при ошибке POST чекбокс возвращается к фактическому значению. Ручная проверка Task 12.

---

### Task 1: Python — настройки свободных рук в `/api/config`

**Files:**
- Modify: `core/main.py` (`VoiceIn`, `get_config`, `set_config`, строки ~1027–1060)
- Test: `core/tests/test_voice_followup.py` (создать)

**Interfaces:**
- Produces: `GET /api/config` → `followup: bool`, `followup_sec: int`; `POST /api/config` принимает `followup?: bool`, `followup_sec?: int`.
- Defaults: `voice_followup = "0"`, `voice_followup_sec = "6"`; clamp 2–30; битое значение в БД → 6.

- [ ] **Step 1: Написать падающий тест** — создать `core/tests/test_voice_followup.py`:

```python
def test_followup_defaults(client):
    cfg = client.get("/api/config").json()
    assert cfg["followup"] is False
    assert cfg["followup_sec"] == 6


def test_followup_config_roundtrip_and_clamp(client):
    assert client.post("/api/config", json={"followup": True, "followup_sec": 12}).json()["ok"] is True
    cfg = client.get("/api/config").json()
    assert cfg["followup"] is True
    assert cfg["followup_sec"] == 12

    client.post("/api/config", json={"followup_sec": 99})
    assert client.get("/api/config").json()["followup_sec"] == 30

    client.post("/api/config", json={"followup_sec": 1})
    assert client.get("/api/config").json()["followup_sec"] == 2

    client.post("/api/config", json={"followup": False})
    assert client.get("/api/config").json()["followup"] is False
```

- [ ] **Step 2: Убедиться, что тест падает**:

```powershell
core\.venv\Scripts\python.exe -m pytest core\tests\test_voice_followup.py -q
```

Ожидание: `KeyError`/проверка `followup` падает — полей ещё нет.

- [ ] **Step 3: Реализовать**

В `VoiceIn` добавить:

```python
    followup: bool | None = None
    followup_sec: int | None = None
```

Перед `get_config` добавить helper:

```python
def _followup_seconds() -> int:
    try:
        value = int(db.setting("voice_followup_sec", "6"))
    except ValueError:
        value = 6
    return max(2, min(30, value))
```

В `get_config` добавить:

```python
        "followup": db.setting("voice_followup", "0") == "1",
        "followup_sec": _followup_seconds(),
```

В `set_config` добавить:

```python
    if body.followup is not None:
        db.set_setting("voice_followup", "1" if body.followup else "0")
    if body.followup_sec is not None:
        db.set_setting("voice_followup_sec", str(max(2, min(30, body.followup_sec))))
```

- [ ] **Step 4: Прогнать тест и весь Python-набор**

```powershell
core\.venv\Scripts\python.exe -m pytest core\tests\test_voice_followup.py -q
core\.venv\Scripts\python.exe -m pytest -q
```

Ожидание: новый тест зелёный, весь набор зелёный.

- [ ] **Step 5: Коммит**

```powershell
git add core/main.py core/tests/test_voice_followup.py
git commit -m "настройки: свободные руки — вкл/выкл и длительность"
```

---

### Task 2: Python — «Тишина» глушит legacy-инициативы

**Files:**
- Modify: `core/behavior/state.py` (`initiative_check`, строки ~63–100)
- Test: `core/tests/test_initiative_focus.py` (создать)

**Interfaces:**
- Produces: `BehaviorState.initiative_check(now: float | None = None) -> str`; без аргумента работает как раньше.
- Gate: после принудительных `initiative_force`, но до `proactive.rules()` и legacy-фолбэков, если `focus_mode == "1"` или `proactive_enabled != "1"` → `""`.

- [ ] **Step 1: Написать падающий тест** — создать `core/tests/test_initiative_focus.py`:

```python
import time

import pytest

from core import db
from core.behavior import proactive
from core.behavior.state import BehaviorState

NOON = time.mktime((2026, 10, 23, 12, 0, 0, 0, 0, -1))


@pytest.fixture
def behavior(db_reset, monkeypatch):
    monkeypatch.setattr(proactive, "rules", lambda *a, **k: "")
    st = BehaviorState()
    st.attention = "own"
    st.energy = 0.1
    st._last_initiative = 0.0
    st._last_user_seen = NOON - 7200
    return st


def test_focus_suppresses_legacy_fallback(behavior):
    db.set_setting("focus_mode", "1")
    assert behavior.initiative_check(now=NOON) == ""


def test_proactive_disabled_suppresses_legacy_fallback(behavior):
    db.set_setting("focus_mode", "0")
    db.set_setting("proactive_enabled", "0")
    assert behavior.initiative_check(now=NOON) == ""


def test_legacy_fallback_works_when_enabled(behavior):
    db.set_setting("focus_mode", "0")
    db.set_setting("proactive_enabled", "1")
    assert behavior.initiative_check(now=NOON) == "welcome_back"


def test_forced_reminder_passes_focus(behavior):
    db.set_setting("focus_mode", "1")
    db.set_setting("initiative_force", "напоминание")
    assert behavior.initiative_check(now=NOON) == "напоминание"
    assert db.setting("initiative_force", "") == ""
```

- [ ] **Step 2: Убедиться, что тесты падают**

```powershell
core\.venv\Scripts\python.exe -m pytest core\tests\test_initiative_focus.py -q
```

Ожидание: `initiative_check()` не принимает `now`, а legacy-фолбэк возвращает `welcome_back` при тишине.

- [ ] **Step 3: Реализовать**

В `initiative_check`:

```python
    def initiative_check(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        forced = db.setting("initiative_force", "")
        # ... блок forced: заменить time.time() на now ...
        if forced:
            db.set_setting("initiative_force", "")
            with self._lock:
                self._last_initiative = now
            return forced

        # «Тишина» и выключенная проактивность глушат и legacy-фолбэки.
        if db.setting("focus_mode", "0") == "1" or db.setting("proactive_enabled", "1") != "1":
            return ""

        topic = proactive.rules(now)
        if topic:
            with self._lock:
                self._last_initiative = now
            return topic

        lt = time.localtime(now)
        # ... остальные проверки: заменить time.time() на now ...
```

Важно: в блоке `forced.startswith("tg:retry:")` сравнение `time.time() < due` тоже заменить на `now < due`; `proactive.rules(now)` принимает время (у функции есть параметр `now`).

- [ ] **Step 4: Прогнать тесты**

```powershell
core\.venv\Scripts\python.exe -m pytest core\tests\test_initiative_focus.py -q
core\.venv\Scripts\python.exe -m pytest -q
```

Ожидание: все три теста зелёные; остальной набор не сломан.

- [ ] **Step 5: Коммит**

```powershell
git add core/behavior/state.py core/tests/test_initiative_focus.py
git commit -m "инициативы: «Тишина» глушит legacy-фолбэки"
```

---

### Task 3: Python — помощники MCP (валидация, сохранение, удаление, проверка)

**Files:**
- Modify: `core/mcp.py` (добавить `re`; helper-функции после `REGISTRY`)
- Test: `core/tests/test_mcp.py` (дополнить в конце)

**Interfaces:**
- `validate_spec(name: str, spec: dict) -> dict` — нормализованный спек; `ValueError` с русским текстом.
- `write_config(cfg: dict) -> None` — атомарно: `path + ".tmp"`, `json.dump(ensure_ascii=False, indent=2)`, `flush`, `fsync`, `os.replace`.
- `save_server(name: str, spec: dict, old_name: str = "") -> dict` — create/update/rename; при rename переносит `perm:mcp_*`; `ValueError` при конфликте/отсутствии старого.
- `delete_server(name: str) -> bool` — удаляет из конфига и стирает permission; `False`, если сервера нет.
- `test_server(name: str, spec: dict) -> dict` — временный `McpServer`; возвращает `{ok, tools, error}`, всегда останавливает процесс.
- `start_registered(name: str, spec: dict) -> str` — остановить старый, запустить новый, обновить `REGISTRY`; вернуть текст ошибки или `""`.
- `stop_registered(name: str) -> None` — остановить, `connected=False`, очистить `errors`.

**Правила валидации:**
- Имя: `^[a-z0-9][a-z0-9-]{0,47}$`.
- `command`: strip, непустая строка.
- `args`: список строк (иначе `ValueError`), strip каждого.
- `env`: объект `str → str`.
- `cwd`: strip; если задан — `os.path.isdir`, иначе `ValueError`.
- `enabled`: `bool(spec.get("enabled", False))`.
- Нормализованный вид: `command`, `args`, `enabled`; `env` и `cwd` добавляются только если непустые.

- [ ] **Step 1: Написать падающие тесты** — дописать в `core/tests/test_mcp.py`:

```python
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
```

- [ ] **Step 2: Убедиться, что тесты падают**

```powershell
core\.venv\Scripts\python.exe -m pytest core\tests\test_mcp.py -q
```

Ожидание: `AttributeError: module 'core.mcp' has no attribute 'validate_spec'` и т.п.

- [ ] **Step 3: Реализовать helper-функции**

Добавить `import re` в шапку `core/mcp.py`. После `REGISTRY = Registry()` добавить:

```python
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


def validate_spec(name: str, spec: dict) -> dict:
    name = str(name or "").strip()
    if not NAME_RE.match(name):
        raise ValueError("имя: 1–48 символов, латиница в нижнем регистре, цифры и дефис")
    if not isinstance(spec, dict):
        raise ValueError("описание сервера должно быть объектом")
    command = str(spec.get("command") or "").strip()
    if not command:
        raise ValueError("команда не может быть пустой")
    raw_args = spec.get("args") or []
    if not isinstance(raw_args, list) or any(not isinstance(a, str) for a in raw_args):
        raise ValueError("аргументы — список строк")
    raw_env = spec.get("env") or {}
    if not isinstance(raw_env, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in raw_env.items()):
        raise ValueError("env — объект строка→строка")
    cwd = str(spec.get("cwd") or "").strip()
    if cwd and not os.path.isdir(cwd):
        raise ValueError("рабочая папка не существует")
    norm = {
        "command": command,
        "args": [a.strip() for a in raw_args],
        "enabled": bool(spec.get("enabled", False)),
    }
    if raw_env:
        norm["env"] = {str(k): str(v) for k, v in raw_env.items()}
    if cwd:
        norm["cwd"] = cwd
    return norm


def write_config(cfg: dict) -> None:
    path = Registry.config_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save_server(name: str, spec: dict, old_name: str = "") -> dict:
    name = str(name or "").strip()
    norm = validate_spec(name, spec)
    old = str(old_name or name).strip()
    cfg = Registry.config()
    servers = cfg.setdefault("servers", {})
    if old != name:
        if name in servers:
            raise ValueError(f"сервер {name} уже существует")
        if old not in servers:
            raise ValueError(f"нет сервера {old}")
        old_perm = db.setting(f"perm:mcp_{old}", "0")
        del servers[old]
        servers[name] = norm
        write_config(cfg)
        db.set_setting(f"perm:mcp_{name}", old_perm)
        db.execute("DELETE FROM settings WHERE key=?", (f"perm:mcp_{old}",))
    else:
        servers[name] = norm
        write_config(cfg)
    return norm


def delete_server(name: str) -> bool:
    name = str(name or "").strip()
    cfg = Registry.config()
    servers = cfg.get("servers") or {}
    if name not in servers:
        return False
    del servers[name]
    write_config(cfg)
    db.execute("DELETE FROM settings WHERE key=?", (f"perm:mcp_{name}",))
    return True


def test_server(name: str, spec: dict) -> dict:
    norm = validate_spec(name, spec)
    srv = McpServer(name, norm["command"], norm["args"], norm.get("env"), norm.get("cwd"))
    try:
        srv.start()
        srv.initialize()
        tools = srv.load_tools()
        return {
            "ok": True,
            "tools": [
                {"name": str(t.get("name", "")), "description": (t.get("description") or "")[:140]}
                for t in tools
            ],
            "error": "",
        }
    except Exception as e:
        return {"ok": False, "tools": [], "error": str(e)}
    finally:
        srv.stop()


def start_registered(name: str, spec: dict) -> str:
    stop_registered(name)
    srv = McpServer(name, str(spec["command"]), [str(a) for a in spec.get("args", [])],
                    spec.get("env") or {}, spec.get("cwd"))
    try:
        srv.start()
        srv.initialize()
        srv.tools = srv.load_tools()
    except Exception as e:
        srv.stop()
        REGISTRY.connected[name] = False
        REGISTRY.errors[name] = str(e)
        return str(e)
    REGISTRY.servers[name] = srv
    REGISTRY.connected[name] = True
    REGISTRY.errors.pop(name, None)
    return ""


def stop_registered(name: str) -> None:
    srv = REGISTRY.servers.pop(name, None)
    if srv:
        srv.stop()
    REGISTRY.connected[name] = False
    REGISTRY.errors.pop(name, None)
```

- [ ] **Step 4: Прогнать тесты**

```powershell
core\.venv\Scripts\python.exe -m pytest core\tests\test_mcp.py -q
core\.venv\Scripts\python.exe -m pytest -q
```

Ожидание: старые и новые MCP-тесты зелёные.

- [ ] **Step 5: Коммит**

```powershell
git add core/mcp.py core/tests/test_mcp.py
git commit -m "mcp: помощники сохранения, удаления и проверки серверов"
```

---

### Task 4: Python — MCP API (`save`, `delete`, `test`, `GET` с env/cwd)

**Files:**
- Modify: `core/main.py` (импорт `Field`; `GET /api/mcp`; модели и эндпоинты после `mcp_toggle`)
- Test: `core/tests/test_mcp.py` (дополнить)

**Interfaces:**
- `GET /api/mcp` каждый сервер: `command`, `args`, `env: dict`, `cwd: str`, `enabled`, `permitted`, `connected`, `error`, `tools`.
- `POST /api/mcp/save` body: `{name, old_name?, command, args, env, cwd, enabled}` → `{ok: true, enabled: bool, error: str}`; ошибка валидации → 400 `{detail}`.
- `POST /api/mcp/delete` body `{name}` → `{ok: true}`; нет сервера → 404.
- `POST /api/mcp/test` body: `{name, command, args, env, cwd}` → `{ok, tools, error}`; ошибка валидации → 400.
- `/api/mcp/toggle` сохраняет контракт, но использует `write_config`/`start_registered`/`stop_registered` и не оставляет процесс при ошибке старта.

- [ ] **Step 1: Написать падающие API-тесты** — дописать в `core/tests/test_mcp.py`:

```python
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
```

- [ ] **Step 2: Убедиться, что тесты падают**

```powershell
core\.venv\Scripts\python.exe -m pytest core\tests\test_mcp.py -q
```

Ожидание: 404/405 на новых путях, в `GET` нет `env`/`cwd`.

- [ ] **Step 3: Реализовать API**

В импорт Pydantic добавить `Field`:

```python
from pydantic import BaseModel, Field
```

В `mcp_status` в словарь сервера добавить:

```python
            "env": {str(k): str(v) for k, v in spec.get("env", {}).items()} if isinstance(spec, dict) and isinstance(spec.get("env"), dict) else {},
            "cwd": str(spec.get("cwd") or "") if isinstance(spec, dict) else "",
```

Отдельно заменить существующую строку:

```python
            "error": status.get(name, {}).get("error", ""),
```

на:

```python
            "error": status.get(name, {}).get("error", "") or mcp.REGISTRY.errors.get(name, ""),
```

Ошибка старта нового сервера живёт в `REGISTRY.errors`, даже если процесса нет в `REGISTRY.servers`.

После `mcp_toggle` добавить модели:

```python
class McpServerIn(BaseModel):
    name: str
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str = ""
    enabled: bool = False


class McpSaveIn(McpServerIn):
    old_name: str = ""


class McpDeleteIn(BaseModel):
    name: str
```

Эндпоинты:

```python
@app.post("/api/mcp/save")
async def mcp_save(body: McpSaveIn):
    try:
        spec = mcp.validate_spec(body.name, body.model_dump())
        mcp.save_server(body.name, spec, body.old_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    old = (body.old_name or "").strip()
    if old and old != body.name:
        mcp.stop_registered(old)

    error = ""
    if spec["enabled"]:
        error = mcp.start_registered(body.name, spec)
    else:
        mcp.stop_registered(body.name)
    db.log_event("mcp", f"сохранён сервер {body.name}")
    return {"ok": True, "enabled": spec["enabled"], "error": error}


@app.post("/api/mcp/delete")
async def mcp_delete(body: McpDeleteIn):
    if not mcp.delete_server(body.name):
        raise HTTPException(status_code=404, detail=f"нет сервера {body.name}")
    mcp.stop_registered(body.name)
    db.log_event("mcp", f"удалён сервер {body.name}")
    return {"ok": True}


@app.post("/api/mcp/test")
async def mcp_test(body: McpServerIn):
    try:
        return mcp.test_server(body.name, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

`mcp_toggle` переписать на helpers, сохранив ответ:

```python
@app.post("/api/mcp/toggle")
async def mcp_toggle(body: McpToggleIn):
    cfg = mcp.REGISTRY.config()
    servers = cfg.setdefault("servers", {})
    spec = servers.get(body.server)
    if not isinstance(spec, dict):
        raise HTTPException(status_code=404, detail=f"нет сервера {body.server} в mcp.json")
    spec["enabled"] = not spec.get("enabled", False)
    try:
        norm = mcp.validate_spec(body.server, spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    mcp.write_config(cfg)
    if spec["enabled"]:
        error = mcp.start_registered(body.server, norm)
        db.log_event("mcp", f"подключён сервер {body.server}" if not error else f"ошибка сервера {body.server}: {error}")
    else:
        mcp.stop_registered(body.server)
        error = ""
        db.log_event("mcp", f"отключён сервер {body.server}")
    return {"ok": True, "enabled": spec["enabled"], "error": error}
```

Примечание: `mcp.start_registered` уже останавливает старый процесс и чистит его при ошибке; отдельный `srv` в эндпоинте не нужен.

- [ ] **Step 4: Прогнать тесты**

```powershell
core\.venv\Scripts\python.exe -m pytest core\tests\test_mcp.py -q
core\.venv\Scripts\python.exe -m pytest -q
```

Ожидание: все API/MCP-тесты зелёные.

- [ ] **Step 5: Коммит**

```powershell
git add core/main.py core/tests/test_mcp.py
git commit -m "mcp: API сохранения, удаления и проверки серверов"
```

---

### Task 5: TS — решение о тосте и обрезка текста

**Files:**
- Create: `src/notify-decision.ts`
- Test: `src/notify-decision.test.ts`

**Interfaces:**
- `shouldToast(hidden: boolean, focus: boolean): boolean` → `hidden || focus`.
- `toastBody(text: string, limit = 240): string` — схлопывает whitespace, обрезает, добавляет `…`.

- [ ] **Step 1: Написать падающий тест** — создать `src/notify-decision.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { shouldToast, toastBody } from "./notify-decision";

describe("shouldToast", () => {
  it("тост при скрытом окне", () => {
    expect(shouldToast(true, false)).toBe(true);
  });
  it("тост в тишине", () => {
    expect(shouldToast(false, true)).toBe(true);
  });
  it("без тоста, когда Ая видна и тишина выключена", () => {
    expect(shouldToast(false, false)).toBe(false);
  });
  it("тост при обоих условиях", () => {
    expect(shouldToast(true, true)).toBe(true);
  });
});

describe("toastBody", () => {
  it("схлопывает пробелы", () => {
    expect(toastBody("  привет \n мир ")).toBe("привет мир");
  });
  it("обрезает до 240 символов", () => {
    const s = toastBody("я".repeat(250));
    expect(s.length).toBe(240);
    expect(s.endsWith("…")).toBe(true);
  });
  it("короткий текст не трогает", () => {
    expect(toastBody("привет")).toBe("привет");
  });
});
```

- [ ] **Step 2: Убедиться, что тест падает**

```powershell
npx vitest run src/notify-decision.test.ts
```

Ожидание: модуль не найден.

- [ ] **Step 3: Реализовать** — `src/notify-decision.ts`:

```ts
export function shouldToast(hidden: boolean, focus: boolean): boolean {
  return hidden || focus;
}

export function toastBody(text: string, limit = 240): string {
  const clean = text.replace(/\s+/g, " ").trim();
  if (clean.length <= limit) return clean;
  return clean.slice(0, Math.max(0, limit - 1)).trimEnd() + "…";
}
```

- [ ] **Step 4: Прогнать тесты**

```powershell
npx vitest run src/notify-decision.test.ts
npm test
```

- [ ] **Step 5: Коммит**

```powershell
git add src/notify-decision.ts src/notify-decision.test.ts
git commit -m "уведомления: решение о тосте и обрезка текста"
```

---

### Task 6: TS — окно свободных рук и решение о взведении

**Files:**
- Create: `src/followup.ts`
- Test: `src/followup.test.ts`

**Interfaces:**
- `FOLLOWUP_MIN = 2`, `FOLLOWUP_MAX = 30`, `FOLLOWUP_DEFAULT = 6`.
- `clampFollowupSec(value: unknown): number` — нечисло → 6, округление, clamp 2–30.
- `FollowUpWindow`: `arm(seconds, now?)`, `active(now?)`, `remaining(now?)`, `expire(now?)`, `cancel()`.
- `ArmDecision`: `enabled`, `voiceTurn`, `mode`, `panelOpen`, `hidden`, `busy`, `pending`, `reply`, `spoke`, `aborted`.
- `shouldArmFollowUp(d): boolean` — все условия должны быть выполнены; `mode !== "auto"`.

- [ ] **Step 1: Написать падающий тест** — создать `src/followup.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { clampFollowupSec, FollowUpWindow, shouldArmFollowUp, type ArmDecision } from "./followup";

describe("clampFollowupSec", () => {
  it("зажимает диапазон 2–30", () => {
    expect(clampFollowupSec(6)).toBe(6);
    expect(clampFollowupSec(1)).toBe(2);
    expect(clampFollowupSec(99)).toBe(30);
    expect(clampFollowupSec("12")).toBe(12);
    expect(clampFollowupSec("x")).toBe(6);
    expect(clampFollowupSec(Number.NaN)).toBe(6);
    expect(clampFollowupSec(6.6)).toBe(7);
  });
});

describe("FollowUpWindow", () => {
  it("взводится, считает остаток и истекает", () => {
    const w = new FollowUpWindow();
    w.arm(6, 1000);
    expect(w.active(1000)).toBe(true);
    expect(w.remaining(1000)).toBe(6);
    expect(w.active(6999)).toBe(true);
    expect(w.remaining(6999)).toBe(1);
    expect(w.active(7000)).toBe(false);
    expect(w.remaining(7000)).toBe(0);
    expect(w.expire(7000)).toBe(true);
    expect(w.active(7000)).toBe(false);
  });

  it("cancel и повторное взведение", () => {
    const w = new FollowUpWindow();
    w.arm(6, 1000);
    w.cancel();
    expect(w.active(1000)).toBe(false);
    w.arm(30, 2000);
    expect(w.remaining(2000)).toBe(30);
  });
});

const base: ArmDecision = {
  enabled: true,
  voiceTurn: true,
  mode: "button",
  panelOpen: true,
  hidden: false,
  busy: false,
  pending: false,
  reply: "ответ",
  spoke: true,
  aborted: false,
};

describe("shouldArmFollowUp", () => {
  it("все условия выполнены", () => {
    expect(shouldArmFollowUp(base)).toBe(true);
  });
  it("не взводится после текста, в auto, без панели, скрыто", () => {
    expect(shouldArmFollowUp({ ...base, voiceTurn: false })).toBe(false);
    expect(shouldArmFollowUp({ ...base, mode: "auto" })).toBe(false);
    expect(shouldArmFollowUp({ ...base, panelOpen: false })).toBe(false);
    expect(shouldArmFollowUp({ ...base, hidden: true })).toBe(false);
  });
  it("не взводится при busy/pending/пустом ответе/без озвучки/abort", () => {
    expect(shouldArmFollowUp({ ...base, busy: true })).toBe(false);
    expect(shouldArmFollowUp({ ...base, pending: true })).toBe(false);
    expect(shouldArmFollowUp({ ...base, reply: "   " })).toBe(false);
    expect(shouldArmFollowUp({ ...base, spoke: false })).toBe(false);
    expect(shouldArmFollowUp({ ...base, aborted: true })).toBe(false);
  });
  it("не взводится, если функция выключена", () => {
    expect(shouldArmFollowUp({ ...base, enabled: false })).toBe(false);
  });
});
```

- [ ] **Step 2: Убедиться, что тест падает**

```powershell
npx vitest run src/followup.test.ts
```

- [ ] **Step 3: Реализовать** — `src/followup.ts`:

```ts
export const FOLLOWUP_MIN = 2;
export const FOLLOWUP_MAX = 30;
export const FOLLOWUP_DEFAULT = 6;

export type VoiceMode = "button" | "auto" | "hotkey";

export function clampFollowupSec(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return FOLLOWUP_DEFAULT;
  return Math.max(FOLLOWUP_MIN, Math.min(FOLLOWUP_MAX, Math.round(n)));
}

export class FollowUpWindow {
  private deadline = 0;

  arm(seconds: number, now = Date.now()): void {
    this.deadline = now + clampFollowupSec(seconds) * 1000;
  }

  active(now = Date.now()): boolean {
    return this.deadline > 0 && now < this.deadline;
  }

  remaining(now = Date.now()): number {
    if (this.deadline <= 0) return 0;
    return Math.max(0, Math.ceil((this.deadline - now) / 1000));
  }

  expire(now = Date.now()): boolean {
    if (this.deadline > 0 && now >= this.deadline) {
      this.deadline = 0;
      return true;
    }
    return false;
  }

  cancel(): void {
    this.deadline = 0;
  }
}

export interface ArmDecision {
  enabled: boolean;
  voiceTurn: boolean;
  mode: VoiceMode;
  panelOpen: boolean;
  hidden: boolean;
  busy: boolean;
  pending: boolean;
  reply: string;
  spoke: boolean;
  aborted: boolean;
}

export function shouldArmFollowUp(d: ArmDecision): boolean {
  return (
    d.enabled &&
    d.voiceTurn &&
    d.mode !== "auto" &&
    d.panelOpen &&
    !d.hidden &&
    !d.busy &&
    !d.pending &&
    !d.aborted &&
    d.spoke &&
    d.reply.trim().length > 0
  );
}
```

- [ ] **Step 4: Прогнать тесты**

```powershell
npx vitest run src/followup.test.ts
npm test
```

- [ ] **Step 5: Коммит**

```powershell
git add src/followup.ts src/followup.test.ts
git commit -m "свободные руки: окно ожидания и правило взведения"
```

---

### Task 7: TS — чистые помощники MCP-формы

**Files:**
- Create: `src/mcp-editor.ts`
- Test: `src/mcp-editor.test.ts`

**Interfaces:**
- `McpForm`: `name`, `command`, `argsText`, `envText`, `cwd`, `enabled` (все строки/boolean).
- `McpServerPayload`: `name`, `command`, `args`, `env`, `cwd`, `enabled`.
- `McpSavePayload extends McpServerPayload`: `old_name`.
- `parseArgs(text): string[]` — по одному аргументу в строке, пустые строки и пробелы по краям отбрасываются.
- `parseEnv(text): { env: Record<string,string>; errors: string[] }` — `KEY=VALUE`, значение может содержать `=`; ошибки для строк без `=` или с пустым ключом.
- `formatArgs`, `formatEnv` — обратное преобразование.
- `validateServerName(name): string | null`.
- `buildServerPayload(form): McpServerPayload` — бросает `Error` с русским текстом.
- `buildSavePayload(form, oldName?): McpSavePayload`.

- [ ] **Step 1: Написать падающий тест** — создать `src/mcp-editor.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { buildSavePayload, buildServerPayload, formatArgs, formatEnv, parseArgs, parseEnv, validateServerName } from "./mcp-editor";

describe("parseArgs", () => {
  it("по одному аргументу в строке, пустые пропускает", () => {
    expect(parseArgs("npx\n  -y \n\n@playwright/mcp@latest")).toEqual(["npx", "-y", "@playwright/mcp@latest"]);
  });
});

describe("parseEnv", () => {
  it("KEY=VALUE, значение может содержать =", () => {
    expect(parseEnv("A=1\n\nB=x=y\n C = 2 ")).toEqual({
      env: { A: "1", B: "x=y", C: "2" },
      errors: [],
    });
  });
  it("битые строки попадают в errors", () => {
    const r = parseEnv("BAD\n=x\nOK=1");
    expect(r.env).toEqual({ OK: "1" });
    expect(r.errors).toHaveLength(2);
  });
});

describe("format", () => {
  it("roundtrip", () => {
    expect(parseArgs(formatArgs(["a", "b"]))).toEqual(["a", "b"]);
    expect(parseEnv(formatEnv({ A: "1", B: "x=y" }))).toEqual({ env: { A: "1", B: "x=y" }, errors: [] });
  });
});

describe("validateServerName", () => {
  it("разрешает латиницу, цифры и дефис", () => {
    expect(validateServerName("playwright")).toBeNull();
    expect(validateServerName("my-server-2")).toBeNull();
  });
  it("отклоняет пустое, пробелы, uppercase и длинное", () => {
    expect(validateServerName("")).not.toBeNull();
    expect(validateServerName("Bad")).not.toBeNull();
    expect(validateServerName("a b")).not.toBeNull();
    expect(validateServerName("a".repeat(49))).not.toBeNull();
  });
});

describe("build payload", () => {
  const form = {
    name: "echo",
    command: "python",
    argsText: "-u\nsrv.py",
    envText: "K=V",
    cwd: "C:\\tmp",
    enabled: true,
  };
  it("собирает сервер и save-полезную нагрузку", () => {
    expect(buildServerPayload(form)).toEqual({
      name: "echo",
      command: "python",
      args: ["-u", "srv.py"],
      env: { K: "V" },
      cwd: "C:\\tmp",
      enabled: true,
    });
    expect(buildSavePayload(form, "old").old_name).toBe("old");
  });
  it("бросает понятные ошибки", () => {
    expect(() => buildServerPayload({ ...form, envText: "BAD" })).toThrow(/KEY=VALUE/);
    expect(() => buildServerPayload({ ...form, command: "  " })).toThrow(/команда/);
    expect(() => buildServerPayload({ ...form, name: "Bad" })).toThrow(/имя/);
  });
});
```

- [ ] **Step 2: Убедиться, что тест падает**

```powershell
npx vitest run src/mcp-editor.test.ts
```

- [ ] **Step 3: Реализовать** — `src/mcp-editor.ts`:

```ts
export interface McpForm {
  name: string;
  command: string;
  argsText: string;
  envText: string;
  cwd: string;
  enabled: boolean;
}

export interface McpServerPayload {
  name: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  cwd: string;
  enabled: boolean;
}

export interface McpSavePayload extends McpServerPayload {
  old_name: string;
}

export function parseArgs(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function formatArgs(args: string[]): string {
  return args.join("\n");
}

export function parseEnv(text: string): { env: Record<string, string>; errors: string[] } {
  const env: Record<string, string> = {};
  const errors: string[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) {
      errors.push(`строка окружения должна быть KEY=VALUE: ${line}`);
      continue;
    }
    const key = line.slice(0, eq).trim();
    if (!key) {
      errors.push(`пустой ключ в строке: ${line}`);
      continue;
    }
    env[key] = line.slice(eq + 1).trim();
  }
  return { env, errors };
}

export function formatEnv(env: Record<string, string>): string {
  return Object.entries(env)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
}

export function validateServerName(name: string): string | null {
  const n = name.trim();
  if (!n) return "имя не может быть пустым";
  if (!/^[a-z0-9][a-z0-9-]{0,47}$/.test(n)) {
    return "имя: 1–48 символов, латиница в нижнем регистре, цифры и дефис";
  }
  return null;
}

export function buildServerPayload(form: McpForm): McpServerPayload {
  const name = form.name.trim();
  const nameError = validateServerName(name);
  if (nameError) throw new Error(nameError);
  const command = form.command.trim();
  if (!command) throw new Error("команда не может быть пустой");
  const { env, errors } = parseEnv(form.envText);
  if (errors.length) throw new Error(errors.join("; "));
  return {
    name,
    command,
    args: parseArgs(form.argsText),
    env,
    cwd: form.cwd.trim(),
    enabled: form.enabled,
  };
}

export function buildSavePayload(form: McpForm, oldName = ""): McpSavePayload {
  return { ...buildServerPayload(form), old_name: oldName.trim() };
}
```

- [ ] **Step 4: Прогнать тесты**

```powershell
npx vitest run src/mcp-editor.test.ts
npm test
```

- [ ] **Step 5: Коммит**

```powershell
git add src/mcp-editor.ts src/mcp-editor.test.ts
git commit -m "консоль: чистые помощники MCP-формы"
```

---

### Task 8: Консоль — редактор MCP-серверов

**Files:**
- Modify: `src/console.ts` (`McpState`, `mcpCard`, строки ~652–692; импорт helper-ов вверху)
- Modify: `src/console.css` (стили формы, если нужно)
- Test: чистые помощники уже покрыты в Task 7; UI проверяется вручную.

**UI:**
- Список серверов: имя, статус, инструменты, переключатель permitted, кнопки «изменить», «проверить», «удалить», «включить/выключить».
- Кнопка «Добавить сервер» над/под списком.
- Форма: имя, команда, аргументы (textarea), env (textarea), рабочая папка, «включён», кнопки «сохранить», «проверить», «отмена»; при редактировании — «удалить».
- Ошибки показываются в `hint-box` формы; сохранение не откатывается при ошибке старта (сервер сохраняется, ошибка видна).

- [ ] **Step 1: Добавить импорт**

В `src/console.ts`:

```ts
import { buildSavePayload, buildServerPayload, formatArgs, formatEnv, type McpForm } from "./mcp-editor";
```

- [ ] **Step 2: Расширить тип и переписать `mcpCard`**

`McpState`:

```ts
type McpState = {
  command: string;
  args: string[];
  env?: Record<string, string>;
  cwd?: string;
  enabled: boolean;
  permitted: boolean;
  connected: boolean;
  error: string;
  tools: { key: string; name: string; description: string }[];
};
```

Структура `mcpCard`:

```ts
function mcpCard(): HTMLElement {
  const card = el("div", "card");
  card.append(el("h3", "sec-title", "MCP-серверы (~/.aya/mcp.json)"));
  const list = el("ul", "set-list");
  const hint = el("div", "hint-box", "Сервер запускает команду на твоём ПК; секреты хранятся локально в открытом виде");
  const editor = el("div", "mcp-editor");
  editor.classList.add("hidden");
  let servers: Record<string, McpState> = {};
  let editing: string | null = null;

  const load = async () => {
    list.innerHTML = "";
    try {
      servers = await fetchJson<Record<string, McpState>>("/api/mcp");
    } catch {
      servers = {};
    }
    const names = Object.keys(servers);
    if (!names.length) {
      list.append(el("li", "hint-box", "Серверов нет — добавь первый"));
      return;
    }
    for (const [name, s] of Object.entries(servers)) {
      const li = el("li", "mcp-item");
      const head = el("div", "mcp-line");
      const dotCls = s.connected ? "ok" : s.enabled && s.error ? "err" : "off";
      head.append(
        el("span", "mcp-name", name),
        el("span", "dim", s.connected ? `${s.tools.length} инстр.` : ""),
        switchEl(s.permitted, async (v) => {
          await postJson(`/api/mcp/perm/${encodeURIComponent(name)}`, { enabled: v });
        }),
      );
      const sub = el("div", "mcp-sub");
      sub.textContent = s.connected
        ? s.tools.map((t) => t.name).join(", ") || "нет инструментов"
        : s.error
          ? `ошибка: ${s.error}`
          : "выключен";
      const controls = el("div", "mcp-line");
      controls.append(
        actionBtn("изменить", async () => openEditor(name)),
        actionBtn("проверить", async () => {
          const res = await postJson("/api/mcp/test", {
            name,
            command: s.command,
            args: s.args,
            env: s.env || {},
            cwd: s.cwd || "",
            enabled: s.enabled,
          });
          sub.textContent = res.ok
            ? `инструменты: ${(res.tools as { name: string }[] | undefined)?.map((t) => t.name).join(", ") || "нет"}`
            : `ошибка: ${String(res.detail || res.error || "не удалось")}`;
        }),
        actionBtn("удалить", async () => {
          if (!confirm(`Удалить сервер ${name}?`)) return;
          await postJson("/api/mcp/delete", { name });
          if (editing === name) closeEditor();
          await load();
        }),
        actionBtn(s.enabled ? "выключить" : "включить", async () => {
          await postJson("/api/mcp/toggle", { server: name });
          await load();
        }),
      );
      head.querySelector(".switch")?.before(el("em", `dot ${dotCls}`));
      li.append(head, sub, controls);
      list.append(li);
    }
  };

  function closeEditor() {
    editing = null;
    editor.classList.add("hidden");
    editor.innerHTML = "";
  }

  function openEditor(name: string | null) {
    editing = name;
    const s = name ? servers[name] : undefined;
    editor.innerHTML = "";
    editor.classList.remove("hidden");

    const nameIn = el("input", "inp") as HTMLInputElement;
    nameIn.placeholder = "имя: латиница, цифры, дефис";
    nameIn.value = name || "";
    const commandIn = el("input", "inp") as HTMLInputElement;
    commandIn.placeholder = "команда: npx, python, cmd…";
    commandIn.value = s?.command || "";
    const argsIn = document.createElement("textarea");
    argsIn.className = "inp";
    argsIn.placeholder = "аргументы — по одному в строке";
    argsIn.value = formatArgs(s?.args || []);
    const envIn = document.createElement("textarea");
    envIn.className = "inp";
    envIn.placeholder = "env — KEY=VALUE, по одному в строке";
    envIn.value = formatEnv(s?.env || {});
    const cwdIn = el("input", "inp") as HTMLInputElement;
    cwdIn.placeholder = "рабочая папка (необязательно)";
    cwdIn.value = s?.cwd || "";
    const enabledIn = document.createElement("input");
    enabledIn.type = "checkbox";
    enabledIn.checked = !!s?.enabled;
    const sw = el("label", "switch");
    sw.append(enabledIn, el("span", "slider"));
    const status = el("div", "hint-box");
    const actions = el("div", "mcp-editor-actions");

    const form = (): McpForm => ({
      name: nameIn.value,
      command: commandIn.value,
      argsText: argsIn.value,
      envText: envIn.value,
      cwd: cwdIn.value,
      enabled: enabledIn.checked,
    });

    actions.append(
      actionBtn("сохранить", async () => {
        try {
          const res = await postJson("/api/mcp/save", buildSavePayload(form(), editing || ""));
          if (!res.ok) {
            status.textContent = String(res.detail || "не удалось сохранить");
            return;
          }
          status.textContent = res.error ? `сохранено, но не запустился: ${String(res.error)}` : "сохранено";
          await load();
          if (!res.error) closeEditor();
        } catch (e) {
          status.textContent = e instanceof Error ? e.message : String(e);
        }
      }),
      actionBtn("проверить", async () => {
        try {
          const res = await postJson("/api/mcp/test", buildServerPayload(form()));
          if (!res.ok) {
            status.textContent = `ошибка: ${String(res.detail || res.error || "не удалось")}`;
            return;
          }
          const tools = (res.tools as { name: string; description: string }[] | undefined) || [];
          status.textContent = tools.length
            ? `инструменты: ${tools.map((t) => `${t.name}${t.description ? ` — ${t.description}` : ""}`).join(", ")}`
            : "сервер ответил, инструментов нет";
        } catch (e) {
          status.textContent = e instanceof Error ? e.message : String(e);
        }
      }),
      actionBtn("отмена", async () => closeEditor()),
    );

    if (editing) {
      actions.append(actionBtn("удалить", async () => {
        if (!confirm(`Удалить сервер ${editing}?`)) return;
        const res = await postJson("/api/mcp/delete", { name: editing });
        if (res.ok) {
          closeEditor();
          await load();
        }
      }));
    }

    const enabledRow = el("div", "seg-row");
    enabledRow.append(sw, el("span", "", "включён"));
    editor.append(nameIn, commandIn, argsIn, envIn, cwdIn, enabledRow, actions, status);
  }

  card.append(list, actionBtn("Добавить сервер", async () => openEditor(null)), editor, hint);
  void load();
  return card;
}
```

- [ ] **Step 3: Добавить CSS**

В `src/console.css`:

```css
#console-root .mcp-editor { display: flex; flex-direction: column; gap: 8px; }
#console-root .mcp-editor.hidden { display: none; }
#console-root .mcp-editor textarea.inp { min-height: 72px; resize: vertical; }
#console-root .mcp-editor-actions { display: flex; flex-wrap: wrap; gap: 6px; }
```

- [ ] **Step 4: Проверить сборку и тесты**

```powershell
npx tsc --noEmit
npm test
```

- [ ] **Step 5: Ручная проверка консоли**

1. Открыть консоль → «Настройки» → MCP.
2. Существующий `playwright` виден, его `env`/`cwd` не потеряны.
3. «Добавить сервер» → имя `echo-test`, команда `python` (или `npx`), аргументы, env, папка; «Проверить» показывает инструменты или понятную ошибку.
4. «Сохранить» при выключенном сервере → появился в списке, `mcp.json` валиден.
5. «Изменить» → переименовать → permission сохранился (переключатель остался в прежнем положении).
6. «Включить» → статус/инструменты; «Выключить».
7. «Удалить» → сервер исчез, `playwright` на месте.
8. Невалидное имя/пустая команда → текст ошибки, конфиг не сломан.

- [ ] **Step 6: Коммит**

```powershell
git add src/console.ts src/console.css
git commit -m "консоль: редактор MCP-серверов"
```

---

### Task 9: Консоль — настройки свободных рук

**Files:**
- Modify: `src/console.ts` (`voiceCard`, строки ~1151–1232; импорт `clampFollowupSec`)
- Test: clamp покрыт в Task 6; UI проверяется вручную.

**UI:** в карточке «Озвучка и голос»:
- переключатель «свободные руки»;
- поле «слушать после ответа, сек» (2–30);
- подсказка: в режиме «авто» настройка неактивна, микрофон и так слушает постоянно.

- [ ] **Step 1: Импорт**

```ts
import { clampFollowupSec } from "./followup";
```

- [ ] **Step 2: Добавить элементы в `voiceCard`**

После `initWrap`:

```ts
  const fuWrap = el("div", "seg-row");
  const fuSwitch = switchEl(false, async (v) => {
    await postJson("/api/config", { followup: v });
  });
  const fuInput = fuSwitch.querySelector("input") as HTMLInputElement;
  const fuSec = document.createElement("input");
  fuSec.type = "number";
  fuSec.min = "2";
  fuSec.max = "30";
  fuSec.className = "inp tiny";
  fuSec.value = "6";
  fuSec.title = "сколько секунд слушать после ответа";
  fuSec.addEventListener("change", async () => {
    const n = clampFollowupSec(Number(fuSec.value));
    fuSec.value = String(n);
    await postJson("/api/config", { followup_sec: n });
  });
  fuWrap.append(fuSwitch, el("span", "", "свободные руки"), fuSec, el("span", "dim", "сек"));
  const fuHint = el("div", "hint-box", "после голосового ответа слушать ещё несколько секунд; в режиме «авто» не нужно");
```

Заменить существующий вызов:

```ts
  card.append(el("div", "hint-box", "голос для озвучки"), voices, vol, volVal, el("div", "hint-box", "микрофон в главном окне"), vmWrap, el("div", "hint-box", "Ая сама заводит разговор"), initWrap);
```

на:

```ts
  card.append(
    el("div", "hint-box", "голос для озвучки"), voices, vol, volVal,
    el("div", "hint-box", "микрофон в главном окне"), vmWrap,
    el("div", "hint-box", "Ая сама заводит разговор"), initWrap,
    el("div", "hint-box", "свободные руки"), fuWrap, fuHint,
  );
```

- [ ] **Step 3: Загрузить настройки**

Расширить тип `fetchJson`:

```ts
const cfg = await fetchJson<{
  tts_voice_id: string;
  tts_volume: number;
  voice_mode: string;
  initiative: boolean;
  followup?: boolean;
  followup_sec?: number;
}>("/api/config");
```

После `renderInit(...)`:

```ts
fuInput.checked = !!cfg.followup;
fuSec.value = String(clampFollowupSec(cfg.followup_sec ?? 6));
```

- [ ] **Step 4: Проверить сборку**

```powershell
npx tsc --noEmit
npm test
```

- [ ] **Step 5: Ручная проверка**

1. Консоль → «Озвучка и голос»: переключатель и поле видны.
2. Включить, поставить 10 → `GET /api/config` отдаёт `followup: true`, `followup_sec: 10`.
3. Поставить 1 → поле становится 2; 99 → 30.
4. Выключить → `followup: false`.
5. Переключить режим микрофона в «авто» → подсказка объясняет, что настройка не нужна.

- [ ] **Step 6: Коммит**

```powershell
git add src/console.ts
git commit -m "консоль: настройки свободных рук"
```

---

### Task 10: Rust/Tauri — трей, чекбоксы, команда синхронизации, уведомления

**Files:**
- Modify: `src-tauri/Cargo.toml`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/capabilities/default.json`
- Modify: `package.json`, `package-lock.json` (npm install)
- Modify: `src-tauri/Cargo.lock` (cargo соберёт)
- Test: `cargo test` (существующий тест хоткея + компиляция)

**Interfaces:**
- Трей: «Показать Аю», «Открыть консоль», разделитель, `CheckMenuItem` «Свободные руки» (id `followup`), «Тишина» (id `focus`), разделитель, «Выход».
- События в окно `main`: `tray-toggle-followup`, `tray-toggle-focus`.
- Команда: `set_tray_check(id: String, checked: bool)`; id `followup`/`focus`; неизвестный id → ошибка.
- «Показать Аю» и «Открыть консоль» вызывают `unminimize()`, `show()`, `set_focus()`.
- Плагин уведомлений: Rust `.plugin(tauri_plugin_notification::init())`; capability `notification:default`.

- [ ] **Step 1: Установить зависимости**

```powershell
npm install @tauri-apps/plugin-notification@^2.7.0
```

В `src-tauri/Cargo.toml` добавить:

```toml
tauri-plugin-notification = "2"
```

- [ ] **Step 2: Добавить capability**

В `src-tauri/capabilities/default.json` в `permissions`:

```json
"notification:default"
```

`core:default` уже включает `core:window:allow-is-visible` и `core:window:allow-is-minimized` — отдельные window-разрешения для JS не нужны. Rust-вызовы `unminimize/show/set_focus` capability не требуют.

- [ ] **Step 3: Реализовать `lib.rs`**

Импорты:

```rust
use tauri::{
    menu::{CheckMenuItem, MenuBuilder, MenuItem, PredefinedMenuItem},
    tray::TrayIconBuilder,
    Emitter, Manager,
};
```

Структура и команда:

```rust
struct TrayChecks {
    followup: CheckMenuItem<tauri::Wry>,
    focus: CheckMenuItem<tauri::Wry>,
}

#[tauri::command]
fn set_tray_check(
    checks: tauri::State<'_, TrayChecks>,
    id: String,
    checked: bool,
) -> Result<(), String> {
    let item = match id.as_str() {
        "followup" => &checks.followup,
        "focus" => &checks.focus,
        _ => return Err(format!("unknown tray check: {id}")),
    };
    item.set_checked(checked).map_err(|e| e.to_string())
}
```

В builder:

```rust
        .plugin(tauri_plugin_notification::init())
        // ... существующие плагины ...
        .invoke_handler(tauri::generate_handler![set_tray_check])
```

В `setup` заменить построение меню:

```rust
            let show = MenuItem::with_id(app, "show", "Показать Аю", true, None::<&str>)?;
            let console = MenuItem::with_id(app, "console", "Открыть консоль", true, None::<&str>)?;
            let sep1 = PredefinedMenuItem::separator(app)?;
            let followup = CheckMenuItem::with_id(app, "followup", "Свободные руки", true, false, None::<&str>)?;
            let focus = CheckMenuItem::with_id(app, "focus", "Тишина", true, false, None::<&str>)?;
            let sep2 = PredefinedMenuItem::separator(app)?;
            let quit = MenuItem::with_id(app, "quit", "Выход", true, None::<&str>)?;
            let menu = MenuBuilder::new(app)
                .items(&[&show, &console, &sep1, &followup, &focus, &sep2, &quit])
                .build()?;
            app.manage(TrayChecks { followup, focus });
```

В `on_menu_event`:

```rust
                    "show" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.unminimize();
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "console" => {
                        if let Some(c) = app.get_webview_window("console") {
                            let _ = c.unminimize();
                            let _ = c.show();
                            let _ = c.set_focus();
                        }
                    }
                    "followup" => {
                        let _ = app.emit_to("main", "tray-toggle-followup", ());
                    }
                    "focus" => {
                        let _ = app.emit_to("main", "tray-toggle-focus", ());
                    }
                    "quit" => app.exit(0),
```

- [ ] **Step 4: Собрать и прогнать Rust-тесты**

```powershell
cargo test --manifest-path src-tauri\Cargo.toml
```

Ожидание: компиляция без ошибок, `hotkey_toggles_only_on_key_press` зелёный. Если `CheckMenuItem<Wry>` не проходит в `State` по `Send/Sync`, заменить `TrayChecks` на хранение `Menu<tauri::Wry>` и получать пункт через `menu.get(id.as_str())`; API `Menu::get` возвращает `MenuItemKind`, у него есть `as_check_menuitem()`.

- [ ] **Step 5: Проверить, что фронт собирается**

```powershell
npx tsc --noEmit
npm test
```

- [ ] **Step 6: Коммит**

```powershell
git add package.json package-lock.json src-tauri/Cargo.toml src-tauri/Cargo.lock src-tauri/src/lib.rs src-tauri/capabilities/default.json
git commit -m "tauri: трей с чекбоксами и уведомления"
```

---

### Task 11: `main.ts` — свободные руки

**Files:**
- Create: `src/window-state.ts`
- Modify: `src/main.ts` (импорты; `openPanel`/`closePanel`; `sendToAya`; `startRec`/`stopRecNow`/`onRecStop`; `beginHold`; «Стоп»; `applyVoiceMode`; `refreshState`; `pollInitiative`)
- Modify: `src/styles.css` (индикатор `#mic.followup`)
- Test: `FollowUpWindow`/`shouldArmFollowUp` покрыты в Task 6; интеграция — ручная.

**Поведение:**
- `sendToAya(text, voice = false)`; из `onRecStop` — `voice=true`.
- После голосового хода, когда TTS-памп доиграл и `!speakAbort`, окно взводится, если `shouldArmFollowUp` разрешает.
- Отмена: текст, ручной микрофон/Ctrl+Space, «Стоп», скрытие/сворачивание, закрытие панели, выключение настройки, инициатива.
- Пустая расшифровка/ошибка STT не взводят цикл.
- Максимум записи follow-up — 30 с.

- [ ] **Step 1: Создать `src/window-state.ts`**

```ts
export async function isAyaHidden(): Promise<boolean> {
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    const w = getCurrentWindow();
    return !(await w.isVisible()) || (await w.isMinimized());
  } catch {
    return typeof document !== "undefined" && document.visibilityState === "hidden";
  }
}
```

- [ ] **Step 2: Импорты и глобальные переменные**

В `src/main.ts`:

```ts
import { clampFollowupSec, FollowUpWindow, shouldArmFollowUp } from "./followup";
import { isAyaHidden } from "./window-state";
```

Рядом с `busy`:

```ts
let panelOpen = false;
let followupOn = false;
let followupSec = 6;
let followupTimer: ReturnType<typeof setInterval> | null = null;
let followupVisibilityTimer: ReturnType<typeof setInterval> | null = null;
let followupRecording = false;
let recStartedAt = 0;
let discardNextRecording = false;
let turnSeq = 0;
const followupWindow = new FollowUpWindow();
```

- [ ] **Step 3: Панель**

`openPanel` → `panelOpen = true;`
`closePanel` → `panelOpen = false; cancelFollowup(true);` (функция объявлена ниже, hoisting работает).

- [ ] **Step 4: `sendToAya` — флаг голосового хода и ожидание TTS**

Изменить сигнатуру:

```ts
async function sendToAya(text: string, voice = false) {
  const myTurn = ++turnSeq;
```

Внутри добавить:

```ts
  let playedAny = false;
  let resReply = "";
  let hadPending = false;
```

В `pump`, после `await playTtsBuffer(data);`:

```ts
        if (!speakAbort) playedAny = true;
```

В `try`, после `connectCore`:

```ts
    resReply = res.reply;
    hadPending = !!res.pending;
```

После `finally` (в конце функции, вне try/finally):

```ts
  // `spokeAny` — очередь речи была заполнена; `playedAny` появляется только
  // после фактического проигрывания. Поэтому сначала ждём, пока памп доиграет,
  // и только потом проверяем, что хотя бы один кусок реально прозвучал.
  if (!voice || myTurn !== turnSeq || speakAbort || hadPending || !spokeAny || !resReply.trim()) return;

  const drained = await (async () => {
    const deadline = Date.now() + 180_000;
    while (Date.now() < deadline) {
      if (myTurn !== turnSeq) return false;
      if (pumpIdle && !speakPlaying && speakQueue.length === 0) return true;
      await new Promise((resolve) => window.setTimeout(resolve, 60));
    }
    return false;
  })();

  if (!drained || myTurn !== turnSeq || speakAbort || busy || pendingId || !playedAny || speakPlaying) return;
  const hidden = await isAyaHidden();
  await loadFollowupConfig();
  // За время await могла начаться инициатива, новый ход или настройку выключили.
  if (myTurn !== turnSeq || speakAbort || busy || pendingId || speakPlaying) return;
  if (!shouldArmFollowUp({
    enabled: followupOn,
    voiceTurn: true,
    mode: voiceMode,
    panelOpen,
    hidden,
    busy,
    pending: !!pendingId,
    reply: resReply,
    spoke: playedAny,
    aborted: speakAbort,
  })) return;
  startFollowup();
```

Важно: `busy` уже сброшен в `finally`; `myTurn` защищает от гонки с новым ходом. `pumpIdle`, `speakQueue`, `speakPlaying` доступны в замыкании.

- [ ] **Step 5: Управление окном и VAD**

Добавить функции (рядом с `stopRecNow`/`startRec`):

```ts
function restoreVoiceHint() {
  voiceHintSet(voiceMode === "hotkey" ? "Ctrl+Space: зажми в любом окне и говори" : "");
}

function clearFollowupTimers() {
  if (followupTimer != null) {
    clearInterval(followupTimer);
    followupTimer = null;
  }
  if (followupVisibilityTimer != null) {
    clearInterval(followupVisibilityTimer);
    followupVisibilityTimer = null;
  }
}

async function loadFollowupConfig(): Promise<void> {
  try {
    const cfg = (await (await fetch(`${CORE_URL}/api/config`)).json()) as {
      followup?: boolean;
      followup_sec?: number;
    };
    if (typeof cfg.followup === "boolean") {
      followupOn = cfg.followup;
      if (!followupOn && followupTimer != null) cancelFollowup(true);
    }
    if (typeof cfg.followup_sec === "number") followupSec = clampFollowupSec(cfg.followup_sec);
  } catch {}
}

function startFollowup() {
  if (!followupOn || voiceMode === "auto" || busy || !panelOpen) return;
  if (rec && rec.state === "recording") return;
  clearFollowupTimers();
  followupRecording = false;
  followupWindow.arm(followupSec);
  micBtn.classList.add("followup");
  followupTimer = setInterval(followupTick, 120);
  followupVisibilityTimer = setInterval(() => {
    void (async () => {
      if (await isAyaHidden()) {
        cancelFollowup(true);
        return;
      }
      // Настройку могли выключить в консоли — не ждём пятисекундного refreshState.
      await loadFollowupConfig();
    })();
  }, 1000);
  voiceHintSet(`свободные руки: слушаю ${followupWindow.remaining()} с…`);
}

function followupTick() {
  // Началась запись — окно уже выполнило свою роль. Дальше работают обычные
  // VAD-правила: тишина ~1,4 с или жёсткий предел 30 с.
  if (rec && rec.state === "recording") {
    if (busy) {
      cancelFollowup(true);
      return;
    }
    voiceHintSet("свободные руки: слушаю…");
    if (Date.now() - recStartedAt > 30_000) stopRecNow();
    else vadTick();
    return;
  }
  if (busy || !followupWindow.active()) {
    followupWindow.expire();
    cancelFollowup();
    return;
  }
  voiceHintSet(`свободные руки: слушаю ${followupWindow.remaining()} с…`);
  vadTick();
}

function cancelFollowup(discard = false) {
  // Останавливаем только запись, начатую свободными руками; ручную не трогаем.
  const wasFollowupRecording = followupRecording && !!(rec && rec.state === "recording");
  followupWindow.cancel();
  clearFollowupTimers();
  micBtn.classList.remove("followup");
  if (wasFollowupRecording) {
    discardNextRecording = discard;
    stopRecNow();
  }
  restoreVoiceHint();
}
```

В `startRec` после `rec.start()`:

```ts
  recStartedAt = Date.now();
  followupRecording = followupTimer != null;
  if (followupRecording) {
    // Окно ожидания израсходовано: запись уже идёт. Таймер оставляем —
    // он теперь следит за тишиной и пределом 30 с, а не за окном.
    followupWindow.cancel();
    micBtn.classList.remove("followup");
  }
  micBtn.classList.add("recording");
```

В `stopRecNow` добавить очистку:

```ts
  micBtn.classList.remove("followup");
  clearFollowupTimers();
  followupWindow.cancel();
```

`startRec` уже вызывает `stopAyaSpeech()` — для follow-up это безвредно (речь уже закончилась).

- [ ] **Step 6: `onRecStop` — голосовой ход, пустая расшифровка, discard**

```ts
async function onRecStop() {
  const wasFollowup = followupRecording;
  followupRecording = false;
  restoreVoiceHint();
  const blob = new Blob(chunks, { type: rec?.mimeType || "audio/webm" });
  chunks = [];
  if (discardNextRecording) {
    discardNextRecording = false;
    return;
  }
  if (!blob.size) return;
  if (busy) return;
  micBtn.classList.add("listening");
  try {
    const text = await transcribe(CORE_URL, blob);
    micBtn.classList.remove("listening");
    if (text) {
      addMsg(text, "user");
      await sendToAya(text, true);
    } else if (voiceMode === "button" && !wasFollowup) {
      addMsg("Не расслышала… повтори громче?", "aya");
    }
  } catch (err) {
    micBtn.classList.remove("listening");
    addMsg(`Распознавание не сработало: ${err instanceof Error ? err.message : err}`, "offline");
  }
}
```

- [ ] **Step 7: Отмена по событиям**

1. В `form.addEventListener("submit", ...)` перед `busy = true`: `cancelFollowup(true);` — отправка текста важнее недописанного голоса.
2. В `beginHold()` после проверки записи: `cancelFollowup();`.
3. В обработчике `stopBtn` первым делом: `cancelFollowup(true);`.
4. В `setStopVisible()` показывать «Стоп» и во время окна свободных рук:
   `stopBtn?.classList.toggle("hidden", !busy && !pendingId && !speakPlaying && followupTimer == null);`
5. В `applyVoiceMode()` в начале: `if (followupTimer != null) cancelFollowup(true);` — смена режима не должна отправлять обрезанную запись свободных рук, но не трогает обычную запись.
6. В `pollInitiative` после `if (!d.topic || !d.reply) return;`: `cancelFollowup();`.
7. В `refreshState` при `followup: false`: если `followupTimer != null` — `cancelFollowup(true);`.

- [ ] **Step 8: Настройки в `refreshState`**

Расширить тип конфига:

```ts
const cfg = (await (await fetch(`${CORE_URL}/api/config`)).json()) as {
  initiative?: boolean;
  followup?: boolean;
  followup_sec?: number;
};
```

После `initiativeOn`:

```ts
if (typeof cfg.followup === "boolean") {
  followupOn = cfg.followup;
  if (!followupOn && followupTimer != null) cancelFollowup(true);
}
if (typeof cfg.followup_sec === "number") followupSec = clampFollowupSec(cfg.followup_sec);
```

`loadFollowupConfig` (Step 5) остаётся для свежей проверки после речи и в секундном таймере окна.

- [ ] **Step 9: CSS индикатора**

В `src/styles.css` после `#mic.listening`:

```css
#mic.followup {
  background: #3f8f57 !important;
  color: #fff !important;
  animation: followpulse 1.4s ease-in-out infinite;
}

@keyframes followpulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(63, 143, 87, 0.55);
  }
  50% {
    box-shadow: 0 0 0 9px rgba(63, 143, 87, 0);
  }
}
```

- [ ] **Step 10: Сборка и ручная проверка**

```powershell
npx tsc --noEmit
npm test
```

Ручной чек-лист (dev-режим, `start-aya.cmd`):
1. Включить follow-up, 6 с; открыть панель; задать вопрос голосом. После ответа микрофон зелёный, подсказка «слушаю 6 с…». Сказать продолжение — запись, ответ, снова окно.
2. Молчать 6 с — окно закрылось, ничего не отправлено.
3. Отправить текст — окно не взводится.
4. Выключить в консоли — после ответа не взводится.
5. Режим `auto` — поведение прежнее, follow-up не взводится.
6. Закрыть панель / скрыть Alt+Shift+A / свернуть / нажать «Стоп» / нажать микрофон — ожидание отменяется.
7. Ответ с `pending`-экшеном — окно не взводится.
8. Пустая расшифровка/ошибка STT — цикл останавливается.
9. Сказать длинно — запись останавливается на 30 с.
10. Начать говорить за секунду до конца окна и продолжать дольше 6 с — запись не обрывается на 6-й секунде; работает тишина/30 с.

- [ ] **Step 11: Коммит**

```powershell
git add src/main.ts src/window-state.ts src/styles.css
git commit -m "свободные руки: ожидание после голосового ответа"
```

---

### Task 12: `main.ts` — тосты и синхронизация трея

**Files:**
- Create: `src/notify.ts`
- Create: `src/tray.ts`
- Modify: `src/main.ts` (импорты; `pollInitiative`; `refreshState`; tray-события; синхронизация чекбоксов)
- Test: `shouldToast`/`toastBody` покрыты в Task 5; интеграция — ручная.

**Поведение:**
- После получения инициативы: если `shouldToast(await isAyaHidden(), focus)` — `notifyAya(toastBody(reply))`, затем обычная озвучка.
- `focus` берём из `/api/proactive` в момент инициативы (не старше одного запроса).
- Трей-чекбоксы: при загрузке и изменении настроек вызывать `set_tray_check`; при клике в трее — переключать фактическую настройку и синхронизировать чекбокс; при ошибке POST — вернуть чекбокс к фактическому значению.
- Разрешение на уведомления запрашивается один раз; отказ не ломает приложение.

- [ ] **Step 1: Создать `src/notify.ts`**

```ts
let permission: Promise<boolean> | null = null;

async function ensurePermission(): Promise<boolean> {
  if (!permission) {
    permission = (async () => {
      try {
        const n = await import("@tauri-apps/plugin-notification");
        if (await n.isPermissionGranted()) return true;
        return (await n.requestPermission()) === "granted";
      } catch {
        return false;
      }
    })();
  }
  return permission;
}

export async function notifyAya(body: string): Promise<boolean> {
  if (!body.trim()) return false;
  if (!(await ensurePermission())) return false;
  try {
    const n = await import("@tauri-apps/plugin-notification");
    n.sendNotification({ title: "Ая", body });
    return true;
  } catch {
    return false;
  }
}
```

- [ ] **Step 2: Создать `src/tray.ts`**

```ts
export type TrayCheck = "followup" | "focus";

export async function setTrayCheck(id: TrayCheck, checked: boolean): Promise<boolean> {
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("set_tray_check", { id, checked });
    return true;
  } catch {
    return false;
  }
}

export async function onTrayToggle(id: TrayCheck, handler: () => void | Promise<void>): Promise<void> {
  try {
    const { listen } = await import("@tauri-apps/api/event");
    await listen(`tray-toggle-${id}`, () => void handler());
  } catch {}
}
```

- [ ] **Step 3: Импорты и глобальные переменные в `main.ts`**

```ts
import { shouldToast, toastBody } from "./notify-decision";
import { notifyAya } from "./notify";
import { setTrayCheck, onTrayToggle } from "./tray";
```

Рядом с `initiativeOn`:

```ts
let focusOn = false;
let lastTrayFollowup: boolean | null = null;
let lastTrayFocus: boolean | null = null;
```

- [ ] **Step 4: Тост в `pollInitiative`**

После `if (!d.topic || !d.reply) return;`:

```ts
    let focus = false;
    try {
      const p = (await (await fetch(`${CORE_URL}/api/proactive`)).json()) as { focus?: boolean };
      focus = !!p.focus;
    } catch {}
    if (shouldToast(await isAyaHidden(), focus)) {
      void notifyAya(toastBody(d.reply));
    }
```

`cancelFollowup();` из Task 11 должен стоять рядом — до озвучки.

- [ ] **Step 5: Синхронизация в `refreshState`**

После блока follow-up:

```ts
  if (lastTrayFollowup !== followupOn) {
    const ok = await setTrayCheck("followup", followupOn);
    if (ok) lastTrayFollowup = followupOn;
  }
  try {
    const p = (await (await fetch(`${CORE_URL}/api/proactive`)).json()) as { focus?: boolean };
    focusOn = !!p.focus;
    if (lastTrayFocus !== focusOn) {
      const ok = await setTrayCheck("focus", focusOn);
      if (ok) lastTrayFocus = focusOn;
    }
  } catch {}
```

- [ ] **Step 6: Обработчики tray-событий**

Добавить в конец инициализации (перед `refreshState()`):

```ts
void onTrayToggle("followup", async () => {
  try {
    const cfg = (await (await fetch(`${CORE_URL}/api/config`)).json()) as { followup?: boolean };
    const next = !cfg.followup;
    await fetch(`${CORE_URL}/api/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ followup: next }),
    });
    followupOn = next;
    if (!next) cancelFollowup();
    if (await setTrayCheck("followup", next)) lastTrayFollowup = next;
  } catch {
    await setTrayCheck("followup", followupOn);
  }
});

void onTrayToggle("focus", async () => {
  try {
    const p = (await (await fetch(`${CORE_URL}/api/proactive`)).json()) as { focus?: boolean };
    const next = !p.focus;
    await fetch(`${CORE_URL}/api/proactive`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ focus: next }),
    });
    focusOn = next;
    if (await setTrayCheck("focus", next)) lastTrayFocus = next;
  } catch {
    await setTrayCheck("focus", focusOn);
  }
});
```

- [ ] **Step 7: Сборка и тесты**

```powershell
npx tsc --noEmit
npm test
```

- [ ] **Step 8: Ручная проверка**

1. Первый запуск: Windows спрашивает разрешение на уведомления; при отказе Ая работает, тостов нет.
2. Скрыть Аю Alt+Shift+A, поставить напоминание на 1 минуту → тост «Ая» + речь.
3. Окно видно, «Тишина» выключена → тоста нет, речь есть.
4. Включить «Тишину» в трее → чекбокс в консоли тоже включён; проактивные/legacy не генерируются; принудительное напоминание приходит с тостом.
5. Переключить «Свободные руки» в трее → в консоли настройка изменилась; и наоборот.
6. «Показать Аю» разворачивает свёрнутое окно; «Открыть консоль» разворачивает консоль.
7. После перезапуска чекбоксы совпадают с настройками.

- [ ] **Step 9: Коммит**

```powershell
git add src/notify.ts src/tray.ts src/main.ts
git commit -m "уведомления: тосты и синхронизация трея"
```

---

### Task 13: Полная проверка, релиз и живой прогон

**Files:** без новых файлов; только проверка и, при необходимости, мелкие правки.

- [x] **Step 1: Все автотесты**

```powershell
core\.venv\Scripts\python.exe -m pytest -q
npm test
npx tsc --noEmit
cargo test --manifest-path src-tauri\Cargo.toml
```

Ожидание: все зелёные, без предупреждений компиляции Rust.

Факт: 199 passed (1 pre-existing warning Discord `audioop`), `npm test` 35 passed, `tsc` чисто, `cargo test` 1 passed.

- [x] **Step 2: Проверить дифф и отсутствие мусора**

```powershell
git status --short
git diff --stat
```

Ожидание: только осмысленные изменения; `.tmp`, логи, `dist` и `target` не в коммите.

Факт: правки после ревью в `5c62cba`; рабочее дерево чисто перед обновлением документации.

- [x] **Step 3: Собрать релиз**

Попросить пользователя закрыть запущенную Аю (трей → «Выход»), иначе `aya.exe` залочен. Проверить:

```powershell
Get-Process aya -ErrorAction SilentlyContinue
```

Если процесс остался — не убивать без подтверждения; попросить закрыть. Затем:

```powershell
npm run tauri build
```

Ожидание: `src-tauri\target\release\aya.exe` обновлён, NSIS-сборка завершилась. Не запускать dev и prod одновременно.

Факт: `aya.exe` 13 675 520 байт (18:21:17), NSIS 5 734 276 байт (18:21:17); запущен через `start-aya-prod.cmd`.

- [ ] **Step 4: Живой прогон релиза**

Запустить `start-aya-prod.cmd` (прод, без Vite). Проверить:
- Ая стартует, ядро на 8330, окно и консоль открываются.
- Alt+Shift+A переключает окно только по нажатию, без мелькания на отпускании.
- Ctrl+Space (PTT) работает в главном окне; хоткей не конфликтует с Alt+Shift+A.
- Трей: оба чекбокса, «Показать Аю» (разворачивает свёрнутое), «Открыть консоль», «Выход».
- Уведомления: тост при скрытом окне/тишине; речь не пропадает.
- MCP: `playwright` на месте; добавить/проверить/переименовать/удалить тестовый сервер; конфиг валиден.
- Свободные руки: включить 6 с, голосовой вопрос → окно → продолжение; выключить → не взводится; `auto` → не взводится.

Статус: старт, ядро на 8330, консоль, API follow-up, MCP CRUD/rename/permission, `playwright`, live-sync консоли и disabled-контролы в `auto` проверены. Пользователь выбрал пропустить ручную проверку Alt+Shift+A, Ctrl+Space, трея, тостов и голосового цикла свободных рук; эти пункты не считаются подтверждёнными.

- [x] **Step 5: Финальный отчёт**

Указать:
- какие тесты и сколько прошло;
- что проверено вживую, а что осталось за пользователем (в частности, подтверждение Ctrl+Space и Alt+Shift+A — предыдущий фикс `b0d6e09` ещё не подтверждён живьём);
- путь к релизному exe и команду запуска;
- известные ограничения v1 (клик по тосту, уведомления только при запущенном приложении, секреты MCP в открытом виде).

---

## Review follow-up (2026-10-23)

Финальный fresh-context review выявил гонки и пограничные случаи; исправления в `5c62cba`:

- Общий `turnSeq` для ходов, записей, инициатив и отмен; инициатива инвалидирует ожидание follow-up, а результат STT проверяется после `await` (текст/новый ход/«Стоп»/закрытие панели/скрытие окна не дают отправить устаревшую запись).
- `recTurn` фиксирует поколение записи; VAD-счётчики сбрасываются на старте и остановке записи.
- Консоль live-синхронизирует follow-up/focus каждые 3 с; контролы follow-up disabled в `auto`. Это добавлено в `src/console.ts`, хотя исходный Task 12 перечислял только `console.css`.
- MCP-маршруты save/delete/toggle/test сделаны sync (threadpool), добавлен `_MCP_LOCK`; имена нормализуются на границе API; `oldName.trim()`; ошибки MCP-действий показываются в UI.
- Быстрые клики по чекбоксам трея сериализуются очередью; при отмене follow-up запись отбрасывается.
- Добавлены тесты нормализации имён MCP API и `oldName.trim()`.

Исходные эскизы плана с `async def` для блокирующих MCP-маршрутов и сырым `body.name` намеренно заменены этой реализацией.

---

## Что не делаем (YAGNI)

- Wake-word и постоянное прослушивание вне окна.
- HTTP/SSE MCP-серверы.
- Клик по тосту и переход в Аю.
- Push-уведомления при закрытом приложении.
- Автоматическое взведение follow-up после текстовых сообщений.
- Отдельные настройки follow-up в `voice-ctl.ts` (достаточно `/api/config`).
