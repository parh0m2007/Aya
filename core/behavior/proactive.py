"""Проактивность 2.0: Ая чувствует твой день.

Сэмплер каждые 15 с запоминает активное окно (GetForegroundWindow) и время
с последнего ввода (GetLastInputInfo) — никакой VLM не нужно, только заголовки.
Накапливает «сколько минут в каком приложении» за день.

Правила инициативы (каждый со своим кулдауном):
- morning — привет в 8-11 утра, раз в сутки;
- break   — >2.5ч в одном приложении без передышки (и пользователь реально
  за клавиатурой), не чаще 1 в 2.5ч;
- idle    — нет ввода 100+ минут, раз в 2ч, мягкое «ты тут?»;
- digest  — после 21:00 вечерний дайджест «чем занимались», раз в сутки.

Тишина: настройки proactive_enabled / focus_mode / digest_enabled.
"""

import ctypes
import threading
import time

from .. import db

SAMPLE_PERIOD = 15.0
BREAK_AFTER = 2.5 * 3600
BREAK_COOLDOWN = 2.5 * 3600
IDLE_AFTER = 100 * 60
IDLE_COOLDOWN = 2 * 3600
ACTIVE_INPUT_WINDOW = 120  # секунд без ввода, при которых человек «за клавиатурой»

_lock = threading.Lock()
_state = {"app": "", "title": "", "since": 0.0, "idle": 0.0}
app_summary: dict[str, float] = {}
_day = {"key": ""}
_thread: threading.Thread | None = None
_stop = threading.Event()


# ── системные заглядывания (в тестах подменяются) ──────────────────
def _fg_title() -> str:
    if not hasattr(ctypes, "windll"):
        return ""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value.strip()
    except Exception:
        return ""


def _idle_seconds() -> float:
    if not hasattr(ctypes, "windll"):
        return 0.0
    try:
        user32 = ctypes.windll.user32

        class LastInputInfo(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LastInputInfo()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        tick = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return ((tick - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return 0.0


def app_name(title: str) -> str:
    """«Page - Google Chrome» / «C:\sys\cmd.exe» → человеческое имя приложения."""
    title = " ".join(str(title or "").split())
    if not title:
        return "Рабочий стол"
    for sep in (" — ", " - "):
        if sep in title:
            tail = title.rsplit(sep, 1)[-1].strip()
            if tail:
                title = tail
    if "\\" in title or "/" in title:  # консольные окна дают полный путь
        title = title.replace("\\", "/").rsplit("/", 1)[-1]
    if title.lower().endswith(".exe"):
        title = title[:-4]
    return title[:40] or "Рабочий стол"


def start() -> None:
    """Фоновый сэмплер активного окна (daemon)."""
    global _thread
    if _thread and _thread.is_alive():
        return

    def loop():
        _stop.clear()
        while not _stop.is_set():
            try:
                _sample()
            except Exception:
                pass
            _stop.wait(SAMPLE_PERIOD)

    _thread = threading.Thread(target=loop, daemon=True, name="aya-proactive")
    _thread.start()


def stop() -> None:
    _stop.set()


def _sample(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    lt = time.localtime(now)
    day_key = (lt.tm_year, lt.tm_yday)
    app = app_name(_fg_title())
    idle = _idle_seconds()
    with _lock:
        if day_key != _day["key"]:
            _day["key"] = day_key
            app_summary.clear()
        prev = _state["app"]
        if app != prev:
            if prev:
                app_summary[prev] = app_summary.get(prev, 0.0) + SAMPLE_PERIOD
            _state["app"] = app
            _state["title"] = _fg_title()
            _state["since"] = now
        else:
            app_summary[app] = app_summary.get(app, 0.0) + min(SAMPLE_PERIOD, 90.0)
        _state["idle"] = idle


def current() -> dict:
    with _lock:
        return {
            "app": _state["app"],
            "title": _state["title"],
            "idle": round(_state["idle"]),
            "in_app_seconds": round(time.time() - _state["since"]) if _state["since"] else 0,
        }


def top_apps(limit: int = 5) -> list[tuple[str, int]]:
    with _lock:
        items = sorted(app_summary.items(), key=lambda kv: -kv[1])[:limit]
    return [(name, round(secs / 60)) for name, secs in items]


def summary_text() -> str:
    tops = top_apps(6)
    if not tops:
        return "Данных об активности пока нет."
    lines = [f"{name} — {mins} мин" for name, mins in tops]
    return "Активность за день: " + "; ".join(lines) + "."


# ── правила инициативы ─────────────────────────────────────────────
def _rule_last(rule: str) -> float:
    try:
        return float(db.setting(f"pro_{rule}_last", "0") or 0)
    except (TypeError, ValueError):
        return 0.0


def _rule_mark(rule: str, now: float) -> None:
    db.set_setting(f"pro_{rule}_last", str(now))


def rules(now: float | None = None) -> str:
    """Тема для инициативы или ''. Порядок: digest > morning > break > idle."""
    now = now if now is not None else time.time()
    if db.setting("proactive_enabled", "1") != "1":
        return ""
    if db.setting("focus_mode", "0") == "1":
        return ""
    lt = time.localtime(now)
    if lt.tm_hour >= 23 or lt.tm_hour < 8:
        return ""

    # вечерний дайджест — после 21:00, раз в сутки
    if db.setting("digest_enabled", "1") == "1":
        boundary = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 21, 0, 0, 0, 0, -1))
        if now >= boundary and _rule_last("digest") < boundary:
            _rule_mark("digest", now)
            return "digest"

    # утренний привет — раз в сутки, окно 8-11
    if 8 <= lt.tm_hour < 11:
        boundary = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 8, 0, 0, 0, 0, -1))
        if _rule_last("morning") < boundary:
            _rule_mark("morning", now)
            return "morning"

    with _lock:
        app = _state["app"]
        since = _state["since"]
        idle = _state["idle"]

    # перерыв: долго в одном приложении и человек живой
    if (
        app
        and since
        and now - since >= BREAK_AFTER
        and idle < ACTIVE_INPUT_WINDOW
        and now - _rule_last("break") >= BREAK_COOLDOWN
    ):
        _rule_mark("break", now)
        return "break"

    # отошёл: ввода нет давно, но не глазуём (иначе не услышим)
    if IDLE_AFTER <= idle < 8 * 3600 and now - _rule_last("idle") >= IDLE_COOLDOWN:
        _rule_mark("idle", now)
        return "idle"

    return ""


def status() -> dict:
    return {
        "enabled": db.setting("proactive_enabled", "1") == "1",
        "focus": db.setting("focus_mode", "0") == "1",
        "digest": db.setting("digest_enabled", "1") == "1",
        "now": current(),
        "top": top_apps(),
    }
