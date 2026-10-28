"""Тексты образцов голоса ищутся по относительному пути — в установленном
приложении ядро лежит не в D:\Aya, а распознавать образец заново = большая загрузка."""
import sys
import types


def test_transcript_found_by_relative_path(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    import shutil

    from core.voice import tts

    # ядро «установлено» в другую папку — как у человека после установщика
    moved = tmp_path / "Programs" / "Aya" / "core" / "voice" / "f5_ru"
    (moved / "refs_real").mkdir(parents=True)
    shutil.copy(tts._trans_path, moved / "refs_real" / "transcripts.json")
    monkeypatch.setattr(tts, "_F5_DIR", moved)
    monkeypatch.setattr(tts, "_trans_path", moved / "refs_real" / "transcripts.json")
    boom = types.ModuleType("f5_tts.infer.utils_infer")
    boom.preprocess_ref_audio_text = lambda *a, **k: (_ for _ in ()).throw(AssertionError("не должна распознавать заново"))
    monkeypatch.setitem(sys.modules, "f5_tts.infer.utils_infer", boom)
    tts._refs.clear()
    path, txt = tts._real_ref("f1")
    assert txt.startswith("Извините") and path.endswith("female_1.wav")
    _path, qtxt = tts._qwen_ref_text()
    assert qtxt  # образец Qwen — female_3, текст есть в файле


def test_new_transcript_goes_to_user_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    from core.voice import tts

    tts._transcript_put("refs_real/new.wav", "новый текст")
    assert tts._transcript_get("refs_real/new.wav") == "новый текст"
    assert (tmp_path / ".aya" / "voices-local" / "transcripts.json").exists()
