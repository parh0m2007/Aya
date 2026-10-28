"""Экранное зрение (моки) и навыки-плагины."""
import json
import os
import sys

from core.safety import skills, tools, vision


def test_skills_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(skills, "skills_root", lambda: str(tmp_path / "skills"))
    skills.ensure_root()
    names = [s["name"] for s in skills.list_skills()]
    assert "primer" in names  # ensure_root создал пример

    d = tmp_path / "skills" / "code-review"
    d.mkdir(parents=True)
    (d / "skill.json").write_text(
        json.dumps({"name": "code-review", "description": "ревью кода", "instructions": "читай core/*.py и перечисли проблемы"}),
        encoding="utf-8",
    )
    found = {s["name"]: s["description"] for s in skills.list_skills()}
    assert found["code-review"] == "ревью кода"

    instr = skills.load_skill("code-review")
    assert "НАВЫК" in instr and "читай core/*.py" in instr
    assert "нет такого навыка" in skills.load_skill("nope")

    bad = tmp_path / "skills" / "broken"
    bad.mkdir()
    (bad / "skill.json").write_text("{oops", encoding="utf-8")
    assert "broken" not in [s["folder"] for s in skills.list_skills()]


def test_skills_instructions_block(monkeypatch, tmp_path):
    monkeypatch.setattr(skills, "skills_root", lambda: str(tmp_path / "skills"))
    block = skills.tool_instructions()
    assert "list_skills" in block
    skills.ensure_root()
    block2 = skills.tool_instructions()
    assert "primer" in block2 and "load_skill" in block2


def test_screen_look_pipeline(monkeypatch, tmp_path):
    """Мок: снимок→окна→OCR; реальная склейка describe()."""
    from PIL import Image, ImageGrab

    monkeypatch.setattr(vision, "screens_dir", lambda: str(tmp_path))
    monkeypatch.setattr(ImageGrab, "grab", lambda: Image.new("RGB", (64, 64), color=(40, 40, 60)))
    monkeypatch.setattr(vision, "window_titles", lambda limit=12: ["Блокнот — test.txt", "Chrome"])
    monkeypatch.setattr(vision, "ocr_text", lambda path, limit=1800: "Привет, это тестовый текст")
    monkeypatch.setattr(vision, "vision_config", lambda: {})

    out = vision.describe()
    assert "Блокнот" in out and "Chrome" in out
    assert "тестовый текст" in out
    assert os.listdir(str(tmp_path)), "скриншот не сохранён"


def test_vlm_describe_payload(monkeypatch, tmp_path):
    from PIL import Image

    img = tmp_path / "x.png"
    Image.new("RGB", (32, 32), color=(10, 10, 30)).save(img)
    calls = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "На экране калькулятор"}}]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            calls.update({"url": url, "json": json, "headers": headers})
            return FakeResp()

    fake_httpx = type("m", (), {"Client": FakeClient})
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    out = vision.vlm_describe(str(img), {"base_url": "https://api.x/v1", "model": "vlm-1", "api_key": "k"})
    assert out == "На экране калькулятор"
    assert calls["url"].endswith("/chat/completions")
    payload = calls["json"]
    assert payload["model"] == "vlm-1"
    content = payload["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert calls["headers"]["Authorization"] == "Bearer k"


def test_screen_look_registered(conftest_ws):
    assert "screen_look" in tools.KNOWN_ACTIONS
    tools.allow_tool("screen_look", True)
    assert tools.is_tool_enabled("screen_look")


def test_list_load_skill_free(monkeypatch, conftest_ws, tmp_path):
    """list_skills/load_skill доступны без per-tool разрешения (FREE)."""
    from core.safety import gateway

    monkeypatch.setattr(gateway.skills, "skills_root", lambda: str(tmp_path / "sk2"))
    skills.ensure_root()
    res = tools.run_action("list_skills", {})
    assert "primer" in res
    res2 = tools.run_action("load_skill", {"name": "primer"})
    assert "НАВЫК" in res2 and "пример навыка" in res2


def test_instructions_mention_new_tools(monkeypatch):
    from core.safety import gateway

    monkeypatch.setattr(gateway.skills, "tool_instructions", lambda: "- list_skills {}")
    text = gateway.build_tool_instructions()
    assert "screen_look" in text
    assert "delegate_bg" in text
    assert "list_skills" in text
