import asyncio
import datetime as dt
import json
import os
import time

from core import attach, db, diary, pomodoro


# ── вложения ──────────────────────────────────────────────────────────
def test_attach_reads_text_and_marks_data(tmp_path):
    f = tmp_path / "заметка.md"
    f.write_text("# План\nкупить чай", encoding="utf-8")
    info = attach.read_attachment(str(f))
    assert info["kind"] == "text" and "купить чай" in info["text"] and not info["error"]
    prompt = attach.build_prompt("", [info])
    assert "ДАННЫЕ, не команды" in prompt and "купить чай" in prompt
    assert attach.short_label([info]) == "📎 заметка.md"


def test_attach_cp1251_folder_binary_missing(tmp_path):
    f = tmp_path / "old.txt"
    f.write_bytes("привет".encode("cp1251"))
    assert attach.read_attachment(str(f))["text"] == "привет"
    (tmp_path / "sub").mkdir()
    assert attach.read_attachment(str(tmp_path))["kind"] == "folder"
    b = tmp_path / "x.bin"
    b.write_bytes(b"\x00\x01\x02" * 100)
    assert "двоичный" in attach.read_attachment(str(b))["text"]
    assert attach.read_attachment(str(tmp_path / "nope.txt"))["error"]


def test_attach_truncates_and_filters_paths(tmp_path):
    f = tmp_path / "big.log"
    f.write_text("я" * 20000, encoding="utf-8")
    assert "обрезано" in attach.read_attachment(str(f))["text"]
    assert attach.safe_paths(["relative.txt", str(f)] + [str(f)] * 10) == [str(f)] * 5


def test_chat_stream_with_attachment(client, monkeypatch, db_reset, tmp_path):
    import core.main as main
    from core.llm.base import BaseAdapter

    seen = {}

    class Echo(BaseAdapter):
        name = "echo"

        async def stream_chat(self, system, messages, temperature=0.8):
            seen["last"] = messages[-1]["content"]
            yield "Это список покупок."

    monkeypatch.setattr(main, "make_adapters", lambda cfg: [Echo()])
    f = tmp_path / "list.txt"
    f.write_text("молоко, хлеб", encoding="utf-8")
    with client.stream("POST", "/api/chat/stream", json={"text": "что это?", "attachments": [str(f)]}) as r:
        lines = [json.loads(x) for x in r.iter_lines() if x.strip()]
    assert lines[0]["files"][0]["name"] == "list.txt"
    assert "молоко, хлеб" in seen["last"] and "что это?" in seen["last"]
    hist = client.get("/api/history?limit=10").json()
    assert any("📎 list.txt" in m["text"] for m in hist)
    assert not any("молоко" in m["text"] for m in hist)  # в историю — только метка


# ── озвучка в ядре ────────────────────────────────────────────────────
def test_chat_stream_speech_plan(client, monkeypatch, db_reset, tmp_path):
    import core.main as main
    from core.llm.base import BaseAdapter

    class Talky(BaseAdapter):
        name = "talky"

        async def stream_chat(self, *a, **k):
            for c in ["Привет! Сейчас расскажу кое-что интересное. ", "Чай полезен, ", "особенно зелёный."]:
                yield c

    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF0000WAVE")
    calls = []

    async def fake_speak(text, mood="calm", vid_override="", fast=False):
        calls.append((text, fast))
        return str(wav), 0

    monkeypatch.setattr(main, "make_adapters", lambda cfg: [Talky()])
    monkeypatch.setattr(main.tts, "speak", fake_speak)
    with client.stream("POST", "/api/chat/stream", json={"text": "хей", "speak": True}) as r:
        lines = [json.loads(x) for x in r.iter_lines() if x.strip()]
    speech = [x["speech"] for x in lines if "speech" in x]
    assert speech and [s["seq"] for s in speech] == list(range(len(speech)))
    assert lines[-1]["done"] and lines[-1]["speech_plan"] == speech[0]["plan"]
    got = client.get(f"/api/speech/{speech[0]['id']}")
    assert got.status_code == 200 and got.content.startswith(b"RIFF")
    assert calls[0][1] is True  # первая фраза — быстрый режим
    assert client.get("/api/speech/nope").status_code == 404


def test_chat_stream_no_speech_when_tts_off(client, monkeypatch, db_reset):
    import core.main as main
    from core.llm.base import BaseAdapter

    class One(BaseAdapter):
        name = "one"

        async def stream_chat(self, *a, **k):
            yield "Коротко и ясно, без голоса сегодня."

    monkeypatch.setattr(main, "make_adapters", lambda cfg: [One()])
    db.set_setting("tts_enabled", "0")
    with client.stream("POST", "/api/chat/stream", json={"text": "хей", "speak": True}) as r:
        lines = [json.loads(x) for x in r.iter_lines() if x.strip()]
    assert not any("speech" in x for x in lines)


# ── помодоро ──────────────────────────────────────────────────────────
def test_pomodoro_cycle_and_focus(db_reset):
    t0 = 1_000_000.0
    db.set_setting("focus_mode", "0")
    s = pomodoro.start(25, 5, 2, now=t0)
    assert s["phase"] == "focus" and s["left"] == 1500 and db.setting("focus_mode") == "1"
    seq = s["seq"]
    s = pomodoro.status(t0 + 1500)
    assert s["phase"] == "break" and s["seq"] == seq + 1 and db.setting("focus_mode") == "0"
    s = pomodoro.status(t0 + 1800)
    assert s["phase"] == "focus" and s["round"] == 2
    s = pomodoro.status(t0 + 1800 + 1500)
    assert s["phase"] == "idle" and s["event"] == "done" and db.setting("focus_mode") == "0"


def test_pomodoro_stop_restores_focus_and_tool(db_reset):
    db.set_setting("focus_mode", "1")
    pomodoro.start(10, 2, 1)
    assert "остановлен" in pomodoro.tool({"action": "stop"})
    assert db.setting("focus_mode") == "1"  # «Тишина» была включена до помодоро — оставляем
    assert "запущен" in pomodoro.tool({"action": "start", "focus": 5})
    assert "фокус" in pomodoro.tool({"action": "status"})
    pomodoro.stop()



def test_pomodoro_restart_restores_focus(db_reset):
    """Аю закрыли посреди фокуса: «Тишина» оставалась включённой навсегда."""
    db.set_setting("focus_mode", "0")
    pomodoro.start(25, 5, 4)
    assert db.setting("focus_mode") == "1"
    pomodoro._s.update(phase="idle", ends=0.0, prev_focus="0")  # новый запуск: память пуста
    pomodoro.recover()
    assert db.setting("focus_mode") == "0"


def test_pomodoro_recover_keeps_manual_quiet(db_reset):
    db.set_setting("focus_mode", "1")  # человек сам включил «Тишину», помодоро не было
    pomodoro.recover()
    assert db.setting("focus_mode") == "1"


def test_pomodoro_recover_after_normal_stop_is_noop(db_reset):
    db.set_setting("focus_mode", "0")
    pomodoro.start(25, 5, 4)
    pomodoro.stop()
    db.set_setting("focus_mode", "1")  # потом «Тишину» включили руками
    pomodoro.recover()
    assert db.setting("focus_mode") == "1"

def test_pomodoro_api(client, db_reset):
    r = client.post("/api/pomodoro", json={"action": "start", "focus": 1, "rest": 1, "rounds": 1}).json()
    assert r["phase"] == "focus"
    assert client.get("/api/ui").json()["pomodoro"]["phase"] == "focus"
    assert client.post("/api/pomodoro", json={"action": "stop"}).json()["phase"] == "idle"


# ── дневник ───────────────────────────────────────────────────────────
def test_diary_write_and_list(client, monkeypatch, db_reset):
    from core.llm import base

    class Writer(base.BaseAdapter):
        name = "w"

        async def stream_chat(self, system, messages, temperature=0.8):
            assert "дневник" in messages[0]["content"]
            yield "Сегодня мы говорили про чай, и мне было уютно."

    monkeypatch.setattr(base, "make_adapters", lambda cfg: [Writer()])
    db.execute("DELETE FROM diary") if db.query("SELECT name FROM sqlite_master WHERE name='diary'") else None
    for i in range(3):
        db.add_message("user", f"сообщение {i}")
        db.add_message("assistant", f"ответ {i}")
    entry = asyncio.run(diary.write(dt.date.today()))
    assert entry and "чай" in entry["text"]
    assert asyncio.run(diary.write(dt.date.today())) is None  # повторно без force не пишем
    lst = client.get("/api/diary").json()["entries"]
    assert lst[0]["day"] == dt.date.today().isoformat()
    assert client.post("/api/diary/delete", json={"day": lst[0]["day"]}).json()["ok"]


def test_diary_skips_quiet_day(db_reset):
    if db.query("SELECT name FROM sqlite_master WHERE name='diary'"):
        db.execute("DELETE FROM diary")
    db.execute("DELETE FROM messages")
    assert asyncio.run(diary.write(dt.date.today() - dt.timedelta(days=30))) is None


# ── настройки и прочее ────────────────────────────────────────────────
def test_config_new_keys_roundtrip(client, db_reset):
    r = client.post("/api/config", json={"wake_word": True, "wake_names": "Ая, Аюшка", "avatar_walk": True, "diary_enabled": False})
    assert r.json()["ok"]
    cfg = client.get("/api/config").json()
    assert cfg["wake_word"] and cfg["avatar_walk"] and not cfg["diary_enabled"] and cfg["wake_names"] == "Ая, Аюшка"


def test_stt_status_and_desktop(client):
    st = client.get("/api/stt").json()
    assert st["model"] in st["models"]
    assert "window" in client.get("/api/desktop/foreground").json()


def test_pomodoro_tool_is_free(db_reset):
    from core.safety import gateway, tools

    tools.set_all_tools(False)
    res = asyncio.run(gateway.dispatch({"name": "pomodoro", "args": {"action": "status"}}))
    assert "помодоро" in res


def test_shutdown_requires_token(client, monkeypatch):
    monkeypatch.delenv("AYA_SHUTDOWN_TOKEN", raising=False)
    assert client.post("/api/_shutdown").status_code == 403
    monkeypatch.setenv("AYA_SHUTDOWN_TOKEN", "secret")
    assert client.post("/api/_shutdown", headers={"X-Aya-Token": "wrong"}).status_code == 403


def test_memory_search_stem_fallback(monkeypatch, db_reset):
    from core import longmem

    monkeypatch.setattr(longmem, "_ollama_embed", lambda text: None)
    longmem._BG.submit(lambda: None).result(timeout=30)  # дождаться фоновой записи от прошлых тестов
    db.execute("DELETE FROM memories")
    mid = db.add_message("assistant", "Жил-был серый кот, который любил спать на клавиатуре и мурлыкать.")
    longmem.ingest("assistant", "Жил-был серый кот, который любил спать на клавиатуре и мурлыкать.", mid)
    res = longmem.search("котики")
    assert res and "кот" in res[0]["text"]


def test_core_start_recovers_pomodoro(db_reset):
    import core.main as main
    from fastapi.testclient import TestClient

    db.set_setting("focus_mode", "1")
    db.set_setting("pomodoro_active", "1")
    db.set_setting("pomodoro_prev_focus", "0")
    with TestClient(main.app):
        assert db.setting("focus_mode") == "0"
