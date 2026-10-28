import json
from pathlib import Path


def _make_model(home: Path, name: str) -> Path:
    src = home / ".aya" / "models" / name
    src.mkdir(parents=True)
    data = {"Version": 3, "FileReferences": {"Moc": f"{name}.moc3"}}
    (src / f"{name}.model3.json").write_text(json.dumps(data), encoding="utf-8")
    (src / f"{name}.moc3").write_bytes(b"MOC3")
    return src


def test_model_route_serves_from_user_folder(tmp_path, monkeypatch, client):
    home = tmp_path / "home"
    _make_model(home, "demo")

    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))

    r = client.get("/models/demo/demo.model3.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["Version"] == 3

    moc = client.get("/models/demo/demo.moc3")
    assert moc.status_code == 200
    assert moc.content == b"MOC3"


def test_model_route_falls_back_to_bundled_public(tmp_path, monkeypatch, client):
    bundled = tmp_path / "work" / "public" / "models" / "bundled"
    bundled.mkdir(parents=True)
    (bundled / "Bundled.model3.json").write_text('{"Version": 3}', encoding="utf-8")
    monkeypatch.chdir(tmp_path / "work")

    r = client.get("/models/bundled/Bundled.model3.json")
    assert r.status_code == 200
    assert r.json()["Version"] == 3


def test_model_select_updates_config(tmp_path, monkeypatch, client):
    home = tmp_path / "home"
    _make_model(home, "demo")

    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))

    r = client.post("/api/models", json={"name": "demo"})
    assert r.json() == {"ok": True, "file": "demo/demo.model3.json"}
    assert client.get("/api/config").json()["live2d_model_file"] == "demo/demo.model3.json"
