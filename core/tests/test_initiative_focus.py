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
