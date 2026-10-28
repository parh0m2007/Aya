import pytest

import core.db as db
import core.voice.tts as tts


def test_frequent_phrases_empty_db(db_reset):
    assert tts.frequent_phrases(5) == []


def test_frequent_phrases_takes_frequent_short_assistant_replies(db_reset):
    for _ in range(3):
        db.add_message("assistant", "Готово!")
    db.add_message("assistant", "Привет!")
    db.add_message("assistant", "x" * 200)
    db.add_message("user", "Готово!")

    assert tts.frequent_phrases(1) == ["Готово!"]
    assert set(tts.frequent_phrases(5)) == {"Готово!", "Привет!"}


def test_warm_phrases_skips_without_fish_key(db_reset, monkeypatch):
    db.set_setting("tts_voice_id", "fish_miyuki")
    monkeypatch.setattr(
        tts, "frequent_phrases", lambda limit=tts.WARM_PHRASES: pytest.fail("без ключа не греем")
    )
    tts.warm_phrases()


def test_warm_phrases_caches_top_phrases(db_reset, monkeypatch):
    db.set_setting("tts_voice_id", "fish_miyuki")
    db.set_setting("fish_api_key", "test-key")
    monkeypatch.setattr(tts, "frequent_phrases", lambda limit=tts.WARM_PHRASES: ["Готово!", "Привет!"])
    seen = []

    async def fish(text, vid):
        seen.append((text, vid))
        return ("/tmp/warm.mp3", 0)

    monkeypatch.setattr(tts, "_fish_speak", fish)
    tts.warm_phrases()
    assert seen == [("Готово!", "fish_miyuki"), ("Привет!", "fish_miyuki")]


def test_warm_phrases_stops_on_first_failure(db_reset, monkeypatch):
    db.set_setting("tts_voice_id", "fish_miyuki")
    db.set_setting("fish_api_key", "test-key")
    monkeypatch.setattr(tts, "frequent_phrases", lambda limit=tts.WARM_PHRASES: ["Раз", "Два"])
    seen = []

    async def fish(text, vid):
        seen.append(text)
        return None

    monkeypatch.setattr(tts, "_fish_speak", fish)
    tts.warm_phrases()
    assert seen == ["Раз"]


def test_warm_runs_phrase_warm(monkeypatch):
    calls = []
    monkeypatch.setattr(tts, "_get_vits", lambda: None)
    monkeypatch.setattr(tts, "_get_f5", lambda: (_ for _ in ()).throw(RuntimeError("skip")))
    monkeypatch.setattr(tts.modelmgr, "start", lambda: None)
    monkeypatch.setattr(tts, "warm_phrases", lambda: calls.append(1))
    tts.warm()
    assert calls == [1]


def _spy_engines(monkeypatch):
    calls = []
    monkeypatch.setattr(tts, "_get_vits", lambda: calls.append("vits"))
    monkeypatch.setattr(tts, "_get_f5", lambda: calls.append("f5"))
    monkeypatch.setattr(tts, "_get_qwen", lambda size: calls.append("qwen"))
    monkeypatch.setattr(tts, "_get_ref", lambda mood: None)
    monkeypatch.setattr(tts, "_real_ref", lambda vid: None)
    monkeypatch.setattr(tts.modelmgr, "start", lambda: None)
    monkeypatch.setattr(tts, "warm_phrases", lambda: None)
    return calls


def test_warm_fish_voice_skips_local_models(db_reset, monkeypatch):
    db.set_setting("tts_voice_id", "fish_miyuki")
    calls = _spy_engines(monkeypatch)

    tts.warm()

    assert calls == []


def test_warm_vits_voice_skips_f5(db_reset, monkeypatch):
    db.set_setting("tts_voice_id", "vits_sveta")
    calls = _spy_engines(monkeypatch)

    tts.warm()

    assert calls == ["vits"]


def test_warm_f5_voice_skips_vits(db_reset, monkeypatch):
    db.set_setting("tts_voice_id", "f3")
    calls = _spy_engines(monkeypatch)

    tts.warm()

    assert calls == ["f5"]


def test_warm_qwen_voice_skips_local_engines(db_reset, monkeypatch):
    db.set_setting("tts_voice_id", "qwen_17")
    calls = _spy_engines(monkeypatch)

    tts.warm()

    assert calls == ["qwen"]


def test_get_f5_marks_engine_used_with_default_vocoder(db_reset, monkeypatch):
    import sys
    import types

    import pytest

    pytest.importorskip("torchaudio", reason="нужен набор «локальные голоса»")

    fake_api = types.ModuleType("f5_tts.api")

    class FakeF5:
        device = "cpu"

        def __init__(self, **kwargs):
            pass

    fake_api.F5TTS = FakeF5
    monkeypatch.setitem(sys.modules, "f5_tts", types.ModuleType("f5_tts"))
    monkeypatch.setitem(sys.modules, "f5_tts.api", fake_api)
    monkeypatch.setattr(tts, "_f5_engine", None)
    db.set_setting("tts_vocoder", "vocos")
    touched = []
    monkeypatch.setattr(tts.modelmgr, "touch", lambda *names: touched.extend(names))

    engine = tts._get_f5()

    assert isinstance(engine, FakeF5)
    assert "f5" in touched
