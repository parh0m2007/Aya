"""Ая работает с твоим личным Telegram (MTProto-юзербот на Telethon).

Конфиг в ~/.aya/config.json:
{
  "telegram_user": {
    "api_id": 12345, "api_hash": "...", "phone": "+7...",
    "session": "1BQANOTEuMTA...",           # StringSession после входа
    "enabled": true,
    "mode": "report" | "draft" | "auto",    # доклады / черновики / автопилот
    "contacts": ["маша", "@petya"],         # пусто = вся личка
    "private_only": true
  }
}

Вход одноразовый (карточка в консоли): телефон → код из Telegram → пароль 2FA.
Сессия хранится локально. Входящие личные сообщения попадают в инбокс и
инициативой («tg:») рассказываются юзеру; ответ — инструмент tg_send.
"""

import asyncio
import json
import os
import threading
import time
import uuid

from . import db

INBOX_KEY = "tg_inbox"
INBOX_MAX = 40
INBOX_TTL = 600  # сек: письмо старше 10 минут — уже не новость, не озвучиваем
BRIDGE_START_WAIT = 12.0  # сек: сколько ждать подключения моста перед отправкой


# ── конфиг ──────────────────────────────────────────────────────────
def config_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".aya", "config.json")


def _norm_proxy(raw: dict | None) -> dict:
    """Нормализуем поле прокси: socks5 (host/port) | mtproto (host/port/secret)."""
    if not isinstance(raw, dict):
        return {}
    t = str(raw.get("type") or "").strip().lower()
    host = str(raw.get("host") or "").strip()
    try:
        port = int(raw.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    secret = str(raw.get("secret") or "").strip()
    if t not in ("socks5", "mtproto") or not host or port <= 0:
        return {}
    if t == "mtproto" and not secret:
        return {}
    return {"type": t, "host": host, "port": port, "secret": secret}


def _proxy_client_args(proxy: dict) -> dict:
    """Kwargs для TelegramClient: socks5 → proxy=, mtproto → MTProxy-коннекшн + secret."""
    if not proxy:
        return {}
    if proxy["type"] == "socks5":
        return {"proxy": ("socks5", proxy["host"], proxy["port"])}
    from telethon.network.connection import ConnectionTcpMTProxyRandomizedIntermediate

    return {
        "connection": ConnectionTcpMTProxyRandomizedIntermediate,
        "proxy": (proxy["host"], proxy["port"], proxy["secret"]),
    }


def load_config() -> dict:
    try:
        with open(config_path(), encoding="utf-8-sig") as f:
            cfg = (json.load(f) or {}).get("telegram_user") or {}
    except (OSError, ValueError):
        cfg = {}
    mode = str(cfg.get("mode", "report") or "report")
    return {
        "api_id": int(cfg.get("api_id") or 0),
        "api_hash": str(cfg.get("api_hash", "")),
        "phone": str(cfg.get("phone", "")),
        "session": str(cfg.get("session", "")),
        "enabled": bool(cfg.get("enabled", False)),
        "mode": mode if mode in ("report", "draft", "auto") else "report",
        "contacts": [str(c).strip() for c in (cfg.get("contacts") or []) if str(c).strip()],
        "private_only": bool(cfg.get("private_only", True)),
        "proxy": _norm_proxy(cfg.get("proxy")),
    }


def save_config(**patch) -> dict:
    try:
        with open(config_path(), encoding="utf-8-sig") as f:
            full = json.load(f)
    except (OSError, ValueError):
        full = {}
    raw = full.get("telegram_user") or {}
    cur = load_config()  # нормализованные дефолты
    for k, v in raw.items():
        if k in cur and cur[k] != v:
            pass  # load_config уже нормализовал
    cur.update({k: v for k, v in patch.items() if v is not None})
    # api_id может прийти строкой/числом
    try:
        cur["api_id"] = int(cur["api_id"] or 0)
    except (TypeError, ValueError):
        cur["api_id"] = 0
    if cur["mode"] not in ("report", "draft", "auto"):
        cur["mode"] = "report"
    cur["contacts"] = [str(c).strip() for c in cur["contacts"] if str(c).strip()]
    cur["proxy"] = _norm_proxy(cur.get("proxy"))
    full["telegram_user"] = cur
    tmp = config_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(full, f, ensure_ascii=False, indent=2)
    os.replace(tmp, config_path())
    return load_config()


def save_session(api_id: int, api_hash: str, phone: str, session: str) -> None:
    save_config(api_id=api_id, api_hash=api_hash, phone=phone, session=session)


# ── инбокс ──────────────────────────────────────────────────────────
def _inbox_read() -> list[dict]:
    try:
        data = json.loads(db.setting(INBOX_KEY, "[]") or "[]")
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def _item_ts(item: dict) -> float:
    """Время письма: время Telegram, иначе время получения (для старых записей)."""
    for key in ("msg_ts", "ts"):
        try:
            value = float(item.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def prune_inbox(max_age: float = INBOX_TTL, now: float | None = None) -> int:
    """Убрать письма старше max_age. Вернуть число удалённых."""
    now = time.time() if now is None else now
    inbox = _inbox_read()
    fresh = []
    for item in inbox:
        ts = _item_ts(item)
        if ts and now - ts > max_age:
            continue
        fresh.append(item)
    if len(fresh) != len(inbox):
        db.set_setting(INBOX_KEY, json.dumps(fresh, ensure_ascii=False))
    return len(inbox) - len(fresh)


def _msg_key(item: dict) -> tuple[int, int] | None:
    """Уникальный ключ Telegram-сообщения: (чат, id)."""
    try:
        chat = int(item.get("chat") or item.get("chat_id") or 0)
        msg_id = int(item.get("msg_id") or 0)
    except (TypeError, ValueError):
        return None
    return (chat, msg_id) if chat and msg_id else None


def inbox_push(item: dict) -> None:
    # id нужен, чтобы подтверждать доставку адресно, не трогая письма, пришедшие позже
    prune_inbox()
    item = dict(item)
    item.setdefault("id", uuid.uuid4().hex)
    inbox = _inbox_read()
    key = _msg_key(item)
    if key and any(_msg_key(it) == key for it in inbox):
        return
    inbox = (inbox + [item])[-INBOX_MAX:]
    db.set_setting(INBOX_KEY, json.dumps(inbox, ensure_ascii=False))


def inbox_text() -> str:
    prune_inbox()
    inbox = _inbox_read()[-10:]
    if not inbox:
        return "новых личных сообщений нет"
    return "\n".join(_fmt_inbox(inbox))


def peek_inbox(limit: int = 8) -> list[dict]:
    """Первые (самые старые) письма, не удаляя их. Показ без подтверждения доставки."""
    prune_inbox()
    return _inbox_read()[:limit]


def drop_inbox(items: list[dict]) -> int:
    """Удалить из инбокса ровно эти письма (по id). Вернуть число удалённых."""
    if not items:
        return 0
    kill_ids = {str(it["id"]) for it in items if it.get("id")}
    kill_legacy = [it for it in items if not it.get("id")]
    all_items = _inbox_read()
    left = []
    for it in all_items:
        ident = it.get("id")
        if ident and str(ident) in kill_ids:
            continue
        if not ident and it in kill_legacy:  # письма старого формата — по равенству
            kill_legacy.remove(it)
            continue
        left.append(it)
    if len(left) != len(all_items):
        db.set_setting(INBOX_KEY, json.dumps(left, ensure_ascii=False))
    return len(all_items) - len(left)


def drop_read(chat_id: int, max_id: int) -> int:
    """Убрать письма чата, прочитанные до max_id включительно."""
    try:
        chat_id = int(chat_id or 0)
        max_id = int(max_id or 0)
    except (TypeError, ValueError):
        return 0
    if not chat_id or not max_id:
        return 0
    all_items = _inbox_read()
    left = []
    for item in all_items:
        key = _msg_key(item)
        if key and key[0] == chat_id and key[1] <= max_id:
            continue
        left.append(item)
    if len(left) != len(all_items):
        db.set_setting(INBOX_KEY, json.dumps(left, ensure_ascii=False))
    return len(all_items) - len(left)


def take_inbox(limit: int = 8) -> list[str]:
    """Забрать (и подтвердить доставку) первые сообщения инбокса в виде строк."""
    items = peek_inbox(limit)
    drop_inbox(items)
    return _fmt_inbox(items)


def format_inbox(items: list[dict]) -> list[str]:
    return _fmt_inbox(items)


def _fmt_inbox(items: list[dict]) -> list[str]:
    out = []
    for it in items:
        stamp = time.strftime("%H:%M", time.localtime(float(it.get("ts") or 0)))
        out.append(f"{stamp} {it.get('from', '?')}: {str(it.get('text', ''))[:200]}")
    return out


def _matches(sender, contacts: list[str]) -> bool:
    """Пустой список = все; иначе сравнение подстрокой по имени/username/id."""
    if not contacts:
        return True
    parts = [
        str(getattr(sender, "first_name", "") or ""),
        str(getattr(sender, "last_name", "") or ""),
        str(getattr(sender, "username", "") or ""),
        str(getattr(sender, "id", "") or ""),
    ]
    hay = " ".join(p.lower() for p in parts if p)
    return any(str(c).lower().lstrip("@") in hay for c in contacts)


# ── медиа: говорящие пометки и расшифровка голосовых ────────────────
VOICE_TRANSCRIBE_MAX_SEC = 180  # длиннее — только пометка: большие файлы жалко гонять
VOICE_TRANSCRIBE_LIMIT = 2  # сколько свежих голосовых расшифровывать за чтение переписки
_VOICE_CACHE_MAX = 200
_voice_cache: dict[tuple[int, int], str] = {}  # (чат, id сообщения) → расшифровка


def _voice_cache_key(msg) -> tuple[int, int]:
    return (int(getattr(msg, "chat_id", 0) or 0), int(getattr(msg, "id", 0) or 0))


def media_label(msg) -> str:
    """Говорящая пометка для не-текстового сообщения (вместо безликого «[медиа]»)."""
    voice = getattr(msg, "voice", None)
    if voice is not None:
        return f"[голосовое, {int(getattr(voice, 'duration', 0) or 0)}с]"
    note = getattr(msg, "video_note", None)
    if note is not None:
        return f"[кружок, {int(getattr(note, 'duration', 0) or 0)}с]"
    if getattr(msg, "audio", None) is not None:
        return "[аудио]"
    video = getattr(msg, "video", None)
    if video is not None:
        return f"[видео, {int(getattr(video, 'duration', 0) or 0)}с]"
    if getattr(msg, "photo", None) is not None:
        return "[фото]"
    if getattr(msg, "sticker", None) is not None:
        return "[стикер]"
    if getattr(msg, "gif", None) is not None:
        return "[гифка]"
    doc = getattr(msg, "document", None)
    if doc is not None:
        fname = ""
        for attr in getattr(doc, "attributes", None) or []:
            if getattr(attr, "file_name", None):
                fname = str(attr.file_name)[:40]
        return f"[файл: {fname}]" if fname else "[файл]"
    if getattr(msg, "geo", None) is not None:
        return "[геолокация]"
    if getattr(msg, "contact", None) is not None:
        return "[контакт]"
    if getattr(msg, "poll", None) is not None:
        return "[опрос]"
    return "[медиа]"


async def voice_text(msg) -> str | None:
    """Расшифровать голосовое локальным whisper'ом; None — не вышло (длинное/ошибка)."""
    voice = getattr(msg, "voice", None)
    if voice is None:
        return None
    if int(getattr(voice, "duration", 0) or 0) > VOICE_TRANSCRIBE_MAX_SEC:
        return None
    key = _voice_cache_key(msg)
    if key in _voice_cache:
        return _voice_cache[key]
    try:
        data = await msg.download_media(file=bytes)
    except Exception:  # noqa: BLE001
        return None
    if not data:
        return None
    try:
        from .voice import stt

        text = await asyncio.to_thread(stt.transcribe, data)
    except Exception:  # noqa: BLE001
        return None
    text = str(text or "").strip()
    if not text:
        return None
    if len(_voice_cache) >= _VOICE_CACHE_MAX:
        _voice_cache.pop(next(iter(_voice_cache)), None)
    _voice_cache[key] = text
    return text


async def message_text(msg) -> str:
    """Текст для инбокса/переписки: подпись, иначе пометка; голосовые — с расшифровкой."""
    text = str(getattr(msg, "message", "") or "").strip()
    if text:
        return text
    label = media_label(msg)
    if getattr(msg, "voice", None) is not None:
        tr = await voice_text(msg)
        if tr:
            return f"[голосовое] {tr}"
        dur = int(getattr(msg.voice, "duration", 0) or 0)
        if dur <= VOICE_TRANSCRIBE_MAX_SEC:
            return f"{label[:-1]} — не смогла расшифровать]"
    return label


def has_session() -> bool:
    """Модульные обёртки (для gateway/tools/tests): состояние сессии и режима."""
    return BRIDGE.has_session()


def mode() -> str:
    return BRIDGE.mode()


def send(chat: str, text: str) -> str:
    return BRIDGE.send(chat, text)


def read_history(chat: str, limit: int = 5) -> str:
    return BRIDGE.read_history(chat, limit)


# ── логин (карточка в консоли) ──────────────────────────────────────
class LoginFlow:
    """Пошаговый вход: phone → code (+password при 2FA) → сессия сохраняется."""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = None
        self._thread: threading.Thread | None = None
        self.state = "idle"  # idle | code_sent | password_needed | done | error
        self.error = ""
        self._phone = ""
        self._hash = ""

    def _start_loop(self):
        if self._thread and self._thread.is_alive():
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="aya-tg-login")
        self._thread.start()

    def _run(self, coro, timeout: float = 90.0):
        if not self._loop:
            raise RuntimeError("логин не начат")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def reset(self):
        self.state = "idle"
        self.error = ""
        self._phone = ""
        self._hash = ""
        cli, loop = self._client, self._loop
        if cli and loop:
            try:
                asyncio.run_coroutine_threadsafe(cli.disconnect(), loop).result(10)
            except Exception:
                pass
        self._client = None

    def phone(self, api_id: int, api_hash: str, phone: str, proxy: dict | None = None) -> dict:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        self.reset()
        if proxy is not None:
            # прокси сохраняем сразу (до коннекта) — мост после входа тоже пойдёт через него
            save_config(proxy=proxy)
        self._start_loop()
        self._phone = str(phone).strip()
        args = _proxy_client_args(load_config()["proxy"])

        async def do():
            c = TelegramClient(
                StringSession(),
                int(api_id),
                api_hash,
                device_model="Aya",
                system_version="Windows",
                **args,
            )
            await c.connect()
            sent = await c.send_code_request(self._phone)
            return sent.phone_code_hash, c

        try:
            self._hash, self._client = self._run(do())
        except Exception as e:  # noqa: BLE001
            self.state, self.error = "error", str(e)[:200]
            return {"state": self.state, "error": self.error}
        # api_id/hash/phone сохраняем сразу, чтобы не потерять на следующем шаге
        save_config(api_id=int(api_id), api_hash=api_hash, phone=self._phone)
        self.state = "code_sent"
        return {"state": self.state, "ok": True, "hint": "код придёт в Telegram"}

    def code(self, code: str) -> dict:
        from telethon.errors import SessionPasswordNeededError

        client = self._client
        if not client or self.state != "code_sent":
            return {"state": self.state or "idle", "error": "сначала запроси код"}

        async def do():
            try:
                await client.sign_in(phone=self._phone, code=str(code).strip(), phone_code_hash=self._hash)
                return "done"
            except SessionPasswordNeededError:
                return "password"

        try:
            res = self._run(do())
        except Exception as e:  # noqa: BLE001
            self.state, self.error = "error", str(e)[:200]
            return {"state": self.state, "error": self.error}
        if res == "password":
            self.state = "password_needed"
            return {"state": self.state, "hint": "введи пароль двухфакторки"}
        return self._finalize()

    def password(self, password: str) -> dict:
        client = self._client
        if not client or self.state != "password_needed":
            return {"state": self.state or "idle", "error": "двухфакторка не запрошена"}

        async def do():
            await client.sign_in(password=str(password))
            return "done"

        try:
            res = self._run(do())
        except Exception as e:  # noqa: BLE001
            self.state, self.error = "error", str(e)[:200]
            return {"state": self.state, "error": self.error}
        return self._finalize()

    def _finalize(self) -> dict:
        client = self._client
        if not client:
            self.state = "error"
            return {"state": "error", "error": "клиент потерян"}
        cfg = load_config()

        async def grab():
            try:
                await client.get_me()
            except Exception:
                pass
            await client.disconnect()

        try:
            self._run(grab())
        except Exception as e:  # noqa: BLE001
            self.error = str(e)[:120]
        try:
            session = client.session.save()
        except Exception:
            session = ""
        if session:
            save_session(cfg["api_id"], cfg["api_hash"], cfg["phone"], session)
            self.state = "done"
            BRIDGE.start()
        else:
            self.state = "error"
            self.error = self.error or "пустая сессия"
        return {"state": self.state, "ok": bool(session), "error": self.error}


# ── мост ────────────────────────────────────────────────────────────
class Bridge:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = None
        self.running = False
        self.connected = False
        self.error = ""
        self.sent = 0
        self.received = 0
        self._me_id = 0
        self._gen = 0  # поколение моста: старые потоки не должны трогать новое состояние
        self._lock = threading.RLock()
        self._watchdog: threading.Thread | None = None
        self._last_start = 0.0
        self.last_exit = ""

    def has_session(self) -> bool:
        return bool(load_config()["session"])

    def mode(self) -> str:
        return load_config()["mode"]

    def _cfg_ready(self) -> dict | None:
        cfg = load_config()
        if cfg["enabled"] and cfg["session"] and cfg["api_id"] and cfg["api_hash"]:
            return cfg
        return None

    def _watchdog_loop(self):
        """Сторож: если мост умер сам — поднимаем его снова."""
        while True:
            time.sleep(10)
            try:
                self._ensure_alive()
            except Exception as e:  # noqa: BLE001
                self.error = f"watchdog: {str(e)[:140]}"

    def _ensure_alive(self) -> bool:
        with self._lock:
            if not self._cfg_ready():
                return False
            if self.running:
                return False
            if self._thread and self._thread.is_alive():
                return False
            if time.time() - self._last_start < 5:  # не долбим Telegram чаще, чем раз в 5 с
                return False
            self.start()
            return True

    def start(self):
        with self._lock:
            if not self._cfg_ready():
                return
            if self._watchdog is None:
                self._watchdog = threading.Thread(
                    target=self._watchdog_loop, daemon=True, name="aya-tg-watchdog"
                )
                self._watchdog.start()
            if self.running or (self._thread and self._thread.is_alive()):
                self.stop()
            self._gen += 1
            gen = self._gen
            self._last_start = time.time()
            self._thread = threading.Thread(
                target=self._main, args=(gen,), daemon=True, name="aya-tg-user"
            )
            self._thread.start()

    def stop(self):
        with self._lock:
            self._gen += 1  # текущий поток больше не хозяин состояния
            client, loop = self._client, self._loop
            if client and loop:
                try:
                    asyncio.run_coroutine_threadsafe(client.disconnect(), loop).result(timeout=10)
                except Exception:
                    pass
            thread = self._thread
            if thread and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=5)
            self._thread = None
            self._client = None
            self._loop = None
            self.running = False
            self.connected = False

    def restart(self):
        with self._lock:
            self.stop()
            self.start()

    def _main(self, gen: int):
        from telethon import TelegramClient, events
        from telethon.sessions import StringSession

        cfg = load_config()
        dropped = prune_inbox()
        if dropped:
            db.log_event("telegram_user", f"при старте убрала старых писем: {dropped}")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = TelegramClient(
            StringSession(cfg["session"]),
            cfg["api_id"],
            cfg["api_hash"],
            device_model="Aya",
            system_version="Windows",
            **_proxy_client_args(cfg["proxy"]),
        )
        with self._lock:
            if gen != self._gen:
                return
            self._loop = loop
            self._client = client

        @client.on(events.NewMessage(incoming=True))
        async def on_msg(event):
            try:
                await self._handle(event)
            except Exception as e:  # noqa: BLE001
                self.error = str(e)[:160]
                db.log_event("telegram_user", f"ошибка обработки: {e}")

        @client.on(events.MessageRead(inbox=True))
        async def on_read(event):
            try:
                n = drop_read(getattr(event, "chat_id", 0), getattr(event, "max_id", 0))
                if n:
                    db.log_event("telegram_user", f"прочитано: убрала {n} писем из инбокса")
            except Exception as e:  # noqa: BLE001
                self.error = str(e)[:160]
                db.log_event("telegram_user", f"ошибка обработки прочтения: {e}")

        try:
            if gen != self._gen:
                return
            self.running = True
            self.error = ""
            loop.run_until_complete(client.connect())
            if gen != self._gen:
                return
            me = loop.run_until_complete(client.get_me())
            self._me_id = getattr(me, "id", 0)
            self.connected = True
            name = getattr(me, "username", None) or getattr(me, "first_name", "")
            db.log_event("telegram_user", f"мой аккаунт подключён: {name}")
            # run_until_disconnected — СИНХРОННЫЙ метод (сам крутит loop);
            # нельзя заворачивать его ещё и в run_until_complete — получим
            # не-awaitable None и «An asyncio.Future... required» при любом дропе связи
            client.run_until_disconnected()
        except Exception as e:  # noqa: BLE001
            if gen == self._gen:
                self.error = str(e)[:200]
                db.log_event("telegram_user", f"ошибка моста: {e}")
        finally:
            if gen != self._gen:
                # нас уже остановили/перезапустили — просто аккуратно закрываем свой клиент
                try:
                    if client.is_connected():
                        loop.run_until_complete(client.disconnect())
                except Exception:
                    pass
            else:
                self.running = False
                self.connected = False
                upd = getattr(client, "_updates_error", None)
                why = f"connected={client.is_connected()}, updates_error={upd!r}" if upd else f"connected={client.is_connected()}"
                self.last_exit = why[:240]
                # поток завершился сам (не stop/restart) — фиксируем причину; сторож поднимет мост
                db.log_event("telegram_user", f"мост завершился: {why[:220]}")


    async def _handle(self, event):
        cfg = load_config()
        if not cfg["enabled"]:
            return
        sender = await event.get_sender()
        if sender is None or getattr(sender, "id", 0) == self._me_id:
            return
        if getattr(sender, "bot", False):
            return
        if cfg["private_only"] and not event.is_private:
            return
        if not _matches(sender, cfg["contacts"]):
            return
        from_name = " ".join(
            p for p in (getattr(sender, "first_name", ""), getattr(sender, "last_name", "")) if p
        ) or (f"@{sender.username}" if getattr(sender, "username", None) else str(getattr(sender, "id", "?")))
        date = getattr(event.message, "date", None)
        try:
            msg_ts = float(date.timestamp()) if date is not None else time.time()
        except (AttributeError, TypeError, ValueError, OSError, OverflowError):
            msg_ts = time.time()
        if msg_ts and time.time() - msg_ts > INBOX_TTL:
            db.log_event("telegram_user", f"пропущено старое входящее от {from_name[:40]}")
            return
        text = await message_text(event.message)
        inbox_push({
            "ts": time.time(),
            "msg_ts": msg_ts,
            "msg_id": int(getattr(event.message, "id", 0) or 0),
            "from": from_name[:60],
            "chat": getattr(event, "chat_id", 0),
            "sender": getattr(sender, "id", 0),
            "text": text[:400],
        })
        self.received += 1
        db.log_event("telegram_user", f"входящее от {from_name[:40]}")
        cur = db.setting("initiative_force", "")
        if not cur.startswith(("reminder:", "report:")):
            db.set_setting("initiative_force", "tg:")

    async def _resolve(self, chat: str):
        """Имя / username / id → entity. Пусто = последний автор из инбокса."""
        chat = str(chat).strip()
        if not chat:
            prune_inbox()
            inbox = _inbox_read()
            if not inbox:
                raise ValueError("не знаю кому — скажи имя получателя")
            sender_id = inbox[-1].get("sender") or inbox[-1].get("chat") or 0
            return await self._client.get_entity(int(sender_id))
        if chat.isdigit():
            return await self._client.get_entity(int(chat))
        if not chat.startswith("@"):
            try:
                return await self._client.get_entity("@" + chat)
            except Exception:
                pass
        try:
            await self._client.get_dialogs(limit=100)
            return await self._client.get_entity(chat)
        except Exception as e:
            raise ValueError(f"не нашла контакт «{chat[:60]}» в моих чатах") from e

    def _wait_ready(self, deadline: float) -> bool:
        """Дождаться подключения; если поток моста мёртв — поднять его."""
        while time.time() < deadline:
            if self.running and self.connected:
                return True
            if not (self._thread and self._thread.is_alive()) and self._cfg_ready():
                self.start()
            time.sleep(0.25)
        return bool(self.running and self.connected)

    def send(self, chat: str, text: str) -> str:
        if not str(text or "").strip():
            return "пустое сообщение отправлять не буду"
        if not (self.running and self.connected):
            if self._cfg_ready():
                # мост мог только что умереть — даём сторожу/старту шанс
                self._wait_ready(time.time() + BRIDGE_START_WAIT)
        client, loop = self._client, self._loop
        if not (client and loop and self.running and self.connected):
            return "мост не запущен — включи и пройди вход в консоли"

        async def do():
            entity = await self._resolve(chat)
            await client.send_message(entity, str(text)[:4000])

        try:
            asyncio.run_coroutine_threadsafe(do(), loop).result(timeout=60)
        except Exception as e:  # noqa: BLE001
            db.log_event("telegram_user", f"ошибка отправки: {e}")
            return f"не получилось отправить: {str(e)[:160]}"
        self.sent += 1
        return f"отправила{(' «' + str(chat) + '»') if str(chat).strip() else ' последнему собеседнику'} от твоего имени"

    def read_history(self, chat: str, limit: int = 5) -> str:
        client, loop = self._client, self._loop
        if not (client and loop and self.running):
            return "мост не запущен — включи и пройди вход в консоли"
        contacts = load_config()["contacts"]

        async def do():
            entity = await self._resolve(chat)
            if contacts:
                name = " ".join(
                    str(getattr(entity, f, "") or "") for f in ("first_name", "last_name", "username")
                )
                if not any(str(c).lower() in name.lower() for c in contacts):
                    return "этот контакт не в разрешённом списке"
            msgs = await client.get_messages(entity, limit=max(1, min(int(limit or 5), 20)))
            # сначала расшифровываем свежие голосовые (ограниченно: whisper небыстрый)
            voice_map: dict[tuple[int, int], str] = {}
            budget = VOICE_TRANSCRIBE_LIMIT
            for m in msgs:  # msgs: от свежих к старым
                if budget <= 0:
                    break
                if getattr(m, "voice", None) is not None:
                    budget -= 1
                    tr = await voice_text(m)
                    if tr:
                        voice_map[_voice_cache_key(m)] = tr
            out = []
            for m in reversed(msgs):
                who = "Ты" if m.out else "Они"
                text = str(m.message or "").strip()
                if not text:
                    text = media_label(m)
                    tr = voice_map.get(_voice_cache_key(m))
                    if tr:
                        text = f"[голосовое] {tr}"
                stamp = time.strftime("%d.%m %H:%M", time.localtime(m.date.timestamp()))
                out.append(f"{stamp} {who}: {text[:200]}")
            return "\n".join(out) if out else "пусто"

        try:
            return asyncio.run_coroutine_threadsafe(do(), loop).result(timeout=90)
        except Exception as e:  # noqa: BLE001
            return f"не смогла прочитать переписку: {str(e)[:160]}"

    def status(self) -> dict:
        cfg = load_config()
        out = {
            "enabled": cfg["enabled"],
            "mode": cfg["mode"],
            "contacts": cfg["contacts"],
            "private_only": cfg["private_only"],
            "proxy": cfg["proxy"],
            "phone_tail": f"...{cfg['phone'][-2:]}" if cfg["phone"] else "",
            "has_session": bool(cfg["session"]),
            "package": True,
            "running": self.running,
            "connected": self.connected,
            "error": self.error,
            "last_exit": self.last_exit,
            "sent": self.sent,
            "received": self.received,
            "login_state": LOGIN.state,
        }
        try:
            import telethon  # noqa: F401
        except ImportError:
            out["package"] = False
        return out


BRIDGE = Bridge()
LOGIN = LoginFlow()
