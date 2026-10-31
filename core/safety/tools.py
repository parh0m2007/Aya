import os
import re
import subprocess
import time

from .. import db

PERM_APPEND = "append"
PERM_CONFIGURED = "configured"
PERM_CONFIRMED = "confirmed"
PERM_DENIED = "denied"

PS_UTF8_PRELUDE = "try { [Console]::OutputEncoding=[System.Text.Encoding]::UTF8 } catch {}; "


def _decode_out(b: bytes) -> str:
    """stdout/stderr любого окна-кодека Windows → читаемый текст (utf-8 → cp866 → cp1251)."""
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def _ps_fix_chains(cmd: str) -> str:
    """'&&' не поддерживается в PS 5.1 — заменяю на ';' вне кавычек."""
    out: list[str] = []
    quote = ""
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if quote:
            out.append(c)
            if c == quote:
                quote = ""
            i += 1
            continue
        if c in ("\"", "'"):
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "&" and cmd.startswith("&&", i):
            out.append(";")
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def is_tool_enabled(name: str) -> bool:
    return db.setting(f"perm:{name}", "0") == "1"


def allow_tool(name: str, val: bool):
    db.set_setting(f"perm:{name}", "1" if val else "0")


def set_all_tools(enabled: bool):
    for n in KNOWN_ACTIONS:
        db.set_setting(f"perm:{n}", "1" if enabled else "0")


def confirm_mode() -> str:
    """ask — спрашивать подтверждение; auto — «доверенный режим», выполнять сразу."""
    return "auto" if db.setting("perm_mode", "ask") == "auto" else "ask"


APP_DISPLAY = {
    "telegram": "Telegram", "discord": "Discord", "spotify": "Spotify", "steam": "Steam",
    "notepad": "Блокнот", "calc": "Калькулятор", "explorer": "Проводник", "paint": "Paint",
    "browser": "браузер", "chrome": "Chrome", "edge": "Edge", "terminal": "терминал",
    "code": "VS Code", "cmd": "командную строку", "word": "Word", "excel": "Excel",
}


KNOWN_ACTIONS = {
    "open_app", "set_volume", "create_note", "search_allowed_folder", "get_task_status",
    "write_allowed_file", "set_reminder", "switch_agent",
    "read_allowed_file", "grep_folder", "run_cmd", "web_search", "web_open", "ssh_run",
    "screen_look", "tg_send",
}
# Всегда доступные (без per-tool разрешения): свободные вспомогательные
FREE_ACTIONS = {"remember_fact", "switch_agent", "list_skills", "load_skill", "delegate", "delegate_bg"}

RATE_LIMIT = "perm_rate_limit"  # действий за минуту, пользователь может задать в config


def _default_browser() -> tuple[str, str]:
    """(exe, процесс) браузера по умолчанию; ("", "") — не нашли."""
    from ..music.worker import default_browser

    return default_browser()


def _open_url(url: str) -> None:
    """Ссылку — прямо браузеру, без cmd: там «&» обрезал адрес и запускал хвост как команду."""
    exe = _default_browser()[0]
    if exe:
        try:
            subprocess.Popen([exe, url], shell=False)
            return
        except OSError:
            pass
    os.startfile(url)  # noqa: S606 — оболочка Windows открывает http(s) браузером


def _speakers_volume():
    """Громкость динамиков по умолчанию (IAudioEndpointVolume через pycaw)."""
    from pycaw.pycaw import AudioUtilities

    return AudioUtilities.GetSpeakers().EndpointVolume


def allowed_folder() -> str:
    return db.setting("allowed_folder", "")


def resolve_allowed_path(args: dict) -> str:
    """Возвращает путь внутри рабочей папки или строку-ошибку."""
    folder = allowed_folder()
    if not folder or not os.path.isdir(folder):
        return "рабочая папка не задана в настройках"
    name = str(args.get("name", "")).strip()
    if not name or "/" in name or "\\" in name or name in (".", "..") or ":" in name:
        return "недопустимое имя файла"
    path = os.path.normpath(os.path.join(folder, name))
    if not path.startswith(os.path.normpath(folder)):
        return "путь вне рабочей папки"
    return path


def resolve_allowed_subpath(args: dict) -> str:
    """Путь внутри рабочей папки с подпапками (для чтения): 'core/x.py' допустимо."""
    folder = allowed_folder()
    if not folder or not os.path.isdir(folder):
        return "рабочая папка не задана в настройках"
    name = str(args.get("name", "")).strip().replace("\\", "/")
    if not name or name.startswith("/") or ":" in name or ".." in name.split("/"):
        return "недопустимое имя файла"
    path = os.path.normpath(os.path.join(folder, name))
    if not path.startswith(os.path.normpath(folder) + os.sep) and path != os.path.normpath(folder):
        return "путь вне рабочей папки"
    return path


def audit(tool: str, args_json: str, result: str, allowed: bool):
    db.log_audit(tool, args_json, result, allowed)


def _launch_app_by_name(app_id: str, extra: list) -> bool:
    """Найти и запустить приложение: PATH → ярлыки меню Пуск → типичные папки установки."""
    import shutil as _sh

    exe = _sh.which(app_id) or _sh.which(app_id + ".exe")
    if exe:
        try:
            subprocess.Popen([exe, *extra], shell=False)
            return True
        except OSError:
            pass
    menus = []
    for env in ("APPDATA", "ProgramData"):
        d = os.environ.get(env, "")
        if d:
            menus.append(os.path.join(d, "Microsoft", "Windows", "Start Menu", "Programs"))
    for m in menus:
        if not os.path.isdir(m):
            continue
        cands: list[tuple[int, str]] = []
        for root, _dirs, files in os.walk(m):
            for f in files:
                fl = f.lower()
                if fl.endswith(".lnk") and app_id in fl:
                    cands.append((0 if fl.startswith(app_id) else 1, os.path.join(root, f)))
        if cands:
            cands.sort()
            try:
                os.startfile(cands[0][1])  # noqa: S606 — открыть ярлык
                return True
            except OSError:
                pass
    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    cap = app_id.capitalize()
    guesses = [
        os.path.join(appdata, cap + " Desktop", cap + ".exe"),
        os.path.join(localappdata, "Programs", cap, cap + ".exe"),
        os.path.join(localappdata, cap, cap + ".exe"),
        os.path.join(appdata, cap, cap + ".exe"),
    ]
    for g in guesses:
        if os.path.isfile(g):
            try:
                subprocess.Popen([g, *extra], shell=False)
                return True
            except OSError:
                continue
    return False


def run_action(name: str, args: dict) -> str:
    if name == "remember_fact":
        key = str(args.get("key", "")).strip()[:40]
        value = str(args.get("value", "")).strip()[:200]
        if not key or not value:
            db.log_audit(name, str(args), "need key and value", False)
            return "need key and value"
        db.add_fact(key, value, "aya")
        db.log_event("tool", f"remember_fact: {key}")
        return f"запомнила: {key}"

    if name == "switch_agent":
        target = str(args.get("agent", "")).strip()
        from .. import agents

        ok, msg = agents.set_active(target)
        db.log_event("agent" if ok else "tool", msg)
        return msg

    if name == "list_skills":
        from . import skills

        found = skills.list_skills()
        if not found:
            return "навыков пока нет — положи их в ~/.aya/skills/<имя>/skill.json"
        return "Установленные навыки:\n" + "\n".join(f"• {s['name']}: {s['description'] or 'без описания'}" for s in found)

    if name == "load_skill":
        from . import skills

        res = skills.load_skill(str(args.get("name", "")))
        db.log_event("tool", f"load_skill {str(args.get('name', ''))[:40]}")
        return res

    if name == "music":
        from .. import music

        return music.in_worker(music.tool, args if isinstance(args, dict) else {})

    if name == "pomodoro":
        from .. import pomodoro

        res = pomodoro.tool(args if isinstance(args, dict) else {})
        db.log_event("tool", f"pomodoro: {res[:60]}")
        return res

    if name == "recall":
        from .. import longmem

        out = longmem.recall(str(args.get("query", "")))
        db.log_event("tool", "recall долгая память")
        return out

    if name == "tg_inbox":
        from .. import bridge_tguser

        return bridge_tguser.inbox_text()

    if name == "tg_read":
        from .. import bridge_tguser

        db.log_event("telegram_user", "tg_read")
        return bridge_tguser.read_history(str(args.get("chat", "")), int(args.get("limit", 5) or 5))

    if name == "tg_send":
        from .. import bridge_tguser

        if not bridge_tguser.has_session():
            return "нет сессии Telegram — пользователь должен пройти вход в консоли (Telegram — твой аккаунт)"
        res = bridge_tguser.send(str(args.get("chat", "")), str(args.get("text", "")))
        db.log_event("telegram_user", f"tg_send {str(args.get('chat', ''))[:30]}: {str(args.get('text', ''))[:60]}")
        return res

    if name == "focus_mode":
        raw = args.get("enable", args.get("on", False))
        enable = str(raw).lower() in ("1", "true", "yes", "on", "да") if isinstance(raw, str) else bool(raw)
        db.set_setting("focus_mode", "1" if enable else "0")
        db.log_event("tool", f"focus_mode {'on' if enable else 'off'}")
        return ("тихий режим включён — не буду отвлекать инициативами, пока не выключат"
                if enable else "тихий режим выключен — снова буду иногда заглядывать")

    if name not in KNOWN_ACTIONS:
        return f"unknown action: {name}"
    if not is_tool_enabled(name):
        db.log_audit(name, str(args), "permission not granted", False)
        return f"permission 'perm:{name}' not granted; asked user to enable in settings"


    if name == "screen_look":
        """Скриншот экрана + окна + OCR/VLM описание."""
        from . import vision

        try:
            out = vision.describe()
        except Exception as e:  # noqa: BLE001
            return f"не смогла посмотреть экран: {str(e)[:150]}"
        db.log_event("tool", "screen_look")
        return out[:4500]

    if name == "open_app":
        app_id = str(args.get("app_id", "")).strip()
        target = str(args.get("target", "")).strip()
        extra = [str(a) for a in (args.get("args") or [])][:4]
        if target:
            # target приоритет: URL или путь к exe
            if "://" in target or target.lower().startswith("www."):
                url = target if "://" in target else "https://" + target
                if not re.match(r"https?://", url, re.IGNORECASE):
                    db.log_audit(name, str(args), "not a web link", False)
                    return "открываю только веб-ссылки (http:// или https://)"
                _open_url(url)
                db.log_event("tool", f"open url {url[:60]}")
                return f"открыла {url[:70]}"
            if re.fullmatch(r"[A-Za-z]:\\.*", target) or target.lower().endswith(".exe"):
                if not os.path.isfile(target):
                    db.log_audit(name, str(args), "target not found", False)
                    return f"файл не найден: {target}"
                subprocess.Popen([target, *extra], shell=False)
                db.log_event("tool", f"opened {os.path.basename(target)}")
                return f"открыла {os.path.basename(target)}"
            return "target должен быть URL или путём к .exe"
        # «открой браузер» — сам браузер со своей стартовой страницей. Раньше здесь
        # открывался duckduckgo.com, и люди видели рекламу браузера DuckDuckGo.
        exe = _default_browser()[0] if app_id.lower() == "browser" else ""
        known = {
            "notepad": ["notepad"], "explorer": ["explorer"], "calc": ["calc"], "paint": ["mspaint"],
            "browser": [exe] if exe else ["cmd", "/c", "start", "", "msedge"],
            "chrome": ["cmd", "/c", "start", "", "chrome"],
            "edge": ["cmd", "/c", "start", "", "msedge"],
            "terminal": ["cmd", "/c", "start", "", "wt"],
        }
        app_id = app_id.lower()
        if app_id in known:
            subprocess.Popen(known[app_id], shell=False)
            db.log_event("tool", f"opened {app_id}")
            return f"открыла {APP_DISPLAY.get(app_id) or app_id}"
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", app_id or "") or app_id.startswith("."):
            db.log_audit(name, str(args), "unknown app", False)
            return f"не поняла, что запускать: {app_id}"
        # умный поиск приложения: PATH → меню Пуск → типичные папки установки
        launched = _launch_app_by_name(app_id, extra)
        if launched:
            db.log_event("tool", f"opened {app_id}")
            return f"открыла {app_id}"
        # последний шанс — доверяем Windows
        subprocess.Popen(["cmd", "/c", "start", "", app_id], shell=False)
        db.log_event("tool", f"launch fallback {app_id}")
        return f"попробовала запустить {app_id} — если не открылся, дай путь до exe или скажи, где стоит"


    if name == "read_allowed_file":
        path = resolve_allowed_subpath(args)
        if not os.path.isabs(path) or not os.path.isfile(path):
            db.log_audit(name, str(args), "not found", False)
            return f"нет такого файла: {str(args.get('name',''))[:100]}"
        try:
            start = max(0, int(args.get("offset", 1) or 1) - 1)
        except (TypeError, ValueError):
            start = 0
        limit = max(1, min(200, int(args.get("limit", 120) or 120)))
        encodings = ("utf-8-sig", "utf-8", "cp1251", "latin-1")
        lines = None
        for enc in encodings:
            try:
                with open(path, encoding=enc) as f:
                    lines = f.readlines()
                break
            except UnicodeDecodeError:
                continue
            except OSError as e:
                db.log_audit(name, str(args), f"read error: {str(e)[:40]}", False)
                return f"не читаю файл: {str(e)[:120]}"
        if lines is None:
            return "не определила кодировку файла"
        sel = lines[start : start + limit]
        if not sel:
            return "(файл кончился или пуст)"
        body = "".join(sel)[:8000]
        head = f"[{os.path.basename(path)}] строки {start + 1}–{start + len(sel)} из {len(lines)}\n"
        db.log_event("tool", f"read {os.path.basename(path)} +{start+1}")
        return head + body


    if name == "set_volume":
        try:
            level = int(args.get("level", 50))
        except (TypeError, ValueError):
            level = 50
        level = max(0, min(100, level))
        from ..music.worker import in_worker

        def apply():
            # громкость динамиков ровно в процентах; раньше жали клавиши громкости
            # от текущего уровня — «30» убавляло на 2%, «50» прибавляло 50%
            vol = _speakers_volume()
            vol.SetMasterVolumeLevelScalar(level / 100, None)
            vol.SetMute(level == 0, None)

        try:
            in_worker(apply, timeout=10)  # COM громкости — в потоке, где он инициализирован
        except Exception as e:  # noqa: BLE001
            db.log_audit(name, str(args), f"volume error: {str(e)[:40]}", False)
            return f"не получилось поменять громкость: {str(e)[:120]}"
        db.log_event("tool", f"volume {level}%")
        return f"громкость {level}%"


    if name == "create_note":
        text = str(args.get("text", ""))[:2000]
        base = os.path.join(os.path.expanduser("~"), ".aya", "notes")
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, f"note-{int(time.time())}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        db.log_event("tool", f"note created {path}")
        return path


    if name == "search_allowed_folder":
        folder = db.setting("allowed_folder", "")
        q = str(args.get("query", "")).lower()
        if not folder or not os.path.isdir(folder):
            return "allowed folder not set in settings"
        hits = []
        for root, dirs, files in os.walk(folder):
            dirs[:] = dirs[:64]
            for f in files:
                if q in f.lower():
                    hits.append(os.path.join(root, f))
                    if len(hits) >= 12:
                        break
            if len(hits) >= 12:
                break
        return "\n".join(hits) if hits else "nothing found"


    if name == "get_task_status":
        rows = db.query("SELECT summary FROM events WHERE kind='task' ORDER BY id DESC LIMIT 5")
        return "\n".join(r[0] for r in rows) or "no tasks"

    if name == "write_allowed_file":
        path = resolve_allowed_path(args)
        if not os.path.isabs(path):
            return path  # строка-ошибка
        content = str(args.get("content", ""))[:20000]
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        db.log_event("tool", f"wrote {path}")
        return f"записано: {os.path.basename(path)}"

    if name == "set_reminder":
        try:
            minutes = int(args.get("minutes", 0))
        except (TypeError, ValueError):
            minutes = 0
        minutes = max(1, min(24 * 60, minutes))
        note = str(args.get("note", ""))[:200]
        import json as _json

        task_id = db.add_task("reminder", {"note": note}, time.time() + minutes * 60)
        db.log_event("task", f"reminder set: +{minutes}m {note}")
        return f"напоминание #{task_id} через {minutes} мин: {note}"

    if name == "grep_folder":
        """Поиск по содержимому файлов рабочей папки (анализ кода)."""
        folder = allowed_folder()
        q = str(args.get("query", "")).strip()
        if not folder or not os.path.isdir(folder):
            return "рабочая папка не задана в настройках"
        if not q:
            return "укажи query"
        sub = str(args.get("sub", "")).strip()
        root = os.path.normpath(os.path.join(folder, sub)) if sub else os.path.normpath(folder)
        if not root.startswith(os.path.normpath(folder)):
            return "путь вне рабочей папки"
        skip_dirs = {".git", "node_modules", ".venv", "__pycache__", "dist", "logs", ".cache", "target", "build"}
        code_exts = {
            ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt", ".css", ".scss", ".html",
            ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh", ".bat", ".ps1", ".cs", ".go", ".rs",
            ".java", ".cpp", ".c", ".h", ".hpp", ".vue", ".svelte", ".rb", ".php", ".sql",
        }
        ql = q.lower()
        matches: list[str] = []
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            if len(matches) >= 40:
                break
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in code_exts:
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    with open(fp, encoding="utf-8", errors="ignore") as f:
                        for ln, line in enumerate(f, 1):
                            if ql in line.lower():
                                rel = os.path.relpath(fp, folder)
                                matches.append(f"{rel}:{ln}: {line.strip()[:160]}")
                                break  # одна строка на файл — так хватит на больше файлов
                except OSError:
                    continue
                if len(matches) >= 40:
                    break
        if not matches:
            return "ничего не нашла по содержимому"
        db.log_event("tool", f"grep '{q}' -> {len(matches)}")
        return f"нашла {len(matches)} совпадений:\n" + "\n".join(matches[:40])

    if name == "run_cmd":
        """Команда PowerShell в рабочей папке (требует подтверждения)."""
        command = str(args.get("command", "")).strip()
        if not command:
            return "укажи command"
        if len(command) > 2000:
            return "команда слишком длинная"
        command = _ps_fix_chains(command)
        folder = allowed_folder()
        cwd = folder if folder and os.path.isdir(folder) else None
        try:
            import subprocess as _sp

            proc = _sp.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_UTF8_PRELUDE + command],
                capture_output=True, timeout=120, cwd=cwd,
            )
            out = _decode_out(proc.stdout or b"").rstrip()
            err = _decode_out(proc.stderr or b"").rstrip()
        except _sp.TimeoutExpired:
            db.log_event("tool", f"cmd timeout: {command[:60]}")
            return "команда не уложилась в 120 секунд — прервала"
        except Exception as e:  # noqa: BLE001
            return f"не запустила PowerShell: {str(e)[:120]}"
        res = out
        if err:
            res = (res + "\n[stderr] " + err) if res else "[stderr] " + err
        if not res:
            res = f"(вывод пуст, код {proc.returncode})"
            if proc.returncode != 0:
                res += " — похоже, команда не выполнилась"
        if "'&&'" in res or "not a valid statement separator" in res:
            res += "\nПодсказка: вместо && в PowerShell ставь ; — я перевела сама, но исходная команда могла сломаться"
        tail = res[-6000:]
        db.log_event("tool", f"cmd: {command[:60]} -> {len(res)} симв.")
        return tail.strip()

    if name == "web_search":
        """Поиск в интернете через DuckDuckGo (без API-ключей)."""
        query = str(args.get("query", "")).strip()
        if not query:
            return "укажи query"
        if len(query) > 300:
            return "запрос слишком длинный"
        import httpx
        import re as _re
        from urllib.parse import unquote

        try:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                r = client.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query},
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                )
                r.raise_for_status()
                html = r.text
        except Exception as e:  # noqa: BLE001
            db.log_audit(name, str(args), f"web err: {str(e)[:40]}", False)
            return f"поиск не удался: {str(e)[:120]}"
        results = []
        for m in _re.finditer(r'<a rel="nofollow" class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html):
            url = m.group(1)
            if "uddg=" in url:
                url = unquote(url.split("uddg=")[1].split("&")[0])
            elif url.startswith("//"):
                url = "https:" + url
            title = _re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if title and url:
                results.append(f"{title}\n{url}")
            if len(results) >= 6:
                break
        if not results:
            # запасной разбор: обычные ссылки-результаты
            for m in _re.finditer(r'class="result__url"[^>]*>(.*?)</a>', html):
                results.append(_re.sub(r"<[^>]+>", "", m.group(1)).strip())
                if len(results) >= 6:
                    break
        if not results:
            db.log_event("tool", f"web empty: {query[:40]}")
            return "ничего не нашлось"
        db.log_event("tool", f"web_search '{query[:40]}' -> {len(results)}")
        return f"Нашла по запросу «{query}»:\n" + "\n\n".join(results)

    if name == "web_open":
        """Скачать страницу и превратить в текст (чтение сайтов)."""
        url = str(args.get("url", "")).strip()
        if not url:
            return "укажи url"
        if not (url.startswith("http://") or url.startswith("https://")):
            url = "https://" + url
        if len(url) > 400:
            return "url слишком длинный"
        import httpx
        import re as _re

        try:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                r = client.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                r.raise_for_status()
                html = r.text
        except Exception as e:  # noqa: BLE001
            db.log_audit(name, str(args), f"web err: {str(e)[:40]}", False)
            return f"страница не открылась: {str(e)[:120]}"
        # html → текст
        html = _re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
        html = _re.sub(r"(?is)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", html)
        text = _re.sub(r"<[^>]+>", " ", html)
        text = _re.sub(r"[ \t]+", " ", text)
        text = _re.sub(r"\n\s*\n+", "\n", text).strip()
        if not text:
            return "страница пустая или целиком на скриптах"
        db.log_event("tool", f"web_open {url[:50]} -> {len(text)} симв.")
        return f"[{url}]\n" + text[:7000]

    if name == "ssh_run":
        """Команда на удалённом сервере (требует подтверждения; хост из allowlist конфига)."""
        import json as _json
        import shutil

        host_alias = str(args.get("host", "")).strip()
        command = str(args.get("command", "")).strip()
        if not host_alias or not command:
            return "укажи host (псевдоним из настроек) и command"
        try:
            cfg_path = os.path.join(os.path.expanduser("~"), ".aya", "config.json")
            with open(cfg_path, encoding="utf-8-sig") as f:
                ssh_cfg = _json.load(f).get("ssh") or {}
        except (OSError, ValueError):
            ssh_cfg = {}
        hosts = ssh_cfg.get("hosts") or {}
        if host_alias not in hosts:
            db.log_audit(name, str(args), "host not allowed", False)
            return (
                f"хост «{host_alias}» не разрешён. Доступные: {', '.join(hosts) or 'нет'}. "
                "Хосты задаются в ~/.aya/config.json в секции ssh.hosts"
            )
        host = str(hosts[host_alias])
        ssh_exe = shutil.which("ssh") or shutil.which("ssh.exe")
        if not ssh_exe:
            return "в системе нет ssh.exe (поставь OpenSSH клиент)"
        cmd = [ssh_exe, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new"]
        key = ssh_cfg.get("key")
        if key:
            key = os.path.expanduser(str(key))
            if os.path.isfile(key):
                cmd += ["-i", key]
        cmd += [host, command[:2000]]
        try:
            import subprocess as _sp

            proc = _sp.run(cmd, capture_output=True, timeout=90)
            out = _decode_out(proc.stdout or b"").rstrip()
            err = _decode_out(proc.stderr or b"").rstrip()
        except _sp.TimeoutExpired:
            return "ssh-команда не уложилась в 90 секунд — прервала"
        except Exception as e:  # noqa: BLE001
            return f"ssh не запустился: {str(e)[:120]}"
        res = out
        if err:
            res = (res + "\n[stderr] " + err) if res else "[stderr] " + err
        if not res:
            res = "(вывод пуст)"
        db.log_event("tool", f"ssh {host_alias}: {command[:60]}")
        return res[-5000:].strip()

    return f"unhandled action: {name}"


__all__ = [
    "run_action",
    "is_tool_enabled",
    "allow_tool",
    "audit",
    "KNOWN_ACTIONS",
    "PERM_APPEND",
    "PERM_CONFIGURED",
    "PERM_CONFIRMED",
    "PERM_DENIED",
    "db",
    "subprocess",
]
