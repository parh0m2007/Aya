"""Озвучка прямо в ядре, пока модель ещё пишет ответ.

Раньше окно получало текст, само резало его на фразы и для каждой отдельно
ходило в /api/speak. Теперь ядро режет поток на фразы и сразу ставит их в
очередь синтеза: первая фраза звучит, пока модель дописывает вторую. Окну
остаётся проиграть готовые куски по порядку (/api/speech/{id}).

Локальный синтез идёт последовательно: GPU не делится между параллельными
F5-инференсами, а первая фраза всегда первая в очереди. Облачному голосу
(parallel > 1) отправляем несколько фраз сразу — пока звучит первая,
следующие уже готовы. Кусок может быть путём к файлу или LiveAudio
(облачный звук, который ещё приходит).
"""
import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field

FIRST_LIMIT = 110
NEXT_LIMIT = 230
EARLY_FIRST = 24
_SENTENCE = re.compile(r"([\s\S]*?[.!?…])[\"»)]?\s")


def group(prev: str, piece: str, limit: int) -> tuple[str, str | None]:
    """(буфер, готовый кусок). Склеиваем короткие фразы до limit символов."""
    if prev and len(prev) + len(piece) + 1 <= limit:
        return f"{prev} {piece}", None
    return piece, (prev or None)


@dataclass
class Piece:
    id: str
    seq: int
    text: str
    fast: bool
    future: "asyncio.Future" = field(repr=False)
    created: float = field(default_factory=time.time)


class Splitter:
    """Чистая логика нарезки потока на куски для озвучки (без asyncio)."""

    def __init__(self):
        self.buf = ""
        self.group = ""
        self.first_done = False

    def _push(self, piece: str, out: list[str]) -> None:
        clean = piece.strip()
        if not clean or clean.startswith('{"action"'):
            return
        buf, ready = group(self.group, clean, FIRST_LIMIT if not self.first_done else NEXT_LIMIT)
        if ready:
            out.append(ready)
            self.first_done = True
        self.group = buf
        # первая фраза — сразу, не дожидаясь второй
        if not self.first_done and len(self.group) >= EARLY_FIRST:
            out.append(self.group)
            self.group = ""
            self.first_done = True

    def feed(self, delta: str) -> list[str]:
        out: list[str] = []
        self.buf += delta
        while True:
            m = _SENTENCE.match(self.buf)
            if not m:
                break
            self._push(m.group(1), out)
            self.buf = self.buf[m.end():]
        return out

    def finish(self) -> list[str]:
        out: list[str] = []
        if self.buf.strip():
            self._push(self.buf, out)
        self.buf = ""
        if self.group.strip():
            out.append(self.group)
        self.group = ""
        return out


PIECES: dict[str, Piece] = {}
PLANS: dict[str, "SpeechPlan"] = {}
_TTL = 600


def _gc() -> None:
    now = time.time()
    for pid in [k for k, p in PIECES.items() if now - p.created > _TTL]:
        PIECES.pop(pid, None)
    for k in [k for k, p in PLANS.items() if now - p.created > _TTL]:
        PLANS.pop(k, None)


class SpeechPlan:
    def __init__(self, mood: str, synth, fast_first: bool = True, parallel: int = 1):
        _gc()
        self.id = uuid.uuid4().hex[:12]
        self.mood = mood
        self.synth = synth  # async (text, mood, fast) -> path | LiveAudio | None
        self.fast_first = fast_first
        self.parallel = max(1, int(parallel))
        self.lives: list = []
        self.split = Splitter()
        self.seq = 0
        self.cancelled = False
        self.created = time.time()
        self.queue: asyncio.Queue[Piece | None] = asyncio.Queue()
        self.worker = asyncio.create_task(self._work())
        PLANS[self.id] = self

    def _make(self, text: str) -> Piece:
        fut = asyncio.get_running_loop().create_future()
        p = Piece(id=f"{self.id}-{self.seq}", seq=self.seq, text=text, fast=self.fast_first and self.seq == 0, future=fut)
        self.seq += 1
        PIECES[p.id] = p
        self.queue.put_nowait(p)
        return p

    def feed(self, delta: str) -> list[Piece]:
        if self.cancelled:
            return []
        return [self._make(t) for t in self.split.feed(delta)]

    def finish(self) -> list[Piece]:
        if self.cancelled:
            return []
        out = [self._make(t) for t in self.split.finish()]
        self.queue.put_nowait(None)
        return out

    def cancel(self) -> None:
        self.cancelled = True
        for p in PIECES.values():
            if p.id.startswith(self.id + "-") and not p.future.done():
                p.future.set_result(None)
        for live in self.lives:
            live.cancel()
        self.queue.put_nowait(None)

    async def _work(self) -> None:
        sem = asyncio.Semaphore(self.parallel)
        running: set[asyncio.Task] = set()
        while True:
            p = await self.queue.get()
            if p is None:
                break
            if p.future.done():
                continue
            if self.cancelled:
                p.future.set_result(None)
                continue
            await sem.acquire()  # фразы стартуют строго по порядку
            if self.cancelled:
                sem.release()
                if not p.future.done():
                    p.future.set_result(None)
                continue
            t = asyncio.create_task(self._one(p, sem))
            running.add(t)
            t.add_done_callback(running.discard)
        if running:
            await asyncio.gather(*running, return_exceptions=True)

    async def _one(self, p: Piece, sem: asyncio.Semaphore) -> None:
        try:
            res = await self.synth(p.text, self.mood, p.fast)
            if self.cancelled and hasattr(res, "cancel"):
                res.cancel()
            if not p.future.done():
                p.future.set_result(res)
            if hasattr(res, "wait_closed"):
                # облачный звук ещё идёт: слот занят, пока он не докачается
                self.lives.append(res)
                await res.wait_closed()
        except Exception:  # noqa: BLE001 — кусок пропускаем, следующий озвучится
            if not p.future.done():
                p.future.set_result(None)
        finally:
            sem.release()


def cancel(plan_id: str) -> bool:
    plan = PLANS.get(plan_id)
    if not plan:
        return False
    plan.cancel()
    return True


async def wait_piece(piece_id: str, timeout: float = 180) -> str | None:
    p = PIECES.get(piece_id)
    if p is None:
        return None
    try:
        return await asyncio.wait_for(asyncio.shield(p.future), timeout)
    except asyncio.TimeoutError:
        return None
