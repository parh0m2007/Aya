"""Долгосрочная память (longmem), проактивность 2.0 и Telegram-юзербот."""
import asyncio
import datetime
import json
import threading
import time

import pytest

from core import db
from core import bridge_tguser
from core.behavior import proactive
from core import longmem


# ── longmem ──────────────────────────────────────────────────────────
@pytest.fixture
def fake_embed(monkeypatch):
    """Детерминированный фейковый эмбеддер: тема = первая буква текста."""
    def embed(text: str):
        topic = text.strip()[:1].lower()
        return [1.0 if topic == a else 0.0 for a in "абвгд"]  # 5-мерное пространство

    monkeypatch.setattr(longmem, "_ollama_embed", embed)
    return embed


def test_schema_has_memories(conftest_ws):
    rows = db.query("SELECT name FROM sqlite_master WHERE type='table' AND name='memories'")
    assert rows


def test_ingest_and_recall(fake_embed, conftest_ws):
    db.execute("DELETE FROM memories")
    for text, role in (("абстракт", "assistant"), ("бегун", "user")):
        mid = db.add_message(role, text)
        longmem.ingest(role, text, mid)
    out = longmem.recall("абв про что-то")
    assert "Ая: абстракт" in out
    assert "бегун" not in out


def test_recall_empty_and_noquery(conftest_ws):
    db.execute("DELETE FROM memories")
    assert "пусто" in longmem.recall("что-то")
    assert "уточни" in longmem.recall("")


def test_recall_hybrid_scores(fake_embed, conftest_ws):
    """Гибрид: лексика усиливает совпадение по смыслу."""
    db.execute("DELETE FROM memories")
    for text in ("абк", "абк", "гдв"):
        mid = db.add_message("user", text)
        longmem.ingest("user", text, mid)
    out = longmem.recall("абк")
    assert out.count("абк") == 2


def test_recall_lexical_fallback(monkeypatch, conftest_ws):
    """Нет эмбеддера → работает лексика."""
    monkeypatch.setattr(longmem, "_ollama_embed", lambda text: None)
    db.execute("DELETE FROM memories")
    mid = db.add_message("user", "мы обсуждали отпуск в Сочи")
    longmem.ingest("user", "мы обсуждали отпуск в Сочи", mid)
    out = longmem.recall("отпуск Сочи")
    assert "Сочи" in out


def test_ingest_skips_service_roles(conftest_ws):
    before = db.query("SELECT COUNT(*) FROM memories")[0][0]
    mid = db.add_message("event", "initiative:x")
    longmem.ingest("event", "initiative:x", mid)
    mid = db.add_message("user", "")
    longmem.ingest("user", "", mid)
    after = db.query("SELECT COUNT(*) FROM memories")[0][0]
    assert after == before


def test_recall_tool_free(conftest_ws):
    from core.safety import tools, gateway

    assert {"recall", "tg_inbox", "tg_read", "focus_mode"} <= gateway.FREE_TOOLS
    res = tools.run_action("recall", {"query": "что-то"})
    assert isinstance(res, str)


# ── proactive ────────────────────────────────────────────────────────
@pytest.fixture
def clean_proactive(conftest_ws):
    db.set_setting("proactive_enabled", "1")
    db.set_setting("focus_mode", "0")
    db.set_setting("digest_enabled", "1")
    for r in ("morning", "break", "idle", "digest"):
        db.set_setting(f"pro_{r}_last", "0")
    with proactive._lock:
        proactive._state.update({"app": "PyCharm", "title": "main.py - PyCharm", "since": 0.0, "idle": 0.0})
        proactive.app_summary.clear()
        proactive._day["key"] = ()
    yield


def _mktime(h, m=0):
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))


def test_rules_morning_once(clean_proactive):
    now = _mktime(9)
    assert proactive.rules(now) == "morning"
    db.set_setting("pro_morning_last", str(now))
    assert proactive.rules(now + 100) == ""  # второй раз в тот же день — нет


def test_rules_digest_evening(clean_proactive):
    now = _mktime(21, 30)
    with proactive._lock:
        proactive.app_summary["PyCharm"] = 7200
    topic = proactive.rules(now)
    assert topic == "digest"
    txt = proactive.summary_text()
    assert "PyCharm" in txt
    # второй раз — тишина
    assert proactive.rules(now + 500) in ("", "morning")  # в 21:38 morning window закрыт (>=11)? нет
    # morning после 21 не стреляет
    assert proactive.rules(time.mktime((time.localtime(now).tm_year, time.localtime(now).tm_mon, time.localtime(now).tm_mday, 21, 31, 0, 0, 0, -1))) == ""


def test_rules_break_after_long_app(clean_proactive):
    now = _mktime(15)
    with proactive._lock:
        proactive._state.update({"app": "PyCharm", "since": now - 3 * 3600, "idle": 30.0})
    assert proactive.rules(now) == "break"
    assert proactive.rules(now + 60) == ""  # кулдаун 2.5ч


def test_rules_idle(clean_proactive):
    now = _mktime(16)
    with proactive._lock:
        proactive._state.update({"app": "Chrome", "since": now - 300, "idle": 110 * 60})
    assert proactive.rules(now) == "idle"


def test_rules_quiet_hours_and_focus(clean_proactive):
    now = _mktime(23, 30)
    assert proactive.rules(now) == ""
    now_day = _mktime(12)
    db.set_setting("focus_mode", "1")
    assert proactive.rules(now_day) == ""
    db.set_setting("focus_mode", "0")
    db.set_setting("proactive_enabled", "0")
    assert proactive.rules(now_day) == ""


def test_rules_ignores_break_when_afk(clean_proactive):
    """Давно в приложении, но ввода нет — это idle-кейс, не break."""
    now = _mktime(17)
    with proactive._lock:
        proactive._state.update({"app": "PyCharm", "since": now - 4 * 3600, "idle": 400.0})
    assert proactive.rules(now) == ""  # break требует живого ввода; idle пока не накопился


def test_focus_mode_tool(conftest_ws):
    from core.safety import tools

    res = tools.run_action("focus_mode", {"enable": True})
    assert "тихий режим включён" in res
    assert db.setting("focus_mode", "0") == "1"
    res = tools.run_action("focus_mode", {"enable": "off"})
    assert "выключен" in res
    assert db.setting("focus_mode", "0") == "0"


def test_day_stats_reset(clean_proactive, monkeypatch):
    monkeypatch.setattr(proactive, "_fg_title", lambda: "")
    monkeypatch.setattr(proactive, "_idle_seconds", lambda: 0.0)
    with proactive._lock:
        proactive._day["key"] = (2099, 1)
        proactive._state.update({"app": "", "title": "", "since": 0.0, "idle": 0.0})
        proactive.app_summary["Старое"] = 999
    proactive._sample(time.time())
    proactive._sample(time.time() + 1)
    # ключ дня сменился → старые накопления стёрлись, копим заново
    assert "Старое" not in proactive.app_summary
    assert "Рабочий стол" in proactive.app_summary


# ── bridge_tguser ────────────────────────────────────────────────────
@pytest.fixture
def tg_cfg(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_tguser, "config_path", lambda: str(tmp_path / "config.json"))
    db.set_setting("tg_inbox", "[]")
    from core.safety import tools

    tools.allow_tool("tg_send", True)
    return tmp_path


def test_tguser_config_roundtrip(tg_cfg):
    bridge_tguser.save_config(api_id=123, api_hash="h", phone="+7900", mode="draft", contacts=["маша"], enabled=True)
    cfg = bridge_tguser.load_config()
    assert cfg["api_id"] == 123 and cfg["mode"] == "draft" and cfg["contacts"] == ["маша"]
    assert cfg["enabled"] is True and cfg["private_only"] is True
    # соседи не побились
    with open(bridge_tguser.config_path(), encoding="utf-8") as f:
        full = json.load(f)
    assert set(full.keys()) >= {"telegram_user"}


def test_tguser_proxy_roundtrip(tg_cfg):
    """Прокси сохраняется в конфиг и отдаётся в load_config/status; снятие тоже."""
    bridge_tguser.save_config(
        proxy={"type": "mtproto", "host": "p.example", "port": "443", "secret": "dd00112233445566778899aabbccddeeff"}  # public-ok: выдуманное значение
    )
    cfg = bridge_tguser.load_config()
    assert cfg["proxy"] == {
        "type": "mtproto",
        "host": "p.example",
        "port": 443,
        "secret": "dd00112233445566778899aabbccddeeff",  # public-ok: выдуманное значение
    }
    assert bridge_tguser.BRIDGE.status()["proxy"]["type"] == "mtproto"
    bridge_tguser.save_config(proxy={})
    assert bridge_tguser.load_config()["proxy"] == {}


def test_proxy_client_args():
    """Кwargs для TelegramClient: socks5 → proxy, mtproto → proxy + MTProxy-коннекшн."""
    a = bridge_tguser._proxy_client_args({"type": "socks5", "host": "127.0.0.1", "port": 7890, "secret": ""})
    assert a == {"proxy": ("socks5", "127.0.0.1", 7890)}
    m = bridge_tguser._proxy_client_args({"type": "mtproto", "host": "p", "port": 443, "secret": "ee0011"})
    from telethon.network.connection import ConnectionTcpMTProxyRandomizedIntermediate
    assert m["connection"] is ConnectionTcpMTProxyRandomizedIntermediate
    assert m["proxy"] == ("p", 443, "ee0011")
    assert bridge_tguser._proxy_client_args({}) == {}


def test_tguser_inbox_flow(tg_cfg):
    bridge_tguser.inbox_push({"ts": time.time() - 5, "from": "Маша", "chat": 111, "sender": 111, "text": "привет!"})
    bridge_tguser.inbox_push({"ts": time.time(), "from": "Петя", "chat": 222, "sender": 222, "text": "salut"})
    text = bridge_tguser.inbox_text()
    assert "Маша" in text and "salut" in text
    taken = bridge_tguser.take_inbox()
    assert len(taken) == 2
    assert bridge_tguser.inbox_text().startswith("новых")


def test_tg_inbox_peek_and_drop(tg_cfg):
    """peek только читает (хронологически, с начала); drop удаляет ровно свои письма."""
    bridge_tguser.inbox_push({"ts": time.time() - 3, "from": "Маша", "chat": 1, "sender": 1, "text": "первое"})
    bridge_tguser.inbox_push({"ts": time.time() - 2, "from": "Петя", "chat": 2, "sender": 2, "text": "второе"})
    bridge_tguser.inbox_push({"ts": time.time() - 1, "from": "Костя", "chat": 3, "sender": 3, "text": "третье"})

    snap = bridge_tguser.peek_inbox(2)
    assert [it["text"] for it in snap] == ["первое", "второе"]
    assert all(it.get("id") for it in snap)
    assert len(bridge_tguser.peek_inbox(10)) == 3  # peek ничего не съел

    assert bridge_tguser.drop_inbox(snap) == 2
    assert [it["text"] for it in bridge_tguser.peek_inbox(10)] == ["третье"]


def test_tg_take_inbox_chronological(tg_cfg):
    """take_inbox отдаёт первые (самые старые) до limit, остальные ждут следующего раза."""
    for i in range(10):
        bridge_tguser.inbox_push(
            {"ts": time.time() + i, "from": f"U{i}", "chat": i, "sender": i, "text": f"m{i}"}
        )
    taken = bridge_tguser.take_inbox(8)
    assert len(taken) == 8 and "U0" in taken[0] and "U7" in taken[-1]
    assert [it["text"] for it in bridge_tguser.peek_inbox(10)] == ["m8", "m9"]


def test_tguser_matches():
    class S:
        first_name = "Мария"
        last_name = "Иванова"
        username = "masha_i"
        id = 555

    assert bridge_tguser._matches(S(), ["иванова"])  # фамилия
    assert bridge_tguser._matches(S(), ["@masha_i"])  # username (без и с @)
    assert bridge_tguser._matches(S(), ["555"])  # id
    assert not bridge_tguser._matches(S(), ["петров"])
    assert bridge_tguser._matches(S(), [])  # пусто = все


def test_tguser_status_idle(tg_cfg):
    s = bridge_tguser.BRIDGE.status()
    assert s["running"] is False
    assert s["mode"] == "report"
    assert s["package"] is True
    assert s["has_session"] is False


def test_bridge_ensure_alive(monkeypatch):
    """Сторож поднимает мост, если он умер; не трогает живой и выключенный."""
    cfg_on = {"enabled": True, "session": "s", "api_id": 1, "api_hash": "h"}
    monkeypatch.setattr(bridge_tguser, "load_config", lambda: dict(cfg_on))
    b = bridge_tguser.Bridge()
    calls = []
    monkeypatch.setattr(b, "start", lambda: calls.append("start"))

    assert b._ensure_alive() is True and calls == ["start"]  # мёртвый — поднимаем
    b.running = True
    assert b._ensure_alive() is False and calls == ["start"]  # живой — не трогаем
    b.running = False
    cfg_on["enabled"] = False
    assert b._ensure_alive() is False and calls == ["start"]  # выключен — не поднимаем


def test_bridge_send_bootstraps(monkeypatch):
    """send сам пытается поднять мост, если тот только что упал."""
    monkeypatch.setattr(bridge_tguser, "BRIDGE_START_WAIT", 0.05)
    monkeypatch.setattr(
        bridge_tguser,
        "load_config",
        lambda: {"enabled": True, "session": "s", "api_id": 1, "api_hash": "h"},
    )
    b = bridge_tguser.Bridge()
    calls = []
    monkeypatch.setattr(b, "start", lambda: calls.append("start"))

    out = b.send("Маша", "привет")
    assert calls == ["start"]
    assert "мост не запущен" in out


def test_tg_send_guard_no_session(tg_cfg):
    from core.safety import tools

    res = tools.run_action("tg_send", {"chat": "маша", "text": "пока"})
    assert "нет сессии" in res


def test_tg_send_draft_confirm(tg_cfg, monkeypatch):
    """draft: dispatch возвращает pending с confirm_id; auto — выполняет сразу."""
    from core.safety import gateway, tools
    import asyncio

    bridge_tguser.save_config(session="sess", enabled=True, mode="draft")
    monkeypatch.setattr(bridge_tguser.BRIDGE, "running", True)
    monkeypatch.setattr(bridge_tguser.BRIDGE, "_client", object())
    monkeypatch.setattr(bridge_tguser.BRIDGE, "_loop", object())
    monkeypatch.setattr(bridge_tguser.BRIDGE, "send", lambda chat, text: "отправила от твоего имени")

    out = asyncio.run(gateway.dispatch({"name": "tg_send", "args": {"chat": "маша", "text": "privet"}}))
    assert isinstance(out, dict) and out["confirm_id"] in gateway.PENDING

    # авто-режим — без кнопки
    bridge_tguser.save_config(mode="auto")
    out2 = asyncio.run(gateway.dispatch({"name": "tg_send", "args": {"chat": "маша", "text": "privet"}}))
    assert out2 == "отправила от твоего имени"


def test_tg_send_confirm_resolution(tg_cfg, monkeypatch):
    """Подтвердили кнопкой → уходит из PENDING и отправляется."""
    from core.safety import gateway
    import asyncio

    bridge_tguser.save_config(session="sess", enabled=True, mode="draft")
    monkeypatch.setattr(bridge_tguser.BRIDGE, "running", True)
    monkeypatch.setattr(bridge_tguser.BRIDGE, "send", lambda chat, text: "отправила")

    out = asyncio.run(gateway.dispatch({"name": "tg_send", "args": {"text": "привет!"}}))
    pid = out["confirm_id"]
    res = gateway.resolve_confirm(pid, True)
    assert res["applied"] is True and "отправила" in res["result"]


def test_tg_inbox_prompt_peeks(tg_cfg):
    """main._init_prompt('tg:') только читает инбокс; удаление — после успешной выдачи."""
    import core.main as main

    bridge_tguser.inbox_push({"ts": time.time(), "from": "Маша", "chat": 1, "sender": 1, "text": "ты где?"})
    prompt = main._init_prompt("tg:")
    assert "Маша" in prompt and "ты где?" in prompt
    assert len(bridge_tguser.peek_inbox(10)) == 1  # письмо цело
    # подтверждение доставки удаляет ровно это письмо
    assert bridge_tguser.drop_inbox(bridge_tguser.peek_inbox(8)) == 1
    assert bridge_tguser.peek_inbox(10) == []


def test_tg_init_prompt_modes_have_no_tools(tg_cfg):
    """Инициатива не передаёт модели инструменты: в промпте их быть не должно,
    иначе gemma отдаёт пустой ответ и доставка писем замирает."""
    import core.main as main

    bridge_tguser.inbox_push({"ts": time.time(), "from": "Маша", "chat": 1, "sender": 1, "text": "ты где?"})
    for m in ("report", "draft", "auto"):
        bridge_tguser.save_config(mode=m)
        prompt = main._init_prompt("tg:")
        assert "Маша" in prompt and "ты где?" in prompt
        assert "tg_send" not in prompt and "tg_read" not in prompt, m
    bridge_tguser.drop_inbox(bridge_tguser.peek_inbox(8))


def _tg_endpoint_mocks(main, monkeypatch, stream):
    monkeypatch.setattr(main.state, "initiative_check", lambda: "tg:")
    monkeypatch.setattr(main, "make_adapters", lambda cfg: [])
    monkeypatch.setattr(main, "build_system_prompt", lambda snap: "system")
    monkeypatch.setattr(main, "build_history", lambda verbatim=4: [])
    monkeypatch.setattr(main, "stream_llm", stream)


def test_tg_initiative_failure_keeps_inbox(tg_cfg, monkeypatch):
    """Сбой генерации не сжигает письма: пустой reply, отложенный повтор, инбокс цел."""
    import asyncio

    import core.main as main

    db.set_setting("initiative_force", "")
    bridge_tguser.inbox_push({"ts": time.time(), "from": "Маша", "chat": 1, "sender": 1, "text": "ты где?"})

    async def boom(*args, **kwargs):
        raise RuntimeError("llm down")
        yield ""  # pragma: no cover

    _tg_endpoint_mocks(main, monkeypatch, boom)

    before = db.query("SELECT COUNT(*) FROM messages WHERE role='assistant'")[0][0]
    out = asyncio.run(main.initiative())

    assert out["reply"] == ""  # молчим вместо ложного «тут тихо»
    assert db.query("SELECT COUNT(*) FROM messages WHERE role='assistant'")[0][0] == before
    assert [it["text"] for it in bridge_tguser.peek_inbox(10)] == ["ты где?"]
    force = db.setting("initiative_force", "")
    assert force.startswith("tg:retry:")
    assert int(force.rsplit(":", 1)[1]) > time.time()
    db.set_setting("initiative_force", "")


def test_tg_initiative_success_drops_inbox(tg_cfg, monkeypatch):
    """Успешная выдача подтверждает доставку: письма уходят из инбокса, ретрай не нужен."""
    import asyncio

    import core.main as main

    db.set_setting("initiative_force", "")
    bridge_tguser.inbox_push({"ts": time.time(), "from": "Костя", "chat": 3, "sender": 3, "text": "привет"})

    async def ok(*args, **kwargs):
        yield "Костя пишет: привет"

    _tg_endpoint_mocks(main, monkeypatch, ok)
    out = asyncio.run(main.initiative())

    assert out["reply"] == "Костя пишет: привет"
    assert bridge_tguser.peek_inbox(10) == []
    assert db.setting("initiative_force", "") == ""


def test_tg_backlog_armed_after_other_initiative(tg_cfg, monkeypatch):
    """Письма ждали приоритетный маркер — после него ставится отложенный tg:."""
    import asyncio

    import core.main as main

    db.set_setting("initiative_force", "")
    bridge_tguser.inbox_push({"ts": time.time(), "from": "Маша", "chat": 1, "sender": 1, "text": "ты где?"})

    async def ok(*args, **kwargs):
        yield "Пора пить чай"

    _tg_endpoint_mocks(main, monkeypatch, ok)
    monkeypatch.setattr(main.state, "initiative_check", lambda: "reminder:чай")
    out = asyncio.run(main.initiative())

    assert out["reply"] == "Пора пить чай"
    assert len(bridge_tguser.peek_inbox(10)) == 1  # этим проходом письма не тронуты
    assert db.setting("initiative_force", "").startswith("tg:retry:")
    db.set_setting("initiative_force", "")


def test_tg_retry_marker_waits(tg_cfg):
    """tg:retry:<ts> не отдаётся раньше срока и не теряется; после срока — обычный tg:."""
    import core.main as main

    db.set_setting("initiative_force", f"tg:retry:{int(time.time()) + 3600}")
    assert main.state.initiative_check() == ""
    assert db.setting("initiative_force", "").startswith("tg:retry:")

    db.set_setting("initiative_force", f"tg:retry:{int(time.time()) - 1}")
    assert main.state.initiative_check() == "tg:"
    assert db.setting("initiative_force", "") == ""


def test_tg_inbox_prunes_by_message_time(tg_cfg):
    """Старое письмо (по времени Telegram) не должно висеть в инбоксе."""
    now = time.time()
    db.set_setting(
        bridge_tguser.INBOX_KEY,
        json.dumps(
            [
                {
                    "id": "old",
                    "ts": now,
                    "msg_ts": now - bridge_tguser.INBOX_TTL - 1,
                    "from": "Маша",
                    "chat": 1,
                    "sender": 1,
                    "msg_id": 1,
                    "text": "старое",
                },
                {
                    "id": "fresh",
                    "ts": now,
                    "msg_ts": now,
                    "from": "Маша",
                    "chat": 1,
                    "sender": 1,
                    "msg_id": 2,
                    "text": "новое",
                },
            ],
            ensure_ascii=False,
        ),
    )
    assert bridge_tguser.prune_inbox(now=now) == 1
    assert [it["text"] for it in bridge_tguser.peek_inbox(10)] == ["новое"]


def test_tg_inbox_prunes_legacy_by_receive_time(tg_cfg):
    """Письма старого формата без msg_ts стареют по времени получения."""
    now = time.time()
    db.set_setting(
        bridge_tguser.INBOX_KEY,
        json.dumps(
            [
                {
                    "id": "legacy",
                    "ts": now - bridge_tguser.INBOX_TTL - 1,
                    "from": "Петя",
                    "chat": 2,
                    "sender": 2,
                    "text": "давно",
                }
            ],
            ensure_ascii=False,
        ),
    )
    assert bridge_tguser.prune_inbox(now=now) == 1
    assert bridge_tguser.peek_inbox(10) == []


def test_tg_inbox_dedupes_message_by_chat_and_id(tg_cfg):
    """Повторная доставка того же Telegram-сообщения не создаёт второе письмо."""
    now = time.time()
    item = {
        "ts": now,
        "msg_ts": now,
        "from": "Маша",
        "chat": 1,
        "sender": 1,
        "msg_id": 42,
        "text": "привет",
    }
    bridge_tguser.inbox_push(item)
    bridge_tguser.inbox_push({**item, "ts": now + 1})
    assert [it["text"] for it in bridge_tguser.peek_inbox(10)] == ["привет"]

    bridge_tguser.inbox_push({**item, "chat": 2})
    assert len(bridge_tguser.peek_inbox(10)) == 2


def test_tg_drop_read_removes_only_older_in_chat(tg_cfg):
    """Прочитанное на телефоне письмо убирается из инбокса, чужие чаты целы."""
    now = time.time()
    for chat, mid, text in ((1, 10, "первое"), (1, 12, "второе"), (2, 20, "третье")):
        bridge_tguser.inbox_push(
            {
                "ts": now,
                "msg_ts": now,
                "from": f"U{chat}",
                "chat": chat,
                "sender": chat,
                "msg_id": mid,
                "text": text,
            }
        )

    assert bridge_tguser.drop_read(1, 11) == 1
    assert [it["text"] for it in bridge_tguser.peek_inbox(10)] == ["второе", "третье"]


def test_handle_stores_telegram_message_identity(tg_cfg, monkeypatch):
    """Входящее хранит id и время сообщения — по ним работают TTL и дедуп."""
    db.set_setting("initiative_force", "")
    bridge_tguser.save_config(enabled=True, contacts=[])
    monkeypatch.setattr(bridge_tguser.BRIDGE, "_me_id", 1)
    msg = _FakeMediaMsg(mid=77, text="привет")

    asyncio.run(bridge_tguser.BRIDGE._handle(_fake_incoming(msg)))
    item = bridge_tguser.peek_inbox(5)[0]
    assert item["msg_id"] == 77
    assert item["chat"] == 7
    assert item["msg_ts"] == pytest.approx(msg.date.timestamp())
    db.set_setting("initiative_force", "")


def test_handle_skips_stale_message(tg_cfg, monkeypatch):
    """Старое входящее не попадает даже в инбокс и не ставит tg:-маркер."""
    db.set_setting("initiative_force", "")
    bridge_tguser.save_config(enabled=True, contacts=[])
    monkeypatch.setattr(bridge_tguser.BRIDGE, "_me_id", 1)
    msg = _FakeMediaMsg(mid=88, text="старое")
    msg.date = datetime.datetime.now() - datetime.timedelta(seconds=bridge_tguser.INBOX_TTL + 5)

    asyncio.run(bridge_tguser.BRIDGE._handle(_fake_incoming(msg)))
    assert bridge_tguser.peek_inbox(5) == []
    assert db.setting("initiative_force", "") == ""


def test_tg_initiative_prunes_stale_without_llm(tg_cfg, monkeypatch):
    """Старый бэклог не доходит до модели и не озвучивается."""
    import asyncio

    import core.main as main

    now = time.time()
    db.set_setting("initiative_force", "")
    db.set_setting(
        bridge_tguser.INBOX_KEY,
        json.dumps(
            [
                {
                    "id": "old",
                    "ts": now,
                    "msg_ts": now - bridge_tguser.INBOX_TTL - 5,
                    "from": "Маша",
                    "chat": 1,
                    "sender": 1,
                    "msg_id": 1,
                    "text": "старое",
                }
            ],
            ensure_ascii=False,
        ),
    )
    calls = []

    async def stream(*args, **kwargs):
        calls.append(1)
        yield "старое"

    _tg_endpoint_mocks(main, monkeypatch, stream)
    out = asyncio.run(main.initiative())

    assert out["reply"] == ""
    assert calls == []
    assert bridge_tguser.peek_inbox(10) == []


def test_subagent_blocked_from_private_tools(conftest_ws):
    from core import agents

    for t in ("recall", "tg_send", "tg_read", "tg_inbox", "focus_mode"):
        assert t in agents.DELEGATE_BLOCKED_TOOLS


# ── входящие медиа: пометки и расшифровка голосовых ──────────────────
class _VoiceDur:
    def __init__(self, duration=12):
        self.duration = duration


class _FakeMediaMsg:
    """Сообщение Telethon для тестов _handle/read_history (только нужные поля)."""

    def __init__(self, mid=101, text="", out=False, seconds=12, kind=None):
        self.id = mid
        self.chat_id = 0
        self.message = text
        self.out = out
        self.date = datetime.datetime.now()
        self.voice = _VoiceDur(seconds) if kind == "voice" else None
        self.photo = object() if kind == "photo" else None
        self.sticker = object() if kind == "sticker" else None
        self.video_note = _VoiceDur(seconds or 5) if kind == "note" else None

    async def download_media(self, file=None):
        return b"OGGDATA"


def _fake_incoming(message, sender_id=7, sender_name="Саша"):
    class Sender:
        id = sender_id
        first_name = sender_name
        last_name = ""
        username = "maks"
        bot = False

    class Event:
        is_private = True
        chat_id = sender_id

        def __init__(self):
            self.message = message

        async def get_sender(self):
            return Sender()

    return Event()


def test_media_label_kinds():
    """Не-текстовые сообщения получают говорящую пометку, а не «[медиа]»."""
    assert bridge_tguser.media_label(_FakeMediaMsg(kind="photo")) == "[фото]"
    assert bridge_tguser.media_label(_FakeMediaMsg(kind="sticker")) == "[стикер]"
    assert bridge_tguser.media_label(_FakeMediaMsg(kind="note", seconds=5)) == "[кружок, 5с]"
    assert bridge_tguser.media_label(_FakeMediaMsg()) == "[медиа]"


def test_handle_voice_message_transcribes(tg_cfg, monkeypatch):
    """Голосовое без текста: мост скачивает и расшифровывает локально; повтор — из кэша."""
    bridge_tguser.save_config(enabled=True, contacts=[])
    monkeypatch.setattr(bridge_tguser.BRIDGE, "_me_id", 1)
    monkeypatch.setattr(bridge_tguser, "_voice_cache", {})
    calls = []

    def fake_stt(data):
        calls.append(1)
        return "привет, как дела"

    monkeypatch.setattr("core.voice.stt.transcribe", fake_stt)
    msg = _FakeMediaMsg(mid=101, kind="voice")
    assert asyncio.run(bridge_tguser.voice_text(msg)) == "привет, как дела"
    assert asyncio.run(bridge_tguser.voice_text(msg)) == "привет, как дела"
    assert len(calls) == 1  # второй раз расшифровка взята из кэша

    asyncio.run(bridge_tguser.BRIDGE._handle(_fake_incoming(msg)))
    inbox = bridge_tguser.peek_inbox(5)
    assert len(inbox) == 1
    assert inbox[0]["from"] == "Саша"
    assert inbox[0]["text"].startswith("[голосовое]")
    assert "привет, как дела" in inbox[0]["text"]


def test_handle_voice_message_stt_fails(tg_cfg, monkeypatch):
    """Не вышло расшифровать — письмо всё равно доходит, с честной пометкой."""
    bridge_tguser.save_config(enabled=True, contacts=[])
    monkeypatch.setattr(bridge_tguser.BRIDGE, "_me_id", 1)
    monkeypatch.setattr(bridge_tguser, "_voice_cache", {})

    def boom(data):
        raise RuntimeError("stt down")

    monkeypatch.setattr("core.voice.stt.transcribe", boom)
    asyncio.run(bridge_tguser.BRIDGE._handle(_fake_incoming(_FakeMediaMsg(kind="voice"))))
    text = bridge_tguser.peek_inbox(5)[0]["text"]
    assert text.startswith("[голосовое, 12с")
    assert "не смогла расшифровать" in text


def test_handle_photo_message_label(tg_cfg, monkeypatch):
    """Фото доходит как «[фото]», расшифровка для него не вызывается."""
    bridge_tguser.save_config(enabled=True, contacts=[])
    monkeypatch.setattr(bridge_tguser.BRIDGE, "_me_id", 1)

    def boom(data):  # pragma: no cover
        raise AssertionError("для фото расшифровка не нужна")

    monkeypatch.setattr("core.voice.stt.transcribe", boom)
    asyncio.run(bridge_tguser.BRIDGE._handle(_fake_incoming(_FakeMediaMsg(kind="photo"))))
    assert bridge_tguser.peek_inbox(5)[0]["text"] == "[фото]"


def _bg_loop():
    """Отдельный поток с живым event loop — нужен read_history (run_coroutine_threadsafe)."""
    loop = asyncio.new_event_loop()
    started = threading.Event()

    def run():
        asyncio.set_event_loop(loop)
        started.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    started.wait(2)
    return loop


def test_read_history_transcribes_voice(tg_cfg, monkeypatch):
    """Чтение переписки: голосовое расшифровывается и попадает в вывод."""
    bridge_tguser.save_config(enabled=True, contacts=[])
    monkeypatch.setattr(bridge_tguser, "_voice_cache", {})
    monkeypatch.setattr("core.voice.stt.transcribe", lambda data: "ну что, идём в бассейн?")

    class Client:
        async def get_messages(self, entity, limit=5):
            return [_FakeMediaMsg(mid=2, kind="voice", seconds=8), _FakeMediaMsg(mid=1, text="привет")]

    async def fake_resolve(chat):
        return object()

    loop = _bg_loop()
    try:
        monkeypatch.setattr(bridge_tguser.BRIDGE, "running", True)
        monkeypatch.setattr(bridge_tguser.BRIDGE, "_client", Client())
        monkeypatch.setattr(bridge_tguser.BRIDGE, "_loop", loop)
        monkeypatch.setattr(bridge_tguser.BRIDGE, "_resolve", fake_resolve)
        out = bridge_tguser.BRIDGE.read_history("Саша", 5)
    finally:
        loop.call_soon_threadsafe(loop.stop)
    assert "привет" in out
    assert "[голосовое" in out
    assert "ну что, идём в бассейн?" in out


def test_read_history_voice_transcribe_cap(tg_cfg, monkeypatch):
    """За одно чтение расшифровываем не больше лимита голосовых (свежие важнее)."""
    bridge_tguser.save_config(enabled=True, contacts=[])
    monkeypatch.setattr(bridge_tguser, "_voice_cache", {})
    calls = []

    def fake_stt(data):
        calls.append(1)
        return "текст " + str(len(calls))

    monkeypatch.setattr("core.voice.stt.transcribe", fake_stt)

    class Client:
        async def get_messages(self, entity, limit=5):
            return [  # от свежих к старым
                _FakeMediaMsg(mid=33, kind="voice"),
                _FakeMediaMsg(mid=32, kind="voice"),
                _FakeMediaMsg(mid=31, kind="voice"),
            ]

    async def fake_resolve(chat):
        return object()

    loop = _bg_loop()
    try:
        monkeypatch.setattr(bridge_tguser.BRIDGE, "running", True)
        monkeypatch.setattr(bridge_tguser.BRIDGE, "_client", Client())
        monkeypatch.setattr(bridge_tguser.BRIDGE, "_loop", loop)
        monkeypatch.setattr(bridge_tguser.BRIDGE, "_resolve", fake_resolve)
        out = bridge_tguser.BRIDGE.read_history("Саша", 5)
    finally:
        loop.call_soon_threadsafe(loop.stop)
    assert len(calls) == bridge_tguser.VOICE_TRANSCRIBE_LIMIT == 2
    assert out.count("[голосовое") == 3  # все три с пометкой, но один — без текста
    assert out.count("текст") == 2


def test_tg_init_prompt_voice_note(tg_cfg):
    """Промпт инициативы объясняет модели, что «[голосовое]» — расшифровка."""
    import core.main as main

    db.set_setting("tg_inbox", "[]")
    bridge_tguser.inbox_push(
        {"ts": time.time(), "from": "Саша", "chat": 7, "sender": 7, "text": "[голосовое] привет, как дела"}
    )
    prompt = main._init_prompt("tg:")
    assert "расшифровка голосового" in prompt
    bridge_tguser.drop_inbox(bridge_tguser.peek_inbox(8))
