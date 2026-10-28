"""Один поток для всего, что трогает плеер и громкость Windows.

COM-объекты громкости, запомненные при приглушении, нельзя использовать из
другого потока при восстановлении — поэтому всё идёт через этот поток.
"""
import asyncio
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor


def _com_init() -> None:
    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception:  # noqa: BLE001
        pass


WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aya-music", initializer=_com_init)


def in_worker(fn, *args, timeout: float = 40):
    """Синхронно выполнить в музыкальном потоке (из любого потока)."""
    if threading.current_thread().name.startswith("aya-music"):
        return fn(*args)
    return WORKER.submit(fn, *args).result(timeout=timeout)


async def call(fn, *args):
    """То же для async-обработчиков ядра."""
    return await asyncio.wrap_future(WORKER.submit(fn, *args))


def default_browser() -> tuple[str, str]:
    """(exe, имя процесса) браузера по умолчанию для https."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
        ) as k:
            prog = winreg.QueryValueEx(k, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog + r"\shell\open\command") as k:
            cmd = winreg.QueryValueEx(k, "")[0]
        exe = cmd.split('"')[1] if cmd.startswith('"') else cmd.split(" ")[0]
        if os.path.isfile(exe):
            return exe, os.path.basename(exe).lower()
    except OSError:
        pass
    return "", ""


def open_in_browser(url: str) -> str:
    """Открыть ссылку именно в браузере. os.startfile отдал бы music.yandex.ru
    приложению, которое перехватывает эти ссылки (старая Яндекс Музыка из Store)."""
    exe, proc = default_browser()
    if exe:
        try:
            subprocess.Popen([exe, url], close_fds=True)
            return proc
        except OSError:
            pass
    os.startfile(url)
    return ""


def open_link(url: str) -> None:
    """Ссылка приложения (yandexmusic://…) — через оболочку Windows."""
    os.startfile(url)
