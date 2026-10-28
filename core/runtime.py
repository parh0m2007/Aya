"""Докачка тяжёлого по кнопке: наборы пакетов и веса локальных голосов.

Наборы ставит встроенный `uv` в ~/.aya/runtime/<набор> (папка переживает
обновление приложения). Ставим во временную `<набор>.part` и переименовываем
только после успеха — половина набора никогда не попадёт в sys.path.
"""
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from . import paths

CORE_DIR = Path(__file__).resolve().parent

PACKS: dict[str, dict] = {
    "gpu": {"req": "requirements-gpu.txt", "size_mb": 1100, "title": "Ускорение распознавания (видеокарта NVIDIA)"},
    "local_voices": {"req": "requirements-local-voices.txt", "size_mb": 4800, "title": "Локальные голоса"},
}

_LOCK = threading.Lock()
_STATE: dict[str, dict] = {}  # name -> {"state": "running"|"error", "error": str}
_WHISPER = {"state": "none", "model": ""}
_MARK = ".complete"


def _pack_dir(name: str) -> Path:
    return paths.runtime_dir() / name


def _installed(name: str) -> bool:
    return (_pack_dir(name) / _MARK).is_file()


def _spawn(fn) -> None:
    threading.Thread(target=fn, daemon=True, name="aya-runtime").start()


def _add_path(p: Path) -> None:
    """Подключить папку набора: модули Python и DLL (nvidia/*/bin, torch/lib)."""
    s = str(p)
    if s not in sys.path:
        sys.path.append(s)
    if os.name != "nt":
        return
    for dll in [*p.glob("nvidia/*/bin"), p / "torch" / "lib"]:
        if dll.is_dir():
            try:
                os.add_dll_directory(str(dll))
            except OSError:
                pass
            os.environ["PATH"] = str(dll) + os.pathsep + os.environ.get("PATH", "")


def activate() -> list[str]:
    """При старте ядра: подключить только полностью установленные наборы."""
    out = []
    for name in PACKS:
        if _installed(name):
            _add_path(_pack_dir(name))
            out.append(str(_pack_dir(name)))
    return out


def _uv() -> str:
    exe = Path(sys.executable).parent / ("uv.exe" if os.name == "nt" else "uv")
    return str(exe) if exe.is_file() else (shutil.which("uv") or "uv")


def _python_exe() -> str:
    """Ядро работает под pythonw.exe (без окна); для uv и проверки берём соседний python.exe."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").is_file():
        return str(exe.parent / "python.exe")
    return str(exe)


# после установки — правда ли набор импортируется (иначе «Готово» было бы ложью)
IMPORT_CHECK = {
    "gpu": "import nvidia.cublas, nvidia.cudnn",
    "local_voices": "import torch, torchaudio, f5_tts, qwen_tts",
}
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: не показывать консоль uv


def _run(cmd: list[str], env: dict | None = None) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3 * 3600, env=env, creationflags=_NO_WINDOW)
        return r.returncode == 0, (r.stderr or r.stdout or "").strip()[-300:]
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)[:300]


def _sleep(sec: float) -> None:
    import time

    time.sleep(sec)


def _rename(src: Path, dst: Path) -> None:
    src.rename(dst)


def _finalize(part: Path, final: Path) -> None:
    """.part → набор. Сразу после распаковки гигабайт Windows часто держит файлы
    (антивирус) — повторяем несколько раз, прежде чем сдаться."""
    shutil.rmtree(final, ignore_errors=True)
    for attempt in range(6):
        try:
            _rename(part, final)
            break
        except OSError:
            if attempt == 5:
                raise
            _sleep(2 * (attempt + 1))
    (final / _MARK).write_text("ok", encoding="utf-8")


def install(name: str) -> dict:
    """Поставить набор в фоне. Повторный вызов во время установки ничего не делает.
    Пакеты уже стоят, а не докачались только веса голосов — докачиваем только их."""
    if name not in PACKS:
        raise KeyError(name)
    with _LOCK:
        if _STATE.get(name, {}).get("state") == "running":
            return status()[name]
        _STATE[name] = {"state": "running", "error": ""}

    def weights() -> None:
        try:
            download_voice_weights()
            _STATE.pop(name, None)
        except Exception as e:  # noqa: BLE001 — пакеты стоят, повтор докачает только веса
            _STATE[name] = {"state": "error", "error": f"веса голосов: {str(e)[:200]}"}

    def work():
        part = paths.runtime_dir() / f"{name}.part"
        final = _pack_dir(name)
        try:
            shutil.rmtree(part, ignore_errors=True)
            py = _python_exe()
            ok, err = _run([_uv(), "pip", "install", "--python", py, "--target", str(part),
                            "-r", str(CORE_DIR / PACKS[name]["req"]), "-c", str(CORE_DIR / "requirements.txt")])
            if ok:
                env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(part), *sys.path]))
                ok, err = _run([py, "-c", IMPORT_CHECK[name]], env=env)
            if not ok:
                shutil.rmtree(part, ignore_errors=True)
                _STATE[name] = {"state": "error", "error": err or "установка не удалась"}
                return
            _finalize(part, final)
            _add_path(final)
        except Exception as e:  # noqa: BLE001 — что бы ни случилось, не оставляем «Качаю…» навсегда
            shutil.rmtree(part, ignore_errors=True)
            _STATE[name] = {"state": "error", "error": str(e)[:300] or "установка не удалась"}
            return
        if name == "local_voices":
            weights()
        else:
            _STATE.pop(name, None)

    if name == "local_voices" and _installed(name):
        _spawn(weights)
    else:
        _spawn(work)
    return status()[name]


def status() -> dict[str, dict]:
    out = {}
    for name, meta in PACKS.items():
        st = _STATE.get(name) or {"state": "done" if _installed(name) else "none", "error": ""}
        out[name] = {**st, "size_mb": meta["size_mb"], "title": meta["title"]}
    return out


def whisper_status() -> dict:
    return dict(_WHISPER)


def prefetch_whisper(size: str = "small") -> None:
    """Скачать модель Whisper заранее (во время мастера), не загружая её в память."""
    if _WHISPER["state"] in ("running", "done") and _WHISPER["model"] == size:
        return
    _WHISPER.update(state="running", model=size)

    def work():
        try:
            from faster_whisper.utils import download_model

            download_model(size)
            _WHISPER["state"] = "done"
        except Exception:  # noqa: BLE001 — докачается при первом распознавании
            _WHISPER["state"] = "error"

    _spawn(work)


F5_REPO = "Misha24-10/F5-TTS_RUSSIAN"
F5_FILES = {  # файл в репозитории → имя, которое ждёт tts.py
    "F5TTS_v1_Base_v2/model_last_inference.safetensors": "model_ru_v2_inference.safetensors",
    "F5TTS_v1_Base/vocab.txt": "vocab_ru.txt",
}
VITS_REPO = "frappuccino/dialogs-ru-vits2"


def f5_dir() -> Path:
    return paths.voices_local_dir() / "f5"


def download_voice_weights() -> None:
    """Веса локальных голосов с Hugging Face (их не распространяем — человек качает сам).
    F5 — в ~/.aya/voices-local/f5, VITS2 — в общий кэш Hugging Face (так его ищет dialogs_vits2)."""
    from huggingface_hub import hf_hub_download

    dst = f5_dir()
    dst.mkdir(parents=True, exist_ok=True)
    for src, name in F5_FILES.items():
        if (dst / name).is_file():
            continue
        got = hf_hub_download(F5_REPO, src)
        tmp = dst / (name + ".part")
        shutil.copyfile(got, tmp)
        tmp.replace(dst / name)
    for f in ("config.json", "averaged_G_615000.pth"):
        hf_hub_download(VITS_REPO, f)


def gpu_present() -> bool:
    """Есть ли драйвер NVIDIA (тогда предлагаем ускорение распознавания)."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        ctypes.WinDLL("nvcuda.dll")
        return True
    except OSError:
        return False
