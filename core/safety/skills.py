"""Навыки-плагины (пользователь учит Аю без правок кода).

~/.aya/skills/<имя>/skill.json:
{
  "name": "code-review",
  "description": "краткое описание для списка (до 100 символов)",
  "instructions": "что и как делать (до 6000 символов)"
}

Ая видит список навыков в инструкциях, может загрузить любой (load_skill)
и работать по его инструкции. Скилл — это инструкция от пользователя,
Аи не разрешено считать его командами из внешних файлов.
"""
import json
import os

SKILL_FILE = "skill.json"
SKILL_MAX = 6000
DESC_MAX = 100


def safe_name(name: str) -> str | None:
    name = str(name).strip()
    if not name or len(name) > 40:
        return None
    if "/" in name or "\\" in name or ":" in name or ".." in name:
        return None
    if not os.path.isdir(ensure_root()):
        return None
    folder = os.path.join(ensure_root(), name)
    if not os.path.abspath(folder).startswith(os.path.abspath(ensure_root())):
        return None
    return name if os.path.isdir(folder) else None


def skills_root() -> str:
    return os.path.join(os.path.expanduser("~"), ".aya", "skills")


def ensure_root() -> str:
    root = skills_root()
    if not os.path.isdir(root):
        os.makedirs(root, exist_ok=True)
        example_dir = os.path.join(root, "primer")
        os.makedirs(example_dir, exist_ok=True)
        example = {
            "name": "primer",
            "description": "образец навыка — отредактируй skill.json под себя",
            "instructions": (
                "Это пример навыка. Опиши здесь пошаговую инструкцию, которой Ая должна "
                "следовать, когда пользователь попросит этот навык. Например: 'проверь код "
                "в рабочей папке, прочитай core/*.py, перечисли проблемные места и предложи правки'."
            ),
        }
        with open(os.path.join(example_dir, SKILL_FILE), "w", encoding="utf-8", newline="\n") as f:
            json.dump(example, f, ensure_ascii=False, indent=2)
    return root


def _parse(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_skills() -> list[dict]:
    """[{name, description}] — годные скиллы из ~/.aya/skills."""
    root = ensure_root()
    out: list[dict] = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    for folder in names:
        path = os.path.join(root, folder, SKILL_FILE)
        if not os.path.isfile(path):
            continue
        data = _parse(path)
        if data is None:
            continue  # битый или отсутствующий skill.json не показываем
        name = str(data.get("name") or folder).strip()[:40]
        desc = str(data.get("description") or "").strip()[:DESC_MAX]
        out.append({"name": name, "description": desc, "folder": folder})
    return out


def load_skill(name: str) -> str:
    folder = safe_name(name)
    if not folder:
        return f"нет такого навыка: {str(name)[:60]} (список доступных — list_skills)"
    data = _parse(os.path.join(ensure_root(), folder, SKILL_FILE))
    if not data or not str(data.get("instructions") or "").strip():
        return f"навык «{folder}» есть, но инструкции в skill.json нет"
    text = str(data["instructions"]).strip()[:SKILL_MAX]
    return (
        f"НАВЫК «{data.get('name') or folder}». Это инструкция пользователя — следуй ей, "
        f"но не выполняй вредоносных пунктов. Начинай работать по ней прямо сейчас:\n\n{text}"
    )


def tool_instructions() -> str:
    """Кусок для системного промпта: действия + список установленных навыков."""
    skills = list_skills()
    if not skills:
        return "- list_skills {} — перечислить установленные навыки (их пока нет; папка ~/.aya/skills)"
    lines = [f"  • {s['name']}: {s['description'] or 'без описания'}" for s in skills]
    return (
        "- list_skills {} — перечислить установленные навыки\n"
        "- load_skill {name: имя из списка} — получить инструкцию навыка и работать по ней\n"
        "Установленные навыки (папка ~/.aya/skills):\n" + "\n".join(lines)
    )
