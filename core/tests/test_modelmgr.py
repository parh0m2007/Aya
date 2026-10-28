import time

import core.voice.modelmgr as mm


def test_idle_minutes_default_and_parse(db_reset):
    assert mm.idle_minutes() == 5
    import core.db as db

    db.set_setting(mm.GLOBAL_KEY, "10")
    assert mm.idle_minutes() == 10
    db.set_setting(mm.GLOBAL_KEY, "garbage")
    assert mm.idle_minutes() == 5


def test_touch_and_unload(db_reset):
    calls = []

    mm.register("fake", lambda: calls.append(1) or True)
    mm.touch("fake")
    st = mm.status()
    assert "fake" in st and st["fake"]["idle_s"] < 5

    done = mm.unload_vram(["fake"])
    assert done == ["fake"] and len(calls) == 1
    # второй раз — уже выгружено
    done = mm.unload_vram(["fake"])
    assert done == ["fake"] and len(calls) == 2


def test_unload_failing_plan_never_crashes(db_reset):
    def boom():
        raise RuntimeError("boom")

    mm.register("boom", boom)
    assert mm.unload_vram(["boom"]) != ["boom"]
