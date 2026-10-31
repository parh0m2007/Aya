"""Характер из конструктора слышен в облачном голосе Fish и в ответах после действий.

Жалоба тестера: противоположные характеры с голосом Fish звучали почти одинаково.
Причины: Fish получал голый текст (подача всегда одна), ответы после действий
писались с жёстким «кратко и тепло», реплики на касания были общими для всех.
"""
import asyncio
import json

import pytest

from core import db, persona
from core.voice import tts

COLD = {"warmth": 5, "humor": 5, "directness": 95, "curiosity": 10, "energy": 5}
HYPER = {"warmth": 95, "humor": 95, "directness": 50, "curiosity": 90, "energy": 95}
MID = {"warmth": 50, "humor": 50, "directness": 50, "curiosity": 50, "energy": 50}


def _save(traits: dict) -> None:
    persona.save({"traits": traits})


def test_voice_style_follows_character(db_reset):
    _save(COLD)
    cold = persona.voice_style()
    _save(HYPER)
    hyper = persona.voice_style()
    assert "cold" in cold["tag"] and "sleepy" in cold["tag"] and cold["tag"].startswith("[")
    assert "excited" in hyper["tag"] and "warm" in hyper["tag"]
    assert cold["speed"] < 1 < hyper["speed"]
    assert cold["temperature"] < hyper["temperature"]
    _save(MID)
    mid = persona.voice_style()
    assert mid["tag"] == "" and mid["speed"] == 1.0


def test_voice_style_off_until_character_is_built(db_reset):
    """Персону ещё не собирали — голос звучит как раньше (и старый кэш годится)."""
    persona.reset()
    assert persona.voice_style() == {}


def test_fish_body_carries_style():
    style = {"tag": "[cold detached tone]", "speed": 0.86, "temperature": 0.6}
    body = tts._fish_body("Привет.", "fish_miyuki", style)
    assert body["text"] == "[cold detached tone] Привет."
    assert body["prosody"] == {"speed": 0.86} and body["temperature"] == 0.6
    plain = tts._fish_body("Привет.", "fish_miyuki")
    assert plain["text"] == "Привет." and "prosody" not in plain and "temperature" not in plain


def test_fish_cache_separates_characters():
    """Та же фраза другим характером — другой файл; без характера — прежний путь кэша."""
    cold = {"tag": "[cold]", "speed": 0.86, "temperature": 0.6}
    hot = {"tag": "[excited]", "speed": 1.14, "temperature": 0.8}
    a = tts._fish_cache_path("Привет", "fish_miyuki", cold)
    b = tts._fish_cache_path("Привет", "fish_miyuki", hot)
    assert a != b
    import hashlib

    old = str(tts._cache_dir / (hashlib.sha1("fish|fish_miyuki|t2|Привет".encode()).hexdigest() + ".wav"))
    assert tts._fish_cache_path("Привет", "fish_miyuki", {}) == old
    assert tts._voice_key("fish_miyuki", {}) == "fish_miyuki"
    assert tts._voice_key("fish_miyuki", cold) != tts._voice_key("fish_miyuki", hot)


def test_fish_request_uses_current_character(monkeypatch, db_reset, tmp_path):
    """Сквозь настоящий _fish_speak: запрос к Fish несёт тег и скорость характера."""
    db.set_setting("fish_api_key", "test-key")  # public-ok: выдуманное значение
    monkeypatch.setattr(tts, "_cache_dir", tmp_path)
    monkeypatch.setattr(tts, "_FISH_FAIL", {"until": 0.0})
    sent = []

    class Resp:
        status_code = 200

        async def aiter_bytes(self):
            yield b"\x00\x01" * 50

    class Stream:
        def __init__(self, body):
            sent.append(body)

        async def __aenter__(self):
            return Resp()

        async def __aexit__(self, *a):
            return False

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, json=None, headers=None):
            return Stream(json)

    monkeypatch.setattr(tts, "_FishClient", Client)
    _save(COLD)
    asyncio.run(tts._fish_speak("Ну и что?", "fish_miyuki"))
    _save(HYPER)
    asyncio.run(tts._fish_speak("Ну и что?", "fish_miyuki"))  # другой характер — не из кэша
    assert len(sent) == 2
    assert sent[0]["text"].startswith("[") and "cold" in sent[0]["text"]
    assert sent[1]["prosody"]["speed"] > 1 > sent[0]["prosody"]["speed"]


def test_tool_reply_keeps_character(db_reset):
    """После действия (музыка, громкость, поиск) ответ писался «кратко и тепло» любым характером."""
    from core import main

    _save(COLD)
    sys_cold = main._comp_system()
    assert "тепло" not in sys_cold
    assert persona.TRAIT_TEXT["warmth"][0] in sys_cold
    persona.save({"address": "вы"})
    assert "«вы»" in main._comp_system()
    persona.reset()
    assert "тёплая" in main._comp_system()  # несобранная Ая — тёплая, как раньше


@pytest.mark.parametrize("topic", ["reminder:попить воды", "report:готово", "morning", "break", "digest", "idle"])
def test_initiative_prompts_do_not_force_warmth(topic, db_reset):
    from core import main

    text = main._init_prompt(topic).lower()
    assert "тепло" not in text and "с теплом" not in text and "тёплый" not in text


def test_reactions_follow_character(client, db_reset):
    _save(COLD)
    cold = {client.post("/api/react", json={"kind": "pat"}).json()["text"] for _ in range(30)}
    assert cold <= set(persona.REACTIONS["cold"]["pat"])
    _save(HYPER)
    hyper = {client.post("/api/react", json={"kind": "poke"}).json()["text"] for _ in range(30)}
    assert hyper <= set(persona.REACTIONS["lively"]["poke"])
    persona.reset()
    default = {client.post("/api/react", json={"kind": "pat"}).json()["text"] for _ in range(30)}
    assert default <= set(persona.REACTIONS["warm"]["pat"])


def test_wizard_sample_uses_draft_character(client, db_reset, monkeypatch, tmp_path):
    """«Послушать» в конструкторе — характером, который сейчас на ползунках, а не сохранённым."""
    _save(MID)
    got = {}
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF")

    async def fake_speak(text, mood="calm", vid_override="", fast=False, style=None):
        got["style"] = style
        return str(wav), 0

    monkeypatch.setattr(tts, "speak", fake_speak)
    r = client.get("/api/speak", params={"text": "Привет", "voice": "fish_miyuki", "traits": json.dumps(COLD)})
    assert r.status_code == 200
    assert "cold" in got["style"]["tag"]
    client.get("/api/speak", params={"text": "Привет", "voice": "fish_miyuki"})
    assert got["style"] is None  # без черновика — сохранённый характер
