import json as _json
import os
import time as _time

import httpx  # noqa: F401 — тесты подменяют httpx.AsyncClient через этот модуль

from . import metrics
from .base import BaseAdapter, LLMError
from .http import get_client, post_stream, read_error_text


class OllamaAdapter(BaseAdapter):
    name = "ollama"

    def __init__(self, cfg: dict):
        self.base_url = cfg.get("base_url", "http://127.0.0.1:11434").rstrip("/")
        self.model = cfg["model"]
        self.api_key = cfg.get("api_key") or os.environ.get("AYA_OLLAMA_API_KEY", "")
        self.num_predict = int(cfg.get("max_tokens", 300))
        # держим модель в памяти между репликами: холодная загрузка — секунды
        self.keep_alive = cfg.get("keep_alive", "30m")

    async def chat(self, system: str, messages: list[dict], temperature: float = 0.8) -> str:
        return "".join([c async for c in self.stream_chat(system, messages, temperature)])

    async def stream_chat(self, system: str, messages: list[dict], temperature: float = 0.8):
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": True,
            "options": {"temperature": temperature, "num_predict": self.num_predict},
        }
        if "127.0.0.1" in self.base_url or "localhost" in self.base_url:
            payload["keep_alive"] = self.keep_alive  # только локальный сервер: облаку поле не нужно
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        t0 = _time.perf_counter()
        first_at: float | None = None
        status = 0
        head = ""
        try:
            client = get_client()
            async with post_stream(client, f"{self.base_url}/api/chat", payload, headers) as r:
                status = r.status_code
                if status >= 400:
                    body = await read_error_text(r, 200)
                    raise LLMError(f"HTTP {status}: {body}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    if len(head) < 240:
                        head += line[:120] + " | "
                    try:
                        data = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    err = data.get("error")
                    if err:
                        raise LLMError(f"поток: {str(err)[:140]}")
                    content = data.get("message", {}).get("content", "")
                    if content:
                        if first_at is None:
                            first_at = (_time.perf_counter() - t0) * 1000
                            metrics.llm_first(self.model, round(first_at, 1))
                        yield content
                    if data.get("done"):
                        break
            if first_at is None:
                raise LLMError(
                    f"пустой поток (status={status}, начало: {head.strip()[:200] or 'тело пустое'})"
                )
        except Exception as e:
            metrics.llm_error(self.model, str(e))
            raise
        finally:
            if first_at is not None:
                metrics.llm_done(self.model, round((_time.perf_counter() - t0) * 1000, 1))
