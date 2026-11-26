"""Windows: who can change a file or folder, and closing PlanetPDF's install folder to everyone
but administrators (security list 2026-11-26, items 3 and 5).

An install folder outside Program Files (D:\\Apps…) often inherits permissions that let every user
write. Then any user could put a program of their own in place of PlanetPDF's, which other users
run, or of its print service, which runs with system rights; and a policy file found there
(``core/policy.py``) could be anyone's. The installer closes the folder; the update's installing
step does the same (``protect_folder``) for installs made before that; and PlanetPDF takes a policy
only from a file nobody but administrators can change (``open_to``).

The rule is the print service's (``printer/src/acl.rs``), on a security descriptor as text (SDDL),
so it can be tested without Windows. ctypes only, loaded when called.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# What lets its holder change a file or a folder's contents: write, append / add, delete a
# folder's children, delete, change permissions, take ownership, and the generic write and all.
CHANGE = 0x2 | 0x4 | 0x40 | 0x0001_0000 | 0x0004_0000 | 0x0008_0000 | 0x4000_0000 | 0x1000_0000
TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
TRUSTED = frozenset({"SY", "BA", "LA", "DA", "EA", "S-1-5-18", "S-1-5-32-544", TRUSTED_INSTALLER})
ADMINISTRATORS, SYSTEM, USERS, APP_PACKAGES = "S-1-5-32-544", "S-1-5-18", "S-1-5-32-545", "S-1-15-2-1"
RIGHTS = {
    "GA": 0x1000_0000, "GX": 0x2000_0000, "GW": 0x4000_0000, "GR": 0x8000_0000,
    "SD": 0x0001_0000, "RC": 0x0002_0000, "WD": 0x0004_0000, "WO": 0x0008_0000,
    "FA": 0x001F_01FF, "FR": 0x0012_0089, "FW": 0x0012_0116, "FX": 0x0012_00A0,
    "CC": 0x1, "DC": 0x2, "LC": 0x4, "SW": 0x8, "RP": 0x10, "WP": 0x20, "DT": 0x40, "LO": 0x80, "CR": 0x100,
}
WORDS = {"BU": "Users", USERS: "Users", "AU": "Authenticated Users", "S-1-5-11": "Authenticated Users",
         "WD": "Everyone", "S-1-1-0": "Everyone", "IU": "Interactive users", "DU": "Domain Users"}


def _trusted(sid: str) -> bool:
    """SYSTEM, Administrators, TrustedInstaller, the built-in Administrator account, and the
    domain's administrators: whoever can change everything on the computer anyway."""
    return sid in TRUSTED or re.fullmatch(r"S-1-5-21-\d+-\d+-\d+-(500|512|519)", sid) is not None


def _mask(rights: str) -> int:
    if rights[:2].lower() == "0x":
        return int(rights, 16)
    if len(rights) % 2 or not all(rights[i:i + 2] in RIGHTS for i in range(0, len(rights), 2)):
        raise ValueError(f"rights that can't be read: {rights}")
    mask = 0
    for i in range(0, len(rights), 2):
        mask |= RIGHTS[rights[i:i + 2]]
    return mask


def others_who_can_change(sddl: str) -> list[str]:
    """Who, besides administrators and the system, can change what this security descriptor
    guards: their SIDs (or SDDL's two-letter names: "BU" users, "AU" signed-in users, "WD"
    everyone), with "owner " in front of an owner. Empty: nobody else. ``ValueError`` when it
    can't be read (count that as open)."""
    if not sddl.startswith("O:"):
        raise ValueError("no owner")
    rest = sddl[2:]
    colon = rest.find(":")  # (the only colons outside the entries follow the letters O: G: D: S:)
    if colon < 2:
        raise ValueError("no owner" if colon == 0 else "no permissions")
    owner = rest[:colon - 1]
    others = [] if _trusted(owner) else [f"owner {owner}"]
    start = sddl.find("D:", 2 + len(owner))
    if start < 0:
        raise ValueError("no permissions")
    dacl = sddl[start + 2:]
    if "S:" in dacl:
        dacl = dacl[:dacl.index("S:")]
    first = dacl.find("(") if "(" in dacl else len(dacl)
    if "NO_ACCESS_CONTROL" in dacl[:first]:
        return ["everyone (no permissions are set)"]
    for ace in (a.lstrip("(") for a in dacl[first:].split(")")):
        if not ace:
            continue
        parts = ace.split(";")
        if len(parts) < 6:
            raise ValueError(f"an entry that can't be read: ({ace})")
        kind, flags, rights, sid = parts[0], parts[1], parts[2], parts[5]
        if kind not in ("A", "OA", "XA"):
            continue  # (a refusal or an audit entry gives nobody anything)
        if "IO" in flags or sid in ("CO", "OW", "S-1-3-0", "S-1-3-4"):
            continue  # (only for what is made inside later, or for whoever makes or owns it)
        if _mask(rights) & CHANGE and not _trusted(sid) and sid not in others:
            others.append(sid)
    return others


def in_words(who: str) -> str:
    if who.startswith("owner "):
        return f"its owner, {in_words(who[6:])}"
    return f"{WORDS[who]} ({who})" if who in WORDS else who


def sddl_of(path: Path) -> str:
    """A file's or folder's owner and permissions as SDDL. ``OSError`` when Windows won't say."""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    owner_and_dacl, se_file_object, revision = 0x1 | 0x4, 1, 1
    advapi32.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
                                               ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                               ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    error = advapi32.GetNamedSecurityInfoW(str(path), se_file_object, owner_and_dacl, None, None, None, None,
                                           ctypes.byref(descriptor))
    if error:
        raise OSError(error, ctypes.FormatError(error), str(path))
    try:
        text = wintypes.LPWSTR()
        if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, revision, owner_and_dacl,
                                                                             ctypes.byref(text), None):
            error = ctypes.get_last_error()
            raise OSError(error, ctypes.FormatError(error), str(path))
        try:
            return text.value or ""
        finally:
            kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        kernel32.LocalFree(descriptor)


def open_to(path: Path) -> list[str]:
    """Who else can change this file or folder (``others_who_can_change``). ``OSError`` /
    ``ValueError`` when that can't be told."""
    return others_who_can_change(sddl_of(path))


def closed_to_others(path: Path) -> bool:
    """Only administrators and Windows itself can change it (False too when that can't be told)."""
    try:
        return not open_to(path)
    except (OSError, ValueError):
        return False


def protect_commands(folder: Path) -> list[list[str]]:
    """What closes ``folder`` to everyone but administrators, as the installer does
    (packaging/PlanetPDF.iss): its owner becomes Administrators; nothing inherited, nothing left
    over; then SYSTEM and Administrators in full, Users and app packages read and run."""
    from planetpdf.core import windows_elevate

    icacls = os.path.join(windows_elevate.system_dir(), "icacls.exe")  # (Windows' own, by its full path)
    target = str(folder)
    return [
        [icacls, target, "/setowner", f"*{ADMINISTRATORS}", "/Q"],
        [icacls, target, "/reset", "/Q"],
        [icacls, target, "/inheritance:r", "/grant:r", f"*{SYSTEM}:(OI)(CI)F", f"*{ADMINISTRATORS}:(OI)(CI)F",
         f"*{USERS}:(OI)(CI)RX", f"*{APP_PACKAGES}:(OI)(CI)RX", "/Q"],
    ]


def protect_folder(folder: Path) -> bool:
    """Close ``folder`` to everyone but administrators (needs an administrator). True when it is
    closed afterwards; nothing is touched when it already was."""
    import subprocess

    if closed_to_others(folder):
        return True
    for command in protect_commands(folder):
        done = subprocess.run(command, capture_output=True, text=True, timeout=120,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if done.returncode != 0:
            break
    return closed_to_others(folder)
