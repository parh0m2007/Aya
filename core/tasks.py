import json
import threading
import time
import traceback

from . import db
from .safety import tools

_state = None  # BehaviorState, проставляется из main


def attach_state(s):
    global _state
    _state = s


def _note_path(name):
    base = tools.os.path.join(tools.os.path.expanduser("~"), ".aya", "notes")
    tools.os.makedirs(base, exist_ok=True)
    return tools.os.path.join(base, name)


def _run_task(task_id, kind, args):
    try:
        if kind == "reminder":
            note = str(args.get("note", "") or "напоминание")
            db.log_event("task", f"reminder due: {note}")
            db.set_setting("initiative_force", f"reminder:{note}")
            db.set_task_status(task_id, "done", "передано в инициативу")
            return

        if kind == "report":
            """delegate_bg: суб-агент сработал в фоне — передаём отчёт в инициативу."""
            agent = str(args.get("agent", "")) or "суб-агент"
            row = db.query("SELECT status, result FROM tasks WHERE id=?", (task_id,))
            status, result_txt = (str(row[0][0]), str(row[0][1])) if row else ("pending", "")
            if status == "done" and result_txt.strip():
                note = f"Отчёт фонового суб-агента «{agent}»: {result_txt[:320]}"
                db.set_setting("initiative_force", f"report:{note}")
                db.set_task_status(task_id, "reported", "передано в инициативу")
                db.log_event("task", f"report due: {agent}")
            elif status == "error":
                note = f"Фоновая задача «{agent}» не удалась: {result_txt[:200]}"
                db.set_setting("initiative_force", f"report:{note}")
                db.set_task_status(task_id, "reported", "ошибка передана в инициативу")
            else:
                # суб-агент ещё трудится — вернём в очередь, перепроверим через минуту
                # (result сохраняем, из-за гонки с фоновым тредом)
                db.execute("UPDATE tasks SET due_at=?, status='pending' WHERE id=?", (time.time() + 60, task_id))
                db.log_event("task", f"report {agent}: ещё выполняется, повтор через минуту")
            return

        if kind == "digest":
            msgs = db.query("SELECT role,text FROM messages ORDER BY id DESC LIMIT 40")
            lines = [f"{('Ты' if r == 'assistant' else 'Пользователь')}: {t[:200]}" for r, t in reversed(msgs)]
            prompt = (
                "Составь краткий дайджест последних событий разговора (что обсуждали, что запомнила).\n"
                "Максимум 4 пункта, простым языком, от лица заметки:\n" + "\n".join(lines)
            )
            text = ""
            try:
                from .llm.base import make_adapter
                from . import main as _m

                cfg = _m.load_config()
                adapter = make_adapter(cfg["llm"])
                import asyncio

                async def _collect():
                    return "".join(
                        [c async for c in adapter.stream_chat("Ты пишешь заметки от имени Аи.", [{"role": "user", "content": prompt}])]
                    )

                text = asyncio.run(_collect())
            except Exception:
                traceback.print_exc()
            if not text.strip():
                text = "Дайджест: модель недоступна."
            path = _note_path(f"digest-{int(time.time())}.md")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text.strip() + "\n")
            db.set_task_status(task_id, "done", path)
            db.log_event("task", f"digest written: {path}")
            return

        db.set_task_status(task_id, "error", f"unknown kind: {kind}")
    except Exception as e:
        db.set_task_status(task_id, "error", str(e))
        traceback.print_exc()


def _loop():
    while True:
        try:
            for tid, due_at, kind, args_json in db.due_tasks():
                args = json.loads(args_json) if args_json else {}
                if kind != "reminder" and _state:
                    _state.activity = "task"
                threading.Thread(target=_run_task, args=(tid, kind, args), daemon=True).start()
        except Exception:
            traceback.print_exc()
        time.sleep(15)


def start():
    threading.Thread(target=_loop, daemon=True).start()
