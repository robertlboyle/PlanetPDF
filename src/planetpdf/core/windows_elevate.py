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


TOKEN_QUERY = 0x0008
TOKEN_ELEVATION_TYPE = 18  # (TokenElevationType: 1 default, 2 full, 3 limited)


def can_elevate() -> bool:
    """Whether this user can say Yes at Windows' administrator prompt: an administrator, running
    elevated or not. A standard user (terminal servers) can't, without someone else's password."""
    if is_admin():
        return True
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(_kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        return False
    try:
        kind, size = wintypes.DWORD(), wintypes.DWORD()
        if not advapi32.GetTokenInformation(token, TOKEN_ELEVATION_TYPE, ctypes.byref(kind), ctypes.sizeof(kind),
                                            ctypes.byref(size)):
            return False
        return kind.value in (2, 3)
    finally:
        _kernel32.CloseHandle(token)


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


def _folder(call) -> str:
    buffer = ctypes.create_unicode_buffer(32768)
    call.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    call.restype = wintypes.UINT
    length = call(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise OSError("Windows didn't say where its folder is")
    return buffer.value


def system_dir() -> str:
    """Windows' System32 folder, asked of Windows itself: never taken from ``SystemRoot`` or
    another environment variable, which a program running as the user can set (security list
    2026-11-26, item 10)."""
    return _folder(_kernel32.GetSystemDirectoryW)


def windows_dir() -> str:
    """Windows' own folder (the computer's, not a terminal-server user's private one)."""
    return _folder(_kernel32.GetSystemWindowsDirectoryW)


UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{6F3C2A51-8E4B-4C1D-9A57-2B7D0E9C4F18}_is1"


def set_installed_version(version: str) -> bool:
    """Apps & features' entry for PlanetPDF (Setup's) says ``version`` (an in-app update left the
    version Setup installed there; review 2026-11-27). As administrator. True when changed."""
    import winreg

    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY, 0,
                                winreg.KEY_READ | winreg.KEY_SET_VALUE | view) as key:
                winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, version)
                try:
                    name = str(winreg.QueryValueEx(key, "DisplayName")[0])
                except OSError:
                    name = ""
                if name.startswith("PlanetPDF "):
                    winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, f"PlanetPDF {version}")
                return True
        except OSError:
            continue
    return False


def registered_install_dir():
    """Where the installer put PlanetPDF (its InstallDir under HKLM, Software, Planet Networks,
    PlanetPDF), or None: the administrator step installs only there (security review 2026-11-25)."""
    import winreg
    from pathlib import Path

    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"Software\Planet Networks\PlanetPDF", 0,
                                winreg.KEY_READ | view) as key:
                return Path(winreg.QueryValueEx(key, "InstallDir")[0])
        except OSError:
            continue
    return None
