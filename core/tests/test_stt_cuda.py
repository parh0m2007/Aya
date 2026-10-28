"""Видеокарта есть, а библиотек CUDA нет (базовый набор без «ускорения»):
распознавание должно работать на CPU, а не ломаться на первой фразе."""
import sys
import types

import core.voice.stt as stt


class FakeModel:
    def __init__(self, size, device, compute_type):
        self.device = device
        FakeModel.made.append(device)

    def transcribe(self, path, **kw):
        if self.device == "cuda" and FakeModel.cuda_breaks:
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return iter([types.SimpleNamespace(text=" привет ")]), None


def _fake(monkeypatch, cuda_libs: bool, cuda_breaks: bool = False):
    FakeModel.made = []
    FakeModel.cuda_breaks = cuda_breaks
    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)
    monkeypatch.setattr(stt, "_model", None)
    monkeypatch.setattr(stt, "_force_cpu", False)
    monkeypatch.setattr(stt, "cuda_libs_available", lambda: cuda_libs)
    monkeypatch.delenv("AYA_STT_DEVICE", raising=False)
    monkeypatch.delenv("AYA_STT_MODEL", raising=False)


def test_auto_without_cuda_libs_uses_cpu(db_reset, monkeypatch):
    _fake(monkeypatch, cuda_libs=False)
    stt.get_model()
    assert FakeModel.made == ["cpu"]


def test_auto_with_cuda_libs_uses_gpu(db_reset, monkeypatch):
    _fake(monkeypatch, cuda_libs=True)
    stt.get_model()
    assert FakeModel.made == ["cuda"]


def test_cuda_failure_at_transcribe_falls_back_to_cpu(db_reset, monkeypatch):
    _fake(monkeypatch, cuda_libs=True, cuda_breaks=True)
    assert stt.transcribe(b"RIFF") == "привет"
    assert FakeModel.made == ["cuda", "cpu"]
    assert stt._info["device"] == "cpu"
