"""Мастер первого запуска: проверка ключей и «пройден ли мастер».

Ключи никогда не возвращаются наружу — только ok/ошибка. Ключ, скопированный
из браузера с пробелами или переводом строки, обрезается.
"""
import httpx

LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def _request(method: str, url: str, headers: dict | None = None, json: dict | None = None, timeout: float = 8.0):
    """Один запрос к внешнему сервису → (статус, тело-JSON или None)."""
    with httpx.Client(timeout=timeout, follow_redirects=True) as c:
        r = c.request(method, url, headers=headers or {}, json=json)
        try:
            body = r.json()
        except ValueError:
            body = None
        return r.status_code, body


def is_local(base_url: str) -> bool:
    return any(h in (base_url or "") for h in LOCAL_HOSTS)


def llm_configured(sec: dict | None) -> bool:
    """Уже есть рабочая модель: ключ или своя Ollama на этом компьютере."""
    sec = sec or {}
    return bool(str(sec.get("api_key") or "").strip()) or is_local(str(sec.get("base_url") or ""))


def _models(ltype: str, body) -> list[str]:
    if not isinstance(body, dict):
        return []
    if ltype == "ollama":
        return [str(m.get("name") or m.get("model")) for m in body.get("models") or [] if isinstance(m, dict)]
    ids = [str(m.get("id")) for m in body.get("data") or [] if isinstance(m, dict) and m.get("id")]
    # у OpenRouter сотни моделей — бесплатные (:free) наверх списка
    return sorted(ids, key=lambda m: not m.endswith(":free"))


def llm_check(ltype: str, base_url: str, api_key: str, model: str = "") -> dict:
    """Список моделей у сервиса и короткий запрос к модели — проверка адреса и ключа.
    → {"ok", "error": "key"|"offline"|"url"|"model"|"", "models"}."""
    base = (base_url or "").strip().rstrip("/")
    key = (api_key or "").strip()
    if not base.startswith(("http://", "https://")):
        return {"ok": False, "error": "url", "models": []}
    url = f"{base}/api/tags" if ltype == "ollama" else f"{base}/models"
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        status, body = _request("GET", url, headers=headers)
    except (httpx.NetworkError, httpx.TimeoutException) as _e:
        # своя Ollama не запущена — это «адрес не отвечает», а не «нет интернета»
        return {"ok": False, "error": "url" if is_local(base) else "offline", "models": []}
    except httpx.HTTPError:
        return {"ok": False, "error": "url", "models": []}
    if status in (401, 403):
        return {"ok": False, "error": "key", "models": []}
    if status != 200:
        return {"ok": False, "error": "url", "models": []}
    models = _models(ltype, body)
    if not key:
        return {"ok": True, "error": "", "models": models}
    # список моделей бывает открыт всем (ollama.com, OpenRouter) — ключ проверяем
    # коротким запросом к самой модели (1–2 токена)
    probe = _probe(ltype, base, key, model or (models[0] if models else ""))
    if probe:
        return {"ok": False, "error": probe, "models": [] if probe == "key" else models}  # paid/model: список оставляем — выбрать другую
    return {"ok": True, "error": "", "models": models}


def _probe(ltype: str, base: str, key: str, model: str) -> str:
    """'' — ключ и модель годятся; иначе код ошибки."""
    if not model:
        return ""
    headers = {"Authorization": f"Bearer {key}"}
    msg = [{"role": "user", "content": "Ответь одним словом: да"}]
    if ltype == "ollama":
        url, body = f"{base}/api/chat", {"model": model, "messages": msg, "stream": False, "options": {"num_predict": 2}}
    else:
        url, body = f"{base}/chat/completions", {"model": model, "messages": msg, "max_tokens": 2}
    try:
        status, _ = _request("POST", url, headers=headers, json=body, timeout=30)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return "offline"
    except httpx.HTTPError:
        return ""  # модель долго думает — ключ при этом принят
    if status in (401, 403):
        return "key"
    if status == 402:
        return "paid"  # ключ верный, но модель платная или на счёте пусто
    if status == 404:
        return "model"
    return ""


def fish_check(key: str) -> dict:
    """Короткий синтез «Привет» с этим ключом. → {"ok", "error": "key"|"offline"|""}."""
    from .voice import tts

    key = (key or "").strip()
    if not key:
        return {"ok": False, "error": "key"}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "model": tts.FISH_MODEL}
    try:
        status, _body = _request("POST", tts.FISH_URL, headers=headers, json=tts._fish_body("Привет", tts.DEFAULT_VOICE), timeout=15)
    except httpx.HTTPError:
        return {"ok": False, "error": "offline"}
    if status in (401, 402, 403):
        return {"ok": False, "error": "key"}
    if status != 200:
        return {"ok": False, "error": "offline"}
    return {"ok": True, "error": ""}
