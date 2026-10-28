"""Звук, который ещё приходит из облака.

Облачный TTS (Fish) отдаёт звук по кусочкам, пока синтезирует. Раньше ядро
ждало весь файл и только потом отдавало его окну — первый звук был через ~3 с.
LiveAudio копит куски по мере прихода, а любой читатель (HTTP-ответ окну)
получает их сразу, даже если подключился позже: читает с начала.

Если облако не ответило и ни одного куска не пришло, производитель кладёт
готовый файл запасного голоса в .path — читатель отдаёт его целиком.
"""
import asyncio


class LiveAudio:
    def __init__(self, media: str = "audio/mpeg"):
        self.media = media
        self.chunks: list[bytes] = []
        self.done = False
        self.path: str | None = None
        self.task: asyncio.Task | None = None
        self._cond = asyncio.Condition()

    async def push(self, data: bytes) -> None:
        if not data:
            return
        async with self._cond:
            self.chunks.append(data)
            self._cond.notify_all()

    async def finish(self, path: str | None = None) -> None:
        async with self._cond:
            if path:
                self.path = path
            self.done = True
            self._cond.notify_all()

    async def ready(self) -> bool:
        """Ждёт первого куска или конца. True — можно стримить, False — смотри .path."""
        async with self._cond:
            await self._cond.wait_for(lambda: bool(self.chunks) or self.done)
            return bool(self.chunks)

    async def wait_closed(self) -> None:
        async with self._cond:
            await self._cond.wait_for(lambda: self.done)

    async def iter_bytes(self):
        i = 0
        while True:
            async with self._cond:
                await self._cond.wait_for(lambda: i < len(self.chunks) or self.done)
                new = self.chunks[i:]
                i = len(self.chunks)
                finished = self.done
            for b in new:
                yield b
            if finished and i >= len(self.chunks):
                return

    def cancel(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
