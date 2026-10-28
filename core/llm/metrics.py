import collections
import threading
import time

_lock = threading.Lock()
_llm: collections.deque = collections.deque(maxlen=100)
_tts: collections.deque = collections.deque(maxlen=100)


def llm_first(model: str, ms_to_first: float) -> None:
    with _lock:
        _llm.append({"ts": time.time(), "kind": "first", "model": model, "ms": ms_to_first})


def llm_done(model: str, ms_total: float) -> None:
    with _lock:
        _llm.append({"ts": time.time(), "kind": "done", "model": model, "ms": ms_total})


def llm_error(model: str, error: str) -> None:
    with _lock:
        _llm.append({"ts": time.time(), "kind": "error", "model": model, "ms": 0.0, "error": error[:160]})


def llm_fallback_used(model: str, error: str) -> None:
    with _lock:
        _llm.append({"ts": time.time(), "kind": "fallback", "model": model, "ms": 0.0, "error": error[:160]})


def tts_record(voice: str, cache_hit: bool, ms: float) -> None:
    with _lock:
        _tts.append({"ts": time.time(), "voice": voice, "hit": bool(cache_hit), "ms": ms})


def clear() -> None:
    with _lock:
        _llm.clear()
        _tts.clear()


def last_fallback() -> str | None:
    """Текст последнего фолбэка LLM (для диагностики)."""
    with _lock:
        for e in reversed(_llm):
            if e["kind"] == "fallback":
                return f"{e.get('model')}: {e.get('error') or 'без описания'}"
    return None


def snapshot() -> dict:
    out: dict = {}
    with _lock:
        llm = list(_llm)
        tts = list(_tts)
    firsts = [e["ms"] for e in llm if e["kind"] == "first"]
    outs = [e["ms"] for e in llm if e["kind"] == "done"]
    errs = [e for e in llm if e["kind"] == "error"]
    fbs = [e for e in llm if e["kind"] == "fallback"]

    def avg(xs, n=20):
        xs = xs[-n:]
        return round(sum(xs) / len(xs), 1) if xs else None

    out["llm_first_ms_avg"] = avg(firsts)
    out["llm_total_ms_avg"] = avg(outs)
    out["llm_errors"] = len(errs)
    out["llm_fallbacks"] = len(fbs)
    if errs:
        out["llm_last_error"] = f"{errs[-1].get('model')}: {errs[-1].get('error')}"
    out["llm_last_fallback"] = last_fallback()
    out["llm_requests"] = len([e for e in llm if e["kind"] == "done"]) + len(errs)
    out["tts_total"] = len(tts)
    out["tts_cache_pct"] = round(100 * len([e for e in tts if e["hit"]]) / len(tts), 1) if tts else None
    out["tts_ms_avg"] = avg([e["ms"] for e in tts])
    out["tts_last_voice"] = tts[-1]["voice"] if tts else None
    return out
