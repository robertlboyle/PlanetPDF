"""PyInstaller runtime hook for the Linux builds (2026-10-26): Read Out Loud's sound library.

sounddevice finds PortAudio only through ctypes.util.find_library, which never looks in the
program's own folder. The system's copy is used when there is one, else the copy bundled with
the program (built with ALSA only: scripts/build_linux.py).
"""

import ctypes.util
import os
import sys

_bundled = os.path.join(getattr(sys, "_MEIPASS", ""), "libportaudio.so.2")
_find_library = ctypes.util.find_library


def _find_with_bundled(name):
    found = _find_library(name)
    if found is None and name == "portaudio" and os.path.isfile(_bundled):
        return _bundled
    return found


ctypes.util.find_library = _find_with_bundled
