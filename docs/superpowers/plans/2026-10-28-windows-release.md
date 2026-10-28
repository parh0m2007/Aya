# Релиз Аи для Windows — план работ

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** выпустить открытый релиз Аи на GitHub. Что в него входит:
- установщик для Windows без прав администратора;
- мастер первого запуска;
- обновления в один клик;
- сборка по тегу;
- две правки клавиш: ответ голосом, когда Ая свёрнута, и свои сочетания.

**Architecture:**
- **Ядро** — те же `.py`, которые запускает переносной Python
  (python-build-standalone 3.11). Он лежит в ресурсах Tauri рядом с `aya.exe`.
- **Тяжёлые пакеты** (CUDA для Whisper, torch для локальных голосов) ставятся
  по кнопке встроенным `uv` в `~/.aya/runtime/<набор>`. Ядро добавляет эти
  папки в `sys.path` при старте.
- **Мастер** — полноэкранный слой в окне консоли. Главное окно прячется,
  пока мастер не пройден.
- **Обновления** — через `tauri-plugin-updater` и `latest.json` в GitHub
  Releases. Их собирает `tauri-action` по тегу `v*`.

**Tech Stack:**
- Tauri 2 (Rust), Vite + TypeScript, vitest;
- Python 3.11, FastAPI, pytest;
- uv, python-build-standalone;
- GitHub Actions (`tauri-apps/tauri-action`).

**Spec:** `docs/superpowers/specs/2026-10-28-windows-release-design.md` — читать вместе с планом.

## Global Constraints

- **Платформа:** только Windows 10/11 x64. Установка «для текущего
  пользователя» (`installMode: currentUser`), без прав администратора.
- **Данные человека** живут в `~/.aya`, и обновление и удаление приложения
  их не трогает. Это:
  - `config.json`, `aya.db`;
  - `voices/`, `models/`, `tts-cache/`, `logs/`;
  - `runtime/`, `voices-local/`.
- **Модель для ответов по умолчанию:** `{"type": "ollama", "base_url": "https://ollama.com", "model": "gemma4:31b-cloud"}`.
  Страница ключей — `https://ollama.com/settings/keys`.
- **Голос по умолчанию:** Fish, модель `s2.1-pro-free`, голос
  `tts_voice_id = "fish_miyuki"`. Без ключа — Edge `ru-RU-SvetlanaNeural`.
  Ключи Fish — fish.audio → API Keys.
- **Репозиторий:** `https://github.com/parh0m2007/Aya`, лицензия MIT.
  Адрес обновлений — `https://github.com/parh0m2007/Aya/releases/latest/download/latest.json`.
- **Клавиши по умолчанию:** «говорить» — `Control+Space`, «показать/скрыть» — `Alt+Shift+A`.
- **Ключи и токены** никогда не возвращаются из API и не пишутся в логи.
  Наружу отдаётся только `*_set` / `api_key_tail`.
- **Тексты интерфейса** по-русски, коротко, без технических слов: «Ая на
  связи», «ключ не подошёл», «нет интернета».
- **Публикуются только** аватар Haru и голоса `refs_real/`. Голос автора
  (`core/voice/f5_ru/refs_user/`) не публикуется.
- **Коммиты** — по-русски, в конце строка
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Нельзя:**
  - завершать процессы пользователя (его Аю, его ядро на 8330);
  - публиковать что-либо на GitHub без явного «да» автора в чате.

## Review Focus

1. **Нет интернета во время мастера.** Проверка ключа отвечает «нет
   интернета», а не висит. Мастер можно пройти с «Пропустить пока». Тест — в Task 6.
2. **Сочетание клавиш занято** Windows или другой программой. Новое не
   применяется, старое продолжает работать, поле пишет «занято». Тест — в Task 2.
3. **Загрузка набора оборвалась посередине** (torch больше 2 ГБ). Набор
   получает состояние `error`, его можно повторить. Половина набора не
   подключается в `sys.path`, ядро стартует. Тест — в Task 5.
4. **Мастер закрыли посередине.** При следующем запуске он открывается на
   том же шаге, а главное окно не висит пустым. Тест — в Task 6.
5. **Ключ вставлен с пробелами или переводом строки** (так часто копируют из
   браузера). Лишнее обрезается, ключ принимается. Тест — в Task 6.

---

### Task 1: Свёрнутая Ая отвечает голосом на клавишу

**Files:**
- Modify: `src/voice-logic.ts`, добавить чистую функцию.
- Modify: `src/main.ts`, `onRecStop()`, ветку `else if (await isAyaHidden()) return;` (~стр. 1077).
- Test: `src/voice-logic.test.ts`

**Interfaces:**
- Produces: `dropWhenHidden(origin: string, hidden: boolean): boolean` —
  true, только если окно скрыто и запись **не** от клавиши (`origin !== "hold"`).
  Записи `wake` и `wake-armed` уходят раньше, по своей ветке.

- [ ] **Step 1: Тест**

```ts
import { dropWhenHidden } from "./voice-logic";
it("скрытая Ая слушает клавишу, но не ловит фоновую речь", () => {
  expect(dropWhenHidden("hold", true)).toBe(false);
  expect(dropWhenHidden("vad", true)).toBe(true);
  expect(dropWhenHidden("button", true)).toBe(true);
  expect(dropWhenHidden("vad", false)).toBe(false);
});
```

- [ ] **Step 2: Тест падает** — `npx vitest run src/voice-logic.test.ts`. Ожидается: FAIL, `dropWhenHidden` не экспортирована.
- [ ] **Step 3: Реализация.** В `onRecStop` заменить ветку на
  `else if (dropWhenHidden(origin, await isAyaHidden())) return;`.
  Перед этим посмотреть настоящие значения `recOrigin` в `main.ts` и
  назвать в тесте именно их.
  - Когда Ая скрыта, `sendToAya` отрабатывает как обычно: голос звучит, ход
    пишется в историю, окно **не** показывается (`showSelf` не вызывать).
  - Убедиться, что в `sendToAya` и `playTtsStream` нет своих проверок
    `isAyaHidden()`, которые глушат звук. Если есть — пропускать их, когда
    ход начат с клавиши.
- [ ] **Step 4: Тест проходит** — `npx vitest run src/voice-logic.test.ts` (PASS), `npx tsc --noEmit` (без ошибок).
- [ ] **Step 5: Проверка вживую** на собранном dev-окне (`npx tauri dev`):
  - Ая в трее → зажать Ctrl+Space → «сколько времени?» → ответ звучит, окно не появляется.
  - То же, когда окно свёрнуто.
- [ ] **Step 6: Коммит** — `клавиша «говорить» работает, когда Ая свёрнута или в трее`.

### Task 2: Свои сочетания клавиш

**Files:**
- Create: `src/hotkey.ts`, `src/hotkey.test.ts` — разбор нажатия и подпись.
- Create: `src/console/hotkey-field.ts` — поле «нажми сочетание».
- Modify:
  - `src/main.ts` — `bindHotkey`, `applyVoiceMode`;
  - `src-tauri/src/lib.rs` — убрать блок «Глобальный хоткей: Alt+Shift+A», стр. 135–151;
  - `core/main.py` — `VoiceIn`, `get_config`, `set_config`;
  - `src/console/pages.ts` — `voicePage`.
- Test: `src/hotkey.test.ts`, `core/tests/test_hotkeys.py`

**Interfaces:**
- Produces (ядро):
  - настройки `hotkey_talk` (по умолчанию `"Control+Space"`) и
    `hotkey_toggle` (по умолчанию `"Alt+Shift+A"`);
  - `POST /api/config` принимает `hotkey_talk`, `hotkey_toggle`;
    `GET /api/config` и `/api/ui` `config` их отдают.
- Produces (TS):
  - `accelFromEvent(e: Pick<KeyboardEvent, "key" | "code" | "ctrlKey" | "altKey" | "shiftKey" | "metaKey">): string | null`
    — строка в формате Tauri (`"Control+Shift+Space"`, `"F9"`). Одиночная
    буква или цифра без модификатора → `null`, иначе она сломает набор
    текста. Одиночные `F1`–`F24`, `Pause`, `ScrollLock`, `Insert` разрешены.
  - `accelLabel(accel: string): string` — `"Control+Shift+Space"` → `"Ctrl+Shift+Space"`.
  - `rebind(gs, oldAccel: string, newAccel: string, handler): Promise<{ ok: boolean; error?: "busy" | "invalid" }>`
    — сначала регистрирует новое. Если не вышло, старое остаётся и
    возвращается `busy`. Если вышло, снимает старое.
  - События Tauri:
    - консоль → главное окно: `aya-hotkeys` `{ key: "hotkey_talk" | "hotkey_toggle", accel }`;
    - ответ: `aya-hotkeys-result` `{ key, ok, error? }`.
  - `hotkeyField(key: "hotkey_talk" | "hotkey_toggle", label: string): HTMLElement`.
- Consumes: `hotkeyAction`, `HoldGate` из `src/voice-ctl.ts`.

- [ ] **Step 1: Тесты TS**

```ts
it("разбирает сочетания", () => {
  const k = (key: string, code: string, m: Partial<Record<"ctrlKey"|"altKey"|"shiftKey"|"metaKey", boolean>> = {}) =>
    accelFromEvent({ key, code, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, ...m });
  expect(k(" ", "Space", { ctrlKey: true })).toBe("Control+Space");
  expect(k(" ", "Space", { ctrlKey: true, shiftKey: true })).toBe("Control+Shift+Space");
  expect(k("F9", "F9")).toBe("F9");
  expect(k("a", "KeyA")).toBeNull();
  expect(k("Control", "ControlLeft", { ctrlKey: true })).toBeNull(); // только модификатор
  expect(k("ф", "KeyA", { altKey: true })).toBe("Alt+A"); // по физической клавише, не по раскладке
  expect(accelLabel("Control+Shift+Space")).toBe("Ctrl+Shift+Space");
});
it("занятое сочетание не ломает старое", async () => {
  const reg = new Set(["Control+Space"]);
  const gs = {
    register: async (a: string) => { if (a === "Alt+Tab") throw new Error("busy"); reg.add(a); },
    unregister: async (a: string) => void reg.delete(a),
  };
  expect(await rebind(gs, "Control+Space", "Alt+Tab", () => {})).toEqual({ ok: false, error: "busy" });
  expect([...reg]).toEqual(["Control+Space"]);
  expect((await rebind(gs, "Control+Space", "F9", () => {})).ok).toBe(true);
  expect([...reg]).toEqual(["F9"]);
});
```

- [ ] **Step 2: Тест ядра** `core/tests/test_hotkeys.py::test_hotkeys_roundtrip`:
  - по умолчанию `GET /api/config` → `hotkey_talk == "Control+Space"`, `hotkey_toggle == "Alt+Shift+A"`;
  - после `POST {"hotkey_talk": "F9"}` → `"F9"`;
  - `POST {"hotkey_talk": ""}` не меняет значение.
- [ ] **Step 3: Тесты падают** — `npx vitest run src/hotkey.test.ts`, `core/.venv/Scripts/python.exe -X utf8 -m pytest core/tests/test_hotkeys.py -q`.
- [ ] **Step 4: Реализация.**
  - **`src/hotkey.ts`** — функции выше.
  - **`main.ts`:**
    - `bindHotkey` берёт сочетание из `lastUi.config.hotkey_talk`;
    - добавить `bindToggle()` на `hotkey_toggle`: показать или скрыть
      главное окно. Логика та же, что была в Rust (`isVisible` → `hide`,
      иначе `show` + `setFocus`);
    - слушать `aya-hotkeys` и вызывать `rebind`. При успехе сохранить
      через `POST /api/config`, затем ответить `aya-hotkeys-result`;
    - подсказка `voiceHintSet` показывает `accelLabel(...)` вместо жёсткого «Ctrl+Space».
  - **`hotkeyField`:**
    - по клику пишет «Нажми сочетание…», ловит `keydown` и шлёт
      `emitTo("main", "aya-hotkeys", …)`;
    - ждёт `aya-hotkeys-result` до 2 с и показывает результат: «Сохранено» /
      «Это сочетание занято — выбери другое» / «Так нельзя — добавь Ctrl,
      Alt или Shift»;
    - Esc отменяет.
  - **Консоль:** поле добавить в карточку голоса в `voicePage`.
- [ ] **Step 5: Тесты проходят** — vitest и pytest выше, плюс `npx tsc --noEmit` и `cd src-tauri && cargo test`.
- [ ] **Step 6: Проверка вживую:**
  - назначить F9 → F9 работает, Ctrl+Space больше не работает;
  - перезапустить → F9 по-прежнему работает;
  - назначить Alt+Tab → «занято», F9 продолжает работать.
- [ ] **Step 7: Коммит** — `свои сочетания клавиш: «говорить» и «показать/скрыть»`.

### Task 3: Ядро находит свой Python и пишет только в ~/.aya

**Files:**
- Create: `core/paths.py`
- Modify:
  - `src-tauri/src/core_proc.rs` — `python()`, путь логов (стр. 79);
  - `core/main.py` — `log_dir` (~стр. 1774), `logp = Path("logs/tts-diag.log")` (~стр. 1188), `bases` в `console_models` (~стр. 126).
- Test: `core/tests/test_paths.py`, Rust-тест в `core_proc.rs`.

**Interfaces:**
- Produces (`core/paths.py`, все возвращают `Path` и создают папку):
  - `data_dir()` → `~/.aya`;
  - `logs_dir()` → `~/.aya/logs`;
  - `runtime_dir()` → `~/.aya/runtime`;
  - `voices_local_dir()` → `~/.aya/voices-local`.
- Produces (Rust):
  - `fn python(root: &Path) -> PathBuf` — порядок:
    1. `root/python/pythonw.exe`;
    2. `root/python/python.exe`;
    3. `root/core/.venv/Scripts/pythonw.exe`;
    4. `root/core/.venv/Scripts/python.exe`.
  - `fn logs_dir() -> PathBuf` — `%USERPROFILE%\.aya\logs`.

- [ ] **Step 1: Тесты.**
  - Rust `python_prefers_bundled_runtime` (tempdir): есть оба Python →
    выбирается `python/pythonw.exe`; есть только venv → venv.
  - pytest `test_paths_live_in_home`: с `monkeypatch.setenv("USERPROFILE"/"HOME", tmp)`
    все четыре функции возвращают пути внутри `tmp/.aya` и папки существуют.
- [ ] **Step 2: Тесты падают** — `cd src-tauri && cargo test python_prefers`, `pytest core/tests/test_paths.py -q`.
- [ ] **Step 3: Реализация.**
  - Логи ядра пишутся в `logs_dir()`, а `core.out/err` — в `logs_dir()` из Rust.
  - `os.chdir` в `core/main.py` оставить: папка установки «для пользователя» доступна на запись. Но ничего, что должно пережить обновление, туда не писать.
- [ ] **Step 4: Тесты проходят**, плюс полный `pytest core/tests -q`.
- [ ] **Step 5: Коммит** — `ядро: свой Python рядом с приложением, логи в ~/.aya`.

### Task 4: Наборы зависимостей и голоса по умолчанию для релиза

**Files:**
- Modify: `core/requirements.txt` — полный базовый набор с закреплёнными версиями.
- Create: `core/requirements-gpu.txt`, `core/requirements-local-voices.txt`.
- Modify:
  - `core/voice/tts.py`:
    - убрать `"user_ru"` из таблицы образцов (~стр. 231);
    - `_qwen_ref_text` и образец Qwen → `refs_real/female_3.wav` (~стр. 316);
  - `core/main.py` — все `db.setting("tts_voice_id", "f3")` и сброс на `"f3"` (стр. 1241, 1560) → `"fish_miyuki"`;
  - то же в `core/voice/tts.py`, стр. 390, 410, 685, 843, 896.
- Test: `core/tests/test_release_defaults.py`

**Interfaces:**
- Produces:
  - константа `DEFAULT_VOICE = "fish_miyuki"` в `core/voice/tts.py`, все места используют её;
  - три файла зависимостей.
- Состав наборов (версии — из `core/.venv`: `pip freeze`):
  - **база:** fastapi, uvicorn[standard], httpx, pydantic, discord.py,
    telethon, pypdf, yt-dlp, yandex-music, winrt-*, pycaw, comtypes,
    edge-tts, faster-whisper, numpy, soundfile, psutil, pillow,
    rapidocr-onnxruntime, huggingface_hub;
  - **gpu:** `nvidia-cublas-cu12`, `nvidia-cudnn-cu12==9.*` — для ctranslate2 4.x без torch;
  - **local-voices:** torch и torchaudio с `--index-url https://download.pytorch.org/whl/cu126`,
    f5-tts, qwen-tts, transformers, librosa, scipy, ema-pytorch, x-transformers,
    torchdiffeq, bigvgan-зависимости — что реально импортирует
    `core/voice/tts.py` для F5, VITS2 и Qwen.

- [ ] **Step 1: Тесты** `test_release_defaults.py`:
  - на чистой БД `GET /api/config` → `tts_voice_id == "fish_miyuki"`;
  - удаление своего голоса, который был выбран, возвращает `"fish_miyuki"`;
  - `"user_ru"` нет в `GET /api/voices`;
  - при пустом ключе Fish `tts.speak` идёт через Edge. Подменить `_edge` /
    `_fish_speak` заглушками и проверить, что вызван Edge.
- [ ] **Step 2: Тесты падают.**
- [ ] **Step 3: Реализация.** Наборы проверить на чистом окружении:
  - `uv venv %TEMP%\aya-base --python 3.11`;
  - `uv pip install -r core/requirements.txt`;
  - `AYA_DISABLE_TASKS=1 python -c "import core.main"` без ошибок.
  Ожидается: импорт без `ModuleNotFoundError`. Повторить, добавив `-r core/requirements-local-voices.txt`, и импортировать `f5_tts`, `qwen_tts`.
- [ ] **Step 4: Тесты проходят**, плюс полный pytest.
- [ ] **Step 5: Коммит** — `зависимости по наборам, голос по умолчанию — Fish`.

### Task 5: Докачка наборов и весов по кнопке

**Files:**
- Create: `core/runtime.py`
- Modify:
  - `core/main.py` — вызвать `runtime.activate()` до импорта `core.voice`; новые эндпоинты;
  - `core/voice/stt.py` — `_add_cuda_dlls` ищет ещё `nvidia/*/bin` в папках наборов;
  - `core/voice/tts.py` — пути весов `_F5_DIR/ckpt`, `_VITS_DIR` и `core/voice/qwen`
    сначала ищутся в `voices_local_dir()`, потом рядом с кодом. Так не сломается среда разработки.
- Test: `core/tests/test_runtime.py`

**Interfaces:**
- Consumes: `paths.runtime_dir()`, `paths.voices_local_dir()` (Task 3); файлы наборов (Task 4).
- Produces (`core/runtime.py`):
  - **`PACKS`** — `{"gpu": {"req": "requirements-gpu.txt", "size_mb": …, "title": "Ускорение распознавания (видеокарта NVIDIA)"}, "local_voices": {"req": "requirements-local-voices.txt", "size_mb": …, "title": "Локальные голоса"}}`.
  - **`activate() -> list[str]`** — добавляет в `sys.path` и в DLL-пути каждую
    `runtime_dir()/<pack>`, где есть маркер `.complete`.
  - **`install(name: str) -> dict`**:
    - запускает в фоне `uv pip install --target <runtime_dir>/<name>.part -r <req>`;
    - `uv` ищется рядом с `sys.executable`, потом в `PATH`;
    - после успеха `.part` переименовывается в `<name>` и пишется `.complete`;
    - при ошибке состояние `error`, `.part` удаляется.
  - **`status() -> dict[str, {"state": "none"|"running"|"done"|"error", "error": str, "size_mb": int, "title": str}]`.**
  - **`prefetch_whisper(size: str = "small") -> None`** — фоновой поток, `faster_whisper.download_model(size)`.
  - **`download_voice_weights() -> None`** — `huggingface_hub.hf_hub_download` в `voices_local_dir()`:
    - `frappuccino/dialogs-ru-vits2` / `averaged_G_615000.pth`;
    - `Misha24-10/F5-TTS_RUSSIAN` / `F5TTS_v1_Base_v2/model_last_inference.safetensors`
      и `vocab.txt`. Перед этим сверить SHA-256 с локальным
      `core/voice/f5_ru/ckpt/model_ru_v2_inference.safetensors`. Если не
      совпадает — найти нужный файл в репозитории модели и записать его в план.
  - **`gpu_present() -> bool`** — `ctypes.WinDLL("nvcuda.dll")` загружается без ошибки.
- Эндпоинты:
  - `GET /api/setup/packs` → `{"packs": status(), "whisper": {"state", "model"}, "gpu": bool}`;
  - `POST /api/setup/packs/{name}` → запускает `install`; для `local_voices` затем `download_voice_weights`;
  - `POST /api/setup/whisper/prefetch`.

- [ ] **Step 1: Тесты** (`subprocess` и `huggingface_hub` подменить):
  - `test_install_success_marks_complete`: заглушка `uv` создаёт файл в
    `--target` и выходит с кодом 0 → `status()["gpu"]["state"] == "done"`,
    есть `runtime/gpu/.complete`, `activate()` вернул этот путь;
  - `test_install_failure_cleans_part`: заглушка выходит с кодом 1 →
    `state == "error"`, `runtime/gpu.part` нет, `activate()` пустой
    *(Review Focus 3)*;
  - `test_activate_skips_incomplete`: есть `runtime/local_voices` без `.complete` → не подключается;
  - `test_unknown_pack_404`: `POST /api/setup/packs/evil` → 404;
  - `test_tts_prefers_downloaded_weights`: файл в `voices_local_dir()/f5/…` имеет приоритет над путём рядом с кодом.
- [ ] **Step 2: Тесты падают.**
- [ ] **Step 3: Реализация** по интерфейсам выше.
- [ ] **Step 4: Тесты проходят**, плюс полный pytest.
- [ ] **Step 5: Коммит** — `докачка наборов и весов голосов в ~/.aya по кнопке`.

### Task 6: Ядро для мастера: состояние и проверки ключей

**Files:**
- Create: `core/setup.py`
- Modify: `core/main.py` — эндпоинты.
- Test: `core/tests/test_setup.py`

**Interfaces:**
- Consumes: `_save_llm_section`, `_llm_view`, `CONFIG_FILE` из `core/main.py`; `runtime.status()` (Task 5).
- Produces:
  - **`GET /api/setup`** → `{"done": bool, "step": int, "llm": _llm_view(...), "fish_key_set": bool}`.
  - **`POST /api/setup`** `{"step"?: int, "done"?: bool}`. Хранится в
    настройках `setup_step` и `setup_done`.
  - **`done`** — true, если стоит `setup_done == "1"` **или** в `config.json`
    есть `llm` с `api_key` либо с `base_url` на `127.0.0.1` / `localhost`.
    Так автор не увидит мастер.
  - **`POST /api/setup/llm/check`** `{"type", "base_url", "api_key", "model"}`
    → `{"ok": bool, "error": "key"|"offline"|"url"|"", "models": list[str]}`:
    - запрос списка моделей: для `ollama` — `GET {base}/api/tags`, для
      `openai_compatible` — `GET {base}/models`, таймаут 8 с;
    - при `ok` сохраняет секцию `llm` через `_save_llm_section`;
    - ключ обрезается `strip()`.
  - **`POST /api/setup/fish/check`** `{"key"}` → `{"ok", "error"}`: синтез
    «Привет» с этим ключом через `_fish_speak`-путь. При `ok` ключ сохраняется
    как `fish_api_key`.

- [ ] **Step 1: Тесты** (httpx подменить через `respx` или monkeypatch клиента):

```python
CLOUD = {"type": "ollama", "base_url": "https://ollama.com", "api_key": "abc", "model": "gemma4:31b-cloud"}

def test_setup_resumes_step(client, db_reset, cfg_tmp):
    client.post("/api/setup", json={"step": 3})
    r = client.get("/api/setup").json()
    assert r["step"] == 3 and r["done"] is False

def test_existing_user_skips_wizard(client, db_reset, cfg_tmp):
    cfg_tmp.write_text('{"llm": {"type": "ollama", "base_url": "https://ollama.com", "api_key": "k"}}', encoding="utf-8")
    assert client.get("/api/setup").json()["done"] is True

def test_llm_check_trims_key_and_saves(client, cfg_tmp, fake_http):
    fake_http.get("https://ollama.com/api/tags", json={"models": [{"name": "gemma4:31b-cloud"}]})
    r = client.post("/api/setup/llm/check", json={**CLOUD, "api_key": "  abc\n"}).json()
    assert r["ok"] and "gemma4:31b-cloud" in r["models"]
    assert json.loads(cfg_tmp.read_text("utf-8"))["llm"]["api_key"] == "abc"
    assert "abc" not in json.dumps(client.get("/api/setup").json())

def test_llm_check_bad_key(client, cfg_tmp, fake_http):
    fake_http.get("https://ollama.com/api/tags", status=401)
    assert client.post("/api/setup/llm/check", json=CLOUD).json() == {"ok": False, "error": "key", "models": []}
    assert "api_key" not in json.loads(cfg_tmp.read_text("utf-8") or "{}").get("llm", {})  # плохой ключ не сохраняется

def test_llm_check_offline(client, cfg_tmp, fake_http):
    fake_http.get("https://ollama.com/api/tags", exc=httpx.ConnectError("no"))
    assert client.post("/api/setup/llm/check", json=CLOUD).json()["error"] == "offline"
```

  Фикстуры `cfg_tmp` (подменённый `CONFIG_FILE`, изначально пустой файл) и
  `fake_http` (подмена ответов httpx по URL) добавить в `core/tests/conftest.py`.
- [ ] **Step 2: Тесты падают.**
- [ ] **Step 3: Реализация.**
- [ ] **Step 4: Тесты проходят**, плюс полный pytest.
- [ ] **Step 5: Коммит** — `ядро: состояние мастера и проверка ключей`.

### Task 7: Мастер первого запуска (интерфейс)

**Files:**
- Create:
  - `src/console/setup.ts` — слой мастера;
  - `src/console/setup-logic.ts` и `src/console/setup-logic.test.ts` — чистая логика шагов.
- Modify:
  - `src/console.ts` — `#setup` открывает мастер; старое «предложить
    `wizard` один раз» отключить, если мастер не пройден;
  - `src/main.ts` — на старте `GET /api/setup`; микрофон из `localStorage["aya.micId"]` в `acquireStream`;
  - `src/console.css` — стили `.setup-*`, в духе `.wiz-*`.

**Interfaces:**
- Consumes: Task 6 (`/api/setup*`), Task 5 (`/api/setup/packs*`, `/api/setup/whisper/prefetch`),
  Task 2 (`hotkeyField`), `/api/speak?text=&voice=`, `/api/models`, `/api/persona`,
  `FISH_VOICES` (через `/api/voices`), `@tauri-apps/plugin-autostart`.
- Produces:
  - **`openSetup(): Promise<void>`** в `setup.ts`.
  - **`STEPS`** в `setup-logic.ts`: `["Привет", "Модель", "Голос", "Микрофон", "Кто она", "Готово"]`.
  - **`llmPreset(kind: "ollama_cloud" | "ollama_local" | "openai"): { type: string; base_url: string; model: string; needsKey: boolean }`**:
    - `ollama_cloud` → `{type: "ollama", base_url: "https://ollama.com", model: "gemma4:31b-cloud", needsKey: true}`;
    - `ollama_local` → `{type: "ollama", base_url: "http://127.0.0.1:11434", model: "", needsKey: false}`;
    - `openai` → `{type: "openai_compatible", base_url: "", model: "", needsKey: true}`.
  - **`errorText(code: "key" | "offline" | "url" | string): string`**:
    - `"key"` → «Ключ не подошёл — проверь, что скопировал целиком»;
    - `"offline"` → «Нет интернета — проверь подключение»;
    - `"url"` → «По этому адресу никто не отвечает».
  - **События:** главное окно ↔ консоль `aya-setup-done` (консоль → главное).
- Поведение (по спеке, раздел «Мастер первого запуска»):
  - **Шаг 0:** «Начать», в фоне `POST /api/setup/whisper/prefetch`.
  - **Шаг 1:**
    - проверка ключа запускается сама на `paste` и `change` (debounce 400 мс);
    - «Открыть страницу ключей» открывает `https://ollama.com/settings/keys`
      (`@tauri-apps/plugin-opener` или `open` из shell — что уже есть в проекте);
    - «Другая модель» раскрывает поля: тип, адрес, ключ, модель, причём
      модель выбирается из `models`, а при пустом списке вписывается текстом;
    - «Дальше» неактивна до `ok`, рядом ссылка «Пропустить пока» с предупреждением.
  - **Шаг 2:**
    - ключ Fish → `POST /api/setup/fish/check` → пробная фраза;
    - выбор голоса из Fish-голосов (`POST /api/config {voice_id}`) с «Послушать»;
    - «Пропустить» → ничего не сохраняется. Отдельного `voice_id` для Edge нет:
    при `voice_id = "fish_miyuki"` и пустом ключе ядро само говорит через
    `_edge_speak` (Task 4 это проверяет). Пробная фраза звучит голосом Edge;
    - карточка «Скачать локальные голоса (~N ГБ)» с прогрессом из `GET /api/setup/packs` раз в 2 с.
  - **Шаг 3:**
    - `<select>` из `enumerateDevices()` (audioinput), полоска уровня через `AnalyserNode`, выбор → `localStorage["aya.micId"]`;
    - переключатель «клавишей / голосом „Ая, …“» (`voice_mode: "hotkey"` / `wake_word: true`);
    - `hotkeyField("hotkey_talk", "Клавиша, чтобы говорить")`;
    - состояние Whisper;
    - если `gpu: true` — карточка «Ускорить распознавание».
  - **Шаг 4:**
    - имя (`POST /api/persona` по образцу `wizard.ts`);
    - выбор модели из `/api/models` (только то, что есть; в релизе это Haru);
    - ссылка «Характер и голос подробнее — „Собрать Аю“».
  - **Шаг 5:**
    - галочка автозапуска, по умолчанию включена (`enable()` / `disable()`);
    - «Познакомиться» → `POST /api/setup {done: true}` → `emitTo("main", "aya-setup-done")` → консоль прячется;
    - строка «Ещё возможности — в консоли».
  - **Каждый «Дальше»** → `POST /api/setup {step}`.
  - **`main.ts` на старте:**
    - если `done === false`, спрятать главное окно, показать консоль и
      `emitTo("console", "aya-nav", "setup")`;
    - на `aya-setup-done` показать главное окно и сказать приветствие
      (`speakOnce("Привет! Я Ая. Зови меня, когда захочешь поговорить.", "joyful")`).
  - **В разделе консоли «Внешний вид»** — кнопка «Пройти настройку заново» (`#setup`).

- [ ] **Step 1: Тесты** `setup-logic.test.ts`:
  - `llmPreset` — три пресета точно как выше;
  - `errorText` — три кода и неизвестный код → «Что-то пошло не так — попробуй ещё раз».
- [ ] **Step 2: Тест падает.**
- [ ] **Step 3: Реализация** `setup-logic.ts`, затем `setup.ts` и правки `console.ts` / `main.ts`.
- [ ] **Step 4:** `npx vitest run`, `npx tsc --noEmit` — всё зелёное.
- [ ] **Step 5: Проверка в браузере.** Поднять ядро на запасном порту с
  временным HOME, открыть `http://127.0.0.1:<порт>/console.html#setup`.
  Пройти все шаги:
  - пустой ключ;
  - неверный ключ → «Ключ не подошёл»;
  - выключенная сеть (подменить `base_url` на `http://10.255.255.1`) → «Нет интернета» или «никто не отвечает»;
  - закрыть на шаге 3, открыть снова → шаг 3 *(Review Focus 4)*.
- [ ] **Step 6: Коммит** — `мастер первого запуска`.

### Task 8: Переносной Python и установщик

**Files:**
- Create: `scripts/build-runtime.ps1`
- Modify: `src-tauri/tauri.conf.json` — `version` `"1.0.0"`, `bundle.resources`, `bundle.windows.nsis.installMode`; `.gitignore` — `src-tauri/runtime/`, `src-tauri/stage/`.

**Interfaces:**
- Consumes: `core/requirements.txt` (Task 4); `python()` и поиск `core/main.py` вверх от exe (Task 3) — в установке ресурсы лежат рядом с `aya.exe`.
- Produces:
  - **`scripts/build-runtime.ps1`:**
    - скачивает python-build-standalone `cpython-3.11.*-x86_64-pc-windows-msvc-install_only.tar.gz`
      (последний выпуск 3.11, URL и SHA-256 — константы в начале скрипта) в `src-tauri/runtime/python`;
    - ставит `uv pip install --python src-tauri/runtime/python/python.exe -r core/requirements.txt`;
    - кладёт `uv.exe` в `src-tauri/runtime/python/`;
    - собирает `src-tauri/stage/core` из `git ls-files core` без `core/tests/**`,
      `*.safetensors`, `*.pth`, `core/voice/f5_ru/refs_user/**`.
  - **`tauri.conf.json`:**
    - `"bundle": {"resources": {"runtime/python/": "python/", "stage/core/": "core/"}, "windows": {"nsis": {"installMode": "currentUser"}}}`;
    - `"targets": ["nsis"]` остаётся.

- [ ] **Step 1:** `pwsh scripts/build-runtime.ps1`. Ожидается:
  - `src-tauri/runtime/python/python.exe -c "import fastapi, faster_whisper, edge_tts"` без ошибок;
  - в `stage/core` нет `refs_user`, `.pth`, `.safetensors`.
- [ ] **Step 2:** `npx tauri build`. Ожидается: `src-tauri/target/release/bundle/nsis/Aya_1.0.0_x64-setup.exe`, размер в пределах 100–250 МБ. Записать фактический размер.
- [ ] **Step 3: Проверка в Windows Sandbox** (чистая система; включается в
  «Компонентах Windows»). Это ручная проверка: попросить автора открыть
  Sandbox или открыть её самому, если разрешено.
  - Установка без UAC.
  - Первый запуск открывает мастер.
  - Ядро отвечает (`http://127.0.0.1:8330/api/ui`).
  - Логи в `%USERPROFILE%\.aya\logs`.
- [ ] **Step 4: Коммит** — `установщик: свой Python и ядро внутри`.

### Task 9: Обновления в один клик

**Files:**
- Modify:
  - `src-tauri/Cargo.toml` — `tauri-plugin-updater = "2"`, `tauri-plugin-process = "2"`;
  - `src-tauri/src/lib.rs` — `.plugin(...)`;
  - `src-tauri/capabilities/*.json` — разрешения `updater:default`, `process:allow-restart`;
  - `src-tauri/tauri.conf.json`:
    - `"bundle": {"createUpdaterArtifacts": true}`;
    - `"plugins": {"updater": {"endpoints": ["https://github.com/parh0m2007/Aya/releases/latest/download/latest.json"], "pubkey": "<публичный ключ>", "windows": {"installMode": "passive"}}}`;
  - `package.json` — `@tauri-apps/plugin-updater`, `@tauri-apps/plugin-process`.
- Create: `src/updater.ts`, `src/updater-logic.ts` и `src/updater-logic.test.ts`.

**Interfaces:**
- Produces:
  - **`updateLine(current: string, next: string): string | null`** —
    `"1.0.0", "1.1.0"` → «Доступна версия 1.1.0 — обновить?». Та же или
    более старая версия → `null`.
  - **`checkForUpdate(): Promise<void>`** — через 30 с после старта и раз в
    6 ч. Если есть обновление, в главном окне пузырь с кнопкой «Обновить»,
    в консоли полоска. По кнопке `downloadAndInstall()` → `relaunch()`.
    Ошибки сети молча игнорируются.
- Ключ подписи:
  - `npx tauri signer generate -w %USERPROFILE%\.tauri\aya.key` — автор
    запускает сам или разрешает Claude; пароль вводит автор;
  - публичный ключ — в `tauri.conf.json`;
  - приватный ключ и пароль автор кладёт в секреты репозитория
    `TAURI_SIGNING_PRIVATE_KEY` и `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`;
  - приватный ключ **не коммитится** и не выводится в чат.

- [ ] **Step 1: Тест** `updateLine` — новее, та же и старее, плюс `"1.0.0"` против `"1.0.10"` (сравнение по числам, не по строке).
- [ ] **Step 2: Тест падает → реализация → тест проходит** — `npx vitest run src/updater-logic.test.ts`.
- [ ] **Step 3:** `npx tauri build` с ключом в окружении. Ожидается: рядом с установщиком `.sig`.
- [ ] **Step 4: Коммит** — `обновления в один клик через GitHub Releases`.

### Task 10: Сборка по тегу в GitHub Actions

**Files:**
- Create: `.github/workflows/release.yml`, `.github/workflows/tests.yml`.

**Interfaces:**
- Consumes: `scripts/build-runtime.ps1` (Task 8), ключи (Task 9).
- Produces:
  - **`tests.yml`** — на `push` и `pull_request`, `windows-latest`:
    - `npm ci`, `npx tsc --noEmit`, `npx vitest run`;
    - `uv venv` + `uv pip install -r core/requirements.txt pytest`, `pytest core/tests -q`;
    - `cargo test --manifest-path src-tauri/Cargo.toml`.
  - **`release.yml`** — на `push` тегов `v*`, `windows-latest`:
    - `npm ci` → `pwsh scripts/build-runtime.ps1`;
    - `tauri-apps/tauri-action@v0` с параметрами `tagName: ${{ github.ref_name }}`,
      `releaseName: "Aya ${{ github.ref_name }}"`, `releaseDraft: true`,
      `includeUpdaterJson: true`, `releaseBody` — шаги про SmartScreen из README;
    - env `GITHUB_TOKEN`, `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`.
  - Релиз создаётся **черновиком**: автор сам нажимает «Publish».

- [ ] **Step 1:** проверить YAML локально (`npx --yes @action-validator/cli .github/workflows/*.yml` или аналог). Ожидается: без ошибок.
- [ ] **Step 2: Коммит** — `сборка релиза и тесты в GitHub Actions`.
- [ ] Настоящий прогон — в Task 11, после публикации.

### Task 11: Подготовка открытого репозитория и публикация

**Files:**
- Create:
  - `scripts/check_public.py` и `core/tests/test_check_public.py`;
  - `LICENSE` — MIT, `Copyright (c) 2026 parh0m2007`;
  - `docs/smartscreen.png` — снимок окна SmartScreen с выделенными «Подробнее» и «Выполнить в любом случае».
- Modify:
  - `README.md` — разделы «Установка», «Если Windows предупреждает», «Где взять ключи», «Для разработчиков»;
  - `.gitignore` — веса моделей, `core/voice/f5_ru/refs_user/`, `public/models/micu/`.

**Interfaces:**
- Produces: `scripts/check_public.py [--tree <ref>]` → код выхода 0 или 1 и
  список находок `path:line: причина`. Проверяет отслеживаемые файлы:
  - размер больше 95 МБ;
  - похожие на ключи строки: `sk-[A-Za-z0-9]{20,}`, `ghp_[A-Za-z0-9]{30,}`,
    `y0_[A-Za-z0-9_-]{20,}` (токен Яндекса), `\b\d{8,10}:[A-Za-z0-9_-]{30,}\b`
    (токен Telegram-бота), `"api_key"\s*:\s*"[^"]{8,}"`, длинные hex-строки
    рядом со словами key/token/secret;
  - пути `refs_user/`, `aya.db`, `config.json` с содержимым;
  - список личных слов из `scripts/private-words.txt`. Этот файл автор
    заполняет сам, и он в `.gitignore`.

- [ ] **Step 1: Тест** `test_check_public.py`: во временном git-репозитории файл с `ghp_` + 36 символов и файл 100 МБ (разреженный) дают код 1 и две находки; чистое дерево даёт код 0.
- [ ] **Step 2: Тест падает → реализация → тест проходит.**
- [ ] **Step 3:**
  - `git rm --cached` для `core/voice/f5_ru/ckpt/model_ru_v2_inference.safetensors`,
    `core/voice/dialogs_vits2/averaged_G_615000.pth`, `core/voice/f5_ru/refs_user/*`;
  - сами файлы на диске автора **не удалять**;
  - `python scripts/check_public.py` → код 0. Каждую находку разобрать с автором.
- [ ] **Step 4:** LICENSE, README, снимок SmartScreen. Коммит — `подготовка к публикации: лицензия, README, проверка секретов`.
- [ ] **Step 5: Чистая история.**
  - `git checkout --orphan public-main`, `git add -A` (уважая `.gitignore`);
  - один коммит `Ая 1.0.0`;
  - `python scripts/check_public.py --tree public-main` → код 0;
  - полная история остаётся в `aya-redesign` и `main` локально.
- [ ] **Step 6: Спросить автора в чате** и дождаться явного «да»:
  - создан ли пустой репозиторий `parh0m2007/Aya`;
  - положены ли секреты `TAURI_SIGNING_PRIVATE_KEY` и `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`;
  - можно ли отправлять.
  После «да»: `git remote add origin https://github.com/parh0m2007/Aya.git`, `git push origin public-main:main`.
- [ ] **Step 7:** после «да» автора — `git tag v1.0.0 && git push origin v1.0.0`.
  - Проверить в Actions, что `release.yml` прошёл, а в Releases лежит черновик с `.exe`, `.sig` и `latest.json`.
  - Автор жмёт «Publish».
- [ ] **Step 8: Итоговая проверка по спеке** («Как понять, что готово»):
  - в Windows Sandbox скачать установщик со страницы релиза, установить, пройти мастер с ключами, услышать приветствие;
  - то же без ключа Fish;
  - свёрнутая Ая отвечает на клавишу;
  - своя клавиша работает после перезапуска.
  Обновление N → N+1 проверяется на следующем релизе `v1.0.1`: настройки и ключи на месте.
