import asyncio
import contextlib

from core.llm import http as llm_http
from core.llm import ollama as ol
from core.llm import openai_compatible as oc


class StreamResp:
    status_code = 200

    def __init__(self, lines, log):
        self._lines, self._log = lines, log

    async def aiter_lines(self):
        for line in self._lines:
            self._log.append(("line", line))
            yield line


class StreamClient:
    """Клиент, который умеет только stream(): post() недоступен — проверяем, что
    адаптеры читают тело по мере прихода, а не целиком."""

    def __init__(self, lines, log):
        self._lines, self._log = lines, log

    @contextlib.asynccontextmanager
    async def stream(self, method, url, **kw):
        self._log.append(("open", method))
        yield StreamResp(self._lines, self._log)


def test_ollama_uses_real_stream(monkeypatch):
    log = []
    lines = ['{"message":{"content":"А"},"done":false}', '{"message":{"content":"я"},"done":true}']
    monkeypatch.setattr(ol, "get_client", lambda: StreamClient(lines, log))
    ad = ol.OllamaAdapter({"base_url": "https://x.test", "model": "m"})

    async def first_then_rest():
        it = ad.stream_chat("s", []).__aiter__()
        first = await it.__anext__()
        seen = list(log)
        rest = [c async for c in it]
        return first, seen, rest

    first, seen, rest = asyncio.run(first_then_rest())
    assert first == "А" and rest == ["я"]
    assert seen == [("open", "POST"), ("line", lines[0])]  # второй кусок ещё не прочитан


def test_openai_uses_real_stream(monkeypatch):
    log = []
    lines = ['data: {"choices":[{"delta":{"content":"При"}}]}', 'data: {"choices":[{"delta":{"content":"вет"}}]}', "data: [DONE]"]
    monkeypatch.setattr(oc, "get_client", lambda: StreamClient(lines, log))
    ad = oc.OpenAICompatibleAdapter({"base_url": "https://x.test/v1", "model": "m", "api_key": "k"})
    out = asyncio.run((lambda: ad.chat("s", []))())
    assert out == "Привет" and log[0] == ("open", "POST")


def test_client_is_shared_per_loop():
    async def two():
        return llm_http.get_client() is llm_http.get_client()

    assert asyncio.run(two())
