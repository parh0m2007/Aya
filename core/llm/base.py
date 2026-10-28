class LLMError(Exception):
    pass


class BaseAdapter:
    name = "base"

    async def chat(self, system: str, messages: list[dict], temperature: float = 0.8) -> str:
        raise NotImplementedError

    async def stream_chat(self, system: str, messages: list[dict], temperature: float = 0.8):
        """Yields token deltas; full text = concatenation. Providers without streaming reuse chat()."""
        raw = await self.chat(system, messages, temperature)
        for chunk in (raw[i : i + 24] for i in range(0, len(raw), 24)):
            yield chunk


async def stream_llm(adapters: list[BaseAdapter], system: str, messages: list[dict], temperature: float = 0.8, first_timeout: float = 20):
    """Stream from first adapter; on failure/empty first token falls through to the next.

    first_timeout — сколько секунд ждать первый токен (облачные модели на холодную
    могут думать дольше 20 с; для фоновых сценариев можно поднять).
    """
    import asyncio

    from . import metrics

    last: Exception | None = None
    for ad in adapters:
        aiter = ad.stream_chat(system, messages, temperature).__aiter__()
        try:
            first = await asyncio.wait_for(aiter.__anext__(), first_timeout)
        except (StopAsyncIteration, Exception) as e:  # noqa: BLE001 — любой сбой = следующий адаптер
            try:
                await aiter.aclose()
            except Exception:
                pass
            if isinstance(e, StopAsyncIteration):
                last = LLMError(f"{ad.name}: пустой ответ")
            else:
                last = e
            metrics.llm_fallback_used(ad.name, str(e) or type(e).__name__)
            continue
        yield first
        try:
            async for chunk in aiter:
                yield chunk
        except Exception as e:
            metrics.llm_error(ad.name, f"mid-stream: {e}")
            raise
        return
    raise last or LLMError("LLM не настроен")


def make_adapters(cfg: dict) -> list[BaseAdapter]:
    """Primary + llm_fallbacks from ~/.aya/config.json."""
    out: list[BaseAdapter] = []
    try:
        out.append(make_adapter(cfg["llm"]))
    except LLMError:
        pass
    for fb in cfg.get("llm_fallbacks", []) or []:
        try:
            out.append(make_adapter(fb))
        except LLMError:
            pass
    return out


def make_adapter(cfg: dict) -> BaseAdapter:
    kind = cfg.get("type", "openai_compatible")
    if kind == "openai_compatible":
        from .openai_compatible import OpenAICompatibleAdapter

        return OpenAICompatibleAdapter(cfg)
    if kind == "ollama":
        from .ollama import OllamaAdapter

        return OllamaAdapter(cfg)
    raise LLMError(f"unknown llm type: {kind}")
