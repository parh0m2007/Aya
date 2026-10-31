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


def _sess(name: str, pid: int, v: float = 0.8):
    class Vol:
        def __init__(self):
            self.v = v

        def GetMasterVolume(self):
            return self.v

        def SetMasterVolume(self, nv, _ctx):
            self.v = nv

    class Proc:
        def __init__(self):
            self.pid = pid

        def name(self):
            return name

    class Sess:
        def __init__(self):
            self.SimpleAudioVolume = Vol()
            self.Process = Proc()

    return Sess()


def test_duck_leaves_other_apps_alone(monkeypatch, db_reset):
    """Тестеры: Ая приглушала звонок в Discord и игру. Тише делаем только музыку."""
    db.set_setting("music_duck", "lower")
    ss = {n: _sess(n, i) for i, n in enumerate(["Discord.exe", "game.exe", "Spotify.exe", "chrome.exe", "Яндекс Музыка.exe"])}
    monkeypatch.setattr(system, "audio_sessions", lambda active_only=True: list(ss.values()))
    system._DUCK.update(reasons=set(), volumes={}, paused=None)
    system.duck(True, "aya")
    low = {n for n, s in ss.items() if s.SimpleAudioVolume.v < 0.5}
    assert low == {"Spotify.exe", "chrome.exe", "Яндекс Музыка.exe"}
    system.duck(False, "aya")
    assert all(abs(s.SimpleAudioVolume.v - 0.8) < 1e-6 for s in ss.values())


def test_duck_restored_after_crash(monkeypatch, db_reset):
    """Ядро упало посреди речи — Windows запомнила бы тихую громкость навсегда.
    При следующем запуске возвращаем как было."""
    db.set_setting("music_duck", "lower")
    s = _sess("chrome.exe", 7)
    monkeypatch.setattr(system, "audio_sessions", lambda active_only=True: [s])
    system._DUCK.update(reasons=set(), volumes={}, paused=None)
    system.duck(True, "aya")
    assert s.SimpleAudioVolume.v < 0.5
    system._DUCK.update(reasons=set(), volumes={}, paused=None)  # «перезапуск»: память потеряна
    system.recover_duck()
    assert abs(s.SimpleAudioVolume.v - 0.8) < 1e-6
    assert db.setting("music_duck_saved", "") == ""
    system.recover_duck()  # повторно — ничего не трогает
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
        ("предыдущий трек", "prev"), ("включи прошлую песню", "prev"), ("переключи песню", "next"), ("что играет?", "now"), ("что это за песня", "now"), ("кто поёт", "now"),
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


def _youtube_in_yandex_browser(monkeypatch, titles):
    """YouTube играет в Яндекс Браузере (без SMTC); titles — заголовки по очереди."""
    seq = list(titles)
    monkeypatch.setattr(system, "_smtc_playing", lambda: None)
    monkeypatch.setattr(system, "_browser_playing", lambda: {"status": "playing", "app": "Яндекс Браузер", "proc": "browser.exe", "via": "keys"})
    monkeypatch.setattr(system, "_browser_title", lambda proc: seq.pop(0) if len(seq) > 1 else seq[0])
    monkeypatch.setattr(system, "_youtube_window", lambda proc: 42)
    monkeypatch.setattr(system.time, "sleep", lambda s: None)
    pressed = []
    monkeypatch.setattr(system, "_press", lambda key: pressed.append(("media", key)))
    monkeypatch.setattr(system, "_youtube_key", lambda hwnd, action: pressed.append(("youtube", hwnd, action)))
    return pressed


def test_youtube_next_and_prev_use_youtube_shortcuts(monkeypatch):
    """Мультимедийные «дальше/назад» Яндекс Браузер YouTube не передаёт — жмём Shift+N/P в окне YouTube."""
    monkeypatch.setattr(system, "_in_game", lambda: False)
    pressed = _youtube_in_yandex_browser(monkeypatch, ["Группа крови", "Спокойная ночь"])
    d = system.control("next")
    assert pressed == [("youtube", 42, "next")]
    assert d["ok"] and d["title"] == "Спокойная ночь"
    pressed = _youtube_in_yandex_browser(monkeypatch, ["Спокойная ночь", "Группа крови"])
    assert system.control("prev")["ok"] and pressed == [("youtube", 42, "prev")]


def test_youtube_pause_still_uses_media_key(monkeypatch):
    monkeypatch.setattr(system, "_in_game", lambda: False)
    pressed = _youtube_in_yandex_browser(monkeypatch, ["Группа крови"])
    system.control("pause")
    assert pressed == [("media", system.KEYS["pause"])]


def test_youtube_not_switched_during_game(monkeypatch, db_reset):
    """В игре не переключаем окно на браузер — это свернуло бы игру."""
    monkeypatch.setattr(system, "_in_game", lambda: True)
    pressed = _youtube_in_yandex_browser(monkeypatch, ["Группа крови"])
    d = system.control("next")
    assert pressed == [] and d["note"] == "game"
    monkeypatch.setattr(music, "control", lambda a: d)
    assert "игр" in music.tool({"action": "next"})


def test_youtube_stuck_explains_playlist(monkeypatch, db_reset):
    monkeypatch.setattr(system, "_in_game", lambda: False)
    _youtube_in_yandex_browser(monkeypatch, ["Группа крови"])
    d = system.control("prev")
    assert d["note"] == "yt_stuck"
    monkeypatch.setattr(music, "control", lambda a: d)
    line = music.tool({"action": "prev"})
    assert "плейлист" in line and "браузер не переключает" not in line


def test_youtube_title_matches_only_youtube():
    assert system._YT_TITLE.search("Группа крови - YouTube, профиль роман — Яндекс Браузер")
    assert system._YT_TITLE.search("(3) Numb – YouTube — Google Chrome")
    assert not system._YT_TITLE.search("Numb - YouTube Music — Яндекс Браузер")
    assert not system._YT_TITLE.search("Группа крови — Яндекс Музыка")


def test_track_end_is_not_user_action(db_reset):
    player.load(_items(3), "", "yandex", "x")
    player.STATE["user_ts"] = 1.0
    player.command("ended")
    assert player.STATE["user_ts"] == 1.0


# ── громкость музыки, а не всего компьютера ───────────────────────────


def _vol_sess(name: str, pid: int, v: float = 0.8, ident: str = ""):
    s = _sess(name, pid, v)
    s.InstanceIdentifier = ident or f"{name}|{pid}"
    return s


@pytest.mark.parametrize(
    "text,want",
    [
        ("сделай музыку потише", {"action": "volume_down"}),
        ("Ая, музыку тише", {"action": "volume_down"}),
        ("сделай потише музыку", {"action": "volume_down"}),
        ("убавь музыку", {"action": "volume_down"}),
        ("приглуши музыку", {"action": "volume_down"}),
        ("сделай трек погромче", {"action": "volume_up"}),
        ("прибавь громкость музыки", {"action": "volume_up"}),
        ("музыку громче!", {"action": "volume_up"}),
        ("громкость музыки 30", {"action": "volume", "level": 30}),
        ("поставь громкость музыки на 45%", {"action": "volume", "level": 45}),
        ("сделай погромче", None),  # без слова «музыка» — может быть и общая громкость: решает модель
        ("заглуши музыку", {"action": "pause"}),
    ],
)
def test_quick_intent_music_volume(text, want):
    assert music.quick_intent(text) == want


def test_music_volume_changes_browser_not_speakers(monkeypatch, db_reset):
    """Жалоба: «сделай музыку тише» меняло громкость всего Windows или ничего не делало."""
    ss = [_vol_sess("browser.exe", 5), _vol_sess("Discord.exe", 6)]
    monkeypatch.setattr(system, "audio_sessions", lambda active_only=True: ss)
    monkeypatch.setattr(system, "now_playing", lambda: {"status": "playing"})
    out = music.tool({"action": "volume_down"})
    assert abs(ss[0].SimpleAudioVolume.v - 0.56) < 1e-6
    assert ss[1].SimpleAudioVolume.v == 0.8  # звонок в Discord не трогаем
    assert "56%" in out and "Яндекс Браузер" in out
    music.tool({"action": "volume_up"})
    assert abs(ss[0].SimpleAudioVolume.v - 0.8) < 1e-6
    music.tool({"action": "volume", "level": 30})
    assert abs(ss[0].SimpleAudioVolume.v - 0.3) < 1e-6


def test_music_volume_steps_are_even_by_ear():
    """Шаг — в разах (≈ −3 дБ), а не минус 10 процентов: на 100% такой шаг не слышен."""
    assert system.step_volume(1.0, "down") == pytest.approx(0.7)
    assert system.step_volume(0.7, "up") == pytest.approx(1.0)
    assert system.step_volume(1.0, "up") == 1.0
    assert system.step_volume(0.04, "down") == 0.0  # совсем тихо — выключаем
    assert system.step_volume(0.0, "up") == pytest.approx(0.1)  # из нуля — слышно сразу


def test_music_volume_while_ducked_survives_restore(monkeypatch, db_reset):
    """Пока Ая говорит, музыка приглушена; «тише» должно пережить возврат громкости."""
    db.set_setting("music_duck", "lower")
    s = _vol_sess("chrome.exe", 7)
    monkeypatch.setattr(system, "audio_sessions", lambda active_only=True: [s])
    monkeypatch.setattr(system, "now_playing", lambda: {"status": "playing"})
    system._DUCK.update(reasons=set(), volumes={}, paused=None)
    system.duck(True, "aya")
    assert abs(s.SimpleAudioVolume.v - 0.2) < 1e-6
    music.tool({"action": "volume_down"})
    assert abs(s.SimpleAudioVolume.v - 0.56 * 0.25) < 1e-6  # всё ещё приглушена, но тише
    assert json.loads(db.setting("music_duck_saved", ""))["chrome.exe"] == pytest.approx(0.56)
    system.duck(False, "aya")
    assert abs(s.SimpleAudioVolume.v - 0.56) < 1e-6


def test_duck_restores_every_session_of_one_process(monkeypatch, db_reset):
    """У браузера бывает несколько звуковых сеансов в одном процессе — вернуть надо все."""
    db.set_setting("music_duck", "lower")
    a, b = _vol_sess("chrome.exe", 7, ident="a"), _vol_sess("chrome.exe", 7, ident="b")
    monkeypatch.setattr(system, "audio_sessions", lambda active_only=True: [a, b])
    system._DUCK.update(reasons=set(), volumes={}, paused=None)
    system.duck(True, "aya")
    system.duck(False, "aya")
    assert abs(a.SimpleAudioVolume.v - 0.8) < 1e-6 and abs(b.SimpleAudioVolume.v - 0.8) < 1e-6


def test_music_volume_nothing_playing(monkeypatch, db_reset):
    monkeypatch.setattr(system, "audio_sessions", lambda active_only=True: [_vol_sess("Discord.exe", 6)])
    monkeypatch.setattr(system, "now_playing", lambda: {"status": "none"})
    assert "не играет" in music.tool({"action": "volume_down"})


def test_own_player_volume(monkeypatch, db_reset):
    """Яндекс Музыка играет в самой Ае — тише делаем её плеер, браузер не трогаем."""
    s = _vol_sess("browser.exe", 5)
    monkeypatch.setattr(system, "audio_sessions", lambda active_only=True: [s])
    player.load(_items(2), "", "yandex", "x")
    seq = player.snapshot()["seq"]
    out = music.tool({"action": "volume_down"})
    snap = player.snapshot()
    assert snap["volume"] == 70 and snap["seq"] > seq and "70%" in out
    assert s.SimpleAudioVolume.v == 0.8
    assert db.setting("music_player_volume", "") == "70"
    assert "максимум" in music.tool({"action": "volume", "level": 100}) or player.snapshot()["volume"] == 100
    assert "максимум" in music.tool({"action": "volume_up"})


def test_player_volume_buttons(client, db_reset):
    player.load(_items(2), "", "yandex", "x")
    r = client.post("/api/music/player", json={"cmd": "voldown"}).json()
    assert r["volume"] == 70 and r["status"] == "playing"
    r = client.post("/api/music/player", json={"cmd": "volup"}).json()
    assert r["volume"] == 100
    assert client.get("/api/music/player").json()["volume"] == 100


def test_model_told_music_volume_is_not_system_volume(db_reset):
    """Модель брала set_volume (весь компьютер) на «сделай музыку тише»."""
    from core.safety import tool_text

    assert "volume_down" in tool_text._music_line() and "volume_up" in tool_text._music_line()
    assert "общая громкость компьютера" in tool_text.GATED_LINES["set_volume"]


def test_music_volume_tolerates_model_variants(monkeypatch, db_reset):
    """Модель пишет громкость по-разному: «30%» строкой, change вместо отдельного действия."""
    s = _vol_sess("browser.exe", 5)
    monkeypatch.setattr(system, "audio_sessions", lambda active_only=True: [s])
    monkeypatch.setattr(system, "now_playing", lambda: {"status": "playing"})
    music.tool({"action": "volume", "level": "30%"})
    assert abs(s.SimpleAudioVolume.v - 0.3) < 1e-6
    music.tool({"action": "volume", "change": "up"})
    assert abs(s.SimpleAudioVolume.v - 0.3 / 0.7) < 1e-3
    assert "level" in music.tool({"action": "volume"})  # без уровня — подсказка, громкость не трогаем
