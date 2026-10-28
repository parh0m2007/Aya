import json
import os

from fastapi.testclient import TestClient


def test_api_smoke(client):
    # state
    r = client.get("/api/state")
    assert r.status_code == 200 and "mood" in r.json()

    # config roundtrip
    r = client.post("/api/config", json={"tts_volume": 155})
    assert r.json()["ok"] is True
    assert client.get("/api/config").json()["tts_volume"] == 155

    # workspace roundtrip
    ws = os.environ["AYA_TEST_WS"]
    assert client.post("/api/workspace", json={"folder": ws}).json()["ok"] is True
    assert client.get("/api/workspace").json()["folder"] == ws
    assert client.post("/api/workspace", json={"folder": "Z:/nope"}).json()["ok"] is False

    # permissions roundtrip
    r = client.post("/api/permissions", json={"tool": "write_allowed_file", "enabled": False})
    assert r.json()["ok"] is True
    assert client.get("/api/permissions").json()["write_allowed_file"] is False

    # confirm for unknown id
    r = client.post("/api/action/confirm", json={"id": "ghost", "approve": True})
    assert r.json()["ok"] is False

    # tasks add/cancel
    r = client.post("/api/tasks", json={"kind": "reminder", "minutes": 7, "note": "тест"})
    tid = r.json()["id"]
    assert tid > 0
    assert any(t["id"] == tid for t in client.get("/api/tasks").json()["tasks"])
    assert client.post("/api/tasks/cancel", json={"id": tid}).json()["ok"] is True

    # initiative endpoint shape (fresh state -> most likely no topic)
    r = client.get("/api/initiative")
    assert r.status_code == 200 and set(r.json()) >= {"topic", "reply"}

    # memory vanish on empty
    assert client.get("/api/memory").json()["facts"] == []

    # diag endpoint writes + rotates
    r = client.post("/api/_diag", json={"tag": "pytest-note"})
    assert r.json()["ok"] is True
    from core import paths

    assert (paths.logs_dir() / "tts-diag.log").exists()  # логи — в ~/.aya/logs


def test_stream_error_answer(client, monkeypatch):
    """broken llm must still end with done + reply (frontend pump needs final line)."""
    import core.main as main
    from core.llm.base import BaseAdapter

    class Broken(BaseAdapter):
        name = "broken"

        async def stream_chat(self, *a, **k):
            raise RuntimeError("нет сети")
            yield b""  # pragma: no cover

    monkeypatch.setattr(main, "make_adapters", lambda cfg: [Broken()])
    with client.stream("POST", "/api/chat/stream", json={"text": "привет"}) as r:
        assert r.status_code == 200
        lines = [ln for ln in r.iter_lines() if ln.strip()]
    assert lines, "empty stream"
    done = json.loads(lines[-1])
    assert done.get("done") is True
    assert done.get("error") == "нет сети"
