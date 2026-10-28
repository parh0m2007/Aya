# Aya — обзор проекта для агента

> Краткая «картина мира» по проекту: что это, как устроено, где что лежит, как запускать и проверять. Адресована новому агенту, которому предстоит работать в кодовой базе.

## Что это

**Ая** — настольная аниме-компаньонка для Windows. Прозрачное окно с Live2D-персонажем поверх всех окон: с ней можно разговаривать (текстом и голосом), она помнит контекст (SQLite + векторная долговременная память), имеет настроения и занятия, проявляет инициативу и может выполнять ограниченные безопасные действия на ПК через шлюз инструментов.

Проект личный, не публичный. Основной язык комментариев и UI — русский.

## Архитектура (три слоя)

```
┌──────────────────────────┐
│ src-tauri/ — оболочка    │  Tauri 2 (Rust): прозрачное always-on-top окно,
│ Tauri 2 (Rust)           │  трей, глобальные хоткеи, автозапуск, уведомления
└───────────┬──────────────┘
            │ webview
┌───────────▼──────────────┐
│ src/ — фронтенд          │  Vite + TypeScript + PIXI.js + pixi-live2d-display
│ avatar, чат, консоль     │  главное окно (index.html) + консоль (console.html)
└───────────┬──────────────┘
            │ HTTP/JSON (http://127.0.0.1:8330)
┌───────────▼──────────────┐
│ core/ — Python-ядро      │  FastAPI: LLM-адаптеры, память, поведение,
│ FastAPI + SQLite         │  шлюз безопасности, голос (STT/TTS), мосты в мессенджеры
└──────────────────────────┘
```

Связь строго локальная: фронтенд ходит на ядро по REST (`src/core-client.ts`), ядро слушает `http://127.0.0.1:8330`. Ядро также само раздаёт собранную консоль и Live2D-модели (`/console.html`, `/assets/*`, `/models/*`).

## Структура репозитория

| Путь | Содержимое |
|---|---|
| `src/` | Фронтенд: `avatar.ts` (Live2D, реакции, взгляд, губы, мимика), `avatar-logic.ts` (чистая логика жестов/эмоций, тесты), `hover-pass.ts` (сквозные клики и взгляд за курсором), `main.ts` (чат, голос, follow-up, меню, быстрые настройки), `console.ts` (оболочка консоли), `voice-ctl.ts`, `tray.ts`, `window-drag.ts`, `window-state.ts`, `notify*.ts`, `speech-group.ts` |
| `src/console/` | Консоль «Aya Studio»: `pages.ts` (страницы), `cards.ts` (карточки настроек), `wizard.ts` (мастер «Собери свою Аю»), `persona-store.ts`, `preview.ts` (одно WebGL-превью), `ui.ts` |
| `src-tauri/` | Rust-оболочка: окно, трей, хоткеи (`Cargo.toml`, `src/`) |
| `core/` | Python-ядро (см. ниже) |
| `core/llm/` | Адаптеры LLM: `openai_compatible.py` (OpenRouter и др.), `ollama.py`, `base.py` (`make_adapters`, `stream_llm`), `http.py` (общий keep-alive клиент, потоковое чтение), `metrics.py` |
| `core/safety/` | `tools.py` (инструменты ПК), `gateway.py` (подтверждения/пермиссии), `skills.py`, `vision.py` (VLM-зрение) |
| `core/behavior/` | `state.py` (настроения/состояния), `proactive.py` (инициатива 2.0: опрос активного окна и неактивности) |
| `core/voice/` | `stt.py` (faster-whisper), `tts.py` (edge-tts + локальные движки), `modelmgr.py` (выгрузка моделей), `textnorm.py` |
| `core/` (корень) | `persona.py` (конструктор персоны → системный промпт, модули), `reply.py` (фильтр стрима JSON-действий, настроение реплики), `main.py` (FastAPI-приложение), `db.py` (SQLite), `longmem.py` (векторная память через Ollama-эмбеддинги), `prompting.py` (системный промпт), `tasks.py` (фоновые задачи), `mcp.py` (MCP-серверы), `agents.py` (персоны-агенты), `bridge_telegram.py`, `bridge_discord.py`, `bridge_tguser.py` |
| `docs/` | Дизайн-документы, планы (`docs/design/plans/`, `docs/design/specs/`) |
| `console.html`, `index.html` | Точки входа фронтенда |

## Ключевые подсистемы ядра

- **LLM.** Конфиг в `~/.aya/config.json`: `type` = `openai_compatible` (base_url, model, api_key) или `ollama`. Режим потоковой выдачи — `/api/chat/stream`.
- **Память.** `core/db.py` — SQLite (`~/.aya/aya.db`): история, события, аудит, пермиссии. `core/longmem.py` — долговременная память на эмбеддингах (nomic-embed-text / bge-m3 / all-minilm через Ollama), воспоминания подмешиваются в историю диалога.
- **Поведение.** `behavior/state.py` — настроения (`MOODS = ["calm", "joyful", "thoughtful"]`) и занятия; `behavior/proactive.py` — инициатива: каждые ~15 мин смотрит активное окно (`GetForegroundWindow`) и бездействие (`GetLastInputInfo`), завязан на VLM-зрение.
- **Безопасный шлюз.** Все инструменты ПК (`core/safety/tools.py`) по умолчанию **выключены**. Включение — через `POST /api/permissions`. Часть инструментов (`write_allowed_file`, `run_cmd`, `ssh_run`) требует подтверждения человеком (`gateway.py`, TTL 10 мин, endpoint `/api/action/confirm`). Журнал всех вызовов — `GET /api/audit`. Доступные инструменты: `open_app`, `set_volume`, `create_note`, `write_allowed_file`, `search_allowed_folder`, `get_task_status`, `remember_fact`, `recall`, `screen_look`, `focus_mode`, `tg_inbox`, `tg_read` и др.
- **Агенты-персоны (M4).** `~/.aya/agents/<name>/SOUL.md` (личность, до 2500 символов) + `skills.json` (`{"delegate": true, "tools": [...]}`). Выбор агента — настройка `agent_persona` в БД. Делегирование подзадач другим LLM-агентам идёт через `delegate {agent, task}` с тем же шлюзом.
- **MCP.** `core/mcp.py` — запуск stdio MCP-серверов из `~/.aya/mcp.json` (`{"servers": {...}}`), управление через `/api/mcp*` и редактор в консоли.
- **Мосты в мессенджеры.** `bridge_telegram.py`, `bridge_discord.py`, `bridge_tguser.py` (юзербот Telegram): Ая может читать/отвечать в чатах, настройки — в консоли.
- **Голос.** Push-to-talk, STT — faster-whisper, TTS — edge-tts (+ локальные модели), lip-sync на фронте (`startTalking`/`stopTalking`).

## API ядра (основные эндпоинты)

Чат: `POST /api/chat`, `POST /api/chat/stream`, `GET /api/pending`, `GET /api/history` ·
состояния: `GET|POST /api/state`, `GET /api/initiative` ·
память: `GET /api/memory`, `POST /api/memory/add`, `POST /api/memory/forget` ·
действия: `POST /api/action/pending`, `POST /api/action/confirm` ·
пермиссии/аудит: `GET|POST /api/permissions`, `GET /api/audit` ·
LLM/конфиг: `GET|POST /api/llm`, `GET|POST /api/config` ·
агенты: `GET /api/agents`, `POST /api/agents/select`, `POST /api/agents/create`, `GET|POST /api/agents/soul/{name}` ·
MCP: `GET /api/mcp`, `POST /api/mcp/{toggle,save,delete,test}`, `POST /api/mcp/perm/{server}` ·
мессенджеры: `GET|POST /api/telegram`, `GET|POST /api/discord`, `GET|POST /api/tguser` (+login/code/password/logout) ·
голос/модели: `POST /api/voice/transcribe`, `GET|POST /api/models`, `GET /api/speak`, `GET|POST /api/gpu` ·
задачи: `GET|POST /api/tasks`, `POST /api/tasks/cancel` ·
отладка: `GET /api/debug/metrics`, `POST /api/debug/unload`, `POST /api/_diag` ·
персона: `GET|POST /api/persona`, `POST /api/persona/preview`, `POST /api/persona/reset` ·
окно Аи: `GET /api/ui` (один опрос: состояние + конфиг + тишина), `POST /api/react` (реплика на касание, без LLM).

## Запуск (разработка)

1. **Ядро** (терминал 1): `core\.venv\Scripts\python core\main.py` (первый раз: создать venv и `pip install -r core\requirements.txt`). Адрес — `http://127.0.0.1:8330`. Переменная `AYA_DISABLE_TASKS` отключает фоновые подсистемы (задачи, MCP, мосты, инициативу) — удобно для отладки.
2. **Приложение** (терминал 2): `npm install`, затем `npm run tauri dev`. Дев-сервер Vite — `http://localhost:5173`.
3. Скрипты в корне: `start-aya.cmd`, `start-aya-prod.cmd`, `stop-aya.cmd`.

Runtime-данные живут в `~/.aya/`: `config.json` (LLM), `aya.db` (SQLite), `SOUL.md` (личность по умолчанию), `agents/`, `models/` (Live2D-модели), `mcp.json`.

## Тесты

- Python: pytest (`pytest.ini`), тесты в `core/tests/` (~23 файла: API-смок, шлюз, LLM, память, мосты, голос, агенты, MCP).
- Фронтенд: vitest (`npm test`) — модули во `src/*.test.ts` (follow-up, notify, speech-group, window-drag, voice-ctl и др.).

## Дорожная карта (из README)

- [x] Этап 1: персонаж, текстовый диалог, состояния, занятия
- [x] Этап 2: голос (push-to-talk, faster-whisper + edge-tts, lip-sync)
- [ ] Этап 3: долговременная память и инициатива (сейчас базовая)
- [ ] Этап 4: безопасные действия (шлюз готов, нужен UI подтверждений)
- [ ] Этап 5: фоновые задачи

## Соглашения и нюансы

- Коммиты на русском, в духе «фича: …», «фикс: …», «документация: …».
- Код ядра сгруппирован по доменам, классы/функции небольшие, много threading-блокировок (MCP, делегирование, STT/TTS) — при работе с общим состоянием проверяй локи.
- Live2D: SDK Cubism проприетарен (бесплатно для инди), модель-плейсхолдер Haru встроена как запасная.
- Консоль настроек — отдельное окно Tauri (`console.html`), раздаётся и ядром, поэтому правки UI консоли нужно собирать (`npm run build`).
- Все опасные действия проходят через шлюз с подтверждением; при добавлении нового инструмента ПК — регистрируй его в `tools.py`, добавь описание в `safety/tool_text.py` (в промпт идут только включённые) и продумывай пермиссию.
- Ничего тяжёлого в `async`-обработчиках: загрузка моделей, сеть, `import torch` — только через `asyncio.to_thread`, иначе замерзает всё ядро (стрим, озвучка, опрос окна).
