import asyncio

import core.llm.metrics as m
from core.llm.base import LLMError, stream_llm


class FakeAdapter:
    def __init__(self, name: str, fail: bool = False):
        self.name = name
        self.fail = fail

    async def stream_chat(self, system, messages, temperature=0.8):
        if self.fail:
            raise RuntimeError("boom " + self.name)
        yield "Х"


async def _gather(ads):
    return "".join([c async for c in stream_llm(ads, "sys", [])])


def test_stream_llm_falls_to_next():
    out = asyncio.run(_gather([FakeAdapter("a", fail=True), FakeAdapter("b"), FakeAdapter("c", fail=True)]))
    assert out == "Х"


def test_stream_llm_all_fail_raises():
    try:
        asyncio.run(_gather([FakeAdapter("a", fail=True)]))
        assert False
    except RuntimeError as e:
        assert "boom" in str(e)


class SlowAdapter:
    name = "slow"

    async def stream_chat(self, system, messages, temperature=0.8):
        await asyncio.sleep(0.3)
        yield "ок"


def test_stream_llm_first_timeout():
    """Первый токен дольше first_timeout — фолбэк; с запасом — успех."""
    async def run(timeout):
        return "".join([c async for c in stream_llm([SlowAdapter()], "s", [], first_timeout=timeout)])

    assert asyncio.run(run(2)) == "ок"
    try:
        asyncio.run(run(0.05))
        assert False
    except (asyncio.TimeoutError, LLMError):
        pass


def test_metrics_last_fallback(db_reset):
    m.clear()
    assert m.last_fallback() is None
    m.llm_fallback_used("ollama", "TimeoutError")
    assert m.last_fallback() == "ollama: TimeoutError"
    assert "llm_last_fallback" in m.snapshot()


def test_ollama_stream_diagnostics(monkeypatch):
    """Пустой поток и ошибки внутри потока поднимают LLMError с деталями."""
    from core.llm import ollama as ol

    class FakeResp:
        status_code = 200

        def __init__(self, lines):
            self._lines = lines

        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            for line in self._lines:
                yield line

    class FakeClient:
        def __init__(self, lines):
            self._resp = FakeResp(lines)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return self._resp

    def make(lines):
        return lambda *a, **k: FakeClient(lines)

    ad = ol.OllamaAdapter({"base_url": "https://example.test", "model": "m", "api_key": "k"})

    async def consume():
        return [c async for c in ad.stream_chat("s", [], 0.8)]

    monkeypatch.setattr(ol.httpx, "AsyncClient", make(['{"model":"m","done":true}']))
    try:
        asyncio.run(consume())
        assert False
    except LLMError as e:
        assert "пустой поток" in str(e)
        assert "done" in str(e)

    monkeypatch.setattr(ol.httpx, "AsyncClient", make(['{"error":"model not found"}']))
    try:
        asyncio.run(consume())
        assert False
    except LLMError as e:
        assert "model not found" in str(e)

    lines = ['{"message":{"content":"при"},"done":false}', '{"message":{"content":"вет"},"done":true}']
    monkeypatch.setattr(ol.httpx, "AsyncClient", make(lines))
    assert asyncio.run(consume()) == ["при", "вет"]


def test_metrics_snapshot_keys(db_reset):
    m.clear()
    m.llm_first("x", 123.0)
    m.llm_done("x", 900.0)
    m.llm_error("x", "timeout")
    m.tts_record("f3", True, 150.0)
    snap = m.snapshot()
    for k in ("llm_first_ms_avg", "llm_errors", "llm_requests", "tts_total", "tts_cache_pct", "llm_last_error"):
        assert k in snap
    assert snap["llm_errors"] == 1
    assert snap["tts_cache_pct"] == 100.0
