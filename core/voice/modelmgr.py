import gc
import threading
import time

from .. import db

_lock = threading.Lock()
_last: dict[str, float] = {}
_unloaders: dict[str, object] = {}
_stop = threading.Event()
_thread: threading.Thread | None = None

GLOBAL_KEY = "gpu_idle_unload_min"


def touch(*names: str) -> None:
    now = time.time()
    with _lock:
        for n in names:
            _last[n] = now


def idle_minutes() -> int:
    try:
        return max(0, int((db.setting(GLOBAL_KEY, "5") or "5").strip()))
    except ValueError:
        return 5


def register(name: str, unload) -> None:
    """unload() -> bool: True если что-то освободил."""
    with _lock:
        _unloaders[name] = unload


def unload_vram(names: list[str] | None = None) -> list[str]:
    done: list[str] = []
    with _lock:
        items = list(_unloaders.items())
    for name, fn in items:
        if names is not None and name not in names:
            continue
        try:
            if fn():
                done.append(name)
        except Exception:
            pass
    if done:
        try:
            gc.collect()
            import torch

            torch.cuda.empty_cache()
        except Exception:
            pass
        db.log_event("gpu", f"unloaded: {', '.join(done)}")
        touch(*done)
    return done


def status() -> dict[str, dict]:
    now = time.time()
    with _lock:
        return {n: {"idle_s": round(now - t, 1)} for n, t in _last.items()}


def _loop() -> None:
    while not _stop.wait(20):
        minutes = idle_minutes()
        if minutes <= 0:
            continue
        now = time.time()
        with _lock:
            due = [n for n, t in _last.items() if now - t > minutes * 60]
        if due:
            unload_vram(due)


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()
