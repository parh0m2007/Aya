"""Игровой режим: пока на весь экран открыта игра, Ая прячется и почти не
тратит ресурсы компьютера.

Тестеры жаловались на подлагивания в Valorant, CS2 и Dota 2. Причины: окно
поверх игры, отрисовка аватара, микрофон «Ая, …» с распознаванием каждой
фразы (3–4 ядра на ~2 с) и модели в памяти. В игре:
- окно прячется и не рисует, микрофон слушает только по клавише (это делает
  главное окно по флагу game из /api/ui);
- распознавание — лёгкая модель на 2 ядрах процессора (stt.set_game);
- локальные голоса выгружаются, говорит облачный (tts.selected_voice);
- процессы Аи — с приоритетом ниже обычного, ядро — ещё и в режиме экономии
  Windows: при нехватке процессора его сначала получает игра;
- Ая сама не заговаривает.

В игру ничего не встраиваем — только смотрим, какое окно на весь экран,
поэтому античитам (Vanguard, VAC) это не мешает.
"""
import ctypes
import threading
import time

from . import db

SETTING = "game_mode"
POLL_SEC = 2.0
ENTER_SAMPLES = 2  # игра на экране два опроса подряд (~4 с) — мелькание окна не считается
LEAVE_AFTER_SEC = 15.0  # alt-tab в Discord между раундами — не выскакиваем сразу

# игры, которые узнаём даже в окне (имена процессов)
KNOWN_GAMES = {
    "valorant-win64-shipping.exe", "valorant.exe", "cs2.exe", "csgo.exe", "dota2.exe",
    "league of legends.exe", "r5apex.exe", "r5apex_dx12.exe", "fortniteclient-win64-shipping.exe",
    "overwatch.exe", "gta5.exe", "gta5_enhanced.exe", "rocketleague.exe", "tslgame.exe",
    "destiny2.exe", "eldenring.exe", "cyberpunk2077.exe", "rainbowsix.exe", "rainbowsix_vulkan.exe",
    "escapefromtarkov.exe", "deadlock.exe", "project8.exe", "marvel-win64-shipping.exe",
    "wow.exe", "hearthstone.exe", "warframe.x64.exe", "rustclient.exe", "pathofexile.exe",
    "pathofexile_x64.exe", "genshinimpact.exe", "starrail.exe", "zenlesszonezero.exe",
    "minecraft.windows.exe", "robloxplayerbeta.exe", "worldoftanks.exe", "wotblitz.exe",
    "tf_win64.exe", "hl2.exe", "bf2042.exe", "cod.exe", "cod24-cod.exe", "helldivers2.exe",
}
# во весь экран, но не игра: видео, браузер, сама Ая
NOT_GAMES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "browser.exe", "vivaldi.exe", "brave.exe",
    "yandex.exe", "vlc.exe", "mpc-hc.exe", "mpc-hc64.exe", "mpc-be64.exe", "potplayermini.exe",
    "potplayermini64.exe", "wmplayer.exe", "video.ui.exe", "microsoft.media.player.exe", "mpv.exe",
    "aya.exe", "msedgewebview2.exe", "explorer.exe", "applicationframehost.exe",
}
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

_state = {"on": False, "app": "", "hits": 0, "last_seen": 0.0}
_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None


def enabled() -> bool:
    return db.setting(SETTING, "1") != "0"


def active() -> bool:
    return bool(_state["on"])


def status() -> dict:
    return {"on": active(), "app": _state["app"] if active() else "", "enabled": enabled()}


def is_game(fg: dict | None) -> bool:
    if not fg:
        return False
    exe = (fg.get("exe") or "").lower()
    if exe in KNOWN_GAMES:
        return True
    if fg.get("cls") in SHELL_CLASSES or exe in NOT_GAMES:
        return False
    return bool(fg.get("d3d") or fg.get("fullscreen"))


def update(fg: dict | None, now: float | None = None) -> str:
    """Один опрос. → "enter" | "leave" | ""."""
    now = time.time() if now is None else now
    with _lock:
        if not enabled():
            _state["hits"] = 0
            return _leave() if _state["on"] else ""
        if is_game(fg):
            _state["hits"] += 1
            _state["last_seen"] = now
            if not _state["on"] and _state["hits"] >= ENTER_SAMPLES:
                return _enter((fg or {}).get("exe") or "игра")
            return ""
        _state["hits"] = 0
        if _state["on"] and now - _state["last_seen"] >= LEAVE_AFTER_SEC:
            return _leave()
        return ""


def _enter(app: str) -> str:
    _state.update(on=True, app=app)
    db.log_event("game", f"игровой режим: {app}")
    _apply(True)
    return "enter"


def _leave() -> str:
    _state.update(on=False, app="")
    db.log_event("game", "игровой режим выключен")
    _apply(False)
    return "leave"


def _apply(on: bool) -> None:
    """Всё, что ядро меняет на время игры. Каждый шаг сам по себе: сбой одного не мешает другим."""
    from .voice import modelmgr, stt

    for step in (
        lambda: stt.set_game(on),
        lambda: on and modelmgr.unload_vram(["f5", "qwen", "vits"]),  # локальные голоса — из видеопамяти
        lambda: set_priority(on),
    ):
        try:
            step()
        except Exception:  # noqa: BLE001
            pass


# ── активное окно ───────────────────────────────────────────────────


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


WS_CAPTION = 0x00C00000


def covers_monitor(win: tuple, mon: tuple, maximized: bool, caption: bool) -> bool:
    """Окно накрывает весь монитор (left, top, right, bottom). Развёрнутое окно с
    заголовком — не игра: при автоскрытии панели задач оно тоже накрывает экран."""
    if maximized and caption:
        return False
    return win[0] <= mon[0] and win[1] <= mon[1] and win[2] >= mon[2] and win[3] >= mon[3]


def foreground_info() -> dict | None:
    """{exe, cls, fullscreen, d3d} активного окна или None."""
    if not hasattr(ctypes, "windll"):
        return None
    try:
        from .desktop import _exe_name

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd or user32.IsIconic(hwnd):
            return None
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        r = _RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        mon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        full = False
        if mon and user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
            m = mi.rcMonitor
            style = user32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
            full = covers_monitor(
                (r.left, r.top, r.right, r.bottom),
                (m.left, m.top, m.right, m.bottom),
                maximized=bool(user32.IsZoomed(hwnd)),
                caption=(style & WS_CAPTION) == WS_CAPTION,
            )
        quns = ctypes.c_int(0)
        d3d = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(quns)) == 0 and quns.value == 3
        return {"exe": _exe_name(user32, ctypes.windll.kernel32, hwnd), "cls": cls.value, "fullscreen": full, "d3d": d3d}
    except Exception:  # noqa: BLE001
        return None


# ── приоритет процессов ─────────────────────────────────────────────


class _POWER_THROTTLING(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]


def priority_targets(me=None) -> tuple[list, list]:
    """(режим экономии + приоритет ниже, только приоритет ниже).

    Ядро и его помощники (MCP) — в режим экономии. Окно Аи (aya.exe и WebView2) —
    только приоритет ниже: звуковой процесс WebView2 не трогаем совсем, иначе
    голос Аи может заикаться, пока игра грузит процессор."""
    import psutil

    me = me or psutil.Process()
    eco = [me]
    low = []
    seen = {me.pid}
    try:
        for c in me.children(recursive=True):
            if c.pid not in seen:
                seen.add(c.pid)
                eco.append(c)
        parent = me.parent()
        if parent is not None and parent.name().lower() == "aya.exe":
            seen.add(parent.pid)
            low.append(parent)
            for c in parent.children(recursive=True):
                if c.pid in seen:
                    continue
                seen.add(c.pid)
                try:
                    cmd = " ".join(c.cmdline()).lower()
                except Exception:  # noqa: BLE001
                    cmd = ""
                if "audio" in cmd:
                    continue
                low.append(c)
    except Exception:  # noqa: BLE001 — процесс завершился или нет доступа
        pass
    return eco, low


def _set_process(pid: int, low: bool, eco: bool) -> None:
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x0200 | 0x1000, False, pid)  # SET_INFORMATION | QUERY_LIMITED_INFORMATION
    if not h:
        return
    try:
        k32.SetPriorityClass(h, 0x4000 if low else 0x20)  # BELOW_NORMAL / NORMAL
        if eco or not low:
            # EcoQoS (Windows 10 1709+): эффективные ядра и пониженная частота.
            # Выключая, возвращаем решение системе (ControlMask = 0).
            st = _POWER_THROTTLING(1, 1 if low else 0, 1 if low else 0)
            k32.SetProcessInformation(h, 4, ctypes.byref(st), ctypes.sizeof(st))  # ProcessPowerThrottling
    finally:
        k32.CloseHandle(h)


def set_priority(low: bool) -> None:
    if not hasattr(ctypes, "windll"):
        return
    eco, only_low = priority_targets()
    for p in eco:
        _set_process(p.pid, low, eco=True)
    for p in only_low:
        _set_process(p.pid, low, eco=False)


# ── фоновый опрос ───────────────────────────────────────────────────


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()

    def loop():
        prefetch_at = time.time() + 90  # лёгкую модель докачаем один раз, не в игре
        while not _stop.wait(POLL_SEC):
            try:
                update(foreground_info() if enabled() else None)
                if prefetch_at and time.time() >= prefetch_at and not active():
                    prefetch_at = 0
                    if enabled():
                        from .voice import stt

                        stt.prefetch_game_model()
            except Exception:  # noqa: BLE001
                pass

    _thread = threading.Thread(target=loop, daemon=True, name="aya-gamemode")
    _thread.start()


def stop() -> None:
    _stop.set()
    if active():
        with _lock:
            _leave()


__all__ = ["active", "covers_monitor", "enabled", "is_game", "priority_targets", "set_priority", "start", "status", "stop", "update"]
