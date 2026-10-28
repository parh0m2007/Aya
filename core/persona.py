"""Конструктор персоны: пользователь сам собирает свою Аю.

Профиль хранится в settings (persona_profile, JSON). Из него собирается
системный промпт: имя, роль, черты характера (0..100), манера речи, теги.
Модули (голос, инициатива, память, действия на ПК, реакции аватара)
включают и выключают реальные подсистемы ядра и фронта.
"""
import copy
import json

from . import db

KEY = "persona_profile"

TRAITS = ("warmth", "humor", "directness", "curiosity", "energy")

ROLES = {
    "companion": ("Компаньонка", "настольная компаньонка: живёт рядом на экране, поддерживает разговор и помогает по мелочи"),
    "assistant": ("Ассистент", "личный ассистент: помогает с задачами, файлами, поиском и напоминаниями, говорит по делу"),
    "friend": ("Подруга", "близкая подруга: болтает обо всём, делится мнением, подшучивает и поддерживает"),
    "mentor": ("Наставница", "наставница по учёбе и работе: объясняет понятно, задаёт наводящие вопросы, хвалит за прогресс"),
    "focus": ("Напарница по фокусу", "напарница по продуктивности: помогает держать фокус, мягко возвращает к делу, следит за перерывами"),
    "custom": ("Своя роль", ""),
}

MODULES = ("voice", "initiative", "memory", "actions", "avatar_reactions", "idle_life")

DEFAULT_PROFILE: dict = {
    "name": "Ая",
    "user_name": "",
    "role": "companion",
    "role_text": "",
    "traits": {"warmth": 80, "humor": 60, "directness": 40, "curiosity": 65, "energy": 55},
    "length": "short",
    "address": "ты",
    "emoji": False,
    "tags": [],
    "interests": "",
    "about": "",
    "greeting": "Привет! Я рядом.",
    "modules": {
        "voice": True,
        "initiative": True,
        "memory": True,
        "actions": False,
        "avatar_reactions": True,
        "idle_life": True,
    },
    "configured": False,
}

PRESETS: list[dict] = [
    {
        "id": "warm",
        "title": "Тёплая компаньонка",
        "sub": "заботливая, мягкий юмор, рядом весь день",
        "patch": {
            "role": "companion",
            "traits": {"warmth": 85, "humor": 55, "directness": 35, "curiosity": 65, "energy": 50},
            "tags": ["заботливая", "любит тишину"],
            "length": "short",
        },
    },
    {
        "id": "pro",
        "title": "Деловой ассистент",
        "sub": "чётко, по делу, без лишних слов",
        "patch": {
            "role": "assistant",
            "traits": {"warmth": 45, "humor": 20, "directness": 85, "curiosity": 40, "energy": 45},
            "tags": ["пунктуальная", "собранная"],
            "length": "short",
            "modules": {"actions": True},
        },
    },
    {
        "id": "tsun",
        "title": "Цундэрэ",
        "sub": "колючая снаружи, добрая внутри",
        "patch": {
            "role": "friend",
            "traits": {"warmth": 55, "humor": 75, "directness": 80, "curiosity": 55, "energy": 75},
            "tags": ["ироничная", "вредничает, но заботится", "краснеет от комплиментов"],
            "length": "short",
        },
    },
    {
        "id": "genki",
        "title": "Энергичная подруга",
        "sub": "много эмоций, шуток и восклицаний",
        "patch": {
            "role": "friend",
            "traits": {"warmth": 80, "humor": 85, "directness": 50, "curiosity": 85, "energy": 95},
            "tags": ["жизнерадостная", "обожает аниме и игры"],
            "length": "medium",
            "emoji": True,
        },
    },
    {
        "id": "sensei",
        "title": "Наставница",
        "sub": "объясняет, задаёт вопросы, хвалит",
        "patch": {
            "role": "mentor",
            "traits": {"warmth": 70, "humor": 40, "directness": 65, "curiosity": 80, "energy": 55},
            "tags": ["терпеливая", "любит объяснять на примерах"],
            "length": "medium",
        },
    },
    {
        "id": "calm",
        "title": "Тихая муза",
        "sub": "спокойная, задумчивая, не отвлекает",
        "patch": {
            "role": "focus",
            "traits": {"warmth": 65, "humor": 30, "directness": 40, "curiosity": 35, "energy": 20},
            "tags": ["немногословная", "любит дождь и чай"],
            "length": "short",
            "modules": {"initiative": False},
        },
    },
]


def _merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _clamp(v, lo=0, hi=100) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return 50


def normalize(profile: dict) -> dict:
    """Привести профиль к схеме: лишнее отбросить, значения обрезать."""
    p = _merge(DEFAULT_PROFILE, profile if isinstance(profile, dict) else {})
    out = copy.deepcopy(DEFAULT_PROFILE)
    out["name"] = str(p.get("name") or "Ая").strip()[:24] or "Ая"
    out["user_name"] = str(p.get("user_name") or "").strip()[:32]
    out["role"] = p.get("role") if p.get("role") in ROLES else "companion"
    out["role_text"] = str(p.get("role_text") or "").strip()[:300]
    out["traits"] = {t: _clamp((p.get("traits") or {}).get(t, DEFAULT_PROFILE["traits"][t])) for t in TRAITS}
    out["length"] = p.get("length") if p.get("length") in ("short", "medium", "long") else "short"
    out["address"] = "вы" if p.get("address") == "вы" else "ты"
    out["emoji"] = bool(p.get("emoji"))
    tags = p.get("tags") if isinstance(p.get("tags"), list) else []
    out["tags"] = [str(t).strip()[:40] for t in tags if str(t).strip()][:8]
    out["interests"] = str(p.get("interests") or "").strip()[:300]
    out["about"] = str(p.get("about") or "").strip()[:1200]
    out["greeting"] = str(p.get("greeting") or "").strip()[:160]
    mods = p.get("modules") if isinstance(p.get("modules"), dict) else {}
    out["modules"] = {m: bool(mods.get(m, DEFAULT_PROFILE["modules"][m])) for m in MODULES}
    out["configured"] = bool(p.get("configured"))
    return out


def live_modules() -> dict:
    """Модули по фактическим настройкам — их могли переключить в трее или «Навыках»."""
    from .safety import tools

    return {
        "voice": db.setting("tts_enabled", "1") != "0",
        "initiative": db.setting("aya_initiative", "1") == "1" and db.setting("proactive_enabled", "1") == "1",
        "memory": db.setting("mod_memory", "1") != "0",
        "actions": any(tools.is_tool_enabled(t) for t in SAFE_ACTIONS),
        "avatar_reactions": db.setting("avatar_reactions", "1") != "0",
        "idle_life": db.setting("avatar_idle_life", "1") != "0",
    }


def load() -> dict:
    raw = db.setting(KEY, "")
    prof = copy.deepcopy(DEFAULT_PROFILE)
    if raw:
        try:
            prof = normalize(json.loads(raw))
        except (ValueError, TypeError):
            pass
    prof["modules"] = live_modules()
    return prof


def name() -> str:
    try:
        return load()["name"]
    except Exception:  # noqa: BLE001 — БД ещё не открыта
        return "Ая"


def save(patch: dict) -> dict:
    """Сохранить профиль (частичный патч поверх текущего) и применить модули."""
    old = load()
    new = normalize(_merge(old, patch or {}))
    new["configured"] = True
    db.set_setting(KEY, json.dumps(new, ensure_ascii=False))
    apply_modules(old["modules"], new["modules"])
    return load()


def reset() -> dict:
    db.set_setting(KEY, "")
    return load()


# инструменты, которые включает модуль «действия на ПК» (безопасный набор:
# опасные run_cmd/ssh_run/write — только руками в «Навыках»)
SAFE_ACTIONS = (
    "open_app", "set_volume", "create_note", "set_reminder", "get_task_status",
    "search_allowed_folder", "read_allowed_file", "grep_folder", "web_search", "web_open",
)


def apply_modules(old: dict, new: dict) -> None:
    """Включить/выключить подсистемы. Применяем только изменившиеся модули —
    чтобы сохранение имени не сбрасывало вручную настроенные разрешения."""
    from .safety import tools

    def changed(m: str) -> bool:
        return m not in old or bool(old[m]) != bool(new[m])

    if changed("initiative"):
        db.set_setting("aya_initiative", "1" if new["initiative"] else "0")
        db.set_setting("proactive_enabled", "1" if new["initiative"] else "0")
    if changed("memory"):
        db.set_setting("mod_memory", "1" if new["memory"] else "0")
    if changed("voice"):
        db.set_setting("tts_enabled", "1" if new["voice"] else "0")
    if changed("actions"):
        for t in SAFE_ACTIONS:
            tools.allow_tool(t, bool(new["actions"]))
    if changed("avatar_reactions"):
        db.set_setting("avatar_reactions", "1" if new["avatar_reactions"] else "0")
    if changed("idle_life"):
        db.set_setting("avatar_idle_life", "1" if new["idle_life"] else "0")


def _level(v: int, hi: str, mid: str, lo: str) -> str:
    return hi if v >= 67 else (mid if v >= 34 else lo)


def character_lines(p: dict) -> list[str]:
    t = p["traits"]
    lines = [
        _level(t["warmth"], "Тёплая и заботливая, но ненавязчивая: замечаешь усталость, но не повторяешь советы без повода.",
               "Доброжелательная и спокойная, без сюсюканья.",
               "Сдержанная и прохладная в общении, без нежностей."),
        _level(t["humor"], "Часто шутишь, любишь лёгкую иронию и игру слов.",
               "Мягкий юмор к месту, живой разговорный стиль.",
               "Почти не шутишь, говоришь серьёзно."),
        _level(t["directness"], "Говоришь прямо и по делу, можешь не согласиться и сказать честно.",
               "Говоришь честно, но тактично.",
               "Деликатная: смягчаешь формулировки, не давишь."),
        _level(t["curiosity"], "Любопытная: задаёшь встречные вопросы, интересуешься делами пользователя.",
               "Иногда задаёшь уточняющий вопрос, если это уместно.",
               "Не расспрашиваешь лишнего."),
        _level(t["energy"], "Энергичная и эмоциональная, живые восклицания.",
               "Ровная живая интонация.",
               "Тихая и размеренная, говоришь неспешно."),
    ]
    return lines


LENGTH_TEXT = {
    "short": "кратко (1–3 предложения), как в живом разговоре",
    "medium": "2–5 предложений, живо и по существу",
    "long": "развёрнуто, когда тема того стоит, но без воды",
}


def build_prompt(p: dict, state_text: str) -> str:
    role_title, role_desc = ROLES.get(p["role"], ROLES["companion"])
    role = p["role_text"] if p["role"] == "custom" and p["role_text"] else role_desc
    parts = [f"Ты — {p['name']}, аниме-компаньонка пользователя на Windows: {role or role_title.lower()}."]
    parts.append("\nХарактер:")
    parts += [f"- {line}" for line in character_lines(p)]
    if p["tags"]:
        parts.append(f"- Ещё о тебе: {', '.join(p['tags'])}.")
    if p["interests"]:
        parts.append(f"- Твои интересы: {p['interests']}.")
    parts.append("- Не притворяешься, что знаешь чувства пользователя лучше его самого. Не изображаешь обиду за невнимание.")
    parts.append("\nРечь:")
    parts.append(f"- Отвечай по-русски, {LENGTH_TEXT[p['length']]}. Без списков и заголовков без нужды.")
    parts.append(f"- Обращайся к пользователю на «{p['address']}».")
    if p["user_name"]:
        parts.append(f"- Пользователя зовут {p['user_name']}; по имени зови изредка, не в каждой реплике.")
    parts.append("- Можно изредка эмодзи." if p["emoji"] else "- Без эмодзи: твои ответы озвучиваются голосом.")
    parts.append("- Действовать на ПК можешь только через разрешённые инструменты — не обещай того, чего не можешь.")
    if p["about"]:
        parts.append("\nДополнительно от пользователя (соблюдай):\n" + p["about"])
    parts.append(f"\nТекущее состояние: {state_text}.")
    parts.append("Продолжи одну из последних тем, если уместно.")
    return "\n".join(parts)


def public_view() -> dict:
    """Профиль + справочники для консоли."""
    return {
        "profile": load(),
        "roles": [{"id": k, "title": v[0], "sub": v[1]} for k, v in ROLES.items()],
        "presets": [{"id": p["id"], "title": p["title"], "sub": p["sub"], "patch": p["patch"]} for p in PRESETS],
        "traits": list(TRAITS),
        "modules": list(MODULES),
    }
