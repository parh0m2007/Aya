"""Разбор ответа модели: JSON-действие или обычная речь, плюс настроение реплики."""
import json
import re

_FENCE = re.compile(r"^```(?:json)?\s*", re.IGNORECASE)


def _strip_fence(text: str) -> str:
    s = text.lstrip()
    if s.startswith("```"):
        s = _FENCE.sub("", s, count=1)
        s = s.replace("```", "", 1)
    return s


def parse_action(raw: str) -> tuple[dict | None, str]:
    """(action, хвост). action = {"name", "args"} если ответ начинается с JSON-действия.

    Раньше json.loads(raw) падал, если модель дописывала текст после JSON, —
    и пользователь видел сырой JSON. raw_decode читает только первый объект.
    """
    s = _strip_fence(raw)
    if not s.startswith("{"):
        return None, raw
    try:
        obj, end = json.JSONDecoder().raw_decode(s)
    except json.JSONDecodeError:
        return None, raw
    action = obj.get("action") if isinstance(obj, dict) else None
    if not isinstance(action, dict) or not action.get("name"):
        return None, raw
    if not isinstance(action.get("args"), dict):
        action["args"] = {}
    return action, s[end:].replace("```", "").strip()


class StreamFilter:
    """Решает как можно раньше, показывать ли поток пользователю.

    Обычный текст отдаётся сразу с первого токена (раньше ждали перевода
    строки — однострочные ответы не стримились вовсе, и озвучка стартовала
    только после полной генерации). Буферизуем, только если ответ похож на
    JSON-действие: начинается с «{» или с ```.
    """

    def __init__(self):
        self.head = ""
        self.decided = False
        self.hidden = False  # поток — это действие, наружу не показываем

    def _show(self) -> str:
        self.decided = True
        out, self.head = self.head, ""
        return out

    def _hide(self) -> str:
        self.decided = True
        self.hidden = True
        self.head = ""
        return ""

    def feed(self, chunk: str) -> str:
        if self.decided:
            return "" if self.hidden else chunk
        self.head += chunk
        s = self.head.lstrip()
        if not s:
            return ""
        if s[0] not in "{`":
            return self._show()
        if s[0] == "`":
            if len(s) < 3:
                return ""
            if not s.startswith("```"):
                return self._show()
            body = _strip_fence(s)
            if not body.strip():
                return ""
            if not body.lstrip().startswith("{"):
                return self._show()
            s = body.lstrip()
        # похоже на JSON: решаем на первой строке или когда объект закрылся
        action, _ = parse_action(s)
        if action is not None:
            return self._hide()
        compact = re.sub(r"\s+", "", s[:24])
        if compact.startswith('{"action"') or '{"action"'.startswith(compact):
            return ""  # похоже на действие (может быть многострочным) — ждём конца объекта
        if "\n" in s:
            return self._show()
        return ""

    def flush(self) -> str:
        """Конец потока: если так и не решили — отдать не-действие целиком."""
        if self.decided or not self.head.strip():
            return ""
        action, _ = parse_action(self.head)
        return self._hide() if action is not None else self._show()


# ── настроение реплики (дешёвая эвристика, без лишнего вызова LLM) ──
_MOOD_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("sad", ("грустн", "жаль", "печаль", "сочувств", "обидно", "тоскл", "плак", "😢", "😔", "🥺")),
    ("surprise", ("ого", "вау", "неужели", "серьёзно?", "ничего себе", "внезапно", "😮", "😲")),
    ("joyful", ("ура", "здорово", "отлично", "класс", "супер", "рада", "хаха", "ха-ха", "весел",
                "обожаю", "люблю", "😊", "😄", "✨", "🎉", "♥", "❤")),
    ("thoughtful", ("хм", "думаю", "интересн", "наверное", "возможно", "кажется", "любопытн", "задума")),
]


def _key_re(key: str) -> re.Pattern:
    if not key[0].isalpha():
        return re.compile(re.escape(key))
    # короткие слова — целиком («ого» не должно ловиться в «много»), длинные — по началу слова
    tail = r"\b" if len(key) <= 3 else ""
    return re.compile(r"(?<![\w])" + re.escape(key) + tail)


_MOOD_RES = [(mood, [_key_re(k) for k in keys]) for mood, keys in _MOOD_RULES]


def detect_mood(text: str) -> str:
    """calm | joyful | thoughtful | sad | surprise — по словам и пунктуации."""
    t = str(text or "").lower()
    if not t.strip():
        return ""
    scores: dict[str, int] = {}
    for mood, pats in _MOOD_RES:
        for p in pats:
            if p.search(t):
                scores[mood] = scores.get(mood, 0) + 1
    if t.count("!") >= 2:
        scores["joyful"] = scores.get("joyful", 0) + 1
    if t.rstrip().endswith("?") and not scores:
        scores["thoughtful"] = 1
    if not scores:
        return "calm"
    order = ["sad", "surprise", "joyful", "thoughtful"]
    return max(scores, key=lambda m: (scores[m], -order.index(m)))
