from pathlib import Path


def test_paths_live_in_home(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    from core import paths

    base = tmp_path / ".aya"
    for fn, sub in ((paths.data_dir, ""), (paths.logs_dir, "logs"), (paths.runtime_dir, "runtime"), (paths.voices_local_dir, "voices-local")):
        p = fn()
        assert isinstance(p, Path) and p == (base / sub if sub else base) and p.is_dir()
