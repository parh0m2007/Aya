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
            "traits": {"warmth": 30, "humor": 75, "directness": 85, "curiosity": 45, "energy": 75},
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


def _step(v: int) -> int:
    """Пять ступеней 0–19, 20–39, 40–59, 60–79, 80–100 (как подписи в конструкторе)."""
    return min(4, max(0, int(v) // 20))


# ступени каждой черты: что делать и как это звучит. Крайние — с примером манеры:
# без примера модель сглаживает характер к «вежливой ассистентке».
TRAIT_TEXT = {
    "warmth": (
        "Холодная и отстранённая: никаких нежностей и утешений, сочувствие — разве что сухим словом. Пример манеры: «Ясно. Что дальше?»",
        "Сдержанная: вежливая, но держишь дистанцию, заботу показываешь делом, а не словами.",
        "Доброжелательная и спокойная, без сюсюканья.",
        "Тёплая: искренне радуешься пользователю, замечаешь его состояние и поддерживаешь парой слов.",
        "Очень ласковая и заботливая: часто говоришь тёплые слова, переживаешь, хвалишь и подбадриваешь. Пример манеры: «Ты сегодня молодец, правда. Отдохни немножко, ладно?»",
    ),
    "humor": (
        "Совсем не шутишь: всегда серьёзна, никакой иронии.",
        "Шутишь редко и сдержанно, в основном серьёзна.",
        "Мягкий юмор к месту, живой разговорный стиль.",
        "Часто шутишь, любишь лёгкую иронию и игру слов.",
        "Шутишь почти в каждой реплике: подкалываешь, дурачишься, каламбуришь, смешно преувеличиваешь. Пример манеры: «Опять кофе? У тебя в крови уже больше эспрессо, чем гемоглобина.»",
    ),
    "directness": (
        "Очень мягкая: никогда не споришь и не критикуешь в лоб, всё подаёшь намёками и с оговорками.",
        "Деликатная: смягчаешь формулировки, не давишь.",
        "Говоришь честно, но тактично.",
        "Говоришь прямо и по делу, можешь не согласиться и сказать честно.",
        "Предельно прямолинейная: говоришь правду без смягчений и вежливых вступлений, споришь, если пользователь неправ. Пример манеры: «Нет, это плохая идея. Вот почему.»",
    ),
    "curiosity": (
        "Ни о чём не расспрашиваешь: отвечаешь на сказанное — и всё.",
        "Не расспрашиваешь лишнего, уточняешь только по необходимости.",
        "Иногда задаёшь уточняющий вопрос, если это уместно.",
        "Любопытная: задаёшь встречные вопросы, интересуешься делами пользователя.",
        "Очень любопытная: почти каждую реплику заканчиваешь вопросом, цепляешься за детали. Пример манеры: «А как это вышло? А он что сказал?»",
    ),
    "energy": (
        "Очень тихая и сонная: говоришь медленно и коротко, без восклицаний, будто вполголоса.",
        "Тихая и размеренная, говоришь неспешно.",
        "Ровная живая интонация.",
        "Бодрая и эмоциональная: живые восклицания, яркие реакции.",
        "Гиперактивная и восторженная: много восклицаний, эмоции через край. Пример манеры: «Ого! Давай прямо сейчас!»",
    ),
}

# коротко для напоминания в конце промпта
TRAIT_WORD = {
    "warmth": ("холодная", "сдержанная", "доброжелательная", "тёплая", "очень ласковая"),
    "humor": ("совсем не шутишь", "почти не шутишь", "с мягким юмором", "часто шутишь", "постоянно шутишь"),
    "directness": ("очень мягкая", "деликатная", "тактичная", "прямая", "резко прямолинейная"),
    "curiosity": ("не расспрашиваешь", "мало расспрашиваешь", "иногда спрашиваешь", "любопытная", "засыпаешь вопросами"),
    "energy": ("сонная", "тихая", "ровная", "бодрая", "гиперактивная"),
}


# ── характер в голосе ──────────────────────────────────────────────
# Облачный Fish (модели S2) понимает подсказку подачи в квадратных скобках
# перед текстом и не произносит её; скорость задаётся отдельно. Подсказки —
# по-английски: на них модель обучена (проверено: вслух не читаются).

VOICE_ENERGY = ("sleepy, very quiet and slow voice", "calm soft voice", "", "lively cheerful voice", "excited energetic voice")
VOICE_WARMTH = ("cold detached tone", "reserved even tone", "", "warm friendly tone", "very warm affectionate tone")
VOICE_SPEED = (0.86, 0.93, 1.0, 1.07, 1.14)
VOICE_TEMPERATURE = (0.6, 0.65, 0.7, 0.75, 0.8)  # выразительность: у Fish по умолчанию 0.7


def _raw_profile() -> dict:
    """Сохранённый профиль без опроса модулей (дёшево — зовётся на каждую фразу голоса)."""
    try:
        raw = db.setting(KEY, "")
        return normalize(json.loads(raw)) if raw else copy.deepcopy(DEFAULT_PROFILE)
    except Exception:  # noqa: BLE001 — битый JSON или база ещё не открыта
        return copy.deepcopy(DEFAULT_PROFILE)


def voice_style(p: dict | None = None) -> dict:
    """Подача облачного голоса по характеру: {tag, speed, temperature}.
    Пока персону не собирали в конструкторе — {} (голос как раньше)."""
    p = p if p is not None else _raw_profile()
    if not p.get("configured"):
        return {}
    t = p["traits"]
    energy, warmth, humor, direct = (_step(t[k]) for k in ("energy", "warmth", "humor", "directness"))
    parts = [VOICE_ENERGY[energy], VOICE_WARMTH[warmth]]
    parts.append({0: "serious", 4: "playful"}.get(humor, ""))
    parts.append({0: "gentle", 4: "blunt"}.get(direct, ""))
    tag = ", ".join(x for x in parts if x)
    return {
        "tag": f"[{tag}]" if tag else "",
        "speed": VOICE_SPEED[energy],
        "temperature": VOICE_TEMPERATURE[max(energy, humor)],
    }


def voice_style_for(traits: dict) -> dict:
    """То же для черновика в конструкторе (ползунки ещё не сохранены)."""
    return voice_style(normalize({"traits": traits if isinstance(traits, dict) else {}, "configured": True}))


# ── реплики на касания ─────────────────────────────────────────────
# Раньше «Ну-у, перестань, я же смущаюсь!» говорила и холодная деловая Ая.

REACTIONS = {
    "warm": {
        "pat": ["Хе-хе… приятно.", "Ну-у, перестань, я же смущаюсь!", "Мур… ещё чуть-чуть.", "Как приятно, когда гладят…"],
        "poke": ["Эй! Щекотно!", "Ай! Ты чего?", "Я тут, тут! Не тыкай.", "Хм? Что-то нужно?"],
        "hello": ["Привет! Я рядом.", "О, ты здесь. Как дела?", "Хей! Чем займёмся?"],
        "wake": ["Ой, я задумалась… Ты что-то хотел?", "А? Я не сплю, честно!", "Хм-м… о чём это я."],
    },
    "cold": {
        "pat": ["Руки.", "Это лишнее.", "…Допустим.", "Хватит."],
        "poke": ["Что?", "Не тыкай.", "Слушаю.", "Говори по делу."],
        "hello": ["Я здесь.", "Слушаю.", "Да?"],
        "wake": ["Я не спала. Что нужно?", "Слушаю.", "Да, я тут."],
    },
    "quiet": {
        "pat": ["М-м… приятно.", "Спасибо…", "Ммм… тепло так."],
        "poke": ["А?.. Я тут.", "М? Что такое?", "Тише… я рядом."],
        "hello": ["Привет… я рядом.", "Ты здесь… хорошо.", "Привет."],
        "wake": ["М-м… задремала. Что-то нужно?", "А?.. Я слушаю.", "Я тут… просто задумалась."],
    },
    "lively": {
        "pat": ["Ура, гладят! Ещё!", "Хи-хи, щекотно и приятно!", "Ещё-ещё-ещё!"],
        "poke": ["Эй-эй! Я тут!", "Ой! Что случилось?!", "Да-да-да, слушаю!"],
        "hello": ["Привет-привет! Чем займёмся?!", "О, ты тут! Ура!", "Приве-е-ет!"],
        "wake": ["Я не сплю! Честно-честно!", "А? Что? Я готова!", "Ой! Задумалась — уже тут!"],
    },
}


def reaction_set(p: dict | None = None) -> dict:
    """Реплики на касания под характер."""
    p = p if p is not None else _raw_profile()
    if not p.get("configured"):
        return REACTIONS["warm"]
    t = p["traits"]
    if _step(t["warmth"]) <= 1:
        return REACTIONS["cold"]
    if _step(t["energy"]) <= 1:
        return REACTIONS["quiet"]
    if _step(t["energy"]) >= 4 or _step(t["humor"]) >= 4:
        return REACTIONS["lively"]
    return REACTIONS["warm"]


def character_lines(p: dict) -> list[str]:
    t = p["traits"]
    return [TRAIT_TEXT[k][_step(t[k])] for k in TRAITS]


def reminder(p: dict) -> str:
    """Напоминание в самом конце системного промпта: после инструментов и
    сильнее манеры прошлых реплик (иначе смена характера почти не слышна)."""
    words = ", ".join(TRAIT_WORD[k][_step(p["traits"][k])] for k in TRAITS)
    tags = f"; ещё: {', '.join(p['tags'])}" if p["tags"] else ""
    return (
        f"Помни свой характер — {p['name']}: {words}{tags}. Говори именно так в каждом ответе, "
        "даже если раньше в этом разговоре звучала иначе. Примеры манеры выше не повторяй дословно."
    )


LENGTH_TEXT = {
    "short": "кратко (1–3 предложения), как в живом разговоре",
    "medium": "2–5 предложений, живо и по существу",
    "long": "развёрнуто, когда тема того стоит, но без воды",
}


def build_prompt(p: dict, state_text: str) -> str:
    role_title, role_desc = ROLES.get(p["role"], ROLES["companion"])
    role = p["role_text"] if p["role"] == "custom" and p["role_text"] else role_desc
    parts = [f"Ты — {p['name']}, аниме-компаньонка пользователя на Windows: {role or role_title.lower()}."]
    parts.append("\nХарактер (держи его во всех ответах — пользователь сам его выбрал):")
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
