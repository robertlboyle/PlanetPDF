"""Which program is at the other end of a local API connection, as the operating system says
(security review 2026-11-21: a program's name was only what it said it was).

Windows: the pipe's client process (``GetNamedPipeClientProcessId``) and its program file
(``QueryFullProcessImageNameW``). Mac: the socket's peer (``LOCAL_PEERPID``) and ``proc_pidpath``.
Linux: ``SO_PEERCRED`` and ``/proc/<pid>/exe``. Small and per system on purpose: no
cross-platform library answers this. "" when it can't be told.
"""

from __future__ import annotations

import ctypes
import os
import socket
import struct
import sys


def peer_program(local_socket) -> str:
    """The program file of the process connected on this QLocalSocket ("" = unknown)."""
    try:
        handle = int(local_socket.socketDescriptor())
    except (TypeError, ValueError, RuntimeError):
        return ""
    if handle in (-1, 0):
        return ""
    try:
        pid = _peer_pid(handle)
        return program_of(pid) if pid else ""
    except (OSError, AttributeError, ValueError):
        return ""


def _peer_pid(handle: int) -> int:
    if sys.platform == "win32":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetNamedPipeClientProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
        kernel32.GetNamedPipeClientProcessId.restype = wintypes.BOOL
        pid = wintypes.ULONG(0)
        return int(pid.value) if kernel32.GetNamedPipeClientProcessId(handle, ctypes.byref(pid)) else 0
    peer = socket.socket(fileno=os.dup(handle))  # (a copy: closing it leaves Qt's open)
    try:
        if sys.platform == "darwin":
            sol_local, local_peerpid = 0, 2
            return struct.unpack("i", peer.getsockopt(sol_local, local_peerpid, 4))[0]
        pid, _uid, _gid = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                              struct.calcsize("3i")))
        return pid
    finally:
        peer.close()


def program_of(pid: int) -> str:
    """A process's program file ("" when it can't be read)."""
    if sys.platform == "win32":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                        ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        process = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not process:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                return buffer.value
            return ""
        finally:
            kernel32.CloseHandle(process)
    if sys.platform == "darwin":
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
        buffer = ctypes.create_string_buffer(4096)
        size = libproc.proc_pidpath(ctypes.c_int(pid), buffer, ctypes.c_uint32(len(buffer)))
        return buffer.value.decode("utf-8", "replace") if size > 0 else ""
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return ""
