"""PyInstaller runtime hook for the Linux builds (2026-10-27): programs PlanetPDF starts get the
system's own library path back.

PyInstaller's starter puts the program's ``_internal`` folder on ``LD_LIBRARY_PATH``, and every
program started from PlanetPDF inherited it: openSUSE's ``sh`` then loaded PlanetPDF's readline
and crashed ("undefined symbol: rl_full_quoting_desired"). The loader reads the variable only
when a process starts, so putting the original back changes nothing for PlanetPDF itself.
"""

import os

_original = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
if _original:
    os.environ["LD_LIBRARY_PATH"] = _original
else:
    os.environ.pop("LD_LIBRARY_PATH", None)
