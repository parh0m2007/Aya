"""Помодоро вместе с Аей: фокус → перерыв → фокус… Пока идёт фокус, включена «Тишина».

Состояние считается лениво при каждом status(): окно Аи опрашивает его раз
в несколько секунд и по смене фазы (seq) говорит короткую фразу.
"""
import threading
import time

from . import db

_lock = threading.Lock()
_s = {
    "phase": "idle",  # idle | focus | break
    "ends": 0.0,
    "started": 0.0,
    "focus_min": 25,
    "break_min": 5,
    "round": 0,
    "rounds": 4,
    "seq": 0,  # растёт на каждой смене фазы
    "event": "",  # start | break | focus | done | stop
    "prev_focus": "0",
}


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _set_focus(on: bool) -> None:
    db.set_setting("focus_mode", "1" if on else "0")


def _event(name: str) -> None:
    _s["seq"] += 1
    _s["event"] = name


def start(focus_min=25, break_min=5, rounds=4, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    with _lock:
        if _s["phase"] == "idle":
            _s["prev_focus"] = db.setting("focus_mode", "0")
        _s.update(
            phase="focus",
            started=now,
            focus_min=_clamp(focus_min, 1, 180, 25),
            break_min=_clamp(break_min, 1, 60, 5),
            rounds=_clamp(rounds, 1, 12, 4),
            round=1,
        )
        _s["ends"] = now + _s["focus_min"] * 60
        _set_focus(True)
        _event("start")
    db.log_event("pomodoro", f"старт {_s['focus_min']}/{_s['break_min']} ×{_s['rounds']}")
    return status(now)


def stop(now: float | None = None) -> dict:
    with _lock:
        if _s["phase"] != "idle":
            _s["phase"] = "idle"
            _s["ends"] = 0.0
            _set_focus(_s["prev_focus"] == "1")
            _event("stop")
    return status(now)


def _advance(now: float) -> None:
    """Перещёлкнуть фазы, если время вышло (возможно, несколько раз — ПК спал)."""
    while _s["phase"] != "idle" and now >= _s["ends"]:
        if _s["phase"] == "focus":
            if _s["round"] >= _s["rounds"]:
                _s["phase"] = "idle"
                _s["ends"] = 0.0
                _set_focus(_s["prev_focus"] == "1")
                _event("done")
                return
            _s["phase"] = "break"
            _s["ends"] += _s["break_min"] * 60
            _set_focus(False)
            _event("break")
        else:
            _s["phase"] = "focus"
            _s["round"] += 1
            _s["ends"] += _s["focus_min"] * 60
            _set_focus(True)
            _event("focus")


def status(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    with _lock:
        _advance(now)
        left = max(0, int(_s["ends"] - now)) if _s["phase"] != "idle" else 0
        total = (_s["focus_min"] if _s["phase"] == "focus" else _s["break_min"]) * 60
        return {
            "phase": _s["phase"],
            "left": left,
            "total": total if _s["phase"] != "idle" else 0,
            "round": _s["round"],
            "rounds": _s["rounds"],
            "focus_min": _s["focus_min"],
            "break_min": _s["break_min"],
            "seq": _s["seq"],
            "event": _s["event"],
        }


def tool(args: dict) -> str:
    """Инструмент модели: pomodoro {action: start|stop|status, focus?, break?, rounds?}."""
    action = str(args.get("action", "start")).lower()
    if action == "stop":
        stop()
        return "помодоро остановлен, тишина снята"
    if action == "status":
        s = status()
        if s["phase"] == "idle":
            return "помодоро сейчас не идёт"
        m, sec = divmod(s["left"], 60)
        what = "фокус" if s["phase"] == "focus" else "перерыв"
        return f"идёт {what}, раунд {s['round']} из {s['rounds']}, осталось {m}:{sec:02d}"
    s = start(args.get("focus", 25), args.get("break", 5), args.get("rounds", 4))
    return f"помодоро запущен: {s['focus_min']} мин фокуса / {s['break_min']} мин перерыва, раундов {s['rounds']}; тишина включена"
