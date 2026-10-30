import json

from core import persona, prompting
from core.safety import tools


def test_default_profile_not_configured(db_reset):
    p = persona.load()
    assert p["name"] == "Ая" and p["configured"] is False
    # не настроенная персона — старый промпт по умолчанию
    assert "Ты — Ая" in prompting.build_system_prompt({"mood": "calm"})


def test_save_builds_prompt_from_traits(db_reset):
    persona.save({
        "name": "Мику", "user_name": "Рома", "role": "mentor", "address": "вы",
        "traits": {"humor": 90, "warmth": 10}, "tags": ["любит чай"],
    })
    text = prompting.build_system_prompt({"mood": "joyful"})
    assert "Ты — Мику" in text
    assert "наставница" in text
    assert "Шутишь почти в каждой реплике" in text
    assert "Холодная" in text
    assert "на «вы»" in text
    assert "Рома" in text and "любит чай" in text
    assert "радостное" in text


def test_every_slider_step_changes_character():
    """Тестеры: «двигаю ползунки — характер не меняется». Каждая из пяти ступеней — свой текст."""
    for trait in persona.TRAITS:
        texts = set()
        for v in (10, 30, 50, 70, 90):
            p = persona.normalize({"traits": {trait: v}})
            texts.add(tuple(persona.character_lines(p)))
        assert len(texts) == 5, trait


def test_presets_differ_clearly():
    """Любые два готовых характера расходятся минимум в трёх чертах из пяти."""
    lines = {pr["id"]: persona.character_lines(persona.normalize(pr["patch"])) for pr in persona.PRESETS}
    ids = list(lines)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            diff = sum(x != y for x, y in zip(lines[a], lines[b]))
            assert diff >= 3, (a, b, diff)


def test_character_reminder_is_last(db_reset):
    """Характер повторяется в самом конце — после длинного списка инструментов
    и важнее манеры из прошлых реплик."""
    persona.save({"traits": {"humor": 95, "warmth": 5}})
    text = prompting.build_system_prompt({"mood": "calm"}, tools="ИНСТРУМЕНТЫ")
    tail = text[text.index("ИНСТРУМЕНТЫ"):]
    assert "Помни свой характер" in tail and "постоянно шутишь" in tail and "холодная" in tail


def test_unconfigured_persona_has_no_reminder(db_reset):
    text = prompting.build_system_prompt({"mood": "calm"}, tools="ИНСТРУМЕНТЫ")
    assert text.endswith("ИНСТРУМЕНТЫ") and "Помни свой характер" not in text


def test_normalize_clamps_and_filters():
    p = persona.normalize({"name": "x" * 99, "traits": {"humor": 999, "warmth": "abc"}, "role": "hacker", "junk": 1})
    assert len(p["name"]) == 24
    assert p["traits"]["humor"] == 100 and p["traits"]["warmth"] == 50
    assert p["role"] == "companion" and "junk" not in p


def test_modules_apply_only_changes(db_reset):
    tools.allow_tool("run_cmd", True)
    persona.save({"modules": {"actions": True, "initiative": False, "memory": False}})
    assert tools.is_tool_enabled("open_app") and tools.is_tool_enabled("web_search")
    assert persona.load()["modules"]["initiative"] is False
    from core import db, longmem

    assert db.setting("aya_initiative") == "0" and not longmem.enabled()
    # сохранение имени не трогает разрешения
    tools.allow_tool("open_app", False)
    persona.save({"name": "Ая-2"})
    assert not tools.is_tool_enabled("open_app")
    assert tools.is_tool_enabled("run_cmd")
    # выключение модуля «действия» снимает только безопасный набор
    tools.allow_tool("open_app", True)
    persona.save({"modules": {"actions": False}})
    assert not tools.is_tool_enabled("web_search")
    assert tools.is_tool_enabled("run_cmd")


def test_persona_api(client, db_reset):
    v = client.get("/api/persona").json()
    assert {"profile", "roles", "presets"} <= set(v)
    assert any(p["id"] == "tsun" for p in v["presets"])
    r = client.post("/api/persona", json={"profile": {"name": "Юки"}}).json()
    assert r["ok"] and r["profile"]["name"] == "Юки"
    assert client.get("/api/config").json()["persona_name"] == "Юки"
    prev = client.post("/api/persona/preview", json={"profile": {"name": "Тест", "length": "long"}}).json()
    assert "Ты — Тест" in prev["prompt"] and "развёрнуто" in prev["prompt"]
    assert persona.load()["name"] == "Юки"  # превью не сохраняет
    assert client.post("/api/persona/reset").json()["profile"]["configured"] is False


def test_ui_and_react(client, db_reset):
    ui = client.get("/api/ui").json()
    assert {"state", "config", "focus"} <= set(ui)
    r = client.post("/api/react", json={"kind": "pat"}).json()
    assert r["text"] and r["mood"] == "joyful"


def test_stream_plain_text_is_streamed_before_end(client, monkeypatch, db_reset):
    import core.main as main
    from core.llm.base import BaseAdapter

    class Talky(BaseAdapter):
        name = "talky"

        async def stream_chat(self, *a, **k):
            for c in ["Привет", ", как ", "дела? Ура!"]:
                yield c

    monkeypatch.setattr(main, "make_adapters", lambda cfg: [Talky()])
    with client.stream("POST", "/api/chat/stream", json={"text": "хей"}) as r:
        lines = [json.loads(ln) for ln in r.iter_lines() if ln.strip()]
    deltas = [x["delta"] for x in lines if "delta" in x]
    assert deltas[0] == "Привет"  # однострочный ответ идёт по кусочкам
    assert lines[-1]["done"] and lines[-1]["reply"] == "Привет, как дела? Ура!"


def test_stream_without_model_is_ndjson(client, monkeypatch, db_reset):
    import core.main as main

    monkeypatch.setattr(main, "make_adapters", lambda cfg: [])
    with client.stream("POST", "/api/chat/stream", json={"text": "хей"}) as r:
        lines = [json.loads(ln) for ln in r.iter_lines() if ln.strip()]
    assert lines[-1]["done"] is True and "Модель не настроена" in lines[-1]["reply"]
