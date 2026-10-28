import sys
import types

import core.voice.modelmgr as modelmgr
import core.voice.stt as stt


def test_whisper_registered_for_unload(db_reset, monkeypatch):
    monkeypatch.setattr(stt, "_model", object())

    done = modelmgr.unload_vram(["whisper"])

    assert done == ["whisper"]
    assert stt._model is None


def test_get_model_marks_whisper_used(db_reset, monkeypatch):
    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = lambda *args, **kwargs: object()
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    monkeypatch.setattr(stt, "_model", None)
    monkeypatch.delenv("AYA_STT_MODEL", raising=False)
    monkeypatch.delenv("AYA_STT_DEVICE", raising=False)
    touched = []
    monkeypatch.setattr(modelmgr, "touch", lambda *names: touched.extend(names))

    stt.get_model()

    assert "whisper" in touched
