import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ["AYA_DISABLE_TASKS"] = "1"
os.environ["AYA_TEST_DB"] = "1"

import core.db as db  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="aya-test-")
TEST_DB = os.path.join(_TMP, "aya.db")
WS_FOLDER = os.path.join(_TMP, "ws")
os.makedirs(WS_FOLDER, exist_ok=True)

db.init(TEST_DB)
os.environ["AYA_TEST_WS"] = WS_FOLDER

import pytest  # noqa: E402


def reset_engine_data(table: str = "") -> None:
    if table:
        db.execute(f"DELETE FROM {table}")
    else:
        for t in ("messages", "events", "audit_log", "settings", "tasks", "facts"):
            db.execute(f"DELETE FROM {t}")
        db.set_setting("allowed_folder", WS_FOLDER)
        db.set_setting("perm_rate_limit", "12")


@pytest.fixture
def db_reset():
    reset_engine_data()
    yield


@pytest.fixture
def conftest_ws():
    return WS_FOLDER


@pytest.fixture
def client(tmp_path_factory, monkeypatch):
    import core.main as main  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    # свой config.json с тестовой моделью: тесты не зависят от настоящего ~/.aya
    # (у автора там рабочий ключ, на чистой машине CI — нет) и не пишут в него.
    # Нужен другой конфиг — фикстура cfg_tmp подменит этот.
    cfg = tmp_path_factory.mktemp("cfg") / "config.json"
    cfg.write_text('{"llm": {"type": "openai_compatible", "base_url": "http://127.0.0.1:9/v1", "model": "test", "api_key": "k"}}', encoding="utf-8")
    monkeypatch.setattr(main, "CONFIG_FILE", cfg)
    with TestClient(main.app) as c:
        reset_engine_data()
        try:
            yield c
        finally:
            reset_engine_data()


@pytest.fixture
def cfg_tmp(tmp_path, monkeypatch):
    """Подменённый ~/.aya/config.json (пустой), чтобы тесты не трогали настоящий."""
    import core.main as main  # noqa: PLC0415

    path = tmp_path / "config.json"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(main, "CONFIG_FILE", path)
    return path


class FakeHttp:
    """Ответы внешних сервисов по URL: get(url, json=..., status=..., exc=...)."""

    def __init__(self):
        self.routes = {}

    def get(self, url, json=None, status=200, exc=None):
        self.routes[("GET", url)] = (status, json, exc)

    def post(self, url, json=None, status=200, exc=None):
        self.routes[("POST", url)] = (status, json, exc)

    def __call__(self, method, url, **_kw):
        status, body, exc = self.routes.get((method, url), (404, None, None))
        if exc:
            raise exc
        return status, body


@pytest.fixture
def fake_http(monkeypatch):
    from core import setup  # noqa: PLC0415

    fake = FakeHttp()
    monkeypatch.setattr(setup, "_request", fake)
    return fake
