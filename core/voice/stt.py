"""Распознавание речи (faster-whisper).

На видеокарте NVIDIA в ~3 раза быстрее CPU (0.8 с против 2.5 с на 13-секундной
фразе). Библиотеки cuBLAS/cuDNN берём из набора «ускорение» (~/.aya/runtime/gpu)
или из установленного torch. Их нет — работаем на CPU: драйвер NVIDIA сам по себе
не спасает, ошибка «cublas64_12.dll не найден» вылезла бы только на первой фразе.
Если CUDA всё же падает при распознавании — переключаемся на CPU и повторяем.
"""
import os
import threading
import uuid

from . import modelmgr

_model = None
_info = {"device": "", "model": "", "error": ""}
_lock = threading.Lock()
_dll_done = False
_force_cpu = False  # CUDA уже подвела в этом запуске — дальше только CPU
_game = False  # игровой режим: лёгкая модель на 2 ядрах процессора

MODELS = ("base", "small", "medium", "large-v3-turbo")
# Замер на русской речи (фраза 2,5–4 с): small на всех ядрах — 1,6 с,
# small на 2 ядрах — 2,2 с, base на 2 ядрах — 0,7 с и в разы меньше нагрузки.
GAME_MODEL = "base"
GAME_THREADS = 2


def _setting(key: str, default: str) -> str:
    try:
        from .. import db

        return db.setting(key, default)
    except Exception:  # noqa: BLE001 — БД ещё не открыта
        return default


def _unload() -> bool:
    global _model
    # в режиме «Ая, …» микрофон слушает постоянно — выгружать распознавание незачем
    if _setting("wake_word", "0") == "1" and _model is not None:
        return False
    with _lock:
        if _model is None:
            return False
        _model = None
        return True


modelmgr.register("whisper", _unload)


def _add_cuda_dlls() -> None:
    """cuBLAS/cuDNN для ctranslate2 — из папки torch/lib (там уже лежат нужные DLL)."""
    global _dll_done
    if _dll_done or os.name != "nt":
        return
    _dll_done = True
    try:
        import importlib.util

        spec = importlib.util.find_spec("torch")
        if not spec or not spec.origin:
            return
        lib = os.path.join(os.path.dirname(spec.origin), "lib")
        if os.path.isdir(lib):
            os.add_dll_directory(lib)
            os.environ["PATH"] = lib + os.pathsep + os.environ.get("PATH", "")
    except Exception:  # noqa: BLE001
        pass


def wanted() -> tuple[str, str]:
    size = os.environ.get("AYA_STT_MODEL") or _setting("stt_model", "small")
    if size not in MODELS:
        size = "small"
    if _game:
        # видеокарта и все ядра нужны игре; лёгкую модель в игре не скачиваем (пинг)
        return (GAME_MODEL if cached(GAME_MODEL) else size), "cpu"
    device = os.environ.get("AYA_STT_DEVICE") or _setting("stt_device", "auto")
    if device not in ("auto", "cuda", "cpu"):
        device = "auto"
    return size, device


def threads() -> int:
    """Сколько ядер даём распознаванию: 0 — сколько решит библиотека (все)."""
    return GAME_THREADS if _game else 0


def cached(size: str) -> bool:
    """Модель уже на диске (не будет скачиваться)."""
    try:
        from faster_whisper.utils import download_model

        download_model(size, local_files_only=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def prefetch_game_model() -> None:
    """Один раз заранее скачать лёгкую модель (~150 МБ) — не во время игры."""
    if cached(GAME_MODEL):
        return
    from .. import runtime

    runtime.prefetch_whisper(GAME_MODEL)


def _preload() -> None:
    try:
        with _lock:
            get_model()
    except Exception:  # noqa: BLE001 — загрузится при первой фразе
        pass


def set_game(on: bool) -> None:
    """Игра началась/закончилась: выгрузить текущую модель и заранее загрузить
    нужную (лёгкая — за полсекунды), чтобы первая фраза не ждала."""
    global _model, _game
    with _lock:
        if _game == on:
            return
        _game = on
        _model = None
    _info.update(device="", model="")
    threading.Thread(target=_preload, daemon=True, name="aya-stt-preload").start()


def cuda_libs_available() -> bool:
    """Есть ли cuBLAS/cuDNN: набор «ускорение» или torch (среда разработки)."""
    import importlib.util

    try:
        from .. import runtime

        if runtime._installed("gpu"):
            return True
    except Exception:  # noqa: BLE001
        pass
    return importlib.util.find_spec("torch") is not None


def get_model():
    global _model
    if _model is not None:
        modelmgr.touch("whisper")
        return _model
    from faster_whisper import WhisperModel

    size, device = wanted()
    _info["error"] = ""
    use_cuda = not _force_cpu and (device == "cuda" or (device == "auto" and cuda_libs_available()))
    if use_cuda:
        _add_cuda_dlls()
        try:
            _model = WhisperModel(size, device="cuda", compute_type="int8_float16")
            _info.update(device="cuda", model=size)
        except Exception as e:  # noqa: BLE001 — нет CUDA/DLL/памяти
            _model = None
            _info["error"] = f"GPU недоступна: {str(e)[:160]}"
    if _model is None:
        _model = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=threads())
        _info.update(device="cpu", model=size)
    modelmgr.touch("whisper")
    return _model


def _run(path: str) -> str:
    segments, _info_ = get_model().transcribe(
        path,
        language="ru",
        beam_size=1,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        condition_on_previous_text=False,
    )
    return " ".join(s.text.strip() for s in segments).strip()


def _cuda_failed(e: Exception) -> None:
    """CUDA подвела на распознавании (нет cuBLAS/cuDNN, мало памяти) — до перезапуска только CPU."""
    global _model, _force_cpu
    _force_cpu = True
    _model = None
    _info["error"] = f"GPU недоступна: {str(e)[:160]}"


def reset() -> None:
    """Сменили модель/устройство в настройках — перегрузим при следующей фразе."""
    global _model, _force_cpu
    with _lock:
        _model = None
        _force_cpu = False  # человек сам выбрал устройство — пробуем снова
    _info.update(device="", model="")


def status() -> dict:
    size, device = wanted()
    return {"model": size, "device": device, "loaded": _model is not None, "active_device": _info["device"], "error": _info["error"], "models": list(MODELS), "game": _game}


def _bad_audio(e: Exception) -> bool:
    """Запись не читается (PyAV): короткий щелчок кнопкой, оборванный webm.
    Это «ничего не сказали», а не сбой распознавания и не сбой видеокарты."""
    return type(e).__module__.split(".")[0] == "av"


def transcribe(wav_bytes: bytes) -> str:
    import tempfile

    tmp = os.path.join(tempfile.gettempdir(), f"aya-rec-{uuid.uuid4().hex[:8]}.webm")
    try:
        with open(tmp, "wb") as f:
            f.write(wav_bytes)
        with _lock:  # модель whisper не любит параллельные вызовы (микрофон + мост)
            try:
                text = _run(tmp)
            except Exception as e:  # noqa: BLE001
                if _bad_audio(e):
                    return ""
                if _info.get("device") != "cuda":
                    raise
                _cuda_failed(e)
                text = _run(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return text
