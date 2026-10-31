import time

import pytest

from core import db
from core.behavior import proactive
from core.behavior.state import BehaviorState

NOON = time.mktime((2026, 10, 23, 12, 0, 0, 0, 0, -1))


@pytest.fixture
def behavior(db_reset, monkeypatch):
    monkeypatch.setattr(proactive, "rules", lambda *a, **k: "")
    st = BehaviorState()
    st.attention = "own"
    st.energy = 0.1
    st._last_initiative = 0.0
    st._last_user_seen = NOON - 7200
    return st


def test_focus_suppresses_legacy_fallback(behavior):
    db.set_setting("focus_mode", "1")
    assert behavior.initiative_check(now=NOON) == ""


def test_proactive_disabled_suppresses_legacy_fallback(behavior):
    db.set_setting("focus_mode", "0")
    db.set_setting("proactive_enabled", "0")
    assert behavior.initiative_check(now=NOON) == ""


def test_legacy_fallback_works_when_enabled(behavior):
    db.set_setting("focus_mode", "0")
    db.set_setting("proactive_enabled", "1")
    assert behavior.initiative_check(now=NOON) == "welcome_back"


def test_forced_reminder_passes_focus(behavior):
    db.set_setting("focus_mode", "1")
    db.set_setting("initiative_force", "напоминание")
    assert behavior.initiative_check(now=NOON) == "напоминание"
    assert db.setting("initiative_force", "") == ""


def test_forced_tg_respects_focus(behavior):
    """Отчёт о Telegram — проактивный, «Тишина» его откладывает, а не пробивает."""
    db.set_setting("focus_mode", "1")
    db.set_setting("proactive_enabled", "1")
    db.set_setting("initiative_force", "tg:")
    assert behavior.initiative_check(now=NOON) == ""
    assert db.setting("initiative_force", "") == "tg:"

    db.set_setting("focus_mode", "0")
    assert behavior.initiative_check(now=NOON) == "tg:"
    assert db.setting("initiative_force", "") == ""


def test_forced_tg_respects_proactive_disabled(behavior):
    db.set_setting("focus_mode", "0")
    db.set_setting("proactive_enabled", "0")
    db.set_setting("initiative_force", "tg:")
    assert behavior.initiative_check(now=NOON) == ""
    assert db.setting("initiative_force", "") == "tg:"


def test_requested_only_delivers_reminder(behavior):
    """«Ая сама заговаривает» выключено — напоминание, которое человек попросил сам, всё равно приходит."""
    db.set_setting("initiative_force", "reminder:выпить воды")
    assert behavior.initiative_check(now=NOON, requested_only=True) == "reminder:выпить воды"
    assert db.setting("initiative_force", "") == ""


def test_requested_only_delivers_agent_report(behavior):
    db.set_setting("initiative_force", "report:итог готов")
    assert behavior.initiative_check(now=NOON, requested_only=True) == "report:итог готов"


def test_requested_only_skips_own_initiative(behavior, monkeypatch):
    monkeypatch.setattr(proactive, "rules", lambda *a, **k: "morning")
    db.set_setting("focus_mode", "0")
    db.set_setting("proactive_enabled", "1")
    assert behavior.initiative_check(now=NOON, requested_only=True) == ""
    db.set_setting("initiative_force", "tg:")
    assert behavior.initiative_check(now=NOON, requested_only=True) == ""
    assert db.setting("initiative_force", "") == "tg:", "письма ждут, маркер не сжигаем"


def test_second_reminder_waits_instead_of_overwriting(db_reset):
    """Два напоминания в одну минуту: второе раньше затирало первое."""
    from core import tasks

    db.set_setting("initiative_force", "reminder:первое")
    tid = db.add_task("reminder", {"note": "второе"}, time.time() - 1)
    tasks._run_task(tid, "reminder", {"note": "второе"})
    assert db.setting("initiative_force", "") == "reminder:первое"
    status, due_at = db.query("SELECT status, due_at FROM tasks WHERE id=?", (tid,))[0]
    assert status == "pending" and due_at > time.time() + 10


def test_reminder_replaces_telegram_marker(db_reset):
    """Маркер писем Telegram восстанавливается сам (письма лежат в ящике) — его можно занять."""
    from core import tasks

    db.set_setting("initiative_force", "tg:retry:123")
    tid = db.add_task("reminder", {"note": "звонок"}, time.time() - 1)
    tasks._run_task(tid, "reminder", {"note": "звонок"})
    assert db.setting("initiative_force", "") == "reminder:звонок"


def test_api_requested_only(client):
    db.set_setting("initiative_force", "reminder:позвонить маме")
    r = client.get("/api/initiative?requested_only=1").json()
    assert r["topic"] == "reminder:позвонить маме"
    assert "позвонить маме" in r["reply"]


def test_api_requested_only_keeps_quiet(client, monkeypatch):
    monkeypatch.setattr(proactive, "rules", lambda *a, **k: "morning")
    db.set_setting("focus_mode", "0")
    db.set_setting("proactive_enabled", "1")
    assert client.get("/api/initiative?requested_only=1").json()["topic"] == ""
