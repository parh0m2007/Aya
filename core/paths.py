"""Где ядро хранит данные человека. Всё — в ~/.aya: переживает обновление и
переустановку приложения (папка установки при обновлении заменяется целиком)."""
from pathlib import Path


def _dir(*parts: str) -> Path:
    p = Path.home().joinpath(".aya", *parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    return _dir()


def logs_dir() -> Path:
    return _dir("logs")


def runtime_dir() -> Path:
    """Докачанные наборы пакетов (ускорение на видеокарте, локальные голоса)."""
    return _dir("runtime")


def voices_local_dir() -> Path:
    """Скачанные веса локальных голосов (F5, VITS2, Qwen)."""
    return _dir("voices-local")
