import json

import httpx

CLOUD = {"type": "ollama", "base_url": "https://ollama.com", "api_key": "abc", "model": "gemma4:31b-cloud"}


def test_setup_resumes_step(client, db_reset, cfg_tmp):
    client.post("/api/setup", json={"step": 3})
    r = client.get("/api/setup").json()
    assert r["step"] == 3 and r["done"] is False


def test_setup_done_flag(client, db_reset, cfg_tmp):
    client.post("/api/setup", json={"done": True})
    assert client.get("/api/setup").json()["done"] is True


def test_existing_user_skips_wizard(client, db_reset, cfg_tmp):
    cfg_tmp.write_text('{"llm": {"type": "ollama", "base_url": "https://ollama.com", "api_key": "k"}}', encoding="utf-8")
    assert client.get("/api/setup").json()["done"] is True


def test_local_ollama_user_skips_wizard(client, db_reset, cfg_tmp):
    cfg_tmp.write_text('{"llm": {"type": "ollama", "base_url": "http://127.0.0.1:11434", "model": "qwen3"}}', encoding="utf-8")
    assert client.get("/api/setup").json()["done"] is True


def test_llm_check_trims_key_and_saves(client, db_reset, cfg_tmp, fake_http):
    fake_http.get("https://ollama.com/api/tags", json={"models": [{"name": "gemma4:31b-cloud"}]})
    fake_http.post("https://ollama.com/api/chat", json={"message": {"content": "да"}})
    r = client.post("/api/setup/llm/check", json={**CLOUD, "api_key": "  abc\n"}).json()
    assert r["ok"] and "gemma4:31b-cloud" in r["models"]
    assert json.loads(cfg_tmp.read_text("utf-8"))["llm"]["api_key"] == "abc"
    assert "abc" not in json.dumps(client.get("/api/setup").json())


def test_llm_check_bad_key(client, db_reset, cfg_tmp, fake_http):
    fake_http.get("https://ollama.com/api/tags", status=401)
    assert client.post("/api/setup/llm/check", json=CLOUD).json() == {"ok": False, "error": "key", "models": []}
    assert "api_key" not in json.loads(cfg_tmp.read_text("utf-8") or "{}").get("llm", {})  # плохой ключ не сохраняется


def test_llm_check_offline(client, db_reset, cfg_tmp, fake_http):
    fake_http.get("https://ollama.com/api/tags", exc=httpx.ConnectError("no"))
    assert client.post("/api/setup/llm/check", json=CLOUD).json()["error"] == "offline"


def test_llm_check_openai_models(client, db_reset, cfg_tmp, fake_http):
    fake_http.get("https://api.example.com/v1/models", json={"data": [{"id": "m1"}, {"id": "m2"}]})
    fake_http.post("https://api.example.com/v1/chat/completions", json={"choices": []})
    body = {"type": "openai_compatible", "base_url": "https://api.example.com/v1/", "api_key": "k", "model": ""}
    r = client.post("/api/setup/llm/check", json=body).json()
    assert r["ok"] and r["models"] == ["m1", "m2"]


def test_fish_check(client, db_reset, fake_http):
    from core import db
    from core.voice import tts

    fake_http.post(tts.FISH_URL, status=402)
    assert client.post("/api/setup/fish/check", json={"key": " bad "}).json() == {"ok": False, "error": "key"}
    assert db.setting("fish_api_key", "") == ""
    fake_http.post(tts.FISH_URL, status=200)
    assert client.post("/api/setup/fish/check", json={"key": " good\n"}).json() == {"ok": True, "error": ""}
    assert db.setting("fish_api_key", "") == "good"
    assert "good" not in json.dumps(client.get("/api/setup").json())


def test_new_user_default_llm_is_ollama_cloud(tmp_path, monkeypatch):
    import core.main as main

    monkeypatch.setattr(main, "CONFIG_FILE", tmp_path / "config.json")
    llm = main.load_config()["llm"]
    assert (llm["type"], llm["base_url"], llm["model"]) == ("ollama", "https://ollama.com", "gemma4:31b-cloud")


def test_llm_check_bad_key_with_public_model_list(client, db_reset, cfg_tmp, fake_http):
    """На ollama.com список моделей открыт всем — ключ проверяем коротким запросом к модели."""
    fake_http.get("https://ollama.com/api/tags", json={"models": [{"name": "gemma4:31b"}]})
    fake_http.post("https://ollama.com/api/chat", status=401)
    assert client.post("/api/setup/llm/check", json=CLOUD).json() == {"ok": False, "error": "key", "models": []}


def test_llm_check_unknown_model(client, db_reset, cfg_tmp, fake_http):
    fake_http.get("https://ollama.com/api/tags", json={"models": [{"name": "gemma4:31b"}]})
    fake_http.post("https://ollama.com/api/chat", status=404)
    r = client.post("/api/setup/llm/check", json={**CLOUD, "model": "nope"}).json()
    assert r["ok"] is False and r["error"] == "model" and r["models"] == ["gemma4:31b"]


def test_llm_check_openai_probe(client, db_reset, cfg_tmp, fake_http):
    fake_http.get("https://api.example.com/v1/models", json={"data": [{"id": "m1"}]})
    fake_http.post("https://api.example.com/v1/chat/completions", status=401)
    body = {"type": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "bad", "model": "m1"}
    assert client.post("/api/setup/llm/check", json=body).json()["error"] == "key"


def test_open_only_known_pages(client, monkeypatch):
    import core.main as main

    opened = []
    monkeypatch.setattr(main.music, "open_in_browser", lambda url: opened.append(url) or "browser.exe")
    assert client.post("/api/setup/open", json={"target": "ollama_keys"}).json()["ok"]
    assert client.post("/api/setup/open", json={"target": "fish_keys"}).json()["ok"]
    assert client.post("/api/setup/open", json={"target": "https://evil.example"}).status_code == 404
    assert opened == ["https://ollama.com/settings/keys", "https://fish.audio/app/api-keys/"]


def test_llm_check_network_drop_is_offline(client, db_reset, cfg_tmp, fake_http):
    fake_http.get("https://ollama.com/api/tags", exc=httpx.ReadError("reset"))
    assert client.post("/api/setup/llm/check", json=CLOUD).json()["error"] == "offline"
    fake_http.get("https://ollama.com/api/tags", exc=httpx.ReadTimeout("slow"))
    assert client.post("/api/setup/llm/check", json=CLOUD).json()["error"] == "offline"


def test_wizard_resumes_after_key_saved(client, db_reset, cfg_tmp, fake_http):
    """Ключ сохранили на шаге «Модель», мастер закрыли — он должен вернуться, а не считаться пройденным."""
    assert client.get("/api/setup").json()["done"] is False
    fake_http.get("https://ollama.com/api/tags", json={"models": [{"name": "gemma4:31b"}]})
    fake_http.post("https://ollama.com/api/chat", json={"message": {"content": "да"}})
    assert client.post("/api/setup/llm/check", json=CLOUD).json()["ok"]
    client.post("/api/setup", json={"step": 2})
    r = client.get("/api/setup").json()
    assert r["done"] is False and r["step"] == 2
    assert "api_key_tail" not in r["llm"]  # даже хвост ключа наружу не отдаём


def test_new_service_without_key_does_not_reuse_old_key(client, db_reset, cfg_tmp, fake_http):
    """Сменили сервис и не вставили ключ — старый ключ Ollama на чужой адрес не уходит."""
    cfg_tmp.write_text(json.dumps({"llm": CLOUD}), encoding="utf-8")
    fake_http.get("https://api.example.com/v1/models", json={"data": [{"id": "m1"}]})  # список открыт всем
    body = {"type": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "", "model": "m1"}
    assert client.post("/api/setup/llm/check", json=body).json()["error"] == "key"
    assert json.loads(cfg_tmp.read_text("utf-8"))["llm"]["base_url"] == "https://ollama.com"


def test_same_service_empty_key_keeps_saved_key(client, db_reset, cfg_tmp, fake_http):
    cfg_tmp.write_text(json.dumps({"llm": CLOUD}), encoding="utf-8")
    fake_http.get("https://ollama.com/api/tags", json={"models": [{"name": "gemma4:31b"}]})
    fake_http.post("https://ollama.com/api/chat", json={"message": {"content": "да"}})
    r = client.post("/api/setup/llm/check", json={**CLOUD, "api_key": "", "model": "gemma4:31b"}).json()
    assert r["ok"] and json.loads(cfg_tmp.read_text("utf-8"))["llm"]["api_key"] == "abc"
