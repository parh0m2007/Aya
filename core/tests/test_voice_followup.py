def test_followup_defaults(client):
    cfg = client.get("/api/config").json()
    assert cfg["followup"] is False
    assert cfg["followup_sec"] == 6


def test_followup_config_roundtrip_and_clamp(client):
    assert client.post("/api/config", json={"followup": True, "followup_sec": 12}).json()["ok"] is True
    cfg = client.get("/api/config").json()
    assert cfg["followup"] is True
    assert cfg["followup_sec"] == 12

    client.post("/api/config", json={"followup_sec": 99})
    assert client.get("/api/config").json()["followup_sec"] == 30

    client.post("/api/config", json={"followup_sec": 1})
    assert client.get("/api/config").json()["followup_sec"] == 2

    client.post("/api/config", json={"followup": False})
    assert client.get("/api/config").json()["followup"] is False
