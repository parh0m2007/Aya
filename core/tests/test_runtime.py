import subprocess
import sys

import pytest


@pytest.fixture
def rt(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    from core import runtime

    runtime._STATE.clear()
    monkeypatch.setattr(runtime, "_spawn", lambda fn: fn())  # без фонового потока — синхронно
    added = []
    monkeypatch.setattr(runtime, "_add_path", lambda p: added.append(str(p)))
    runtime._added = added
    yield runtime
    runtime._STATE.clear()


CALLS = []


def _fake_uv(code: int, import_code: int = 0):
    CALLS.clear()

    def run(cmd, **kw):
        CALLS.append((cmd, kw))
        if "--target" not in cmd:  # проверка импорта после установки
            return subprocess.CompletedProcess(cmd, import_code, "", "ImportError: torchaudio" if import_code else "")
        target = cmd[cmd.index("--target") + 1]
        from pathlib import Path

        Path(target).mkdir(parents=True, exist_ok=True)
        (Path(target) / "pkg.py").write_text("x = 1")
        return subprocess.CompletedProcess(cmd, code, "", "boom" if code else "")

    return run


def test_install_success_marks_complete(rt, monkeypatch, tmp_path):
    monkeypatch.setattr(rt.subprocess, "run", _fake_uv(0))
    rt.install("gpu")
    assert rt.status()["gpu"]["state"] == "done"
    assert (tmp_path / ".aya" / "runtime" / "gpu" / ".complete").exists()
    assert str(tmp_path / ".aya" / "runtime" / "gpu") in rt.activate()


def test_install_failure_cleans_part(rt, monkeypatch, tmp_path):
    monkeypatch.setattr(rt.subprocess, "run", _fake_uv(1))
    rt.install("gpu")
    st = rt.status()["gpu"]
    assert st["state"] == "error" and st["error"]
    assert not (tmp_path / ".aya" / "runtime" / "gpu.part").exists()
    assert not (tmp_path / ".aya" / "runtime" / "gpu").exists()
    assert rt.activate() == []


def test_activate_skips_incomplete(rt, tmp_path):
    (tmp_path / ".aya" / "runtime" / "local_voices").mkdir(parents=True)
    assert rt.activate() == []
    assert rt.status()["local_voices"]["state"] == "none"


def test_unknown_pack_404(client):
    assert client.post("/api/setup/packs/evil").status_code == 404


def test_tts_prefers_downloaded_weights(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    from core.voice import tts

    assert tts._f5_ckpt_dir() == tts._F5_DIR / "ckpt"  # ничего не скачано — веса рядом с кодом (разработка)
    got = tmp_path / ".aya" / "voices-local" / "f5"
    got.mkdir(parents=True)
    (got / "model_ru_v2_inference.safetensors").write_bytes(b"w")
    (got / "vocab_ru.txt").write_text("v")
    assert tts._f5_ckpt_dir() == got


def test_packs_endpoint_lists_both(client):
    r = client.get("/api/setup/packs").json()
    assert set(r["packs"]) == {"gpu", "local_voices"} and "gpu" in r and "whisper" in r


def test_install_runs_hidden_with_base_constraints(rt, monkeypatch):
    monkeypatch.setattr(rt.subprocess, "run", _fake_uv(0))
    rt.install("gpu")
    cmd, kw = CALLS[0]
    assert "-c" in cmd and cmd[cmd.index("-c") + 1].endswith("requirements.txt")
    assert not cmd[cmd.index("--python") + 1].lower().endswith("pythonw.exe")
    if sys.platform == "win32":
        assert kw.get("creationflags", 0) & 0x08000000  # CREATE_NO_WINDOW — без окна консоли


def test_broken_set_is_not_marked_done(rt, monkeypatch, tmp_path):
    monkeypatch.setattr(rt.subprocess, "run", _fake_uv(0, import_code=1))
    rt.install("local_voices")
    st = rt.status()["local_voices"]
    assert st["state"] == "error" and "ImportError" in st["error"]
    assert not (tmp_path / ".aya" / "runtime" / "local_voices" / ".complete").exists()
    assert rt.activate() == []


def test_finalize_failure_is_error_not_stuck(rt, monkeypatch):
    monkeypatch.setattr(rt.subprocess, "run", _fake_uv(0))
    monkeypatch.setattr(rt, "_sleep", lambda s: None)

    def nope(src, dst):
        raise PermissionError("файл занят антивирусом")

    monkeypatch.setattr(rt, "_rename", nope)
    rt.install("gpu")
    assert rt.status()["gpu"]["state"] == "error"
    monkeypatch.setattr(rt.subprocess, "run", _fake_uv(0))
    assert rt.install("gpu")["state"] in ("running", "done", "error")  # повтор возможен, а не «Качаю…» навсегда


def test_weights_retry_keeps_installed_packages(rt, monkeypatch, tmp_path):
    monkeypatch.setattr(rt.subprocess, "run", _fake_uv(0))
    fails = [True]

    def weights():
        if fails[0]:
            raise OSError("сеть пропала")

    monkeypatch.setattr(rt, "download_voice_weights", weights)
    rt.install("local_voices")
    assert rt.status()["local_voices"]["state"] == "error"
    pack = tmp_path / ".aya" / "runtime" / "local_voices"
    assert (pack / ".complete").exists()
    fails[0] = False
    monkeypatch.setattr(rt.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("пакеты не переставляем")))
    rt.install("local_voices")
    assert rt.status()["local_voices"]["state"] == "done" and (pack / "pkg.py").exists()
