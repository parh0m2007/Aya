"""Яндекс Музыка через неофициальную библиотеку yandex-music.

Поиск работает без токена. С токеном (и Плюсом) — полные треки, «Мне
нравится», «Моя волна» и похожие на трек: тогда Ая играет сама, в своём окне.
Без токена Яндекс отдаёт только 30-секундные отрывки, поэтому трек
открывается на сайте в браузере.
"""
import threading
import time

from .. import db

_LOCK = threading.Lock()
_CLIENT = {"token": "", "client": None, "anon": None}
# как получить токен: вход в Яндекс → в адресной строке access_token=…
TOKEN_HELP_URL = "https://oauth.yandex.ru/authorize?response_type=token&client_id=23cabbbdc6cd418abb4b39c32c41195d"


def token() -> str:
    return db.setting("ym_token", "").strip()


def client(need_token: bool = True):
    tok = token()
    with _LOCK:
        if tok:
            if _CLIENT["client"] is None or _CLIENT["token"] != tok:
                from yandex_music import Client

                _CLIENT["client"], _CLIENT["token"] = Client(tok).init(), tok
            return _CLIENT["client"]
        if need_token:
            raise RuntimeError("нужен токен Яндекс Музыки — его можно добавить в консоли: Голос → Музыка")
        if _CLIENT["anon"] is None:
            from yandex_music import Client

            _CLIENT["anon"] = Client().init()
        return _CLIENT["anon"]


def reset() -> None:
    with _LOCK:
        _CLIENT["client"], _CLIENT["token"] = None, ""


def account() -> dict:
    """Проверка токена для консоли: кто вошёл и есть ли Плюс."""
    if not token():
        return {"ok": False, "error": "токен не задан"}
    try:
        st = client().me
        acc = st.account
        plus = bool(getattr(st, "plus", None) and st.plus.has_plus)
        return {"ok": True, "login": acc.login or acc.display_name or "", "plus": plus}
    except Exception as e:  # noqa: BLE001
        reset()
        return {"ok": False, "error": str(e)[:160]}


def target() -> str:
    """aya — играет сама Ая (нужен токен); web — сайт в браузере; app — приложение."""
    v = db.setting("music_yandex_target", "aya")
    if v not in ("aya", "web", "app"):
        v = "aya"
    if v == "aya" and not token():
        return "web"
    return v


def _artists(obj) -> str:
    try:
        return ", ".join(a.name for a in (obj.artists or []) if a and a.name)
    except Exception:  # noqa: BLE001
        return ""


def _cover(obj, size: str = "200x200") -> str:
    uri = getattr(obj, "cover_uri", None) or getattr(obj, "og_image", None) or ""
    return ("https://" + uri.replace("%%", size)) if uri else ""


def item(track) -> dict:
    album = track.albums[0].id if getattr(track, "albums", None) else None
    return {
        "id": f"{track.id}:{album}" if album else str(track.id),
        "track": str(track.id),
        "album": str(album or ""),
        "title": track.title or "",
        "artist": _artists(track),
        "cover": _cover(track),
        "duration": int((track.duration_ms or 0) / 1000),
    }


def find(query: str) -> dict | None:
    """Лучший результат поиска: трек, исполнитель, альбом или плейлист."""
    c = client(need_token=False)
    res = c.search(str(query or "").strip()[:200], nocorrect=False)
    if not res:
        return None
    best = getattr(res, "best", None)
    kind = getattr(best, "type", "") if best else ""
    obj = getattr(best, "result", None) if best else None
    if kind not in ("track", "artist", "album", "playlist") and res.tracks and res.tracks.results:
        kind, obj = "track", res.tracks.results[0]
    if obj is None:
        return None
    if kind == "track":
        it = item(obj)
        return {"kind": "track", "title": it["title"], "artist": it["artist"], "ids": {"track": it["track"], "album": it["album"]}, "obj": obj}
    if kind == "artist":
        return {"kind": "artist", "title": obj.name or "", "artist": obj.name or "", "ids": {"artist": obj.id}, "obj": obj}
    if kind == "album":
        return {"kind": "album", "title": obj.title or "", "artist": _artists(obj), "ids": {"album": obj.id}, "obj": obj}
    owner = getattr(obj.owner, "login", "") if obj.owner else ""
    return {"kind": "playlist", "title": obj.title or "", "artist": owner, "ids": {"owner": owner, "kind": obj.kind}, "obj": obj}


# ── очередь для собственного плеера ───────────────────────────────────


def _station(station: str, last: str = "") -> list[dict]:
    st = client().rotor_station_tracks(station, queue=last or None)
    return [item(s.track) for s in (st.sequence or []) if s and s.track]


def queue_for(found: dict) -> tuple[list[dict], str]:
    """(треки, источник для подкачки). Источник: station:… — бесконечная волна."""
    c = client()
    kind = found["kind"]
    if kind == "track":
        first = item(found["obj"])
        try:
            rest = [x for x in _station(f"track:{first['track']}") if x["track"] != first["track"]]
        except Exception:  # noqa: BLE001 — нет похожих: один трек
            rest = []
        return [first] + rest, f"station:track:{first['track']}"
    if kind == "artist":
        tr = c.artists_tracks(found["ids"]["artist"], page_size=40).tracks
        return [item(t) for t in tr if t], ""
    if kind == "album":
        al = c.albums_with_tracks(found["ids"]["album"])
        return [item(t) for vol in (al.volumes or []) for t in vol if t], ""
    if kind == "playlist":
        pl = c.users_playlists(found["ids"]["kind"], found["ids"]["owner"])
        tracks = [s.track for s in (pl.tracks or []) if s and s.track]
        if not tracks:
            tracks = [s.track for s in pl.fetch_tracks() if s and s.track]
        return [item(t) for t in tracks[:200]], ""
    if kind == "likes":
        likes = c.users_likes_tracks()
        ids = [t.id for t in (likes.tracks or [])[:150]]
        return [item(t) for t in c.tracks(ids) if t], ""
    if kind == "wave":
        return _station("user:onyourwave"), "station:user:onyourwave"
    return [], ""


def more(source: str, last_track: str) -> list[dict]:
    """Подкачка для бесконечной волны."""
    if not source.startswith("station:"):
        return []
    try:
        return _station(source[len("station:"):], last_track)
    except Exception:  # noqa: BLE001
        return []


_URLS: dict[str, tuple[float, str]] = {}


def stream_url(track_id: str) -> str:
    """Прямая ссылка на mp3 лучшего качества (живёт недолго — кэш на 5 минут)."""
    now = time.time()
    hit = _URLS.get(track_id)
    if hit and now - hit[0] < 300:
        return hit[1]
    c = client(need_token=False)
    tr = c.tracks([track_id.split(":")[0]])[0]
    infos = [i for i in tr.get_download_info(get_direct_links=True) if i.codec == "mp3"]
    if not infos:
        raise RuntimeError("нет mp3 для трека")
    best = max(infos, key=lambda i: (not i.preview, i.bitrate_in_kbps or 0))
    _URLS[track_id] = (now, best.direct_link)
    if len(_URLS) > 200:
        for k in list(_URLS)[:100]:
            _URLS.pop(k, None)
    return best.direct_link


# ── ссылки для сайта и приложения ────────────────────────────────────


def web_url(found: dict) -> str:
    ids = found.get("ids", {})
    return "https://music.yandex.ru/" + {
        "track": f"album/{ids.get('album')}/track/{ids.get('track')}" if ids.get("album") else f"track/{ids.get('track')}",
        "album": f"album/{ids.get('album')}",
        "artist": f"artist/{ids.get('artist')}",
        "playlist": f"users/{ids.get('owner')}/playlists/{ids.get('kind')}",
        "likes": f"users/{ids.get('owner')}/playlists/3",
        "wave": "",
    }[found["kind"]]


def app_url(found: dict) -> str:
    """Ссылки приложения (нашлись в самом приложении)."""
    ids = found.get("ids", {})
    return "yandexmusic://" + {
        "track": f"track/{ids.get('track')}",
        "album": f"album/{ids.get('album')}",
        "artist": f"artist/{ids.get('artist')}",
        "playlist": f"users/{ids.get('owner')}/playlists/{ids.get('kind')}",
        "likes": f"users/{ids.get('owner')}/playlists/3",
        "wave": "radio/user:onyourwave",
    }[found["kind"]]
