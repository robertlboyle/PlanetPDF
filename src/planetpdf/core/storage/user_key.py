"""A key the operating system keeps for this user, for sealing files a program running as the user
might otherwise rewrite unnoticed (security review 2026-11-25: the local API's list of approved
programs was a plain file a program could add itself to).

- Windows: kept in the profile folder encrypted with DPAPI (``CryptProtectData``: only this user,
  on this account, can unlock it; it roams with the profile).
- macOS: a generic password in the user's login Keychain (service "PlanetPDF").
- Linux: the desktop's secret store through ``secret-tool`` (libsecret), when it's installed.
- Otherwise: a file in the profile only this user can read (weaker: a note in the log says so).

This only stops the easy way (editing a file): a program running as the user can do what the user
can, including asking the operating system for the key. Pure Python (ctypes; no Qt).
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)
SERVICE = "PlanetPDF"
KEY_BYTES = 32


def user_key(folder: Path, name: str) -> bytes:
    """The key called ``name`` (made the first time it's asked for)."""
    folder = Path(folder)
    if sys.platform == "win32":
        try:
            return _dpapi_key(folder / f"{name}.key")
        except OSError as exc:
            log.warning("Windows couldn't keep the %s key (%s); a plain key file is used", name, exc)
    elif sys.platform == "darwin":
        try:
            return _keychain_key(name)
        except Exception as exc:  # noqa: BLE001 (the Keychain refused, or isn't there)
            log.warning("The Keychain couldn't keep the %s key (%s); a plain key file is used", name, exc)
    elif shutil.which("secret-tool"):
        key = _secret_tool_key(name)
        if key is not None:
            return key
    return _file_key(folder / f"{name}.plain-key")


def _file_key(path: Path) -> bytes:
    try:
        text = path.read_text(encoding="ascii").strip()
        if len(text) == 2 * KEY_BYTES:
            return bytes.fromhex(text)
    except (OSError, ValueError):
        pass
    key = secrets.token_bytes(KEY_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key.hex(), encoding="ascii")
    if sys.platform != "win32":
        os.chmod(path, 0o600)
    return key


# --- Windows: DPAPI ---------------------------------------------------------------------------------


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    call = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    call.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR if protect else ctypes.c_void_p, ctypes.c_void_p,
                     ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    call.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.create_string_buffer(data, len(data))
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    ui_forbidden = 0x1
    if not call(ctypes.byref(source), "PlanetPDF" if protect else None, None, None, None, ui_forbidden,
                ctypes.byref(result)):
        raise OSError(ctypes.get_last_error(), "CryptProtectData" if protect else "CryptUnprotectData")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def _dpapi_key(path: Path) -> bytes:
    try:
        key = _dpapi(path.read_bytes(), protect=False)
        if len(key) == KEY_BYTES:
            return key
    except FileNotFoundError:
        pass
    except OSError as exc:  # (another account's, or damaged: a new one, so nothing old is trusted)
        log.warning("The %s key couldn't be unlocked (%s): a new one is made", path.name, exc)
    key = secrets.token_bytes(KEY_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_dpapi(key, protect=True))
    return key


# --- macOS: the Keychain ----------------------------------------------------------------------------


def _keychain_key(name: str) -> bytes:
    import ctypes
    import ctypes.util

    pointer = ctypes.c_void_p
    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    sec = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Security"))
    for fn, restype, args in (("CFDictionaryCreateMutable", pointer, [pointer, ctypes.c_long, pointer, pointer]),
                              ("CFDictionarySetValue", None, [pointer, pointer, pointer]),
                              ("CFStringCreateWithCString", pointer, [pointer, ctypes.c_char_p, ctypes.c_uint32]),
                              ("CFDataCreate", pointer, [pointer, ctypes.c_char_p, ctypes.c_long]),
                              ("CFDataGetLength", ctypes.c_long, [pointer]),
                              ("CFDataGetBytePtr", pointer, [pointer]),
                              ("CFRelease", None, [pointer])):
        f = getattr(cf, fn)
        f.restype, f.argtypes = restype, args
    for fn in ("SecItemCopyMatching", "SecItemAdd"):
        f = getattr(sec, fn)
        f.restype, f.argtypes = ctypes.c_int32, [pointer, ctypes.POINTER(pointer)]

    def const(lib, symbol: str) -> int:
        return pointer.in_dll(lib, symbol).value

    def text(value: str) -> int:
        return cf.CFStringCreateWithCString(None, value.encode("utf-8"), 0x08000100)

    owned = [text(SERVICE), text(name)]

    def query(*extra) -> int:
        d = cf.CFDictionaryCreateMutable(None, 0, ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")),
                                         ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryValueCallBacks")))
        owned.append(d)
        cf.CFDictionarySetValue(d, const(sec, "kSecClass"), const(sec, "kSecClassGenericPassword"))
        cf.CFDictionarySetValue(d, const(sec, "kSecAttrService"), owned[0])
        cf.CFDictionarySetValue(d, const(sec, "kSecAttrAccount"), owned[1])
        for key, value in extra:
            cf.CFDictionarySetValue(d, key, value)
        return d

    try:
        result = pointer()
        status = sec.SecItemCopyMatching(query((const(sec, "kSecReturnData"), const(cf, "kCFBooleanTrue")),
                                               (const(sec, "kSecMatchLimit"), const(sec, "kSecMatchLimitOne"))),
                                         ctypes.byref(result))
        if status == 0 and result.value:
            size = cf.CFDataGetLength(result.value)
            data = ctypes.string_at(cf.CFDataGetBytePtr(result.value), size) if size > 0 else b""
            cf.CFRelease(result.value)
            if len(data) == KEY_BYTES:
                return data
        if status not in (0, -25300):  # (-25300: errSecItemNotFound)
            raise OSError(status, "SecItemCopyMatching")
        key = secrets.token_bytes(KEY_BYTES)
        value = cf.CFDataCreate(None, key, len(key))
        owned.append(value)
        status = sec.SecItemAdd(query((const(sec, "kSecValueData"), value)), None)
        if status != 0:
            raise OSError(status, "SecItemAdd")
        return key
    finally:
        for item in owned:
            if item:
                cf.CFRelease(item)


# --- Linux: libsecret through secret-tool -----------------------------------------------------------


def _secret_tool_key(name: str) -> bytes | None:
    attributes = ["service", SERVICE, "account", name]
    try:
        found = subprocess.run(["secret-tool", "lookup", *attributes], capture_output=True, text=True, timeout=20)
        text = found.stdout.strip()
        if found.returncode == 0 and len(text) == 2 * KEY_BYTES:
            return bytes.fromhex(text)
        key = secrets.token_bytes(KEY_BYTES)
        stored = subprocess.run(["secret-tool", "store", f"--label={SERVICE} {name}", *attributes],
                                input=key.hex(), capture_output=True, text=True, timeout=20)  # (the key on stdin)
        return key if stored.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
