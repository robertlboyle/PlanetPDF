"""Windows only: run a program as administrator (the UAC prompt) and wait for processes.
ctypes only, imported only on Windows (core/update_helper.py)."""

from __future__ import annotations

import ctypes
import subprocess
from ctypes import wintypes

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NO_CONSOLE = 0x00008000
SEE_MASK_FLAG_NO_UI = 0x00000400  # (Windows' own error box: never; we say what went wrong)
ERROR_CANCELLED = 1223
# When Windows says the installing step didn't start, the updater still looks this long for signs
# that it did (its prompt can fail and run the program anyway: user 2026-10-26)
START_GRACE_SECONDS = 10
INFINITE = 0xFFFFFFFF
SYNCHRONIZE = 0x00100000
SW_HIDE = 0


class ElevationCanceled(Exception):
    """The user said No at the UAC prompt (or has no administrator account)."""


class ElevationFailed(OSError):
    """Windows couldn't start the program as administrator (not the user's doing: user
    2026-10-26, "File system error (-1073740791)" from Windows' prompt on Windows 11)."""


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
    ]


_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
_shell32.ShellExecuteExW.restype = wintypes.BOOL
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def run_elevated(command: list[str]) -> int:
    """Run ``command`` as administrator (Windows asks: UAC) and wait; its exit code."""
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NO_CONSOLE | SEE_MASK_FLAG_NO_UI
    info.lpVerb = "runas"
    info.lpFile = command[0]
    info.lpParameters = subprocess.list2cmdline(command[1:])
    info.nShow = SW_HIDE
    if not _shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == ERROR_CANCELLED:
            raise ElevationCanceled()
        raise ElevationFailed(error, ctypes.FormatError(error) or f"Windows error {error}")
    if not info.hProcess:  # (started, but Windows gave nothing to wait on)
        return -1
    try:
        _kernel32.WaitForSingleObject(info.hProcess, INFINITE)
        code = wintypes.DWORD()
        _kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        _kernel32.CloseHandle(info.hProcess)


def wait_for_process(pid: int, timeout_ms: int) -> None:
    """Until process ``pid`` has ended (or the time is up; it's gone already: at once)."""
    handle = _kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return
    try:
        _kernel32.WaitForSingleObject(handle, timeout_ms)
    finally:
        _kernel32.CloseHandle(handle)
