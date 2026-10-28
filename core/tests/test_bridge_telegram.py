import json
import os
from unittest.mock import patch

import pytest

from core import bridge_telegram


@pytest.fixture()
def tg_conftest(conftest_ws, monkeypatch, tmp_path):
    cfgp = tmp_path / "config.json"
    monkeypatch.setattr(bridge_telegram, "config_path", lambda: str(cfgp))
    return cfgp


def test_load_save_config(tg_conftest):
    assert bridge_telegram.load_tg_config()["enabled"] is False
    bridge_telegram.save_tg_config("123:abc", "555", True)
    cfg = bridge_telegram.load_tg_config()
    assert cfg == {"token": "123:abc", "chat_id": "555", "enabled": True}
    with open(tg_conftest, encoding="utf-8") as f:
        data = json.load(f)
    assert "telegram" in data


def test_start_without_config(tg_conftest):
    b = bridge_telegram.Bridge()
    b.start()
    assert b.running is False
    assert b._thread is None


def test_handle_denies_foreign_chat(tg_conftest, conftest_ws):
    bridge_telegram.save_tg_config("123:abc", "111", True)
    b = bridge_telegram.Bridge()
    b.running = True
    sent = []

    def fake_api(method, data=None, timeout=40):
        sent.append((method, data))
        return {"ok": True, "result": {"id": 1, "username": "aya_bot"}}

    with patch.object(b, "_api", side_effect=fake_api):
        b._handle({"chat": {"id": 999}, "text": "привет"})
    assert "sendMessage" in sent[-1][0]
    assert "только" in sent[-1][1]["text"]
    # в наш локальный chat сообщение не ушло
    assert b.received == 0


def test_handle_asks_core_and_replies(tg_conftest, conftest_ws, monkeypatch):
    bridge_telegram.save_tg_config("123:abc", "111", True)
    b = bridge_telegram.Bridge()
    b.running = True

    posted = {}

    def fake_urlopen(req, timeout=None):
        posted["url"] = req.full_url
        posted["body"] = json.loads(req.data.decode("utf-8"))
        resp = json.dumps({"reply": "Привет!"}).encode("utf-8")
        import io

        return io.BytesIO(resp)

    sent = []

    def fake_api(method, data=None, timeout=40):
        sent.append(data)
        return {"ok": True}

    monkeypatch.setattr(bridge_telegram.urllib.request, "urlopen", fake_urlopen)
    with patch.object(b, "_api", side_effect=fake_api):
        b._handle({"chat": {"id": "111"}, "text": "как дела", "from": {"first_name": "Юля"}})
    assert posted["body"] == {"text": "[Telegram, от Юля] как дела"}
    assert "127.0.0.1:8330/api/chat" in posted["url"]
    assert sent and sent[-1]["chat_id"] == "111"
    assert sent[-1]["text"] == "Привет!"
    assert b.received == 1 and b.sent == 1


def test_send_splits_long_text(tg_conftest):
    b = bridge_telegram.Bridge()
    calls = []

    def fake_api(method, data=None, timeout=40):
        calls.append(data)
        return {"ok": True}

    with patch.object(b, "_api", side_effect=fake_api):
        b._send("111", "x" * (bridge_telegram.MAX_LEN + 5))
    assert len(calls) == 2
    assert len(calls[1]["text"]) == 5


def test_status_hides_token(tg_conftest):
    bridge_telegram.save_tg_config("123456:secretcode", "555", True)
    st = bridge_telegram.BRIDGE.status()
    assert st["token_tail"] == "....code" or st["token_tail"] == "...code"
    assert "secretcode" not in st["token_tail"]
    assert st["has_token"] is True
    assert st["chat_id"] == "555"
