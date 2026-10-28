"""Долгосрочная память Аи: индекс всех разговоров + поиск по смыслу.

Как работает:
- каждое сообщение (user / assistant / aya) сохраняется в таблицу memories;
- векторизация — локальный Ollama (/api/embeddings): nomic-embed-text,
  bge-m3, all-minilm — что установлено, то и используется; всё остаётся
  на машине, ничего не уходит в облако;
- если Ollama недоступна или модель не скачана — работает лексический
  fallback (по общим токенам), recall доступен всегда.

Инструмент recall {query} — свободный для основной Аи, суб-агентам закрыт.
"""
import math
import os
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import db

# Один фоновый поток: эмбеддинги считаются по очереди и не задерживают ответ.
# Раньше ingest() звался прямо в обработчике чата — синхронный запрос к Ollama
# блокировал event loop до первого токена (на холодной модели — секунды).
_BG = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aya-mem")
_TAGS = {"ts": 0.0, "names": set()}
_TAGS_LOCK = threading.Lock()

MODEL_FAIL = {"ts": 0.0}
_COOLDOWN = 600.0  # после неудачи не дёргаем ollama 10 минут


def _ollama_base() -> str:
    return os.environ.get("AYA_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


def _candidate_models() -> list[str]:
    own = db.setting("mem_embed_model", "").strip()
    base = ["nomic-embed-text", "bge-m3", "all-minilm", "snowflake-arctic-embed"]
    return ([own] if own else []) + [m for m in base if m != own]


def _norm(vec: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vec)) or 1.0


def _ollama_embed(text: str) -> list[float] | None:
    """Локальные эмбеддинги через ollama; None = нет модели/сервера."""
    if time.time() - MODEL_FAIL["ts"] < _COOLDOWN:
        return None
    try:
        import httpx

        base = _ollama_base()
        with httpx.Client(timeout=20, trust_env=False) as c:
            installed = _installed_models(c, base)
            for model in _candidate_models():
                if installed and model not in installed:
                    continue
                r = c.post(base + "/api/embeddings", json={"model": model, "prompt": text[:2000]})
                if r.status_code != 200:
                    continue
                vec = r.json().get("embedding")
                if vec:
                    return [float(x) for x in vec]
    except Exception:
        pass
    MODEL_FAIL["ts"] = time.time()
    return None


def _installed_models(c, base: str) -> set:
    """Список моделей Ollama кэшируем на 5 минут — не дёргаем /api/tags на каждую реплику."""
    with _TAGS_LOCK:
        if time.time() - _TAGS["ts"] < 300:
            return set(_TAGS["names"])
    try:
        tags = c.get(base + "/api/tags").json().get("models") or []
        names = {str(m.get("name", "")).replace(":latest", "") for m in tags}
    except Exception:  # noqa: BLE001
        names = set()
    with _TAGS_LOCK:
        _TAGS["ts"], _TAGS["names"] = time.time(), names
    return set(names)


def enabled() -> bool:
    """Модуль «долгая память» можно выключить в конструкторе."""
    return db.setting("mod_memory", "1") != "0"


def ingest_bg(role: str, text: str, msg_id) -> None:
    """Неблокирующая запись в индекс (для обработчиков чата)."""
    if not enabled():
        return
    try:
        _BG.submit(_safe_ingest, role, text, msg_id)
    except RuntimeError:  # интерпретатор завершается
        pass


def _safe_ingest(role: str, text: str, msg_id) -> None:
    try:
        ingest(role, text, msg_id)
    except Exception:  # noqa: BLE001 — память не должна ронять разговор
        pass


def ingest(role: str, text: str, msg_id: int) -> None:
    """Добавить сообщение в индекс (роль — только настоящие реплики)."""
    if role not in ("user", "assistant", "aya") or msg_id is None:
        return
    text = str(text or "").strip()[:4000]
    if not text:
        return
    vec = _ollama_embed(text)
    blob = struct.pack(f"{len(vec)}f", *vec) if vec else b""
    dn = _norm(vec) if vec else 0.0
    db.execute(
        "INSERT OR REPLACE INTO memories(msg_id,ts,role,text,vec,dnorm) VALUES(?,?,?,?,?,?)",
        (msg_id, time.time(), role, text, blob, dn),
    )


def _tokens(q: str) -> list[str]:
    return [t for t in "".join(ch if ch.isalnum() else " " for ch in q.lower()).split() if len(t) > 1]


def _scored(query: str) -> list[tuple[float, float, str, str]] | None:
    """Оценки похожести (score, ts, role, text); None — память пуста."""
    rows = db.query(
        "SELECT msg_id,ts,role,text,vec,dnorm FROM memories ORDER BY msg_id DESC LIMIT 4000"
    )
    if not rows:
        return None
    qv = _ollama_embed(query)
    qn = _norm(qv) if qv else 0.0
    qtok = set(_tokens(query))
    scored: list[tuple[float, float, str, str]] = []
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        np = None
    sem: dict[int, float] = {}
    if qv is not None and np is not None:
        # векторно: одна матрица вместо питоновского цикла по 4000×768
        q = np.asarray(qv, dtype=np.float32)
        idx, mats, norms = [], [], []
        for i, r in enumerate(rows):
            if r[4] and r[5] and len(r[4]) == 4 * len(qv):
                idx.append(i)
                mats.append(np.frombuffer(r[4], dtype=np.float32))
                norms.append(float(r[5]))
        if mats:
            dots = np.vstack(mats) @ q
            sims = dots / (np.asarray(norms, dtype=np.float32) * (qn or 1.0))
            sem = {i: float(v) for i, v in zip(idx, sims)}
    elif qv is not None:
        for i, r in enumerate(rows):
            if r[4] and r[5] and len(r[4]) == 4 * len(qv):
                vec = struct.unpack(f"{len(r[4]) // 4}f", r[4])
                sem[i] = sum(a * b for a, b in zip(qv, vec)) / (qn * float(r[5]) or 1.0)
    for i, (_mid, ts, role, text, _vec, _dn) in enumerate(rows):
        tt = set(_tokens(str(text)))
        lex = (len(qtok & tt) / len(qtok | tt)) if (qtok and tt and len(qtok | tt) > 1) else 0.0
        if qv is not None:
            s = sem.get(i, 0.0) * 0.7 + 0.3 * lex  # гибрид: смысл + слова
        else:
            s = lex
        if s > 0.08:
            scored.append((s, ts, str(role), str(text)[:180]))
    scored.sort(key=lambda r: -r[0])
    return scored


def search(query: str, limit: int = 10) -> list[dict]:
    """Для консоли: похожие прошлые реплики списком."""
    query = str(query or "").strip()[:400]
    if not query:
        return []
    limit = max(1, min(limit, 30))
    scored = _scored(query) or []
    out = [
        {"score": round(sc, 3), "ts": ts, "role": "aya" if role in ("assistant", "aya") else "user", "text": text}
        for sc, ts, role, text in scored[:limit]
    ]
    if len(out) < limit:
        # без эмбеддингов оценка по словам строгая для длинных фраз — добираем
        # совпадениями по основам слов («кот» найдёт «кота», «котиком»)
        stems = [t[: max(3, len(t) - 3)] for t in _tokens(query) if len(t) >= 3]
        if stems:
            seen = {o["text"] for o in out}
            rows = db.query("SELECT ts,role,text FROM memories ORDER BY msg_id DESC LIMIT 4000")
            for ts, role, text in rows:
                words = _tokens(str(text))
                hit = sum(1 for s in stems if any(w.startswith(s) for w in words))
                if hit and str(text)[:180] not in seen:
                    out.append({"score": round(0.05 * hit / len(stems), 3), "ts": ts, "role": "aya" if role in ("assistant", "aya") else "user", "text": str(text)[:180]})
                    seen.add(str(text)[:180])
                if len(out) >= limit:
                    break
    return out


def recall(query: str, limit: int = 3) -> str:
    """Найти похожие прошлые сообщения; текст-ответ для Аи."""
    query = str(query or "").strip()[:400]
    if not query:
        return "что вспомнить? уточни запрос"
    scored = _scored(query)
    if scored is None:
        return "в долгой памяти пока пусто — мы ведь только начинаем?"
    if not scored:
        return "ничего похожего в памяти не нашла — совсем не помню про это"
    out = []
    for _s, ts, role, text in scored[: max(1, min(limit, 6))]:
        stamp = time.strftime("%d.%m", time.localtime(ts))
        who = "Ая" if role in ("assistant", "aya") else "Ты"
        out.append(f"{stamp} | {who}: {text}")
    return "Вот что нашла в долгой памяти:\n" + "\n".join(out)


def stats() -> dict:
    total = db.query("SELECT COUNT(*) FROM memories")
    with_vec = db.query("SELECT COUNT(*) FROM memories WHERE dnorm > 0")  # реально векторизованные
    return {"indexed": int(total[0][0]) if total else 0, "vectors": int(with_vec[0][0]) if with_vec else 0}
