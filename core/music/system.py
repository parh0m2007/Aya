"""Любой плеер Windows: что играет, пауза/дальше/назад, приглушение.

Сначала — System Media Transport Controls (winrt): так управляются приложение
Яндекс Музыки, Spotify, Chrome, Edge. Некоторые браузеры (Яндекс Браузер)
туда не отдают сеанс — тогда видим их по звуковому сеансу (pycaw), название
берём из заголовка окна, а управляем мультимедийными клавишами.

Все функции синхронные и должны идти в музыкальном потоке (worker.py).
"""
import asyncio
import json
import re
import threading
import time

from .. import db

KEYS = {"toggle": 0xB3, "play": 0xB3, "pause": 0xB3, "next": 0xB0, "prev": 0xB1, "stop": 0xB2}
_FRIENDLY = (
    ("yandex.music", "Яндекс Музыка"),
    ("y.music", "Яндекс Музыка"),
    ("chrome", "Chrome"),
    ("msedge", "Edge"),
    ("opera", "Opera"),
    ("firefox", "Firefox"),
    ("308046b0af4a39cb", "Firefox"),
    ("spotify", "Spotify"),
    ("browser", "Яндекс Браузер"),
    ("yandex", "Яндекс Браузер"),
)
# процессы, чей звук — это плеер в браузере (для запасного пути без SMTC)
BROWSERS = {"browser.exe", "chrome.exe", "msedge.exe", "opera.exe", "firefox.exe", "vivaldi.exe", "brave.exe"}
# свой звук и системные — не приглушаем и не считаем музыкой
SELF_PROCS = {"aya.exe", "python.exe", "pythonw.exe", "msedgewebview2.exe", "audiodg.exe"}
_STATUS = {4: "playing", 5: "paused", 3: "stopped", 2: "stopped", 1: "opened", 0: "closed"}
_LAST = {"via": "", "proc": "", "aumid": "", "ts": 0.0}  # кем управляли последним


def friendly_app(name: str) -> str:
    low = (name or "").lower()
    for key, title in _FRIENDLY:
        if key in low:
            return title
    base = re.split(r"[!\\/]", name or "")[-1] if name else ""
    return base.replace(".exe", "") or "плеер"


def _run(coro):
    return asyncio.run(coro)


def _winrt():
    try:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as M

        return M
    except Exception:  # noqa: BLE001 — нет пакета winrt: работаем клавишами
        return None


async def _sessions():
    M = _winrt()
    if M is None:
        return None, []
    mgr = await M.request_async()
    return mgr, list(mgr.get_sessions())


def _status_of(s) -> str:
    try:
        return _STATUS.get(int(s.get_playback_info().playback_status), "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


async def _describe(s) -> dict:
    out = {"app": friendly_app(s.source_app_user_model_id), "aumid": s.source_app_user_model_id, "title": "", "artist": "", "via": "smtc"}
    out["status"] = _status_of(s)
    try:
        p = await s.try_get_media_properties_async()
        out["title"], out["artist"] = p.title or "", p.artist or ""
    except Exception:  # noqa: BLE001
        pass
    return out


def _smtc_playing():
    async def go():
        _mgr, sessions = await _sessions()
        for s in sessions:
            if _status_of(s) == "playing":
                return await _describe(s)
        return None

    try:
        return _run(go()) if _winrt() else None
    except Exception:  # noqa: BLE001
        return None


# ── звуковые сеансы (pycaw) ──────────────────────────────────────────


def audio_sessions(active_only: bool = True) -> list:
    try:
        from pycaw.pycaw import AudioUtilities
    except Exception:  # noqa: BLE001
        return []
    out = []
    try:
        for s in AudioUtilities.GetAllSessions():
            p = s.Process
            if not p:
                continue
            name = p.name().lower()
            if name in SELF_PROCS:
                continue
            if active_only and s.State != 1:
                continue
            out.append(s)
    except Exception:  # noqa: BLE001
        return []
    return out


def _windows(pid: int) -> list[tuple[int, str]]:
    """Видимые окна процесса: [(hwnd, заголовок)]."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _l):
        if not user32.IsWindowVisible(hwnd):
            return True
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return found


def _window_titles(pid: int) -> list[str]:
    return [t for _h, t in _windows(pid)]


def _browser_title(proc_name: str) -> str:
    """«Группа крови - YouTube, профиль …» → «Группа крови»."""
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return ""
    for p in psutil.process_iter(["name"]):
        if (p.info.get("name") or "").lower() != proc_name:
            continue
        for t in _window_titles(p.pid):
            m = re.match(r"^(?:\(\d+\)\s*)?(.+?)\s+[-–—]\s+(YouTube|Яндекс Музыка|Yandex Music|VK|ВКонтакте)", t)
            if m:
                return m.group(1).strip()
    return ""


# Яндекс Браузер не передаёт YouTube мультимедийные «дальше/назад» — зато сам
# YouTube понимает Shift+N / Shift+P (назад — только в плейлисте; Ая открывает
# YouTube с «миксом», так что плейлист есть)
YT_KEYS = {"next": "N", "prev": "P"}
_YT_TITLE = re.compile(r"\s[-–—]\s+YouTube(?!\s+Music)\b")


def _youtube_window(proc_name: str) -> int:
    """hwnd окна браузера, где активная вкладка — YouTube (не YouTube Music)."""
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return 0
    for p in psutil.process_iter(["name"]):
        if (p.info.get("name") or "").lower() != proc_name:
            continue
        for hwnd, t in _windows(p.pid):
            if _YT_TITLE.search(t):
                return hwnd
    return 0


def _youtube_key(hwnd: int, action: str) -> None:
    """На миг сделать окно с YouTube активным, нажать Shift+N/P и вернуть фокус.
    Без Alt-трюка: Alt открыл бы меню браузера и оно съело бы Shift+N."""
    import ctypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    prev = user32.GetForegroundWindow()

    def focus(hwnd_to: int) -> None:
        fg = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        me = kernel32.GetCurrentThreadId()
        user32.AttachThreadInput(me, fg, True)
        user32.SetForegroundWindow(hwnd_to)
        user32.BringWindowToTop(hwnd_to)
        user32.AttachThreadInput(me, fg, False)

    focus(hwnd)
    time.sleep(0.3)
    vk = ord(YT_KEYS[action])
    user32.keybd_event(0x10, 0, 0, 0)  # Shift
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)
    user32.keybd_event(0x10, 0, 2, 0)
    time.sleep(0.15)
    if prev and prev != hwnd:
        focus(prev)


def _in_game() -> bool:
    try:
        from .. import gamemode

        return gamemode.active()
    except Exception:  # noqa: BLE001
        return False


def _browser_playing() -> dict | None:
    for s in audio_sessions():
        name = s.Process.name().lower()
        if name in BROWSERS:
            return {"status": "playing", "app": friendly_app(name), "aumid": "", "proc": name, "title": _browser_title(name), "artist": "", "via": "keys"}
    return None


# ── что играет / управление ──────────────────────────────────────────


def now_playing() -> dict:
    """{status: playing|paused|none, app, title, artist, via}."""
    d = _smtc_playing() or _browser_playing()
    if d:
        return d
    # ничего не играет: покажем то, что стоит на паузе (последнее управляемое)
    if _LAST["via"] == "keys" and time.time() - _LAST["ts"] < 6 * 3600:
        proc = _LAST["proc"]
        return {"status": "paused", "app": friendly_app(proc), "aumid": "", "proc": proc, "title": _browser_title(proc) if proc in BROWSERS else "", "artist": "", "via": "keys"}

    async def paused():
        mgr, sessions = await _sessions()
        cur = mgr.get_current_session() if mgr else None
        s = cur or (sessions[0] if sessions else None)
        return await _describe(s) if s else None

    try:
        d = _run(paused()) if _winrt() else None
    except Exception:  # noqa: BLE001
        d = None
    return d or {"status": "none", "app": "", "title": "", "artist": "", "aumid": "", "via": ""}


def _press(key: int) -> None:
    import ctypes

    ctypes.windll.user32.keybd_event(key, 0, 0, 0)
    ctypes.windll.user32.keybd_event(key, 0, 2, 0)


def _smtc_do(aumid: str, action: str) -> dict | None:
    async def go():
        _mgr, sessions = await _sessions()
        target = None
        for s in sessions:
            if aumid and s.source_app_user_model_id == aumid:
                target = s
                break
        if target is None:
            return None
        fn = {
            "play": target.try_play_async,
            "pause": target.try_pause_async,
            "toggle": target.try_toggle_play_pause_async,
            "next": target.try_skip_next_async,
            "prev": target.try_skip_previous_async,
            "stop": target.try_pause_async,
        }[action]
        ok = await fn()
        await asyncio.sleep(0.3)  # дать плееру обновить название
        d = await _describe(target)
        d["ok"] = bool(ok)
        return d

    try:
        return _run(go())
    except Exception:  # noqa: BLE001
        return None


def control(action: str) -> dict:
    """play / pause / toggle / next / prev / stop для того, что играет сейчас
    (или того, чем управляли последним — чтобы «продолжи» вернуло нужный плеер)."""
    action = action if action in KEYS else "toggle"
    playing = _smtc_playing() or _browser_playing()
    if playing and action == "play":
        return {**playing, "ok": True}  # уже играет
    target = playing
    if target is None and time.time() - _LAST["ts"] < 6 * 3600:
        target = {"via": _LAST["via"], "aumid": _LAST["aumid"], "proc": _LAST["proc"]}
    if target and target.get("via") == "smtc" and target.get("aumid"):
        d = _smtc_do(target["aumid"], action)
        if d:
            _LAST.update(via="smtc", aumid=target["aumid"], proc="", ts=time.time())
            return d
    if target and target.get("via") == "keys":
        proc = target.get("proc", "")
        base = {"app": friendly_app(proc), "artist": "", "via": "keys", "proc": proc}
        if action in ("pause", "stop") and not playing:
            return {**base, "ok": True, "status": "paused", "title": _browser_title(proc)}
        before = _browser_title(proc) if proc in BROWSERS else ""
        yt = _youtube_window(proc) if action in YT_KEYS and proc in BROWSERS else 0
        if yt and _in_game():
            # переключать окно браузера посреди игры = свернуть игру
            return {**base, "ok": False, "status": "playing", "title": before, "note": "game"}
        if yt:
            _youtube_key(yt, action)
        else:
            _press(KEYS[action])
        _LAST.update(via="keys", proc=proc, aumid="", ts=time.time())
        if action in ("next", "prev"):
            # не все браузеры понимают «дальше» с клавиатуры (Яндекс Браузер — нет):
            # проверяем по заголовку окна, сменился ли трек
            deadline = time.time() + 3.0
            after = before
            while time.time() < deadline and after == before:
                time.sleep(0.4)
                after = _browser_title(proc)
            if before and after == before:
                return {**base, "ok": False, "status": "playing", "title": before, "note": "yt_stuck" if yt else "no_next"}
            return {**base, "ok": True, "status": "playing", "title": after}
        # звуковой сеанс гаснет с задержкой ~1 с — состояние знаем сами
        status = {"pause": "paused", "stop": "paused", "play": "playing"}.get(action, "paused" if playing else "playing")
        return {**base, "ok": True, "status": status, "title": before}
    # не знаем, кто играет: системный плеер по умолчанию или просто клавиша
    if _winrt():
        async def cur():
            mgr, _s = await _sessions()
            c = mgr.get_current_session() if mgr else None
            return c.source_app_user_model_id if c else ""

        try:
            aumid = _run(cur())
        except Exception:  # noqa: BLE001
            aumid = ""
        if aumid:
            d = _smtc_do(aumid, action)
            if d:
                _LAST.update(via="smtc", aumid=aumid, proc="", ts=time.time())
                return d
    _press(KEYS[action])
    return {"ok": True, "via": "keys", "status": "unknown", "app": "", "title": "", "artist": ""}


def remember_browser(proc: str = "") -> None:
    """Мы сами открыли музыку в браузере — дальше управляем им клавишами."""
    _LAST.update(via="keys", proc=proc or "browser", aumid="", ts=time.time())


def describe_line(d: dict) -> str:
    if not d or d.get("status") in (None, "none", "closed"):
        return "сейчас ничего не играет"
    what = " — ".join(x for x in (d.get("artist"), d.get("title")) if x) or "без названия"
    state = {"playing": "играет", "paused": "на паузе", "stopped": "остановлено"}.get(d.get("status", ""), d.get("status", ""))
    return f"{state}: {what} ({d.get('app') or 'плеер'})"


# ── приглушение, пока Ая говорит ─────────────────────────────────────

_DUCK = {"reasons": set(), "since": 0.0, "volumes": {}, "paused": None}
_DUCK_LOCK = threading.Lock()
DUCK_MODES = ("lower", "pause", "off")
_DUCK_SAVED = "music_duck_saved"  # {процесс: громкость до приглушения} — на случай падения

# приглушаем только то, где играет музыка: браузер и плееры. Звонки, игры,
# видео в отдельных программах не трогаем.
PLAYER_PROCS = {
    "spotify.exe", "vlc.exe", "aimp.exe", "foobar2000.exe", "wmplayer.exe", "music.ui.exe",
    "itunes.exe", "applemusic.exe", "deezer.exe", "winamp.exe", "musicbee.exe", "tidal.exe",
    "yandexmusic.exe", "soundcloud.exe", "potplayermini64.exe", "potplayermini.exe",
}


def is_music_proc(name: str) -> bool:
    low = (name or "").lower()
    return low in BROWSERS or low in PLAYER_PROCS or "music" in low or "музык" in low


def duck_mode() -> str:
    v = db.setting("music_duck", "lower")
    return v if v in DUCK_MODES else "lower"


def duck_level() -> float:
    try:
        return max(0.05, min(0.9, float(db.setting("music_duck_level", "0.25"))))
    except ValueError:
        return 0.25


def duck(on: bool, reason: str = "speak", on_timer=None) -> dict:
    """Ая начала (on) / закончила (off) говорить или слушать."""
    mode = duck_mode()
    with _DUCK_LOCK:
        if on:
            first = not _DUCK["reasons"]
            _DUCK["reasons"].add(reason)
            _DUCK["since"] = time.time()
            if on_timer:
                t = threading.Timer(95, on_timer)
                t.daemon = True
                t.start()
            if not first or mode == "off":
                return {"ducked": bool(_DUCK["volumes"] or _DUCK["paused"]), "mode": mode}
            return _duck_apply(mode)
        _DUCK["reasons"].discard(reason)
        if _DUCK["reasons"]:
            return {"ducked": True, "mode": mode}
        return _duck_restore()


def _session_key(s, i: int) -> str:
    """Свой ключ для каждого звукового сеанса: у браузера их бывает несколько в одном процессе."""
    try:
        ident = s.InstanceIdentifier
        if ident:
            return str(ident)
    except Exception:  # noqa: BLE001
        pass
    return f"{s.Process.pid}:{i}"


def _save_ducked() -> None:
    """Windows запоминает громкость программы: если ядро упадёт посреди речи,
    музыка так и осталась бы тихой — записываем, к чему вернуть."""
    saved = {name: level for _vol, level, name in _DUCK["volumes"].values()}
    db.set_setting(_DUCK_SAVED, json.dumps(saved, ensure_ascii=False) if saved else "")


def _duck_apply(mode: str) -> dict:
    if mode == "lower":
        level = duck_level()
        for i, s in enumerate(audio_sessions()):
            try:
                name = s.Process.name()
                if not is_music_proc(name):
                    continue
                vol = s.SimpleAudioVolume
                cur = vol.GetMasterVolume()
                _DUCK["volumes"][_session_key(s, i)] = (vol, cur, name.lower())
                vol.SetMasterVolume(cur * level, None)
            except Exception:  # noqa: BLE001
                continue
        if _DUCK["volumes"]:
            _save_ducked()
            return {"ducked": True, "mode": "lower"}
        return {"ducked": False, "mode": "lower"}
    playing = _smtc_playing() or _browser_playing()
    if not playing:
        return {"ducked": False, "mode": mode}
    if playing.get("via") == "smtc":
        r = _smtc_do(playing["aumid"], "pause")
        _DUCK["paused"] = ("smtc", playing["aumid"]) if r else None
    else:
        _press(KEYS["pause"])
        _DUCK["paused"] = ("keys", playing.get("proc", ""))
    return {"ducked": bool(_DUCK["paused"]), "mode": "pause"}


def _duck_restore() -> dict:
    restored = False
    for vol, level, _name in list(_DUCK["volumes"].values()):
        try:
            vol.SetMasterVolume(level, None)
            restored = True
        except Exception:  # noqa: BLE001
            pass
    if _DUCK["volumes"]:
        db.set_setting(_DUCK_SAVED, "")
    _DUCK["volumes"].clear()
    paused, _DUCK["paused"] = _DUCK["paused"], None
    if paused:
        via, who = paused
        if via == "smtc":
            _smtc_do(who, "play")
        elif not _browser_playing():  # юзер сам не включил — продолжаем
            _press(KEYS["play"])
        restored = True
    return {"ducked": False, "restored": restored}


def release_duck() -> None:
    """Ядро закрывается — вернуть всё, что приглушили."""
    with _DUCK_LOCK:
        _DUCK["reasons"].clear()
        _duck_restore()


def recover_duck() -> None:
    """При запуске: прошлый раз ядро закрылось посреди речи — вернуть громкость."""
    try:
        saved = json.loads(db.setting(_DUCK_SAVED, "") or "{}")
    except ValueError:
        saved = {}
    if not saved:
        return
    with _DUCK_LOCK:
        for s in audio_sessions(active_only=False):
            try:
                want = saved.get(s.Process.name().lower())
                vol = s.SimpleAudioVolume
                if want is not None and vol.GetMasterVolume() < want:
                    vol.SetMasterVolume(float(want), None)
            except Exception:  # noqa: BLE001
                continue
        db.set_setting(_DUCK_SAVED, "")


def duck_watchdog() -> None:
    """Окно могло закрыться посреди речи: через 90 с возвращаем музыку сами."""
    with _DUCK_LOCK:
        if _DUCK["reasons"] and time.time() - _DUCK["since"] > 90:
            _DUCK["reasons"].clear()
            _duck_restore()


# ── громкость самой музыки ────────────────────────────────────────────

VOL_STEP = 0.7  # «тише» — в 0,7 раза (≈ −3 дБ): на слух шаги одинаковые на любой громкости


def step_volume(cur: float, change: str) -> float:
    """Один шаг «тише»/«громче». Минус 10 процентов на 100% почти не слышно,
    поэтому шаг — в разах."""
    if change == "down":
        new = cur * VOL_STEP
        return 0.0 if new < 0.05 else round(new, 4)
    if change == "up":
        return min(1.0, round(max(cur / VOL_STEP, 0.1), 4))
    return cur


def _music_sessions() -> list:
    """Где играет музыка: браузер, плеер. Ничего не играет — то, чем управляли последним."""
    found = []
    for s in audio_sessions():
        try:
            if is_music_proc(s.Process.name()):
                found.append(s)
        except Exception:  # noqa: BLE001
            continue
    if found or not _LAST["proc"] or time.time() - _LAST["ts"] > 6 * 3600:
        return found
    out = []
    for s in audio_sessions(active_only=False):
        try:
            if s.Process.name().lower() == _LAST["proc"]:
                out.append(s)
        except Exception:  # noqa: BLE001
            continue
    return out


def music_volume(change: str = "", level: float | None = None) -> dict:
    """Громкость музыки — программы в микшере Windows (браузер с YouTube,
    приложение Яндекс Музыки), а не всего компьютера. Пока Ая говорит и музыка
    приглушена, меняем то, к чему громкость вернётся после речи."""
    with _DUCK_LOCK:
        found = _music_sessions()
        if not found:
            return {"ok": False, "note": "none"}
        ducked_by = duck_level()
        out = None
        for i, s in enumerate(found):
            try:
                vol = s.SimpleAudioVolume
                key = _session_key(s, i)
                held = _DUCK["volumes"].get(key)
                cur = held[1] if held else vol.GetMasterVolume()
                new = max(0.0, min(1.0, float(level))) if level is not None else step_volume(cur, change)
                if held:
                    _DUCK["volumes"][key] = (held[0], new, held[2])
                    vol.SetMasterVolume(new * ducked_by, None)
                else:
                    vol.SetMasterVolume(new, None)
                if out is None:
                    out = {"ok": True, "level": round(new * 100), "was": round(cur * 100), "app": friendly_app(s.Process.name()), "via": "mixer"}
            except Exception:  # noqa: BLE001
                continue
        if _DUCK["volumes"]:
            _save_ducked()
        return out or {"ok": False, "note": "error"}
