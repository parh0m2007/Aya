"""Дневник Аи: каждый вечер она пишет короткую запись о прошедшем дне.

Запись — от первого лица, по реальным разговорам дня и активности на ПК.
Хранится в SQLite (таблица diary), смотреть и удалять — в консоли → «Память».
"""
import asyncio
import datetime as dt
import threading
import time
import traceback

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS diary(
  day TEXT PRIMARY KEY,
  ts REAL, text TEXT, mood TEXT, messages INTEGER
);
"""

WRITE_HOUR = 22  # после этого часа пишем запись за сегодня
MIN_MESSAGES = 3
_thread: threading.Thread | None = None
_stop = threading.Event()
_busy = threading.Lock()


def _ensure() -> None:
    db.execute(SCHEMA.strip())


def _load_config() -> dict:
    """~/.aya/config.json напрямую: импорт core.main из потока заново выполнил бы модуль ядра."""
    import json
    from pathlib import Path

    try:
        return json.loads((Path.home() / ".aya" / "config.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def enabled() -> bool:
    return db.setting("diary_enabled", "1") != "0"


def _bounds(day: dt.date) -> tuple[float, float]:
    start = dt.datetime.combine(day, dt.time.min).timestamp()
    return start, start + 86400


def day_messages(day: dt.date) -> list[tuple[str, str]]:
    a, b = _bounds(day)
    rows = db.query(
        "SELECT role,text FROM messages WHERE ts>=? AND ts<? AND role IN ('user','assistant','aya') ORDER BY id",
        (a, b),
    )
    return [(r, t) for r, t in rows if t and not str(t).startswith("initiative:")]


def entries(limit: int = 60) -> list[dict]:
    _ensure()
    rows = db.query("SELECT day,ts,text,mood,messages FROM diary ORDER BY day DESC LIMIT ?", (limit,))
    return [{"day": r[0], "ts": r[1], "text": r[2], "mood": r[3], "messages": r[4]} for r in rows]


def has(day: dt.date) -> bool:
    _ensure()
    return bool(db.query("SELECT 1 FROM diary WHERE day=?", (day.isoformat(),)))


def delete(day: str) -> bool:
    _ensure()
    if not db.query("SELECT 1 FROM diary WHERE day=?", (day,)):
        return False
    db.execute("DELETE FROM diary WHERE day=?", (day,))
    return True


def build_prompt(name: str, day: dt.date, msgs: list[tuple[str, str]], activity: str) -> str:
    lines = []
    for role, text in msgs[-60:]:
        who = "Я" if role in ("assistant", "aya") else "Он"
        lines.append(f"{who}: {str(text).replace(chr(10), ' ')[:220]}")
    return (
        f"Ты — {name}. Сейчас вечер {day.strftime('%d.%m.%Y')}. Напиши запись в свой личный дневник о сегодняшнем дне "
        "с пользователем: 3–6 предложений, от первого лица, тепло и по-человечески, с конкретными деталями из разговоров "
        "(о чём говорили, что он делал, что тебе запомнилось или понравилось). Без списков и заголовков, без выдумок "
        "сверх того, что было.\n\n"
        + (f"Активность на ПК за день: {activity}\n\n" if activity else "")
        + "Разговоры дня:\n"
        + "\n".join(lines)
    )


async def write(day: dt.date | None = None, force: bool = False) -> dict | None:
    """Написать (или переписать при force) запись за день. None — писать не о чем."""
    from . import persona
    from .behavior import proactive
    from .llm.base import make_adapters, stream_llm
    from .reply import detect_mood

    _ensure()
    day = day or dt.date.today()
    if has(day) and not force:
        return None
    msgs = day_messages(day)
    if len([1 for r, _ in msgs if r == "user"]) < (1 if force else MIN_MESSAGES):
        return None
    adapters = make_adapters(_load_config())
    if not adapters:
        return None
    activity = proactive.summary_text() if day == dt.date.today() else ""
    prompt = build_prompt(persona.name(), day, msgs, activity)
    text = "".join([c async for c in stream_llm(adapters, "Ты ведёшь личный дневник.", [{"role": "user", "content": prompt}], first_timeout=60)])
    text = text.strip()
    if not text:
        return None
    mood = detect_mood(text)
    db.execute(
        "INSERT OR REPLACE INTO diary(day,ts,text,mood,messages) VALUES(?,?,?,?,?)",
        (day.isoformat(), time.time(), text, mood, len(msgs)),
    )
    db.log_event("diary", f"запись за {day.isoformat()}")
    return {"day": day.isoformat(), "text": text, "mood": mood, "messages": len(msgs)}


def _tick() -> None:
    if not enabled() or not _busy.acquire(blocking=False):
        return
    try:
        today = dt.date.today()
        yesterday = today - dt.timedelta(days=1)
        # пропустили вчерашний вечер (ПК был выключен) — допишем
        if not has(yesterday) and len(day_messages(yesterday)) >= MIN_MESSAGES:
            asyncio.run(write(yesterday))
        if dt.datetime.now().hour >= WRITE_HOUR and not has(today):
            asyncio.run(write(today))
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    finally:
        _busy.release()


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()

    def loop():
        time.sleep(60)  # не мешаем старту ядра
        while not _stop.is_set():
            _tick()
            _stop.wait(600)

    _thread = threading.Thread(target=loop, daemon=True, name="aya-diary")
    _thread.start()


def stop() -> None:
    _stop.set()
