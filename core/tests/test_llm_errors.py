from core.llm.base import friendly_error


def test_paid_model_tells_how_to_fix():
    t = friendly_error("HTTP 402: payment required")
    assert "платн" in t and "OpenRouter" in t and "Пройти настройку заново" in t


def test_daily_limit():
    t = friendly_error('HTTP 429: {"error": "rate limit exceeded: free-models-per-day"}')
    assert "50" in t and "завтра" in t


def test_bad_key():
    assert "Ключ" in friendly_error("HTTP 401: invalid api key")


def test_other_errors_stay_as_is():
    assert friendly_error("поток: обрыв") == "Ошибка LLM: поток: обрыв"


def test_chat_reply_is_friendly(client, db_reset, cfg_tmp, monkeypatch):
    import core.main as main
    from core.llm.base import LLMError

    async def broken(*_a, **_k):
        raise LLMError("HTTP 402: payment required")
        yield  # noqa: unreachable — генератор

    cfg_tmp.write_text('{"llm": {"type": "openai_compatible", "base_url": "https://x/v1", "model": "m", "api_key": "k"}}', encoding="utf-8")
    monkeypatch.setattr(main, "stream_llm", broken)
    reply = client.post("/api/chat", json={"text": "привет"}).json()["reply"]
    assert "платн" in reply and "HTTP 402" not in reply
