import sqlite3
import threading
import time
import json

_conn = None
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY,
  ts REAL, role TEXT, text TEXT, mood TEXT
);
CREATE TABLE IF NOT EXISTS facts(
  id INTEGER PRIMARY KEY,
  ts REAL, key TEXT, value TEXT, source TEXT
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY,
  ts REAL, kind TEXT, summary TEXT
);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY,
  ts REAL, tool TEXT, args TEXT, result TEXT, allowed INTEGER
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY,
  created REAL, due_at REAL, kind TEXT,
  args TEXT, status TEXT, result TEXT
);
CREATE TABLE IF NOT EXISTS memories(
  msg_id INTEGER PRIMARY KEY,
  ts REAL, role TEXT, text TEXT, vec BLOB, dnorm REAL
);
"""


def init(path: str):
    global _conn
    _conn = sqlite3.connect(path, check_same_thread=False)
    try:
        # WAL: чтение не ждёт записи, commit дешевле (fsync реже) — меньше лагов UI
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA temp_store=MEMORY")
    except sqlite3.DatabaseError:
        pass
    _conn.executescript(SCHEMA)
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)")
    _conn.commit()


def execute(sql: str, params=()):
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur.lastrowid


def query(sql: str, params=()):
    with _lock:
        cur = _conn.execute(sql, params)
        return cur.fetchall()


def setting(key: str, default: str = "") -> str:
    row = query("SELECT value FROM settings WHERE key=?", (key,))
    return row[0][0] if row else default


def set_setting(key: str, value: str):
    execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))


def log_event(kind: str, summary: str):
    execute("INSERT INTO events(ts,kind,summary) VALUES(?,?,?)", (time.time(), kind, summary))


def log_audit(tool: str, args: str, result: str, allowed: bool):
    execute(
        "INSERT INTO audit_log(ts,tool,args,result,allowed) VALUES(?,?,?,?,?)",
        (time.time(), tool, args, result, int(allowed)),
    )


def add_message(role: str, text: str, mood: str = ""):
    return execute(
        "INSERT INTO messages(ts,role,text,mood) VALUES(?,?,?,?)", (time.time(), role, text, mood)
    )


def recent_messages(limit: int = 16):
    rows = query("SELECT role,text FROM messages ORDER BY id DESC LIMIT ?", (limit,))
    return list(reversed(rows))


def add_fact(key: str, value: str, source: str):
    existing = query("SELECT id FROM facts WHERE key=?", (key,))
    if existing:
        execute("UPDATE facts SET value=?, ts=?, source=? WHERE key=?", (value, time.time(), source, key))
    else:
        execute("INSERT INTO facts(ts,key,value,source) VALUES(?,?,?,?)", (time.time(), key, value, source))


def facts():
    return query("SELECT key,value,source FROM facts ORDER BY id DESC LIMIT 50")


def forget_fact(key: str):
    execute("DELETE FROM facts WHERE key=?", (key,))


def add_task(kind: str, args: dict, due_at: float) -> int:
    return execute(
        "INSERT INTO tasks(created,due_at,kind,args,status,result) VALUES(?,?,?,?, 'pending', '')",
        (time.time(), due_at, kind, json.dumps(args, ensure_ascii=False)),
    )


def set_task_status(task_id: int, status: str, result: str = ""):
    execute("UPDATE tasks SET status=?, result=? WHERE id=?", (status, result[:400], task_id))


def tasks_list():
    return query("SELECT id,created,due_at,kind,args,status,result FROM tasks ORDER BY id DESC LIMIT 30")


def task_by_id(task_id: int):
    rows = query("SELECT id,created,due_at,kind,args,status,result FROM tasks WHERE id=?", (task_id,))
    return rows[0] if rows else None


def due_tasks():
    return query("SELECT id,due_at,kind,args FROM tasks WHERE status='pending' AND due_at<=?", (time.time(),))


def cancel_task(task_id: int) -> bool:
    if not task_by_id(task_id):
        return False
    set_task_status(task_id, "cancelled")
    return True
