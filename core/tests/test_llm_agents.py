from core.safety import gateway


def test_agents_adapters_prefers_llm_agents():
    cfg = {
        "llm": {"type": "openai_compatible", "base_url": "http://main", "model": "main-m", "api_key": "k1"},
        "llm_agents": {"type": "openai_compatible", "base_url": "http://agents", "model": "atri", "api_key": "k2"},
    }
    ads = gateway.agents_adapters(cfg)
    assert [a.model for a in ads] == ["atri"]


def test_agents_adapters_falls_back_to_main():
    cfg = {"llm": {"type": "openai_compatible", "base_url": "http://main", "model": "main-m", "api_key": "k1"}}
    ads = gateway.agents_adapters(cfg)
    assert [a.model for a in ads] == ["main-m"]


def test_agents_adapters_broken_agents_config(client):
    cfg = {"llm": {"type": "openai_compatible", "base_url": "http://main", "model": "m", "api_key": "k"}, "llm_agents": {}}
    ads = gateway.agents_adapters(cfg)
    assert [a.model for a in ads] == ["m"]


def test_llm_api_get_post(client, tmp_path, monkeypatch):
    import core.main as main

    fake = tmp_path / "config.json"
    fake.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main, "CONFIG_FILE", fake)
    r = client.post(
        "/api/llm",
        json={
            "llm": {"base_url": "http://x", "model": "abc", "api_key": "sk-test-123456789"},  # public-ok: выдуманное значение
            "llm_agents": {"base_url": "https://discovery-api.intern-ai.org.cn/v1", "model": "Atri-Dawn", "api_key": "sk-shhh"},
        },
    )
    assert r.status_code == 200
    d = r.json()
    assert d["llm"]["model"] == "abc"
    assert d["llm"]["api_key_set"] is True
    assert "sk-test" not in d["llm"]["api_key_tail"]
    assert d["llm_agents"]["model"] == "Atri-Dawn"

    g = client.get("/api/llm")
    jd = g.json()
    assert jd["llm"]["base_url"] == "http://x"
    assert jd["llm_agents"]["base_url"] == "https://discovery-api.intern-ai.org.cn/v1"
    assert jd["llm"]["api_key_set"] is True
    # ключ не отдаётся целиком
    assert "sk-test-123456789" not in g.text
    assert "sk-shhh" not in g.text
