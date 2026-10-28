import asyncio
import json
import time

from core import db
from core.voice import speech_plan, tts
from core.voice.live_audio import LiveAudio


def test_live_audio_late_reader_gets_everything():
    async def run():
        live = LiveAudio()
        await live.push(b"ab")

        async def reader():
            return b"".join([b async for b in live.iter_bytes()])

        t = asyncio.create_task(reader())
        await asyncio.sleep(0)
        await live.push(b"cd")
        await live.finish("x.mp3")
        late = b"".join([b async for b in live.iter_bytes()])
        return await t, late, await live.ready()

    first, late, ready = asyncio.run(run())
    assert first == late == b"abcd" and ready is True


def test_live_audio_fallback_path_without_chunks():
    async def run():
        live = LiveAudio()
        await live.finish("fallback.mp3")
        return await live.ready(), live.path

    assert asyncio.run(run()) == (False, "fallback.mp3")


def test_plan_parallel_starts_pieces_together_and_keeps_order():
    starts = []

    async def run():
        async def synth(text, mood, fast):
            starts.append((text, time.perf_counter()))
            await asyncio.sleep(0.2)
            return text + ".wav"

        plan = speech_plan.SpeechPlan("calm", synth, parallel=3)
        pieces = plan.feed("Первая длинная фраза для проверки. Вторая фраза тоже длинная и подробная, чтобы не склеилась. ")
        pieces += plan.finish()
        t0 = time.perf_counter()
        got = [await speech_plan.wait_piece(p.id, 5) for p in pieces]
        return pieces, got, time.perf_counter() - t0

    pieces, got, took = asyncio.run(run())
    assert len(pieces) >= 2 and got == [p.text + ".wav" for p in pieces]
    assert [s[0] for s in starts] == [p.text for p in pieces]  # стартуют по порядку
    assert took < 0.2 * len(pieces) - 0.05  # и одновременно, а не по одной


def test_plan_single_worker_is_sequential():
    active = {"now": 0, "max": 0}

    async def run():
        async def synth(text, mood, fast):
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
            await asyncio.sleep(0.05)
            active["now"] -= 1
            return "a.wav"

        plan = speech_plan.SpeechPlan("calm", synth)
        pieces = plan.feed("Раз, это первая фраза для теста. Два, это вторая фраза, она длиннее первой. ") + plan.finish()
        for p in pieces:
            await speech_plan.wait_piece(p.id, 5)

    asyncio.run(run())
    assert active["max"] == 1  # видеокарте — по одной фразе


def _cloud_chat(client, monkeypatch, fake_fish, tmp_path):
    import core.main as main
    from core.llm.base import BaseAdapter

    class Talky(BaseAdapter):
        name = "talky"

        async def stream_chat(self, *a, **k):
            yield "Привет! Сейчас расскажу кое-что интересное про облака. "
            yield "А потом ещё немного про дождь."

    monkeypatch.setattr(main, "make_adapters", lambda cfg: [Talky()])
    monkeypatch.setattr(tts, "_cache_dir", tmp_path)
    monkeypatch.setattr(tts, "_index_get", lambda key: None)
    monkeypatch.setattr(tts, "_index_put", lambda key, path: None)
    monkeypatch.setattr(tts, "fish_warm_connection", lambda: asyncio.sleep(0))
    monkeypatch.setattr(tts, "_fish_speak", fake_fish)
    db.set_setting("tts_enabled", "1")
    db.set_setting("tts_voice_id", "fish_miyuki")
    db.set_setting("fish_api_key", "test-key")
    tts._FISH_FAIL["until"] = 0.0
    with client.stream("POST", "/api/chat/stream", json={"text": "хей", "speak": True}) as r:
        lines = [json.loads(x) for x in r.iter_lines() if x.strip()]
    return [x["speech"] for x in lines if "speech" in x]


def test_chat_cloud_voice_streams_pieces(client, monkeypatch, db_reset, tmp_path):
    async def fake_fish(text, vid, on_chunk=None):
        for part in (b"ID3", b"-", text.encode()[:6]):
            await asyncio.sleep(0.01)
            await on_chunk(part)
        p = tmp_path / "done.mp3"
        p.write_bytes(b"ID3-full")
        return str(p), 0

    speech = _cloud_chat(client, monkeypatch, fake_fish, tmp_path)
    assert speech and all(s["stream"] for s in speech)
    r = client.get(f"/api/speech/{speech[0]['id']}")
    assert r.status_code == 200 and r.content.startswith(b"ID3-") and r.headers["content-type"].startswith("audio/pcm")


def test_chat_cloud_voice_falls_back_when_fish_fails(client, monkeypatch, db_reset, tmp_path):
    async def dead_fish(text, vid, on_chunk=None):
        return None

    edge = tmp_path / "edge.mp3"
    edge.write_bytes(b"EDGE")
    monkeypatch.setattr(tts, "_edge_speak", lambda text: (str(edge), 0))
    speech = _cloud_chat(client, monkeypatch, dead_fish, tmp_path)
    r = client.get(f"/api/speech/{speech[0]['id']}")
    assert r.status_code == 200 and r.content == b"EDGE"


def test_fish_latency_setting_and_key_hidden(client, db_reset):
    db.set_setting("fish_api_key", "secret-key")
    assert client.post("/api/config", json={"fish_latency": "low"}).json()["ok"]
    cfg = client.get("/api/config").json()
    assert cfg["fish_latency"] == "low" and cfg["fish_key_set"] is True
    assert "secret-key" not in json.dumps(cfg)
    client.post("/api/config", json={"fish_latency": "warp"})  # мусор не сохраняем
    assert client.get("/api/config").json()["fish_latency"] == "low"
    db.set_setting("fish_latency", "")
    assert tts.fish_latency() == "balanced"


def test_fish_body_has_latency(db_reset):
    db.set_setting("fish_latency", "normal")
    body = tts._fish_body("привет", "fish_miyuki")
    assert body["latency"] == "normal" and body["reference_id"] == tts.FISH_VOICES["fish_miyuki"]


def test_fish_cooldown_after_auth_error(db_reset):
    db.set_setting("fish_api_key", "k")
    tts._FISH_FAIL["until"] = 0.0
    assert tts.fish_ready()
    tts._fish_failed(402)
    assert not tts.fish_ready()
    tts._FISH_FAIL["until"] = 0.0
