import json

import pytest

from core import db, music
from core.music import player, system, yandex, youtube


@pytest.fixture(autouse=True)
def clean_player():
    player.STATE.update(seq=0, cmd="", items=[], pos=-1, status="idle", source="", service="", title="")
    system._LAST.update(via="", proc="", aumid="", ts=0.0)
    yield
    player.STATE.update(items=[], pos=-1, status="idle")


def _items(n):
    return [{"id": str(i), "track": str(i), "album": "", "title": f"t{i}", "artist": "a", "cover": "", "duration": 100} for i in range(n)]


def test_youtube_play_opens_browser_with_mix(monkeypatch, db_reset):
    opened = []
    monkeypatch.setattr(youtube, "search", lambda q, n=5: [{"id": "abcdefghijk", "title": "Numb", "channel": "Linkin Park", "duration": 186}])
    monkeypatch.setattr(music, "open_in_browser", lambda url: opened.append(url) or "browser.exe")
    out = music.tool({"action": "play", "query": "linkin park numb"})
    assert "YouTube" in out and "Numb" in out
    assert opened == ["https://www.youtube.com/watch?v=abcdefghijk&list=RDabcdefghijk"]
    assert system._LAST["via"] == "keys"  # дальше «пауза» — клавишей браузеру


def test_pick_song_skips_shorts_and_long_mixes():
    res = [{"id": "1", "duration": 30}, {"id": "2", "duration": 5 * 3600}, {"id": "3", "duration": 200}]
    assert youtube.pick_song(res)["id"] == "3"


def test_yandex_without_token_opens_site(monkeypatch, db_reset):
    db.set_setting("ym_token", "")
    db.set_setting("music_service", "yandex")
    opened = []
    monkeypatch.setattr(yandex, "find", lambda q: {"kind": "track", "title": "Группа крови", "artist": "КИНО", "ids": {"track": "1", "album": "2"}})
    monkeypatch.setattr(music, "open_in_browser", lambda url: opened.append(url) or "browser.exe")
    out = music.tool({"action": "play", "query": "кино группа крови"})
    assert opened == ["https://music.yandex.ru/album/2/track/1"] and "нажми" in out
    assert "токен" in music.tool({"action": "play", "what": "wave"})


def test_yandex_with_token_plays_in_aya(monkeypatch, db_reset):
    db.set_setting("ym_token", "tok")
    db.set_setting("music_yandex_target", "aya")
    monkeypatch.setattr(yandex, "find", lambda q: {"kind": "artist", "title": "Земфира", "artist": "Земфира", "ids": {"artist": "9"}})
    monkeypatch.setattr(yandex, "queue_for", lambda found: (_items(3), ""))
    out = music.tool({"action": "play", "query": "земфира", "service": "yandex"})
    assert "играю сама" in out and player.playing()
    snap = player.snapshot()
    assert snap["cmd"] == "load" and snap["current"]["title"] == "t0" and snap["count"] == 3


def test_player_queue_flow_and_routing(db_reset, monkeypatch):
    player.load(_items(2), "", "yandex", "x")
    monkeypatch.setattr(system, "now_playing", lambda: {"status": "none"})
    assert "на паузе" in music.tool({"action": "pause"})
    assert player.STATE["status"] == "paused"
    assert "играет" in music.tool({"action": "resume"})
    s = player.command("ended")
    assert s["pos"] == 1 and s["cmd"] == "track"
    s = player.command("ended")
    assert s["status"] == "idle" and s["cmd"] == "stop"


def test_player_refills_wave(monkeypatch):
    calls = []
    monkeypatch.setattr(yandex, "more", lambda src, last: calls.append((src, last)) or [{**_items(1)[0], "id": "x", "track": "x"}])
    player.load(_items(2), "station:user:onyourwave", "yandex", "Моя волна")
    s = player.command("next")
    assert calls and s["count"] == 3


def test_duck_lowers_and_restores_volume(monkeypatch, db_reset):
    db.set_setting("music_duck", "lower")

    class Vol:
        def __init__(self):
            self.v = 0.8

        def GetMasterVolume(self):
            return self.v

        def SetMasterVolume(self, v, _ctx):
            self.v = v

    class Proc:
        pid = 42

        def name(self):
            return "browser.exe"

    class Sess:
        def __init__(self):
            self.SimpleAudioVolume = Vol()
            self.Process = Proc()

    s = Sess()
    monkeypatch.setattr(system, "audio_sessions", lambda active_only=True: [s])
    system._DUCK.update(reasons=set(), volumes={}, paused=None)
    assert system.duck(True, "speak")["ducked"]
    assert abs(s.SimpleAudioVolume.v - 0.2) < 1e-6
    system.duck(True, "listen")  # вторая причина — не приглушаем повторно
    assert abs(s.SimpleAudioVolume.v - 0.2) < 1e-6
    system.duck(False, "speak")
    assert abs(s.SimpleAudioVolume.v - 0.2) < 1e-6  # ещё слушает
    system.duck(False, "listen")
    assert abs(s.SimpleAudioVolume.v - 0.8) < 1e-6


def test_duck_off_mode_does_nothing(monkeypatch, db_reset):
    db.set_setting("music_duck", "off")
    monkeypatch.setattr(system, "audio_sessions", lambda active_only=True: pytest.fail("не должен трогать громкость"))
    system._DUCK.update(reasons=set(), volumes={}, paused=None)
    assert not system.duck(True, "speak")["ducked"]
    system.duck(False, "speak")


def test_music_config_api_hides_token(client, db_reset):
    r = client.post("/api/music/config", json={"service": "yandex", "duck": "pause", "ym_token": "secret-ym", "yandex_target": "web"}).json()  # public-ok: выдуманное значение
    assert r["ok"] and r["service"] == "yandex" and r["duck"] == "pause" and r["ym_token_set"]
    assert "secret-ym" not in json.dumps(r)
    client.post("/api/music/config", json={"duck": "loud"})
    assert client.post("/api/music/config", json={}).json()["duck"] == "pause"


def test_music_player_api_and_stream_guard(client, db_reset):
    player.load(_items(2), "", "yandex", "x")
    assert client.get("/api/music/player").json()["count"] == 2
    assert client.post("/api/music/player", json={"cmd": "ended"}).json()["pos"] == 1
    assert client.get("/api/music/stream/..%2Fetc").status_code in (400, 404)
    assert client.get("/api/ui").json()["music"]["count"] == 2


def test_browser_title_parsing(monkeypatch):
    import types

    fake = types.SimpleNamespace(process_iter=lambda attrs: [types.SimpleNamespace(info={"name": "browser.exe"}, pid=7)])
    monkeypatch.setitem(__import__("sys").modules, "psutil", fake)
    monkeypatch.setattr(system, "_window_titles", lambda pid: ["(2751) Группа крови - YouTube, профиль роман — Яндекс Браузер"])
    assert system._browser_title("browser.exe") == "Группа крови"


def test_music_tool_is_free(db_reset, monkeypatch):
    import asyncio

    from core.safety import gateway, tools

    tools.set_all_tools(False)
    monkeypatch.setattr(music, "now_playing", lambda: {"status": "none"})
    res = asyncio.run(gateway.dispatch({"name": "music", "args": {"action": "now"}}))
    assert "ничего не играет" in res


@pytest.mark.parametrize(
    "text,action",
    [
        ("пауза", "pause"), ("Ая, пауза!", "pause"), ("поставь на паузу", "pause"), ("выключи музыку", "pause"),
        ("продолжи музыку", "resume"), ("сними с паузы", "resume"),
        ("следующий трек", "next"), ("включи следующую песню", "next"), ("переключи", "next"), ("пропусти", "next"),
        ("предыдущий трек", "prev"), ("что играет?", "now"), ("что это за песня", "now"), ("кто поёт", "now"),
        ("продолжи", None), ("дальше", None), ("продолжи рассказ", None), ("стоп", None),
        ("включи кино группа крови", None), ("переключи вкладку", None),
    ],
)
def test_quick_intent(text, action):
    assert (music.quick_intent(text) or {}).get("action") == action


def test_chat_quick_command_skips_first_llm_pass(client, monkeypatch, db_reset):
    import core.main as main
    from core.llm.base import BaseAdapter

    prompts = []

    class Spy(BaseAdapter):
        name = "spy"

        async def stream_chat(self, system, messages, temperature=0.8):
            prompts.append(messages[-1]["content"])
            yield "Поставила на паузу."

    monkeypatch.setattr(main, "make_adapters", lambda cfg: [Spy()])
    monkeypatch.setattr(music, "control", lambda a: {"ok": True, "status": "paused", "app": "Chrome", "title": "Numb", "artist": "", "via": "smtc"})
    with client.stream("POST", "/api/chat/stream", json={"text": "пауза"}) as r:
        lines = [json.loads(x) for x in r.iter_lines() if x.strip()]
    assert lines[-1]["reply"] == "Поставила на паузу."
    assert len(prompts) == 1 and "music" in prompts[0] and "на паузе" in prompts[0]  # только пересказ результата


def test_resume_goes_to_last_controlled_player(db_reset, monkeypatch):
    """Волна на паузе → включили YouTube → пауза YouTube → «продолжи» = YouTube, не волна."""
    import time

    player.load(_items(3), "station:user:onyourwave", "yandex", "Моя волна")
    music.control("pause")
    assert player.STATE["status"] == "paused"
    player.STATE["user_ts"] = time.time() - 60  # волной управляли минуту назад
    sent = []
    monkeypatch.setattr(system, "now_playing", lambda: {"status": "paused", "app": "Яндекс Браузер", "via": "keys", "proc": "browser.exe"})
    monkeypatch.setattr(system, "control", lambda a: sent.append(a) or {"ok": True, "status": "playing", "via": "keys", "app": "Яндекс Браузер", "title": "10ТБ"})
    system.remember_browser("browser.exe")  # YouTube открыли/поставили на паузу позже
    assert music.control("play")["app"] == "Яндекс Браузер"
    assert sent == ["play"] and player.STATE["status"] == "paused"  # волна не ожила
    assert music.now_playing()["app"] == "Яндекс Браузер"
    # а если потом снова скомандовать волне — она снова главная
    player.command("play")
    assert music.control("pause")["via"] == "aya"


def test_track_end_is_not_user_action(db_reset):
    player.load(_items(3), "", "yandex", "x")
    player.STATE["user_ts"] = 1.0
    player.command("ended")
    assert player.STATE["user_ts"] == 1.0
