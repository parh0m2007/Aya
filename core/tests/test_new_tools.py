"""Новые инструменты: чтение файлов, grep, cmd (через подтверждение), web (mock), ssh (конфиг)."""
import asyncio
import os
import sys

from core.safety import gateway, tools


def _enable(tool: str) -> None:
    tools.allow_tool(tool, True)
    tools.db.set_setting("allowed_folder", os.environ["AYA_TEST_WS"])


def test_read_allowed_file(conftest_ws):
    _enable("read_allowed_file")
    p = os.path.join(conftest_ws, "note.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("строка один\nстрока два\nстрока три\n")
    res = tools.run_action("read_allowed_file", {"name": "note.txt"})
    assert "строка два" in res
    assert "строки 1–3 из 3" in res


def test_read_file_offset(conftest_ws):
    _enable("read_allowed_file")
    p = os.path.join(conftest_ws, "code.py")
    with open(p, "w", encoding="utf-8") as f:
        f.write("a=1\nb=2\nc=3\nd=4\n")
    res = tools.run_action("read_allowed_file", {"name": "code.py", "offset": 2, "limit": 2})
    assert "b=2" in res and "c=3" in res and "a=1" not in res


def test_read_file_inside_subfolder(conftest_ws):
    _enable("read_allowed_file")
    sub = os.path.join(conftest_ws, "pkg")
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "mod.py"), "w", encoding="utf-8") as f:
        f.write("x = 1\n")
    res = tools.run_action("read_allowed_file", {"name": "pkg/mod.py"})
    assert "x = 1" in res


def test_read_file_outside_rejected(conftest_ws):
    _enable("read_allowed_file")
    res = tools.run_action("read_allowed_file", {"name": "../../etc/passwd"})
    assert "нет такого файла" in res or "недопустимое" in res or "вне рабочей" in res


def test_grep_folder(conftest_ws):
    _enable("grep_folder")
    sub = os.path.join(conftest_ws, "proj")
    os.makedirs(os.path.join(sub, "node_modules"), exist_ok=True)
    with open(os.path.join(sub, "main.py"), "w", encoding="utf-8") as f:
        f.write("def hello():\n    print('hello world')\n")
    with open(os.path.join(sub, "node_modules", "junk.js"), "w", encoding="utf-8") as f:
        f.write("hello world from node_modules\n")
    res = tools.run_action("grep_folder", {"query": "hello world"})
    assert "main.py:2" in res
    assert "node_modules" not in res


def test_grep_nothing(conftest_ws):
    _enable("grep_folder")
    res = tools.run_action("grep_folder", {"query": "zzzzqqqq"})
    assert "ничего не нашла" in res


def test_open_app_url(monkeypatch, conftest_ws):
    _enable("open_app")
    calls = []
    monkeypatch.setattr(tools.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    res = tools.run_action("open_app", {"target": "https://duckduckgo.com"})
    assert "открыла" in res
    assert calls and "start" in calls[0] and "https://duckduckgo.com" in calls[0]


def test_open_app_any_exe(monkeypatch, conftest_ws):
    _enable("open_app")
    calls = []
    monkeypatch.setattr(tools.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(os, "startfile", lambda p: calls.append(("startfile", p)))
    import shutil

    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    res = tools.run_action("open_app", {"app_id": "zoom"})
    assert "zoom" in res
    assert calls


def test_open_app_rejects_garbage(conftest_ws):
    _enable("open_app")
    res = tools.run_action("open_app", {"app_id": "notepad & calc"})
    assert "не поняла" in res


def test_open_app_finds_start_menu(monkeypatch, tmp_path, conftest_ws):
    """Поиск приложения по ярлыку в меню Пуск (telegram и иже с ними)."""
    _enable("open_app")
    programs = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Myapp"
    programs.mkdir(parents=True)
    (programs / "Myapp.lnk").write_bytes(b"")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramData", str(tmp_path / "no-such"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "no-such-local"))
    import shutil

    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    opened = []
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p))
    res = tools.run_action("open_app", {"app_id": "myapp"})
    assert res == "открыла myapp"
    assert opened and opened[0].endswith("Myapp.lnk")


def test_open_app_honest_when_not_found(monkeypatch, tmp_path, conftest_ws):
    _enable("open_app")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramData", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    import shutil

    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(tools.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    res = tools.run_action("open_app", {"app_id": "nonexistentapp"})
    assert "попробовала запустить" in res
    assert calls and "start" in calls[0]


def _fake_httpx(html: str):
    class FakeResp:
        text = html

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return FakeResp()

        def get(self, *a, **k):
            return FakeResp()

    return type("httpx", (), {"Client": FakeClient})


def test_web_search_mock(monkeypatch, conftest_ws):
    _enable("web_search")
    html = (
        '<div class="results">'
        '<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage">Пример страницы</a>'
        '<a class="result__snippet" >какой-то текст</a>'
        "</div>"
    )
    monkeypatch.setitem(sys.modules, "httpx", _fake_httpx(html))
    res = tools.run_action("web_search", {"query": "пример"})
    assert "https://example.com/page" in res
    assert "Пример страницы" in res


def test_web_open_mock(monkeypatch, conftest_ws):
    _enable("web_open")
    html = "<html><head><style>x{}</style></head><body><h1>Заголовок</h1><p>Текст страницы</p><script>evil()</script></body></html>"
    monkeypatch.setitem(sys.modules, "httpx", _fake_httpx(html))
    res = tools.run_action("web_open", {"url": "example.com"})
    assert "Текст страницы" in res
    assert "evil()" not in res
    assert "Заголовок" in res


def test_run_cmd_confirm_flow(client, conftest_ws):
    """run_cmd требует подтверждения; после approve — реально выполняется."""
    import core.main as main  # noqa: F401

    _enable("run_cmd")
    res = asyncio.run(gateway.dispatch({"name": "run_cmd", "args": {"command": "Write-Output 'privet'"}}))
    assert isinstance(res, dict) and "confirm_id" in res
    pid = res["confirm_id"]
    out = gateway.resolve_confirm(pid, True)
    assert out["applied"] is True
    assert "privet" in out["result"]


def test_run_cmd_declined(client, conftest_ws):
    _enable("run_cmd")
    res = asyncio.run(gateway.dispatch({"name": "run_cmd", "args": {"command": "Write-Output 'x'"}}))
    out = gateway.resolve_confirm(res["confirm_id"], False)
    assert out["applied"] is False


def test_ssh_not_configured(conftest_ws):
    _enable("ssh_run")
    res = tools.run_action("ssh_run", {"host": "prod", "command": "ls"})
    assert "не разрешён" in res


def test_delegate_cannot_run_cmd(conftest_ws):
    """Суб-агент не должен получать run_cmd (защита от prompt-инъекций)."""
    from core import agents

    assert "run_cmd" in agents.DELEGATE_BLOCKED_TOOLS
    assert "ssh_run" in agents.DELEGATE_BLOCKED_TOOLS
