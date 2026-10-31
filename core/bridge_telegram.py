"""Мост Telegram: long-polling getUpdates → локальный /api/chat → sendMessage.

Конфиг в ~/.aya/config.json (раньше — core/config.json в папке установки,
его обновление стирало; старые настройки подхватываются оттуда):
{
  "telegram": {"token": "123:...", "chat_id": "12345", "enabled": true}
}
Ответы разрешены только из одного чата (chat_id).
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request

from . import db

API = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT = 30
MAX_LEN = 3500


def config_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".aya", "config.json")


LEGACY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def _read(path: str) -> dict:
    """{} — файла нет; битый JSON — ValueError (такой файл перезаписывать нельзя)."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def _section() -> dict:
    try:
        tg = _read(config_path()).get("telegram")
        if not tg:
            tg = _read(LEGACY_PATH).get("telegram")
    except (OSError, ValueError):
        tg = None
    return tg if isinstance(tg, dict) else {}


def load_tg_config() -> dict:
    cfg = _section()
    return {
        "token": str(cfg.get("token", "")),
        "chat_id": str(cfg.get("chat_id", "")),
        "enabled": bool(cfg.get("enabled", False)),
    }


def save_tg_config(token: str, chat_id: str, enabled: bool) -> None:
    """Пустой token — оставить сохранённый (консоль не присылает токен, если поле пустое)."""
    path = config_path()
    cfg = _read(path)  # битый файл — ValueError: общий конфиг (модель, ключи) не затираем
    token = token or load_tg_config()["token"]
    cfg["telegram"] = {"token": token, "chat_id": chat_id, "enabled": enabled}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Bridge:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._offset = 0
        self.running = False
        self.error = ""
        self.sent = 0
        self.received = 0

    # ── Telegram API ──────────────────────────────────────────
    def _api(self, method: str, data: dict | None = None, timeout: int = 40):
        cfg = load_tg_config()
        if not cfg["token"]:
            raise RuntimeError("нет токена")
        url = API.format(token=cfg["token"], method=method)
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        else:
            req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ── lifecycle ─────────────────────────────────────────────
    def start(self):
        cfg = load_tg_config()
        if not (cfg["enabled"] and cfg["token"] and cfg["chat_id"]):
            return
        if self.running:
            self.stop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="aya-tg")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None
        self.running = False

    def restart(self):
        self.stop()
        self.start()

    # ── цикл ──────────────────────────────────────────────────
    def _loop(self):
        self.running = True
        self.error = ""
        try:
            me = self._api("getMe", timeout=15)
            if not me.get("ok"):
                self.error = f"getMe: {me}"
                db.log_event("telegram", f"ошибка getMe: {me}")
                self.running = False
                return
        except Exception as e:
            self.error = str(e)
            db.log_event("telegram", f"ошибка getMe: {e}")
            self.running = False
            return
        db.log_event("telegram", f"мост запущен (@{me['result'].get('username')})")
        while not self._stop.is_set():
            try:
                data = self._api(
                    "getUpdates",
                    {"offset": self._offset, "timeout": POLL_TIMEOUT, "allowed_updates": ["message"]},
                    timeout=POLL_TIMEOUT + 15,
                )
            except Exception as e:
                self.error = str(e)[:160]
                time.sleep(5)
                continue
            if self._stop.is_set():
                break
            if not data.get("ok"):
                self.error = str(data)[:160]
                time.sleep(5)
                continue
            for upd in data.get("result") or []:
                self._offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                if not (msg.get("chat") or {}).get("id") or not msg.get("text"):
                    continue
                self._handle(msg)

    def _handle(self, msg: dict):
        chat_id = str(msg["chat"]["id"])
        cfg = load_tg_config()
        if cfg["chat_id"] and chat_id != cfg["chat_id"]:
            db.log_event("telegram", f"чужой чат {chat_id} закрыт")
            try:
                self._api("sendMessage", {"chat_id": chat_id, "text": "Этот бот отвечает только в назначенном чате."}, timeout=15)
            except Exception:
                pass
            return
        text = str(msg["text"])[:2000]
        sender = (msg.get("from") or {}).get("first_name", "?")
        self.received += 1
        reply = self._ask_core(f"[Telegram, от {sender}] {text}")
        self.sent += 1
        self._send(chat_id, reply)

    def _ask_core(self, text: str) -> str:
        body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8330/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                out = json.loads(resp.read().decode("utf-8"))
            reply = str(out.get("reply", ""))
            if out.get("pending"):
                reply += "\n(жмёт кнопку подтверждения в окне Аи)"
            return reply or "(пустой ответ)"
        except Exception as e:
            return f"ошибка: {str(e)[:200]}"

    def _send(self, chat_id: str, text: str):
        for chunk_start in range(0, len(text), MAX_LEN):
            chunk = text[chunk_start : chunk_start + MAX_LEN]
            try:
                self._api("sendMessage", {"chat_id": chat_id, "text": chunk})
            except Exception as e:
                self.error = str(e)[:160]
                db.log_event("telegram", f"ошибка отправки: {e}")
                return

    def status(self) -> dict:
        cfg = load_tg_config()
        token = cfg["token"]
        return {
            "enabled": cfg["enabled"],
            "chat_id": cfg["chat_id"],
            "token_tail": f"...{token[-4:]}" if token else "",
            "has_token": bool(token),
            "running": self.running,
            "error": self.error,
            "sent": self.sent,
            "received": self.received,
        }


BRIDGE = Bridge()
