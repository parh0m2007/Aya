"""Музыка: YouTube, Яндекс Музыка, собственный плеер Аи и любой плеер Windows.

- YouTube — поиск через yt-dlp, видео открывается в браузере (с «миксом»:
  после песни играют похожие). Дальше пауза/дальше — мультимедийными клавишами.
- Яндекс Музыка — по токену Ая играет сама, в своём окне (полные треки с
  Плюсом, «Моя волна», «Мне нравится», похожие на трек). Без токена трек
  открывается на сайте.
- Пока Ая говорит или слушает, музыка приглушается и потом возвращается.
"""
from .. import db
from . import player, system, yandex, youtube
from .system import DUCK_MODES, describe_line, duck_mode
from .worker import call, in_worker, open_in_browser, open_link

__all__ = ["DUCK_MODES", "call", "in_worker", "describe_line", "duck_mode", "player", "system", "yandex", "youtube"]


def default_service() -> str:
    v = db.setting("music_service", "youtube")
    return v if v in ("youtube", "yandex") else "youtube"


def now_playing() -> dict:
    """Своё — если играет; иначе то, что играет в Windows."""
    if player.playing():
        return player.describe()
    d = system.now_playing()
    if d.get("status") != "playing" and player.active() and (_own_is_latest() or d.get("status") == "none"):
        return player.describe()
    return d


def _own_is_latest() -> bool:
    """Своим плеером управляли позже, чем браузером/системным плеером?"""
    return player.STATE.get("user_ts", 0.0) >= system._LAST.get("ts", 0.0)


def control(action: str) -> dict:
    """Команда своему плееру, если он играет. Если он на паузе — только когда
    больше ничего не играет и последним управляли именно им (иначе «продолжи»
    после паузы YouTube включило бы волну Яндекса)."""
    if player.active():
        other = system.now_playing() if not player.playing() else None
        if player.playing() or ((other or {}).get("status") != "playing" and _own_is_latest()):
            cmd = {"toggle": "pause" if player.playing() else "play"}.get(action, action)
            player.command(cmd)
            return {**player.describe(), "ok": True}
    return system.control(action)


def duck(on: bool, reason: str = "speak") -> dict:
    return system.duck(on, reason, on_timer=lambda: in_worker(system.duck_watchdog))


# ── включить ──────────────────────────────────────────────────────────


def play_youtube(query: str) -> dict:
    found = youtube.pick_song(youtube.search(query))
    if not found:
        return {"ok": False, "error": "на YouTube ничего не нашла"}
    url = youtube.watch_url(found["id"], youtube.radio_on())
    if player.playing():
        player.command("pause")  # своё — на паузу, чтобы не играло одновременно
    proc = open_in_browser(url)
    system.remember_browser(proc)
    return {"ok": True, "service": "youtube", "title": found["title"], "artist": found["channel"], "url": url}


def play_yandex(query: str = "", what: str = "") -> dict:
    """what: likes — «Мне нравится», wave — «Моя волна», иначе поиск query."""
    try:
        if what == "likes":
            acc = yandex.client().me.account
            found = {"kind": "likes", "title": "Мне нравится", "artist": "", "ids": {"owner": acc.login or acc.uid}}
        elif what == "wave":
            if not yandex.token():
                return {"ok": False, "error": "для «Моей волны» нужен токен Яндекс Музыки (консоль → Голос → Музыка)"}
            found = {"kind": "wave", "title": "Моя волна", "artist": "", "ids": {}}
        else:
            found = yandex.find(query)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}
    if not found:
        return {"ok": False, "error": "в Яндекс Музыке ничего не нашла"}
    tgt = yandex.target()
    head = {"ok": True, "service": "yandex", "kind": found["kind"], "title": found["title"], "artist": found["artist"]}
    if tgt == "aya":
        try:
            items, source = yandex.queue_for(found)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Яндекс не отдал треки: {str(e)[:160]}"}
        if not items:
            return {"ok": False, "error": "в этом источнике нет треков"}
        player.load(items, source, "yandex", found["title"])
        first = items[0]
        return {**head, "where": "aya", "title": first["title"] if found["kind"] == "track" else found["title"], "artist": first["artist"] if found["kind"] == "track" else found["artist"], "count": len(items)}
    if tgt == "app":
        open_link(yandex.app_url(found))
        return {**head, "where": "app"}
    url = yandex.web_url(found)
    proc = open_in_browser(url)
    system.remember_browser(proc)
    return {**head, "where": "web", "url": url}


# ── короткие команды без модели ───────────────────────────────────────

import re as _re

_NAME = r"(?:(?:ая|аюшка|aya)[,!\s]+)?"
_MUSIC = r"(?:музык\w*|песн\w*|трек\w*|плеер\w*|воспроизведени\w*)"
_QUICK = (
    ("pause", _re.compile(rf"^{_NAME}(?:поставь\s+)?(?:на\s+)?пауз[ауы]?(?:\s+{_MUSIC})?$|^{_NAME}(?:останови|выключи|приостанови|заглуши)\s+{_MUSIC}$")),
    ("resume", _re.compile(rf"^{_NAME}(?:продолжи|включи\s+обратно|верни|сними\s+с\s+паузы)\s+{_MUSIC}$|^{_NAME}сними\s+с\s+паузы$")),
    ("next", _re.compile(rf"^{_NAME}(?:(?:включи\s+|поставь\s+)?следующ\w+\s+{_MUSIC}|переключи(?:\s+{_MUSIC})?|(?:другую|дальше)\s+{_MUSIC}|{_MUSIC}\s+дальше|пропусти(?:\s+{_MUSIC})?)$")),
    ("prev", _re.compile(rf"^{_NAME}(?:(?:включи\s+)?предыдущ\w+\s+{_MUSIC}|верни\s+(?:прошл|предыдущ)\w+\s+{_MUSIC})$")),
    ("now", _re.compile(rf"^{_NAME}(?:что\s+(?:это\s+|сейчас\s+|у\s+меня\s+)?(?:за\s+)?(?:играет|{_MUSIC})|какая\s+(?:это\s+|сейчас\s+)?(?:песня|музыка)(?:\s+играет)?|кто\s+(?:это\s+)?поёт|кто\s+(?:это\s+)?поет)$")),
)


def quick_intent(text: str) -> dict | None:
    """«пауза», «следующий трек», «что играет» → действие сразу, без модели.
    Двусмысленное («продолжи», «дальше» без слова «музыка») — не трогаем."""
    t = _re.sub(r"[.!?…]+$", "", str(text or "").strip().lower()).replace("ё", "е")
    t = _re.sub(r"\s+", " ", t)
    if not t or len(t) > 60:
        return None
    for action, rx in _QUICK:
        if rx.match(t):
            return {"action": action}
    return None


# ── инструмент для Аи ─────────────────────────────────────────────────


def _played_line(r: dict) -> str:
    if not r.get("ok"):
        return f"не получилось: {r.get('error', 'ошибка')}"
    what = " — ".join(x for x in (r.get("artist"), r.get("title")) if x)
    if r.get("service") == "youtube":
        return f"включила на YouTube в браузере: {what}"
    where = r.get("where")
    if where == "aya":
        n = r.get("count", 0)
        return f"играю сама из Яндекс Музыки: {what}" + (f" (в очереди {n} треков)" if n > 1 else "")
    if where == "app":
        return f"открыла в приложении Яндекс Музыки: {what}"
    return f"открыла в Яндекс Музыке в браузере: {what} — нажми ▶ (чтобы я включала сама, нужен токен в консоли)"


def tool(args: dict) -> str:
    """music {action, query?, service?, what?} — для модели."""
    action = str(args.get("action") or ("play" if args.get("query") or args.get("what") else "now")).lower()
    what = str(args.get("what") or "").lower()
    query = str(args.get("query") or "")
    if action in ("play", "search") and (query or what):
        service = str(args.get("service") or "").lower() or default_service()
        if what in ("likes", "wave"):
            service = "yandex"
        r = play_yandex(query, what if what in ("likes", "wave") else "") if service == "yandex" else play_youtube(query)
        db.log_event("tool", f"music play {service}: {query[:60] or what}")
        return _played_line(r)
    if action in ("now", "status", "what"):
        return describe_line(now_playing())
    mapping = {"resume": "play", "continue": "play", "stop": "pause", "skip": "next", "back": "prev", "previous": "prev"}
    act = mapping.get(action, action)
    if act not in system.KEYS:
        return "не поняла команду для музыки: play, pause, resume, next, prev, now"
    d = control(act)
    db.log_event("tool", f"music {act}")
    if d.get("note") == "no_next":
        return (
            f"не получилось: {d.get('app') or 'браузер'} не переключает треки с клавиатуры. "
            "Предложи включить другую песню по названию или похожую — это я умею"
        )
    if d.get("via") == "keys" and not d.get("title"):
        return {"pause": "поставила на паузу", "play": "продолжила", "next": "переключила дальше", "prev": "вернула назад"}.get(act, "готово")
    return describe_line(d)
