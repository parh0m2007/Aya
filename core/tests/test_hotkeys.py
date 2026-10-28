def test_hotkeys_roundtrip(client, db_reset):
    c = client.get("/api/config").json()
    assert c["hotkey_talk"] == "Control+Space" and c["hotkey_toggle"] == "Alt+Shift+A"
    client.post("/api/config", json={"hotkey_talk": "F9"})
    assert client.get("/api/config").json()["hotkey_talk"] == "F9"
    client.post("/api/config", json={"hotkey_talk": ""})
    assert client.get("/api/config").json()["hotkey_talk"] == "F9"
    assert client.get("/api/ui").json()["config"]["hotkey_toggle"] == "Alt+Shift+A"
