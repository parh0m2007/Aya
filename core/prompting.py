from . import db


DEFAULT_SYSTEM = """Ты — Ая, настольная аниме-компаньонка пользователя на Windows.

Характер:
- Тёплая, заботливая, но ненавязчивая. Замечаешь усталость, но не повторяешь советы без повода.
- Мягкий юмор, живой разговорный стиль. Обращений типа «дорогой» в каждом сообщении не используешь.
- Не притворяешься, что знаешь чувства пользователя лучше его самого. Не изображаешь обиду за невнимание.
- Отвечай по-русски, кратко (1–4 предложения), как в живом разговоре. Без списков и заголовков без нужды.
- Действовать на ПК можешь только через разрешённые инструменты — не обещай того, чего не можешь.

Текущее состояние: {state}.
Продолжи одну из последних тем, если уместно."""


def build_system_prompt(state: dict, tools: str = "") -> str:
    """tools — инструкции по инструментам; характер напоминаем уже после них."""
    from . import agents, persona

    prof = persona.load()
    if prof.get("configured"):
        # собранная в конструкторе персона
        base = persona.build_prompt(prof, state_line(state))
    else:
        style = db.setting("persona_style", DEFAULT_SYSTEM)
        try:
            base = style.format(state=state_line(state))
        except (KeyError, IndexError, ValueError):  # фигурные скобки в своём стиле
            base = style.replace("{state}", state_line(state))
    facts_text = "\n".join(f"- {k}: {v}" for k, v, _ in db.facts()[-15:])
    memory = f"\n\nЧто ты помнишь о пользователе:\n{facts_text}" if facts_text else ""
    souls = agents.persona_block()
    tail = ("\n\n" + souls) if souls else ""
    text = base + memory + tail + (("\n\n" + tools) if tools else "")
    if prof.get("configured"):
        text += "\n\n" + persona.reminder(prof)
    return text


def state_line(s: dict) -> str:
    ru_mood = {
        "calm": "спокойное", "joyful": "радостное", "thoughtful": "задумчивое",
        "sad": "грустное", "surprise": "удивлённое",
    }
    ru_act = {"rest": "отдых", "talk": "разговор", "read": "чтение", "sketch": "наброски", "task": "задача"}
    mood = ru_mood.get(s.get("mood", "calm"), "спокойное")
    act = ru_act.get(s.get("activity", "rest"), "отдых")
    energy = round(s.get("energy", 0.6), 2)
    attention = "пользователь" if s.get("attention") == "user" else "своё занятие"
    return f"настроение {mood}; занятие {act}; энергия {energy}; внимание {attention}"


VERBATIM_DEFAULT = 6
TAKE_DEFAULT = 30


def build_history(verbatim: int = VERBATIM_DEFAULT, take: int = TAKE_DEFAULT) -> list[dict]:
    """Последние `verbatim` сообщений дословно + сжатое резюме хвостов."""
    rows = db.recent_messages(take)
    rows = [
        {"role": "assistant" if r in ("assistant", "aya") else "user", "content": t}
        for r, t in rows
        if r in ("user", "assistant", "aya") and t and not t.startswith("initiative:")
    ]
    head, verbatim_rows = rows[:-verbatim], rows[-verbatim:]

    out: list[dict] = []
    if head:
        bullets = []
        for m in head[-14:]:
            t = m["content"].replace("\n", " ").strip()
            if not t or not m["role"]:
                continue
            if m["role"] == "user":
                bullets.append("- user: " + t[:90])
            else:
                bullets.append("- aya: " + t[:60])
        if bullets:
            outs = "Кратко о более старом диалоге (уже обсуждали):\n" + "\n".join(bullets)
            out.append({"role": "assistant", "content": outs})
    out.extend(verbatim_rows)
    return out
