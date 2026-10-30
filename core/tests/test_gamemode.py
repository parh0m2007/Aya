"""Игровой режим: Ая прячется и экономит ресурсы, пока на весь экран открыта игра."""
import pytest

from core import db, gamemode


def fg(exe="", fullscreen=False, d3d=False, cls="UnrealWindow"):
    return {"exe": exe, "cls": cls, "fullscreen": fullscreen, "d3d": d3d}


@pytest.fixture
def gm(db_reset, monkeypatch):
    calls = []
    monkeypatch.setattr(gamemode, "_apply", lambda on: calls.append(on))
    gamemode._state.update(on=False, app="", hits=0, last_seen=0.0)
    yield calls
    gamemode._state.update(on=False, app="", hits=0, last_seen=0.0)


def test_known_games_even_windowed():
    for exe in ("valorant-win64-shipping.exe", "cs2.exe", "dota2.exe"):
        assert gamemode.is_game(fg(exe))


def test_any_fullscreen_app_counts():
    assert gamemode.is_game(fg("somenewgame.exe", fullscreen=True))
    assert gamemode.is_game(fg("", d3d=True))  # античит не дал узнать имя — хватает полноэкранного D3D
    assert gamemode.is_game(fg("", fullscreen=True))


def test_not_games():
    assert not gamemode.is_game(None)
    assert not gamemode.is_game(fg("code.exe"))  # обычное окно
    assert not gamemode.is_game(fg("chrome.exe", fullscreen=True))  # видео во весь экран
    assert not gamemode.is_game(fg("vlc.exe", fullscreen=True))
    assert not gamemode.is_game(fg("explorer.exe", fullscreen=True, cls="Progman"))  # рабочий стол
    assert not gamemode.is_game(fg("aya.exe", fullscreen=True))


def test_enters_after_two_samples_and_leaves_after_pause(gm):
    game = fg("cs2.exe", fullscreen=True)
    assert gamemode.update(game, now=100) == ""  # мелькнуло — ещё не игра
    assert gamemode.update(game, now=102) == "enter"
    assert gamemode.active() and gamemode.status()["app"] == "cs2.exe" and gm == [True]
    assert gamemode.update(fg("discord.exe"), now=105) == ""  # alt-tab в Discord — не выходим сразу
    assert gamemode.update(game, now=110) == ""
    assert gamemode.update(fg("explorer.exe"), now=120) == ""
    assert gamemode.update(fg("explorer.exe"), now=126) == "leave"  # 16 с без игры
    assert not gamemode.active() and gm == [True, False]


def test_disabled_setting_never_enters_and_leaves_at_once(gm):
    game = fg("cs2.exe", fullscreen=True)
    gamemode.update(game, now=1)
    gamemode.update(game, now=3)
    assert gamemode.active()
    db.set_setting("game_mode", "0")
    assert gamemode.update(game, now=5) == "leave"
    gamemode.update(game, now=7)
    gamemode.update(game, now=9)
    assert not gamemode.active() and gm == [True, False]


def test_ui_reports_game(client, gm):
    gamemode.update(fg("dota2.exe"), now=1)
    gamemode.update(fg("dota2.exe"), now=3)
    assert client.get("/api/ui").json()["game"] == {"on": True, "app": "dota2.exe", "enabled": True}


def test_no_initiative_during_game(client, gm, monkeypatch):
    import core.main as main

    monkeypatch.setattr(main.state, "initiative_check", lambda: "break")
    gamemode.update(fg("cs2.exe"), now=1)
    gamemode.update(fg("cs2.exe"), now=3)
    r = client.get("/api/initiative").json()
    assert r["topic"] == "" and r["reply"] == ""


def test_config_toggle(client, db_reset):
    assert client.get("/api/config").json()["game_mode"] is True
    client.post("/api/config", json={"game_mode": False})
    assert client.get("/api/config").json()["game_mode"] is False and not gamemode.enabled()


# ── что меняется в игре ─────────────────────────────────────────────


def test_stt_uses_light_model_on_two_cores_in_game(monkeypatch):
    from core.voice import stt

    monkeypatch.setattr(stt, "cached", lambda size: True)
    monkeypatch.setattr(stt, "_preload", lambda: None)
    stt._model = object()
    stt.set_game(True)
    try:
        assert stt._model is None  # основная модель выгружена
        assert stt.wanted() == ("base", "cpu") and stt.threads() == 2
        assert stt.status()["game"] is True
    finally:
        stt.set_game(False)
    assert stt.threads() == 0 and stt.status()["game"] is False


def test_stt_in_game_never_downloads(monkeypatch):
    """Лёгкой модели ещё нет на диске — в игре не качаем (пинг!), берём основную на 2 ядрах."""
    from core.voice import stt

    monkeypatch.setattr(stt, "cached", lambda size: False)
    monkeypatch.setattr(stt, "_preload", lambda: None)
    stt.set_game(True)
    try:
        size, device = stt.wanted()
        assert size != "base" and device == "cpu" and stt.threads() == 2
    finally:
        stt.set_game(False)


def test_local_voice_becomes_cloud_in_game(db_reset, monkeypatch):
    from core.voice import tts

    db.set_setting("tts_voice_id", "qwen_17")
    monkeypatch.setattr(gamemode, "active", lambda: False)
    assert tts.selected_voice() == "qwen_17" and not tts.is_cloud()
    monkeypatch.setattr(gamemode, "active", lambda: True)
    assert tts.selected_voice() == tts.DEFAULT_VOICE and tts.is_cloud()


def test_game_speech_never_loads_local_engines(db_reset, monkeypatch, tmp_path):
    """Нет ключа Fish — в игре говорит Edge, а не тяжёлые локальные голоса."""
    import asyncio

    from core.voice import tts

    db.set_setting("tts_voice_id", "f3")
    db.set_setting("fish_api_key", "")
    monkeypatch.setattr(gamemode, "active", lambda: True)
    monkeypatch.setattr(tts, "_index_get", lambda key: None)
    monkeypatch.setattr(tts, "_index_put", lambda key, path: None)

    async def boom(*a, **k):
        raise AssertionError("локальный голос в игре")

    for name in ("_f5_real", "_f5_speak", "_vits_speak", "_qwen_speak"):
        monkeypatch.setattr(tts, name, boom)
    monkeypatch.setattr(tts, "_edge_speak", lambda text: (str(tmp_path / "e.mp3"), 1))
    path, _ = asyncio.run(tts.speak("Привет", "calm"))
    assert path.endswith("e.mp3")


def test_priority_targets_skip_audio_process(monkeypatch):
    """Голос Аи не должен заикаться: звуковой процесс WebView2 не трогаем."""

    class P:
        def __init__(self, pid, name, cmd="", kids=(), parent=None):
            self.pid, self._name, self._cmd, self._kids, self._parent = pid, name, cmd, list(kids), parent

        def name(self):
            return self._name

        def cmdline(self):
            return self._cmd.split()

        def children(self, recursive=False):
            return self._kids

        def parent(self):
            return self._parent

    mcp = P(11, "node.exe")
    me = P(10, "pythonw.exe", kids=[mcp])
    render = P(21, "msedgewebview2.exe", "--type=renderer")
    audio = P(22, "msedgewebview2.exe", "--type=utility --utility-sub-type=audio.mojom.AudioService")
    aya = P(1, "aya.exe", kids=[me, mcp, render, audio])
    me._parent = aya
    eco, low = gamemode.priority_targets(me)
    assert sorted(p.pid for p in eco) == [10, 11]  # ядро и его помощники — ещё и режим экономии
    assert sorted(p.pid for p in low) == [1, 21]  # окно — только приоритет ниже
