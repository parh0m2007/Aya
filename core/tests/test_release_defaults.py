import asyncio


def test_default_voice_is_fish(client, db_reset):
    from core.voice import tts

    assert tts.DEFAULT_VOICE == "fish_miyuki"
    assert client.get("/api/config").json()["tts_voice_id"] == "fish_miyuki"


def test_deleting_selected_custom_voice_falls_back_to_fish(client, db_reset, monkeypatch, tmp_path):
    from core import db
    from core.voice import tts

    monkeypatch.setattr(tts, "is_custom", lambda vid: True)
    monkeypatch.setattr(tts, "CUSTOM_DIR", tmp_path)
    db.set_setting("tts_voice_id", "custom_x")
    client.post("/api/voices/custom/custom_x/delete", json={})
    assert db.setting("tts_voice_id", "") == "fish_miyuki"


def test_author_voice_not_built_in():
    from core.voice import tts

    assert "user_ru" not in tts.REAL_REFS
    assert all("refs_user" not in p for p in tts.REAL_REFS.values())


def test_no_fish_key_speaks_with_edge(db_reset, monkeypatch, tmp_path):
    from core import db
    from core.voice import tts

    db.set_setting("fish_api_key", "")
    db.set_setting("tts_voice_id", "fish_miyuki")
    monkeypatch.setattr(tts, "_cache_dir", tmp_path)
    monkeypatch.setattr(tts, "_index_get", lambda key: None)
    monkeypatch.setattr(tts, "_index_put", lambda key, path: None)
    called = []
    monkeypatch.setattr(tts, "_edge_speak", lambda text: called.append(text) or (str(tmp_path / "e.mp3"), 1))
    path, _ok = asyncio.run(tts.speak("Привет", "calm"))
    assert called == ["Привет"] and path.endswith("e.mp3")



def test_installer_ships_socks_proxy_support():
    """Без python-socks Telethon молча игнорирует SOCKS5-прокси и идёт напрямую
    (в логах: «proxy argument will be ignored»), а Telegram в РФ так не отвечает.
    В среде разработки пакет стоит, в установщике (core/requirements.txt) его не было."""
    from pathlib import Path

    req = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8")
    names = [line.split("==")[0].split("[")[0].strip().lower() for line in req.splitlines() if line and not line.startswith("#")]
    assert "python-socks" in names
