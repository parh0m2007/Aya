"""MCP-хост: управление серверами Model Context Protocol (stdio).

mcp.json в ~/.aya:
{
  "servers": {
    "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:\\docs"], "enabled": false}
  }
}

Каждый сервер — отдельный процесс, общение через JSON-RPC 2.0 (newline-delimited).
Инструменты видны как mcp_<сервер>_<инструмент>; вызовы проходят через
safety.gateway (rate limit + perm:mcp_<сервер> + аудит).
"""
import json
import os
import re
import subprocess
import threading
import time
import queue

from . import db

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "aya", "version": "0.1"}
REQUEST_TIMEOUT = 60.0
TOOL_TIMEOUT = 120.0


class McpError(Exception):
    pass


class McpServer:
    """Один подключённый MCP-сервер (stdio)."""

    def __init__(self, name: str, command: str, args: list, env: dict | None = None, cwd: str | None = None):
        self.name = name
        self.command = command
        self.args = args
        self.env_extra = env or {}
        self.cwd = cwd
        self.proc: subprocess.Popen | None = None
        self.tools: list[dict] = []
        self._next_id = 1
        self._lock = threading.Lock()
        self._replies: dict[int, queue.Queue] = {}
        self._reader: threading.Thread | None = None
        self.alive = False
        self.error = ""

    # ── транспорт ──────────────────────────────────────────────
    def _env(self) -> dict:
        env = os.environ.copy()
        env.pop("APPDATA", None) or env.setdefault("APPDATA", "")
        # кириллица в аргументах/ответах python-серверов
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.update(self.env_extra)
        return env

    def _read_loop(self):
        """Читает строки-сообщения и раскладывает ответы по pending id."""
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            rid = msg.get("id")
            if rid is not None and rid in self._replies:
                self._replies[rid].put(msg)
            # notifications/requests от сервера игнорируем

    # ── lifecycle ──────────────────────────────────────────────
    def start(self):
        """Стартует сервер. Замечание: python-серверы следует запускать с `-u`
        (например cmd='python', args=['-u', 'srv.py']) — без него stdout буферизуется и ответы висят."""
        self.stop()
        self.error = ""
        try:
            self.proc = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._env(),
                cwd=self.cwd or None,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as e:
            self.error = str(e)
            raise McpError(f"не удалось запустить {self.name}: {e}") from e
        self.alive = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _rpc(self, method: str, params: dict | None, timeout: float) -> dict:
        rid = self._next_id
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        q: queue.Queue = queue.Queue()
        with self._lock:
            if not (self.proc and self.proc.stdin and self.proc.poll() is None):
                raise McpError(f"сервер {self.name} не запущен")
            self._replies[rid] = q
            try:
                self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            except (OSError, ValueError) as e:
                self._replies.pop(rid, None)
                self.alive = False
                self.error = str(e)
                raise McpError(f"сервер {self.name} отвалился: {e}") from e
        try:
            resp = q.get(timeout=timeout)
        except queue.Empty:
            self._replies.pop(rid, None)
            raise McpError(f"таймаут {timeout:.0f}с на {self.name}.{method}")
        self._replies.pop(rid, None)
        return self._handle(resp, method)

    def _handle(self, resp, method: str) -> dict:
        if resp is None:
            raise McpError(f"сервер {self.name} не ответил на {method}")
        if "error" in resp:
            err = resp.get("error") or {}
            raise McpError(f"{method}: {err.get('message', err)}")
        return resp.get("result") or {}

    def initialize(self) -> dict:
        info = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            REQUEST_TIMEOUT,
        )
        # уведомление initialized (без id — сервер не отвечает)
        try:
            with self._lock:
                self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
                self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise McpError(str(e)) from e
        return info

    def load_tools(self) -> list[dict]:
        """tools/list → [{name, description, inputSchema}] c префиксом сервера."""
        return self._rpc("tools/list", {}, REQUEST_TIMEOUT).get("tools") or []

    def call(self, tool: str, args: dict) -> str:
        res = self._rpc("tools/call", {"name": tool, "arguments": args}, TOOL_TIMEOUT)
        if res.get("isError"):
            raise Exception(self._content_text(res))
        return self._content_text(res)

    @staticmethod
    def _content_text(res: dict) -> str:
        parts = res.get("content") or []
        chunks = []
        for p in parts:
            if p.get("type") == "text":
                chunks.append(str(p.get("text", "")))
            elif p.get("type") == "resource":
                chunks.append(str(p.get("text") or p.get("uri") or ""))
        if not chunks and res.get("structuredContent"):
            chunks.append(json.dumps(res["structuredContent"], ensure_ascii=False))
        return "\n".join(chunks)

    def stop(self):
        if self.proc:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.terminate()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except OSError:
                    pass
            self.proc = None
        self.alive = False
        self.tools = []


# ── реестр ─────────────────────────────────────────────────────
class Registry:
    """Все MCP-серверы из ~/.aya/mcp.json."""

    def __init__(self):
        self.servers: dict[str, McpServer] = {}
        self.connected: dict[str, bool] = {}
        self.errors: dict[str, str] = {}

    @staticmethod
    def config_path() -> str:
        return os.path.join(os.path.expanduser("~"), ".aya", "mcp.json")

    @staticmethod
    def config() -> dict:
        try:
            with open(Registry.config_path(), encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    def start_all(self, enabled_only: bool = True, silent: bool = False):
        cfg = self.config()
        servers = cfg.get("servers") or {}
        for name, spec in servers.items():
            if not isinstance(spec, dict) or not spec.get("command"):
                continue
            if enabled_only and not spec.get("enabled", False):
                continue
            srv = McpServer(name, str(spec["command"]), [str(a) for a in spec.get("args", [])],
                            spec.get("env") or {}, spec.get("cwd"))
            self.servers[name] = srv
        for name, srv in list(self.servers.items()):
            try:
                srv.start()
                srv.initialize()
                tools = srv.load_tools()
                srv.tools = tools
                self.connected[name] = True
                self.errors.pop(name, None)
                if not silent:
                    db.log_event("mcp", f"подключён сервер {name} ({len(tools)} инструментов)")
            except Exception as e:  # McpError и прочее
                self.connected[name] = False
                self.errors[name] = str(e)
                if not silent:
                    db.log_event("mcp", f"ошибка сервера {name}: {e}")

    def stop_all(self):
        for srv in self.servers.values():
            srv.stop()
        self.servers.clear()
        self.connected.clear()

    def tool_map(self) -> dict[str, tuple[str, str, dict]]:
        """mcp_<srv>_<tool> → (server, tool, input_schema)."""
        out: dict[str, tuple[str, str, dict]] = {}
        for sname, srv in self.servers.items():
            if not self.connected.get(sname):
                continue
            for t in srv.tools:
                tool = t.get("name", "")
                if not tool:
                    continue
                key = f"mcp_{sname}_{tool}"
                out[key] = (sname, tool, t.get("inputSchema") or {})
        return out

    def status(self) -> dict:
        out = {}
        for name, srv in self.servers.items():
            out[name] = {
                "connected": self.connected.get(name, False),
                "error": self.errors.get(name, ""),
                "tools": [
                    {
                        "key": f"mcp_{name}_{t.get('name', '')}",
                        "name": t.get("name", ""),
                        "description": (t.get("description") or "")[:140],
                    }
                    for t in srv.tools
                ],
            }
        return out

    def call_tool(self, key: str, args: dict) -> str:
        m = self.tool_map()
        if key not in m:
            raise McpError(f"нет такого MCP-инструмента: {key}")
        server_name, tool, _schema = m[key]
        srv = self.servers.get(server_name)
        if not srv:
            raise McpError(f"сервер {server_name} недоступен")
        return srv.call(tool, args)


REGISTRY = Registry()

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


def validate_spec(name: str, spec: dict) -> dict:
    name = str(name or "").strip()
    if not NAME_RE.match(name):
        raise ValueError("имя: 1–48 символов, латиница в нижнем регистре, цифры и дефис")
    if not isinstance(spec, dict):
        raise ValueError("описание сервера должно быть объектом")
    command = str(spec.get("command") or "").strip()
    if not command:
        raise ValueError("команда не может быть пустой")
    raw_args = spec.get("args") or []
    if not isinstance(raw_args, list) or any(not isinstance(a, str) for a in raw_args):
        raise ValueError("аргументы — список строк")
    raw_env = spec.get("env") or {}
    if not isinstance(raw_env, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in raw_env.items()):
        raise ValueError("env — объект строка→строка")
    cwd = str(spec.get("cwd") or "").strip()
    if cwd and not os.path.isdir(cwd):
        raise ValueError("рабочая папка не существует")
    norm = {
        "command": command,
        "args": [a.strip() for a in raw_args],
        "enabled": bool(spec.get("enabled", False)),
    }
    if raw_env:
        norm["env"] = {str(k): str(v) for k, v in raw_env.items()}
    if cwd:
        norm["cwd"] = cwd
    return norm


def write_config(cfg: dict) -> None:
    path = Registry.config_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save_server(name: str, spec: dict, old_name: str = "") -> dict:
    name = str(name or "").strip()
    norm = validate_spec(name, spec)
    old = str(old_name or name).strip()
    cfg = Registry.config()
    servers = cfg.setdefault("servers", {})
    if old != name:
        if name in servers:
            raise ValueError(f"сервер {name} уже существует")
        if old not in servers:
            raise ValueError(f"нет сервера {old}")
        old_perm = db.setting(f"perm:mcp_{old}", "0")
        del servers[old]
        servers[name] = norm
        write_config(cfg)
        db.set_setting(f"perm:mcp_{name}", old_perm)
        db.execute("DELETE FROM settings WHERE key=?", (f"perm:mcp_{old}",))
    else:
        servers[name] = norm
        write_config(cfg)
    return norm


def delete_server(name: str) -> bool:
    name = str(name or "").strip()
    cfg = Registry.config()
    servers = cfg.get("servers") or {}
    if name not in servers:
        return False
    del servers[name]
    write_config(cfg)
    db.execute("DELETE FROM settings WHERE key=?", (f"perm:mcp_{name}",))
    return True


def test_server(name: str, spec: dict) -> dict:
    norm = validate_spec(name, spec)
    srv = McpServer(name, norm["command"], norm["args"], norm.get("env"), norm.get("cwd"))
    try:
        srv.start()
        srv.initialize()
        tools = srv.load_tools()
        return {
            "ok": True,
            "tools": [
                {"name": str(t.get("name", "")), "description": (t.get("description") or "")[:140]}
                for t in tools
            ],
            "error": "",
        }
    except Exception as e:
        return {"ok": False, "tools": [], "error": str(e)}
    finally:
        srv.stop()


def start_registered(name: str, spec: dict) -> str:
    stop_registered(name)
    srv = McpServer(name, str(spec["command"]), [str(a) for a in spec.get("args", [])],
                    spec.get("env") or {}, spec.get("cwd"))
    try:
        srv.start()
        srv.initialize()
        srv.tools = srv.load_tools()
    except Exception as e:
        srv.stop()
        REGISTRY.connected[name] = False
        REGISTRY.errors[name] = str(e)
        return str(e)
    REGISTRY.servers[name] = srv
    REGISTRY.connected[name] = True
    REGISTRY.errors.pop(name, None)
    return ""


def stop_registered(name: str) -> None:
    srv = REGISTRY.servers.pop(name, None)
    if srv:
        srv.stop()
    REGISTRY.connected[name] = False
    REGISTRY.errors.pop(name, None)


def permitted(server_name: str) -> bool:
    return db.setting(f"perm:mcp_{server_name}", "0") == "1"


def set_permitted(server_name: str, val: bool):
    db.set_setting(f"perm:mcp_{server_name}", "1" if val else "0")


def tool_instructions() -> str:
    """Кусок текста в системный промпт: список MCP-инструментов."""
    tm = REGISTRY.tool_map()
    if not tm:
        return ""
    lines = []
    for key, (sname, tool, schema) in sorted(tm.items()):
        srv = REGISTRY.servers[sname]
        desc = next((t.get("description") or "" for t in srv.tools if t.get("name") == tool), "").replace("\n", " ")[:160]
        args_doc = _args_doc(schema)
        lines.append(f"- {key} {args_doc} — {desc}" if desc else f"- {key} {args_doc}")
    return "\nMCP-инструменты (вызываются той же JSON-строкой, action.name = ключ):\n" + "\n".join(lines)


def _args_doc(schema: dict) -> str:
    props = schema.get("properties") or {}
    if not props:
        return "{}"
    req = set(schema.get("required") or [])
    parts = []
    for pname, spec in list(props.items())[:12]:
        mark = "*" if pname in req else ""
        parts.append(f"{pname}{mark}: {str(spec.get('type', 'string'))}")
    return "{" + ", ".join(parts) + "}"


def tool_summary() -> list[dict]:
    return [
        {
            "key": k,
            "server": s,
            "tool": t,
            "description": next((x.get("description") or "" for x in REGISTRY.servers[s].tools if x.get("name") == t), "")[:120],
        }
        for k, (s, t, _sch) in sorted(REGISTRY.tool_map().items())
    ]


def remove_all_mcp_tool_names(all_tools: set[str]) -> set[str]:
    """Отфильтровывает mcp_* из общего набора (для /api/tools)."""
    return {n for n in all_tools if not n.startswith("mcp_")}
