"""Экранное зрение Аи: скриншот всего экрана → OCR (+ VLM, если настроена llm_vision).

Порядок:
1. Снимок экрана сохраняется в ~/.aya/screens (ротация, 20 штук).
2. Список видимых заголовков окон.
3. Если в ~/.aya/config.json есть секция llm_vision (openai-совместимый
   endpoint с мультимодальной моделью) — описание приходит от неё.
4. Иначе — RapidOCR по снимку (текстовый фолбэк, работает всегда).

Инструмент screen_look доступен основной Ае (не суб-агентам).
"""
import base64
import json
import os
import time

PROMPT = (
    "Ты — глаза ассистента Аи. Опиши кратко и по делу, что на скриншоте: "
    "какие приложения открыты, что написано, что происходит. До 6 предложений, по-русски."
)


def screens_dir() -> str:
    path = os.path.join(os.path.expanduser("~"), ".aya", "screens")
    os.makedirs(path, exist_ok=True)
    return path


def take_shot() -> str:
    from PIL import ImageGrab

    path = os.path.join(screens_dir(), f"scr-{int(time.time())}.png")
    ImageGrab.grab().save(path)
    _rotate()
    return path


def _rotate(keep: int = 20):
    try:
        shots = sorted(
            (p for p in os.listdir(screens_dir()) if p.startswith("scr-") and p.endswith(".png")),
        )
        for old in shots[:-keep]:
            try:
                os.remove(os.path.join(screens_dir(), old))
            except OSError:
                pass
    except OSError:
        pass


def window_titles(limit: int = 12) -> list[str]:
    import ctypes

    titles: list[str] = []
    user32 = ctypes.windll.user32
    enum_cb = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                t = buf.value.strip()
                if t and t not in titles:
                    titles.append(t)
        return True

    try:
        user32.EnumWindows(enum_cb(_cb), None)
    except Exception:
        pass
    return titles[:limit]


_OCR = None


def ocr_text(path: str, limit: int = 1800) -> str:
    """RapidOCR по снимку (ленивая загрузка), текст строкой или ''."""
    global _OCR
    try:
        if _OCR is None:
            from rapidocr_onnxruntime import RapidOCR

            _OCR = RapidOCR()
        result, _ = _OCR(path)
        if not result:
            return ""
        lines = [str(r[1]).strip() for r in result if r and len(r) > 1 and str(r[1]).strip()]
        return "\n".join(lines)[:limit]
    except Exception:
        return ""


def vlm_describe(path: str, cfg: dict) -> str:
    """Запрос в openai-совместимую VLM (llm_vision в config.json)."""
    import httpx

    base = str(cfg.get("base_url", "")).rstrip("/")
    model = str(cfg.get("model", ""))
    if not base or not model:
        return ""
    if not base.endswith("/chat/completions"):
        base += "/chat/completions"
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    headers = {"Content-Type": "application/json"}
    key = str(cfg.get("api_key") or "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
        "max_tokens": int(cfg.get("max_tokens", 400)),
    }
    with httpx.Client(timeout=90, trust_env=False) as client:
        r = client.post(base, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    return str(((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")).strip()


def vision_config() -> dict:
    path = os.path.join(os.path.expanduser("~"), ".aya", "config.json")
    try:
        with open(path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        sec = cfg.get("llm_vision") or {}
        return sec if isinstance(sec, dict) else {}
    except (OSError, ValueError):
        return {}


def describe() -> str:
    path = take_shot()
    titles = window_titles()
    parts = []
    if titles:
        parts.append("Открытые окна: " + "; ".join(titles))
    cfg = vision_config()
    if cfg.get("base_url") and cfg.get("model"):
        try:
            text = vlm_describe(path, cfg)
            if text:
                parts.append("Что вижу на экране: " + text)
        except Exception as e:  # noqa: BLE001
            parts.append(f"(модель зрения недоступна: {str(e)[:120]})")
    ocr = ocr_text(path)
    if ocr:
        parts.append("Текст на экране (OCR):\n" + ocr)
    if not cfg.get("base_url"):
        parts.append(f"(скриншот: {path})")
    return "\n\n".join(parts).strip() or "экран пустой, не разглядела ничего"
