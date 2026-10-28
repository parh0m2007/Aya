"""Где на рабочем столе активное окно — чтобы Ая могла сесть на его край."""
import ctypes
import os

_SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow"}
_SKIP_TITLES = {"Aya", "Aya Console", "Program Manager"}
_SKIP_EXE = {"aya.exe"}


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _exe_name(user32, kernel32, hwnd) -> str:
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = ctypes.c_ulong(520)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(h)
    return ""


def foreground() -> dict | None:
    """Прямоугольник активного окна в физических пикселях или None."""
    if not hasattr(ctypes, "windll"):
        return None
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd or user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
            return None
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value in _SKIP_CLASSES:
            return None
        n = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, title, n + 1)
        if title.value in _SKIP_TITLES or _exe_name(user32, kernel32, hwnd) in _SKIP_EXE:
            return None
        r = _RECT()
        # DWMWA_EXTENDED_FRAME_BOUNDS — без невидимой рамки-тени Windows 10/11
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
            user32.GetWindowRect(hwnd, ctypes.byref(r))
        return {
            "x": r.left,
            "y": r.top,
            "w": r.right - r.left,
            "h": r.bottom - r.top,
            "maximized": bool(user32.IsZoomed(hwnd)),
            "title": title.value[:80],
        }
    except Exception:  # noqa: BLE001
        return None
