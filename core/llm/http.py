"""Общий HTTP-клиент для LLM: одно keep-alive соединение на event loop.

Раньше каждый ответ открывал новый httpx.AsyncClient — это лишний TCP+TLS
handshake (100–300 мс к облаку) на каждое сообщение. Клиент привязан к
event loop (фоновые суб-агенты крутят свой loop через asyncio.run), поэтому
кэшируем по циклу.
"""
import asyncio
import contextlib
import weakref

import httpx

_CLIENTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, object]" = weakref.WeakKeyDictionary()

TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
LIMITS = httpx.Limits(max_keepalive_connections=8, keepalive_expiry=120.0)


def get_client():
    """Клиент текущего event loop (создаётся лениво)."""
    loop = asyncio.get_running_loop()
    client = _CLIENTS.get(loop)
    if client is None or getattr(client, "is_closed", False):
        try:
            client = httpx.AsyncClient(timeout=TIMEOUT, limits=LIMITS)
        except TypeError:  # подменённый в тестах клиент без этих аргументов
            client = httpx.AsyncClient()
        _CLIENTS[loop] = client
    return client


@contextlib.asynccontextmanager
async def post_stream(client, url: str, json: dict, headers: dict | None = None):
    """POST с настоящим потоковым чтением тела.

    `client.post()` дочитывает ответ целиком до возврата — токены приходили
    одной пачкой в конце генерации. `client.stream()` отдаёт строки по мере
    появления. Фейковые клиенты в тестах умеют только post — для них фолбэк.
    """
    if hasattr(client, "stream"):
        async with client.stream("POST", url, json=json, headers=headers or {}) as resp:
            yield resp
    else:
        yield await client.post(url, json=json, headers=headers or {})


async def read_error_text(resp, limit: int = 400) -> str:
    """Текст тела ответа (для потокового ответа его нужно сначала дочитать)."""
    try:
        if hasattr(resp, "aread"):
            await resp.aread()
        return str(resp.text)[:limit]
    except Exception:  # noqa: BLE001
        return ""
