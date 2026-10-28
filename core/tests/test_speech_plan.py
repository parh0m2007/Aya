import asyncio

from core.voice import speech_plan as sp


def test_splitter_flushes_first_phrase_early():
    s = sp.Splitter()
    assert s.feed("Конечно, сейчас всё открою и покажу. ") == ["Конечно, сейчас всё открою и покажу."]
    # дальше короткие фразы копятся до лимита
    assert s.feed("Готово. Что ещё? ") == []
    assert s.finish() == ["Готово. Что ещё?"]


def test_splitter_short_first_waits_and_skips_json():
    s = sp.Splitter()
    assert s.feed("Ой. ") == []
    assert s.feed('{"action":{"name":"x"}}. ') == []
    assert s.finish() == ["Ой."]


def test_splitter_tail_without_punctuation():
    s = sp.Splitter()
    assert s.feed("Привет") == []
    assert s.finish() == ["Привет"]


def test_plan_synthesizes_in_order_and_fast_first():
    calls = []

    async def synth(text, mood, fast):
        calls.append((text, fast))
        await asyncio.sleep(0)
        return f"/tmp/{len(calls)}.wav"

    async def run():
        plan = sp.SpeechPlan("calm", synth)
        a = plan.feed("Это первая длинная фраза для озвучки. ")
        b = plan.feed("А вот вторая фраза, она тоже довольно длинная, чтобы не склеиться с третьей и уйти отдельно. ")
        c = plan.finish()
        pieces = a + b + c
        paths = [await sp.wait_piece(p.id, 5) for p in pieces]
        return pieces, paths

    pieces, paths = asyncio.run(run())
    assert [p.seq for p in pieces] == list(range(len(pieces)))
    assert paths == [f"/tmp/{i + 1}.wav" for i in range(len(pieces))]
    assert calls[0][1] is True and all(not f for _, f in calls[1:])


def test_plan_cancel_skips_rest():
    async def synth(text, mood, fast):
        await asyncio.sleep(0.05)
        return "x.wav"

    async def run():
        plan = sp.SpeechPlan("calm", synth)
        ps = plan.feed("Первая фраза, которая уже идёт в синтез. ") + plan.finish()
        plan.feed("Ещё что-то. ")
        sp.cancel(plan.id)
        return [await sp.wait_piece(p.id, 5) for p in ps]

    out = asyncio.run(run())
    assert all(x in (None, "x.wav") for x in out)
