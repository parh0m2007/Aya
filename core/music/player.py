"""Собственный плеер Аи: очередь треков живёт в ядре, играет окно.

Окно узнаёт о командах по seq: из ответа чата (поле music), из /api/ui и
частым опросом /api/music/player, пока что-то играет. Звук окно берёт через
ядро (/api/music/stream/{id}) — прямые ссылки Яндекса короткоживущие и без CORS.
"""
import threading
import time

from . import yandex

_LOCK = threading.Lock()
STATE = {"seq": 0, "cmd": "", "items": [], "pos": -1, "status": "idle", "source": "", "service": "", "title": "", "updated": 0.0, "user_ts": 0.0}
# user_ts — когда своим плеером последний раз управлял человек (не «трек доиграл»)


def snapshot() -> dict:
    with _LOCK:
        cur = STATE["items"][STATE["pos"]] if 0 <= STATE["pos"] < len(STATE["items"]) else None
        return {
            "seq": STATE["seq"],
            "cmd": STATE["cmd"],
            "status": STATE["status"],
            "service": STATE["service"],
            "title": STATE["title"],
            "pos": STATE["pos"],
            "count": len(STATE["items"]),
            "current": dict(cur) if cur else None,
        }


def _bump(cmd: str) -> None:
    STATE["seq"] += 1
    STATE["cmd"] = cmd
    STATE["updated"] = time.time()


def load(items: list[dict], source: str, service: str, title: str) -> dict:
    with _LOCK:
        STATE["items"] = list(items)
        STATE["pos"] = 0 if items else -1
        STATE["status"] = "playing" if items else "idle"
        STATE["source"], STATE["service"], STATE["title"] = source, service, title
        _bump("load")
        STATE["user_ts"] = time.time()
    return snapshot()


def active() -> bool:
    return STATE["status"] in ("playing", "paused") and STATE["pos"] >= 0


def playing() -> bool:
    return STATE["status"] == "playing" and STATE["pos"] >= 0


def _refill() -> None:
    """Волна бесконечная: когда треки кончаются — подкачиваем ещё."""
    if STATE["pos"] < len(STATE["items"]) - 2 or not STATE["source"]:
        return
    last = STATE["items"][-1]["track"] if STATE["items"] else ""
    seen = {x["track"] for x in STATE["items"][-50:]}
    extra = [x for x in yandex.more(STATE["source"], last) if x["track"] not in seen]
    STATE["items"].extend(extra)


def command(cmd: str) -> dict:
    """pause | play | next | prev | stop | ended (трек доиграл — от окна)."""
    user = cmd not in ("ended", "error")
    with _LOCK:
        if STATE["pos"] < 0:
            return snapshot_unlocked()
        if cmd == "pause":
            STATE["status"] = "paused"
        elif cmd in ("play", "resume"):
            STATE["status"] = "playing"
            cmd = "play"
        elif cmd == "stop":
            STATE["status"] = "idle"
        elif cmd in ("next", "ended", "error"):
            try:
                _refill()
            except Exception:  # noqa: BLE001
                pass
            if STATE["pos"] + 1 < len(STATE["items"]):
                STATE["pos"] += 1
                STATE["status"] = "playing"
            else:
                STATE["status"] = "idle"
                cmd = "stop"
            cmd = "track" if cmd != "stop" else cmd
        elif cmd == "prev":
            STATE["pos"] = max(0, STATE["pos"] - 1)
            STATE["status"] = "playing"
            cmd = "track"
        else:
            return snapshot_unlocked()
        if user:
            STATE["user_ts"] = time.time()
        _bump(cmd)
    return snapshot()


def snapshot_unlocked() -> dict:
    cur = STATE["items"][STATE["pos"]] if 0 <= STATE["pos"] < len(STATE["items"]) else None
    return {"seq": STATE["seq"], "cmd": STATE["cmd"], "status": STATE["status"], "service": STATE["service"], "title": STATE["title"], "pos": STATE["pos"], "count": len(STATE["items"]), "current": dict(cur) if cur else None}


def describe() -> dict:
    s = snapshot()
    cur = s["current"] or {}
    return {"status": s["status"] if s["status"] != "idle" else "none", "app": "Яндекс Музыка, играю сама", "title": cur.get("title", ""), "artist": cur.get("artist", ""), "via": "aya"}
