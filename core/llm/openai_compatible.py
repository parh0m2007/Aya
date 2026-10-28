import asyncio
import json as _json
import os
import time as _time

import httpx  # noqa: F401 — тесты подменяют httpx.AsyncClient через этот модуль

from . import metrics
from .base import BaseAdapter, LLMError
from .http import get_client, post_stream, read_error_text


class OpenAICompatibleAdapter(BaseAdapter):
    name = "openai_compatible"

    def __init__(self, cfg: dict):
        self.base_url = cfg["base_url"].rstrip("/")
        self.model = cfg["model"]
        self.api_key = cfg.get("api_key") or os.environ.get("AYA_LLM_API_KEY", "")
        self.max_tokens = int(cfg.get("max_tokens", 300))
        if not self.api_key:
            raise LLMError("api_key not set (env AYA_LLM_API_KEY)")

    async def chat(self, system: str, messages: list[dict], temperature: float = 0.8) -> str:
        return "".join([c async for c in self.stream_chat(system, messages, temperature)])

    async def stream_chat(self, system: str, messages: list[dict], temperature: float = 0.8):
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        t0 = _time.perf_counter()
        first_at: float | None = None
        try:
            client = get_client()
            for attempt in range(2):
                async with post_stream(client, f"{self.base_url}/chat/completions", payload, headers) as r:
                    if r.status_code >= 400:
                        text = (await read_error_text(r)).lower()
                        busy = "busy" in text or "rate limit" in text or "channel" in text
                        if r.status_code in (400, 429, 500, 502, 503) and busy and attempt < 1:
                            await asyncio.sleep(4)
                            continue
                        raise LLMError(f"HTTP {r.status_code}: {text[:200]}")
                    async for line in r.aiter_lines():
                        line = line.removeprefix("data: ").strip()
                        if not line or line == "[DONE]":
                            continue
                        try:
                            data = _json.loads(line)
                        except _json.JSONDecodeError:
                            continue
                        choices = data.get("choices") or []
                        if not choices:
                            continue
                        delta = (choices[0].get("delta") or {}).get("content") or ""
                        if delta:
                            if first_at is None:
                                first_at = (_time.perf_counter() - t0) * 1000
                                metrics.llm_first(self.model, round(first_at, 1))
                            yield delta
                    return
            raise LLMError("no success on retry")
        except LLMError as e:
            metrics.llm_error(self.model, str(e))
            raise
        except Exception as e:
            metrics.llm_error(self.model, str(e))
            raise
        finally:
            if first_at is not None:
                metrics.llm_done(self.model, round((_time.perf_counter() - t0) * 1000, 1))
