import random
import threading
import time

from .. import db
from . import proactive

MOODS = ["calm", "joyful", "thoughtful"]
# настроения, которые может задать реплика (эвристика по тексту ответа)
REPLY_MOODS = MOODS + ["sad", "surprise"]
ACTIVITIES = ["rest", "talk", "read", "sketch", "task"]

QUIET_HOURS = (23, 8)


class BehaviorState:
    def __init__(self):
        self.mood = "calm"
        self.energy = 0.6
        self.engagement = 0.5
        self.activity = "rest"
        self.attention = "own"
        self._lock = threading.Lock()
        self._last_initiative = 0.0
        self._last_user_seen = time.time()
        self._quiet_from, self._quiet_to = QUIET_HOURS
        self._drift_thread = threading.Thread(target=self._drift_loop, daemon=True)
        self._drift_thread.start()


    def snapshot(self) -> dict:
        with self._lock:
            return {
                "mood": self.mood,
                "energy": round(self.energy, 2),
                "engagement": round(self.engagement, 2),
                "activity": self.activity,
                "attention": self.attention,
            }

    def on_user_message(self):
        with self._lock:
            self.attention = "user"
            self.activity = "talk"
            self.engagement = min(1.0, self.engagement + 0.2)
            self._last_user_seen = time.time()

    def on_reply(self, mood_hint: str = ""):
        with self._lock:
            if mood_hint in REPLY_MOODS:
                self.mood = mood_hint
                self._mood_set_at = time.time()

    def drift(self):
        with self._lock:
            self.energy = min(1.0, self.energy + 0.02) if random.random() < 0.6 else max(0.1, self.energy - 0.01)
            self.engagement = max(0.1, self.engagement - 0.02)
            idled = time.time() - self._last_user_seen
            if self.attention == "user" and idled > 90:
                self.attention = "own"
                self.activity = random.choice(["rest", "read", "sketch"])
                db.log_event("behavior", f"switched to own activity: {self.activity}")
            # настроение от реплики держится пару минут, потом снова дрейфует
            if random.random() < 0.05 and self.attention != "user" and time.time() - getattr(self, "_mood_set_at", 0) > 120:
                self.mood = random.choices(MOODS, weights=[0.6, 0.25, 0.15])[0]

    def initiative_check(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        forced = db.setting("initiative_force", "")
        if forced.startswith("tg:retry:"):
            # отложенный повтор недоставленных писем: ждём, маркер не сбрасываем
            try:
                due = float(forced.rsplit(":", 1)[1])
            except (ValueError, IndexError):
                due = 0.0
            if now < due:
                return ""
            forced = "tg:"
        if forced:
            # Telegram-отчёт — проактивный; «Тишина» откладывает его, но не теряет.
            if forced.startswith("tg:") and (
                db.setting("focus_mode", "0") == "1"
                or db.setting("proactive_enabled", "1") != "1"
            ):
                return ""
            db.set_setting("initiative_force", "")
            with self._lock:
                self._last_initiative = now
            return forced

        # «Тишина» и выключенная проактивность глушат и legacy-фолбэки.
        if db.setting("focus_mode", "0") == "1" or db.setting("proactive_enabled", "1") != "1":
            return ""

        # Проактивность 2.0: умные касания со своими кулдаунами
        topic = proactive.rules(now)
        if topic:
            with self._lock:
                self._last_initiative = now
            return topic
        lt = time.localtime(now)
        if self._quiet_from <= lt.tm_hour or lt.tm_hour < self._quiet_to:
            return ""
        if self.attention == "user":
            return ""
        if now - self._last_initiative < 600:
            return ""
        topics = []
        if self._last_user_seen and now - self._last_user_seen > 3600:
            topics.append("welcome_back")
        if self.energy > 0.8 and random.random() < 0.15:
            topics.append("light_ping")
        if topics:
            self._last_initiative = now
            return topics[0]
        return ""

    def _drift_loop(self):
        while True:
            time.sleep(20)
            self.drift()
