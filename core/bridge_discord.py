"""Мост Discord: бот discord.py в фоновом потоке → локальный /api/chat → ответ в канал.

Конфиг в ~/.aya/config.json:
{
  "discord": {"token": "...", "channel_id": "123456789", "enabled": true}
}

Внимание: в Developer Portal бота должен быть включён privileged intent
MESSAGE CONTENT INTENT, иначе сообщения не читаются.
"""
import asyncio
import json
import os
import threading

from . import db


def config_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".aya", "config.json")


def load_config() -> dict:
    try:
        with open(config_path(), encoding="utf-8-sig") as f:
            cfg = (json.load(f) or {}).get("discord") or {}
    except (OSError, ValueError):
        cfg = {}
    return {
        "token": str(cfg.get("token", "")),
        "channel_id": str(cfg.get("channel_id", "")),
        "enabled": bool(cfg.get("enabled", False)),
    }


def save_config(token: str, channel_id: str, enabled: bool) -> None:
    """Пустой token — оставить сохранённый (консоль не присылает токен, если поле пустое)."""
    path = config_path()
    try:
        with open(path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        cfg = {}
    # битый JSON — ValueError наружу: общий конфиг (модель, ключи) не затираем
    token = token or load_config()["token"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cfg["discord"] = {"token": token, "channel_id": channel_id, "enabled": enabled}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Bridge:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._client = None
        self._loop = None
        self.running = False
        self.error = ""
        self.sent = 0
        self.received = 0

    def start(self):
        cfg = load_config()
        if not (cfg["enabled"] and cfg["token"]):
            return
        if self.running:
            self.stop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="aya-discord")
        self._thread.start()

    def stop(self):
        self._stop.set()
        client, loop = self._client, self._loop
        if client and loop:
            try:
                asyncio.run_coroutine_threadsafe(client.close(), loop).result(timeout=6)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None
        self._client = None
        self._loop = None
        self.running = False

    def restart(self):
        self.stop()
        self.start()

    def _loop(self):
        try:
            import discord
        except ImportError:
            self.error = "нет пакета discord.py (pip install discord.py)"
            db.log_event("discord", self.error)
            return
        cfg = load_config()
        token = cfg["token"]
        if not token:
            return
        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client

        @client.event
        async def on_ready():
            self.running = True
            self.error = ""
            db.log_event("discord", f"мост запущен ({client.user})")

        @client.event
        async def on_message(message):
            if message.author.bot:
                return
            cfg2 = load_config()
            if cfg2["channel_id"] and str(message.channel.id) != str(cfg2["channel_id"]):
                return
            text = str(message.content or "")[:2000]
            if not text.strip():
                return
            self.received += 1
            sender = message.author.display_name
            reply = self._ask_core(f"[Discord, от {sender}] {text}")
            self.sent += 1
            try:
                await message.channel.send(reply[:2000])
            except Exception as e:
                self.error = str(e)[:160]
                db.log_event("discord", f"ошибка отправки: {e}")

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            client.run(token, log_handler=None)
        except discord.errors.LoginFailure:
            self.error = "неверный токен бота"
            db.log_event("discord", self.error)
        except Exception as e:
            self.error = str(e)[:200]
            db.log_event("discord", f"ошибка моста: {e}")
        finally:
            self.running = False

    def _ask_core(self, text: str) -> str:
        import urllib.request

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
                reply += "\n(нужно подтверждение в окне Аи)"
            return reply or "(пустой ответ)"
        except Exception as e:
            return f"ошибка: {str(e)[:200]}"

    def status(self) -> dict:
        cfg = load_config()
        token = cfg["token"]
        out = {
            "enabled": cfg["enabled"],
            "channel_id": cfg["channel_id"],
            "token_tail": f"...{token[-4:]}" if token else "",
            "has_token": bool(token),
            "package": True,
            "running": self.running,
            "error": self.error,
            "sent": self.sent,
            "received": self.received,
        }
        try:
            import discord  # noqa: F401
        except ImportError:
            out["package"] = False
        return out


BRIDGE = Bridge()
