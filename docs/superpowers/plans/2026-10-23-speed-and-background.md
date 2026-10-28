# Ускорение озвучки и фоновая работа — план реализации

> **Для агентов:** ОБЯЗАТЕЛЬНЫЙ СУБ-СКИЛЛ: `superpowers:subagent-driven-development` (рекомендовано) или `superpowers:executing-plans`. Шаги помечены `- [ ]`.

**Goal:** Ая быстрее начинает говорить (короткий первый кусок + прогретый кэш частых фраз), не дросселирует таймеры в свёрнутом окне и запускается релизной сборкой вместо dev-режима.

**Architecture:** Три независимых слоя: (1) фронтенд режет первую группу озвучки короче — чистая функция `speechChunkLimit`/`groupSpeech` в отдельном модуле с vitest-тестами; (2) ядро при старте догревает TTS-кэш самыми частыми короткими репликами Аи из истории (только fish, только при наличии ключа, ошибка — стоп); (3) Rust добавляет WebView2-флаги против фонового дросселирования, релизная сборка даёт быстрый exe и отдельный прод-лаунчер.

**Tech Stack:** TypeScript/Vite/vitest, Python/FastAPI/pytest, Rust/Tauri 2, WebView2, PowerShell 5.1.

**Spec:** выбор пользователя 8 + 1 + 2 + 5 из меню от 2026-10-23 (отдельного файла спеки нет; цель и ограничения ниже).

## Global Constraints

- Работаем прямо в `D:\Aya` (без worktree): пользователь тестирует живьём через `npm run tauri dev` + ядро из `start-aya.cmd`.
- `start-aya.cmd` остаётся dev-лаунчером; прод — новый `start-aya-prod.cmd`. Не заменять.
- Фронт-тесты: `npm test` (vitest). Бэк-тесты: `core\.venv\Scripts\python.exe -m pytest core\tests -q` из `D:\Aya`.
- PowerShell 5.1: не использовать `&&`; вывод pytest в файл не редиректить (UTF-16).
- Русские комментарии и тексты — в стиле проекта.
- Ядро: прогрев не должен блокировать живые запросы, грузить qwen/f5 как fallback и долбить сеть после первой ошибки.
- Первый кусок озвучки — 110 символов, последующие — 230 (как сейчас).

## Review Focus

1. Нет fish-ключа/нет сети при прогреве — ядро не падает, не печатает пачку трейсбеков, не грузит qwen (тесты Task 2).
2. Пустая история сообщений — прогрев молча ничего не делает (тест Task 2).
3. Граница лимита группировки — ровно в лимит склеиваем, на символ больше режем (тест Task 1).
4. Первая фраза длиннее 110 символов — уходит как есть, без потери текста и пустых кусков (тест Task 1).
5. Одновременный запуск dev- и release-приложения — общий WebView2-профиль `dev.aya.companion` и один глобальный хоткей; в отчёте предупредить «закрыть dev перед прод» (ручная проверка, Task 4).

---

### Task 1: Фронтенд — короткий первый кусок озвучки

**Files:**
- Create: `src/speech-group.ts`
- Modify: `src/main.ts` (импорт вверху; `pushGroup` внутри `sendToAya`, строки ~168–196)
- Test: `src/speech-group.test.ts`

**Interfaces:**
- Produces: `FIRST_SPEECH_CHUNK: number = 110`, `SPEECH_CHUNK: number = 230`, `speechChunkLimit(first: boolean): number`, `groupSpeech(prev: string, piece: string, limit: number): { buf: string; flush: string | null }`.

- [ ] **Step 1: Написать падающий тест** — создать `src/speech-group.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { FIRST_SPEECH_CHUNK, SPEECH_CHUNK, groupSpeech, speechChunkLimit } from "./speech-group";

describe("speechChunkLimit", () => {
  it("первый кусок короче последующих", () => {
    expect(FIRST_SPEECH_CHUNK).toBe(110);
    expect(SPEECH_CHUNK).toBe(230);
    expect(speechChunkLimit(true)).toBe(110);
    expect(speechChunkLimit(false)).toBe(230);
  });
});

describe("groupSpeech", () => {
  it("первый кусок кладёт в буфер без сброса", () => {
    expect(groupSpeech("", "Привет!", 110)).toEqual({ buf: "Привет!", flush: null });
  });

  it("склеивает, пока влезает в лимит (включая ровную границу)", () => {
    expect(groupSpeech("Привет!", "Как дела?", 110)).toEqual({ buf: "Привет! Как дела?", flush: null });
    const prev = "x".repeat(100);
    expect(groupSpeech(prev, "123456789", 110)).toEqual({ buf: `${prev} 123456789`, flush: null });
  });

  it("на символе сверх лимита сбрасывает накопленное и начинает новый кусок", () => {
    const prev = "x".repeat(100);
    expect(groupSpeech(prev, "1234567890", 110)).toEqual({ buf: "1234567890", flush: prev });
  });

  it("длинная первая фраза уходит как есть, ничего не теряя", () => {
    const long = "слово ".repeat(40).trim();
    expect(groupSpeech("", long, 110)).toEqual({ buf: long, flush: null });
  });
});
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `npx vitest run src/speech-group.test.ts`
Expected: FAIL — `Failed to resolve import "./speech-group"`.

- [ ] **Step 3: Реализовать `src/speech-group.ts`**

```ts
/* Группировка реплик Аи для озвучки. Первый кусок короче — голос стартует раньше;
   дальше куски до 230 символов, чтобы не плодить лишние запросы к TTS. */
export const FIRST_SPEECH_CHUNK = 110;
export const SPEECH_CHUNK = 230;

export function speechChunkLimit(first: boolean): number {
  return first ? FIRST_SPEECH_CHUNK : SPEECH_CHUNK;
}

/* prev — накопленный кусок, piece — новая фраза. Если вместе влезают в limit —
   склеиваем; иначе flush = prev (пора озвучивать), buf = piece. */
export function groupSpeech(
  prev: string,
  piece: string,
  limit: number,
): { buf: string; flush: string | null } {
  if (prev && prev.length + piece.length + 1 <= limit) {
    return { buf: `${prev} ${piece}`, flush: null };
  }
  return { buf: piece, flush: prev || null };
}
```

- [ ] **Step 4: Подключить в `src/main.ts`**

Добавить импорт рядом с `import { initVoice, ... } from "./voice-ctl";`:

```ts
import { groupSpeech, speechChunkLimit } from "./speech-group";
```

В `sendToAya` рядом с `let groupBuf = "";` добавить `let firstGroupQueued = false;`. Заменить тело `pushGroup` на:

```ts
  const pushGroup = (piece: string | null = null) => {
    if (piece) {
      const clean = piece.trim();
      if (clean.startsWith('{"action"')) return; // сырой JSON экшена не озвучиваем
      const { buf, flush } = groupSpeech(groupBuf, clean, speechChunkLimit(!firstGroupQueued));
      if (flush) {
        speakQueue.push(flush);
        firstGroupQueued = true;
      }
      groupBuf = buf;
      spokeAny = true;
      return;
    }
    if (groupBuf) {
      speakQueue.push(groupBuf);
      firstGroupQueued = true;
      spokeAny = true;
    }
    groupBuf = "";
  };
```

- [ ] **Step 5: Прогнать тесты и типы**

Run: `npm test` → Expected: PASS (8 старых + 5 новых).
Run: `npx tsc --noEmit` → Expected: exit 0.

- [ ] **Step 6: Коммит**

```bash
git add src/speech-group.ts src/speech-group.test.ts src/main.ts
git commit -m "озвучка: первый кусок 110 символов — голос стартует раньше"
```

---

### Task 2: Ядро — прогрев TTS-кэша частыми репликами

**Files:**
- Modify: `core/voice/tts.py` (после `warm()`, строки ~336–352; сам `warm()` получает вызов `warm_phrases()`)
- Test: `core/tests/test_tts_warm.py`

**Interfaces:**
- Produces: `WARM_PHRASES: int = 6`, `frequent_phrases(limit: int = WARM_PHRASES) -> list[str]`, `warm_phrases(limit: int = WARM_PHRASES) -> None`.
- `warm()` вызывает `warm_phrases()` после `modelmgr.start()`.

- [ ] **Step 1: Написать падающие тесты** — создать `core/tests/test_tts_warm.py`:

```python
import pytest

import core.db as db
import core.voice.tts as tts


def test_frequent_phrases_empty_db(db_reset):
    assert tts.frequent_phrases(5) == []


def test_frequent_phrases_takes_frequent_short_assistant_replies(db_reset):
    for _ in range(3):
        db.add_message("assistant", "Готово!")
    db.add_message("assistant", "Привет!")
    db.add_message("assistant", "x" * 200)
    db.add_message("user", "Готово!")

    assert tts.frequent_phrases(1) == ["Готово!"]
    assert set(tts.frequent_phrases(5)) == {"Готово!", "Привет!"}


def test_warm_phrases_skips_without_fish_key(db_reset, monkeypatch):
    db.set_setting("tts_voice_id", "fish_miyuki")
    monkeypatch.setattr(
        tts, "frequent_phrases", lambda limit=tts.WARM_PHRASES: pytest.fail("без ключа не греем")
    )
    tts.warm_phrases()


def test_warm_phrases_caches_top_phrases(db_reset, monkeypatch):
    db.set_setting("tts_voice_id", "fish_miyuki")
    db.set_setting("fish_api_key", "test-key")
    monkeypatch.setattr(tts, "frequent_phrases", lambda limit=tts.WARM_PHRASES: ["Готово!", "Привет!"])
    seen = []

    async def fish(text, vid):
        seen.append((text, vid))
        return ("/tmp/warm.mp3", 0)

    monkeypatch.setattr(tts, "_fish_speak", fish)
    tts.warm_phrases()
    assert seen == [("Готово!", "fish_miyuki"), ("Привет!", "fish_miyuki")]


def test_warm_phrases_stops_on_first_failure(db_reset, monkeypatch):
    db.set_setting("tts_voice_id", "fish_miyuki")
    db.set_setting("fish_api_key", "test-key")
    monkeypatch.setattr(tts, "frequent_phrases", lambda limit=tts.WARM_PHRASES: ["Раз", "Два"])
    seen = []

    async def fish(text, vid):
        seen.append(text)
        return None

    monkeypatch.setattr(tts, "_fish_speak", fish)
    tts.warm_phrases()
    assert seen == ["Раз"]


def test_warm_runs_phrase_warm(monkeypatch):
    calls = []
    monkeypatch.setattr(tts, "_get_vits", lambda: None)
    monkeypatch.setattr(tts, "_get_f5", lambda: (_ for _ in ()).throw(RuntimeError("skip")))
    monkeypatch.setattr(tts.modelmgr, "start", lambda: None)
    monkeypatch.setattr(tts, "warm_phrases", lambda: calls.append(1))
    tts.warm()
    assert calls == [1]
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `core\.venv\Scripts\python.exe -m pytest core\tests\test_tts_warm.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'frequent_phrases'`.

- [ ] **Step 3: Реализовать в `core/voice/tts.py`**

Добавить над `def warm()`:

```python
WARM_PHRASES = 6


def frequent_phrases(limit: int = WARM_PHRASES) -> list[str]:
    """Самые частые короткие реплики Аи из истории — их выгодно держать в TTS-кэше."""
    rows = db.query(
        "SELECT text FROM messages WHERE role='assistant' AND length(text) BETWEEN 2 AND 60 "
        "GROUP BY text ORDER BY COUNT(*) DESC, MAX(id) DESC LIMIT ?",
        (limit,),
    )
    return [r[0].strip() for r in rows if r[0] and r[0].strip()]


def warm_phrases(limit: int = WARM_PHRASES) -> None:
    """Греет кэш fish-голоса частыми репликами: они зазвучат мгновенно.

    Только fish и только при наличии ключа: fallback на qwen/f5 здесь не нужен —
    не хотим на старте тянуть тяжёлые модели. Первая ошибка — стоп, не долбим сеть.
    """
    vid = db.setting("tts_voice_id", "f3")
    if vid not in FISH_VOICES or not db.setting("fish_api_key", "").strip():
        return
    import asyncio

    for text in frequent_phrases(limit):
        try:
            if asyncio.run(_fish_speak(text, vid)) is None:
                return
        except Exception:
            return
```

В конец `warm()` (после `modelmgr.start()`) добавить `warm_phrases()`.

- [ ] **Step 4: Прогнать тесты**

Run: `core\.venv\Scripts\python.exe -m pytest core\tests\test_tts_warm.py -q` → PASS.
Run: `core\.venv\Scripts\python.exe -m pytest core\tests -q` → PASS (все прежние + новые).

- [ ] **Step 5: Проверить на живом ядре после перезапуска**

После рестарта ядра в `logs\core.out` не должно быть трейсбеков, в `~/.aya/tts-cache` появляются mp3 под топ-фразы. (Живой рестарт делает пользователь; проверка по логам — в конце.)

- [ ] **Step 6: Коммит**

```bash
git add core/voice/tts.py core/tests/test_tts_warm.py
git commit -m "tts: прогрев кэша частыми репликами при старте ядра"
```

---

### Task 3: Rust — не дросселировать таймеры в скрытом окне

**Files:**
- Modify: `src-tauri/src/lib.rs:21–27`

**Interfaces:**
- Produces: WebView2 получает флаги `--disable-background-timer-throttling`, `--disable-backgrounding-occluded-windows`, `--disable-renderer-backgrounding` в дополнение к автоплей-флагам.

- [ ] **Step 1: Добавить флаги**

Заменить строку аргументов в `additional_browser_args` на:

```rust
                    builder = builder.additional_browser_args(
                        "--autoplay-policy=no-user-gesture-required \
                         --disable-background-timer-throttling \
                         --disable-backgrounding-occluded-windows \
                         --disable-renderer-backgrounding \
                         --disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection",
                    );
```

- [ ] **Step 2: Проверить компиляцию**

Run: `cargo check --manifest-path src-tauri\Cargo.toml`
Expected: exit 0 (если `tauri dev` сейчас пересобирает — cargo может подождать лок; это нормально).

- [ ] **Step 3: Проверить флаги в живом процессе** (после перезапуска окна — `tauri dev` перезапустит сам при изменении lib.rs)

Run:
```
Get-CimInstance Win32_Process -Filter "name='msedgewebview2.exe'" | Where-Object { $_.CommandLine -match 'disable-background-timer-throttling' } | Measure-Object
```
Expected: Count ≥ 1. Если 0 — окно ещё не перезапустилось; повторить после рестарта.

- [ ] **Step 4: Коммит**

```bash
git add src-tauri/src/lib.rs
git commit -m "webview2: не дросселировать таймеры в фоне (инициатива/VAD при свёрнутом окне)"
```

---

### Task 4: Release-сборка и прод-лаунчер

**Files:**
- Create: `start-aya-prod.cmd`
- Build artifact (не коммитится): `src-tauri/target/release/aya.exe`

**Interfaces:**
- Produces: `start-aya-prod.cmd` — поднимает ядро (если порт 8330 закрыт) и запускает релизный exe, как `start-aya.cmd`, но без Vite/dev.

- [ ] **Step 1: Создать `start-aya-prod.cmd`**

```bat
@echo off
title Aya launcher (release)
cd /d "%~dp0"

REM ============================================================
REM  Aya launcher (release exe): core + built app, no Vite.
REM  Для повседневки. Разработка — start-aya.cmd.
REM ============================================================

call :port_busy 8330
if %ERRORLEVEL% EQU 0 (
  echo [core] already running - skipping.
) else (
  if not exist logs mkdir logs
  for %%F in (core.out core.err) do (
    if exist logs\%%F (
      if exist logs\%%F.1 del logs\%%F.1
      ren logs\%%F %%F.1 >nul
    )
  )
  echo [core] starting...
  start "Aya core" /min cmd /c "core\.venv\Scripts\pythonw.exe core\main.py 1>>logs\core.out 2>>logs\core.err"
)

echo [window] starting Aya (release)...
start "" "%~dp0src-tauri\target\release\aya.exe"
goto :eof

:port_busy
netstat -ano | findstr /R /C:":%1 .*LISTENING" >nul 2>&1
exit /b %ERRORLEVEL%
```

- [ ] **Step 2: Собрать релиз**

Run (долго, 5–15 мин): `npm run tauri build -- --no-bundle`
Expected: exit 0; `vitest` не запускается (это отдельно), `tsc && vite build` проходят.

- [ ] **Step 3: Проверить артефакт**

Run: `Get-Item src-tauri\target\release\aya.exe | Select-Object FullName, Length, LastWriteTime`
Expected: файл существует, `LastWriteTime` — свежий.

- [ ] **Step 4: Коммит**

```bash
git add start-aya-prod.cmd
git commit -m "запуск: прод-лаунчер релизной сборки (быстрый старт без Vite)"
```

- [ ] **Step 5: Передать пользователю на живую проверку**

Попросить: закрыть dev-Аю (трей → «Выход», остановить терминал `tauri dev`), запустить `start-aya-prod.cmd`, проверить хоткей/голос/трей/инициативу в свёрнутом окне. Не запускать dev и release одновременно (общий профиль WebView2 и один хоткей).

---

## Проверка после всех задач

- `npm test` → зелёный; `npx tsc --noEmit` → exit 0.
- `core\.venv\Scripts\python.exe -m pytest core\tests -q` → зелёный.
- `cargo check --manifest-path src-tauri\Cargo.toml` → exit 0.
- Флаги WebView2 видны в командной строке процесса.
- Прод-лаунчер запускает приложение; dev-лаунчер не изменён.
