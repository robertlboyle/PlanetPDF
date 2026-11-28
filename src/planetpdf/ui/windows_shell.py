"""Windows-only shell integration. Imported only when running on Windows.

Without an explicit AppUserModelID, Windows groups the window under the Python
interpreter and shows *its* icon in the taskbar when running from source.
"""

from __future__ import annotations

import ctypes

APP_USER_MODEL_ID = "PlanetNetworks.PlanetPDF"


def set_app_user_model_id() -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass  # cosmetic only


def session_id() -> int:
    """This process's Windows session (each user on a terminal server has their own)."""
    from ctypes import wintypes

    session = wintypes.DWORD(0)
    try:
        kernel32 = ctypes.windll.kernel32
        if kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(session)):
            return int(session.value)
    except (AttributeError, OSError):
        pass
    return 0


SM_REMOTESESSION = 0x1000


def is_remote_session() -> bool:
    """This user is working over Remote Desktop: on a terminal server, or a PC reached from
    elsewhere. PlanetPDF then doesn't offer per-user copies of big downloads (each user would get
    their own: the natural voices, 2026-11-27)."""
    try:
        return bool(ctypes.windll.user32.GetSystemMetrics(SM_REMOTESESSION))
    except (AttributeError, OSError):
        return False


GENERIC_READ, GENERIC_WRITE, FILE_READ_ATTRIBUTES = 0x80000000, 0x40000000, 0x0080
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
SECURITY_SQOS_PRESENT, SECURITY_IDENTIFICATION = 0x00100000, 0x00010000
ERROR_PIPE_BUSY = 231
PIPE_PREFIX = "\\\\.\\pipe\\"


def open_pipe(name: str, timeout_ms: int = 500, overlapped: bool = True) -> int | None:
    """Connect to a named pipe so that the program at the other end can learn who is calling but
    can't act as this user (identification only; security list 2026-11-26, item 2: Qt's and
    Python's own ways of opening a pipe allow impersonation). ``name`` with or without the
    pipe prefix in front. The handle (for Qt: overlapped), or None when nothing listens."""
    import time
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel32.WaitNamedPipeW.restype = wintypes.BOOL
    path = name if name.startswith(PIPE_PREFIX) else PIPE_PREFIX + name
    flags = SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION | (FILE_FLAG_OVERLAPPED if overlapped else 0)
    invalid = wintypes.HANDLE(-1).value
    end = time.monotonic() + timeout_ms / 1000
    while True:
        handle = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE | FILE_READ_ATTRIBUTES, 0, None,
                                      OPEN_EXISTING, flags, None)
        if handle not in (None, 0, invalid):
            return int(handle)
        left = int((end - time.monotonic()) * 1000)
        if ctypes.get_last_error() != ERROR_PIPE_BUSY or left <= 0:
            return None  # (nothing listens there, or it stayed busy)
        kernel32.WaitNamedPipeW(path, left)  # (every instance is taken: wait for a free one)


def close_handle(handle: int) -> None:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle(handle)


def allow_foreground() -> None:
    """Let the already-running PlanetPDF bring its window to the front when this start
    hands it a file (Windows only lets the program the user is using do that)."""
    try:
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except (AttributeError, OSError):
        pass


def force_foreground(hwnd: int) -> bool:
    """Bring a window to the front even when PlanetPDF isn't the program in use (user 2026-11-22: a
    printout's question "is hidden in the background and I was waiting for something to happen").
    Windows refuses SetForegroundWindow to a program that didn't get the last input, and the
    printer's service started PlanetPDF / handed it the file, so neither counts. The usual ways
    round it: an Alt key tap (which lifts the lock for the next SetForegroundWindow) and joining
    the input of the window in front for the moment of the switch. Flashes the taskbar button if
    it still didn't work. True = it's in front."""
    from ctypes import wintypes

    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        hwnd = wintypes.HWND(hwnd)
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        front = user32.GetForegroundWindow()
        if front == hwnd.value:
            return True
        mine = kernel32.GetCurrentThreadId()
        theirs = user32.GetWindowThreadProcessId(front, None) if front else 0
        joined = bool(theirs and theirs != mine and user32.AttachThreadInput(mine, theirs, True))
        try:
            user32.keybd_event(0x12, 0, 0, 0)  # Alt down
            user32.keybd_event(0x12, 0, 2, 0)  # Alt up (KEYEVENTF_KEYUP)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
        finally:
            if joined:
                user32.AttachThreadInput(mine, theirs, False)
        if user32.GetForegroundWindow() == hwnd.value:
            return True

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                        ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]

        # FLASHW_ALL | FLASHW_TIMERNOFG: flash until it's brought forward
        user32.FlashWindowEx(ctypes.byref(FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x3 | 0xC, 0, 0)))
    except (AttributeError, OSError):
        pass
    return False


CHOOSE_PDF_APP = "--choose-pdf-app"
# what Windows records when PlanetPDF is picked: the installer's ProgId (packaging/PlanetPDF.iss),
# or the "Open with" entry for the launcher
PDF_PROG_IDS = ("PlanetPDF.Document", r"Applications\PlanetPDF.exe")
PDF_CHOICE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf"


def pdf_app() -> str | None:
    """The ProgId of the app this user picked for PDF files (user 2026-10-23: "say so" when it's
    already PlanetPDF). Windows 11 keeps it in ``UserChoiceLatest``, older Windows in
    ``UserChoice``; None when the user never chose (the computer-wide default applies)."""
    import winreg

    for key in ("UserChoiceLatest", "UserChoice"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{PDF_CHOICE_KEY}\{key}") as k:
                value, _ = winreg.QueryValueEx(k, "ProgId")
        except OSError:
            continue
        if value:
            return str(value)
    return None


def pdf_app_is_planetpdf() -> bool:
    prog_id = pdf_app()
    return bool(prog_id) and prog_id.lower() in (p.lower() for p in PDF_PROG_IDS)


def choose_pdf_app_main() -> int:
    """``PlanetPDF.exe --choose-pdf-app`` (the installer, as the user): the chooser, or nothing at
    all when PlanetPDF already opens PDF files (the chooser would offer only "Just once"; user
    2026-11-22: a message saying so "is annoying to see every upgrade. Just stay silent")."""
    if pdf_app_is_planetpdf():
        return 0
    return 0 if choose_pdf_app() else 1


SAMPLE_TEXT = ("PDF files now open in the app you chose.\n\n"
               "To change it later: in PlanetPDF, Edit > Preferences > General; or Windows Settings > "
               "Apps > Default apps.\n\nYou can close this file.")  # (">": Helvetica has no "▸")


def choose_pdf_app(hwnd: int = 0) -> bool:
    """Windows' own "How do you want to open .pdf files?" chooser; its Always button makes the app
    picked the default (user 2026-10-18: the Default apps page had "nothing to click on"; Windows
    lets only the user choose, so this is the most a program may do). True when it opened.

    Only ``OAIF_ALLOW_REGISTRATION | OAIF_EXEC`` (user 2026-10-23: nothing was asked): Windows 10
    and 11 answer ``OAIF_REGISTER_EXT`` / ``OAIF_FORCE_REGISTRATION`` with "To change your default
    apps, go to Settings > Apps > Default apps" instead of the chooser. ``OAIF_EXEC`` opens the
    sample in the app picked, so it's a one-page PDF saying what happened."""
    import tempfile
    from ctypes import wintypes
    from pathlib import Path

    class OPENASINFO(ctypes.Structure):
        _fields_ = [("pcszFile", wintypes.LPCWSTR), ("pcszClass", wintypes.LPCWSTR), ("oaifInFlags", ctypes.c_int)]

    OAIF_ALLOW_REGISTRATION, OAIF_EXEC = 0x1, 0x4
    CANCELED = -2147023673  # HRESULT_FROM_WIN32(ERROR_CANCELLED): the chooser opened, then closed
    sample = Path(tempfile.gettempdir()) / "PDF files.pdf"  # (what the chooser names)
    write_sample(sample)
    info = OPENASINFO(str(sample), None, OAIF_ALLOW_REGISTRATION | OAIF_EXEC)
    hr = ctypes.windll.shell32.SHOpenWithDialog(wintypes.HWND(hwnd), ctypes.byref(info))
    return hr in (0, CANCELED)


def write_sample(path) -> None:
    """The one-page PDF the chooser opens in the app picked."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(pymupdf.Rect(72, 72, 540, 400), SAMPLE_TEXT, fontsize=16, fontname="helv")
    doc.save(path)
    doc.close()


def window_identity(hwnd: int, *, icon: str, command: str, name: str = "PlanetPDF") -> bool:
    """Tell the taskbar which app a window is and which icon it has (user 2026-10-18: the
    installed PlanetPDF showed Windows' generic icon on the taskbar). With an explicit
    AppUserModelID, Windows 11 takes the taskbar icon from a shortcut with that ID, else shows a
    generic one; a window's own relaunch properties (``icon`` = "path,index", ``command`` = how to
    start it again) settle it whatever shortcuts there are. True when Windows took them."""
    from ctypes import POINTER, Structure, Union, byref, c_ulong, c_ushort, c_void_p, c_wchar_p, WINFUNCTYPE
    from ctypes.wintypes import HWND

    class GUID(Structure):
        _fields_ = [("a", c_ulong), ("b", c_ushort), ("c", c_ushort), ("d", ctypes.c_ubyte * 8)]

        def __init__(self, text: str):
            import uuid

            u = uuid.UUID(text)
            super().__init__(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))

    class PROPERTYKEY(Structure):
        _fields_ = [("fmtid", GUID), ("pid", c_ulong)]

    class _Value(Union):
        _fields_ = [("pwszVal", c_wchar_p), ("pad", ctypes.c_byte * 16)]

    class PROPVARIANT(Structure):
        _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort), ("value", _Value)]

    VT_LPWSTR = 31
    APP_MODEL = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"
    keys = [(5, APP_USER_MODEL_ID), (2, command), (4, name), (3, icon)]  # ID, command, name, icon
    try:
        store = c_void_p()
        hr = ctypes.windll.shell32.SHGetPropertyStoreForWindow(
            HWND(hwnd), byref(GUID("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")), byref(store))
        if hr != 0 or not store:
            return False
        vtable = ctypes.cast(ctypes.cast(store, POINTER(c_void_p))[0], POINTER(c_void_p))
        set_value = WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(PROPERTYKEY), POINTER(PROPVARIANT))(vtable[6])
        commit = WINFUNCTYPE(ctypes.c_long, c_void_p)(vtable[7])
        release = WINFUNCTYPE(c_ulong, c_void_p)(vtable[2])
        try:
            ok = True
            for pid, text in keys:
                value = PROPVARIANT()
                value.vt = VT_LPWSTR
                value.value.pwszVal = text
                ok &= set_value(store, byref(PROPERTYKEY(GUID(APP_MODEL), pid)), byref(value)) == 0
            return ok and commit(store) == 0
        finally:
            release(store)
    except (AttributeError, OSError, ValueError):
        return False  # cosmetic only


def relaunch_info() -> dict[str, str]:
    """This copy's icon and command for ``window_identity``: the installed launcher (beside
    ``versions``), else this program; from source, Python with the bundled icon."""
    import sys
    from pathlib import Path

    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        launcher = exe.parents[2] / "PlanetPDF.exe"
        target = launcher if launcher.is_file() and exe.parent.parent.name == "versions" else exe
        return {"icon": f"{target},0", "command": f'"{target}"'}
    from planetpdf.core.resources import app_icon_path

    return {"icon": f"{app_icon_path()},0", "command": f'"{sys.executable}" -m planetpdf'}
