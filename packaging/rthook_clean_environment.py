"""PyInstaller run-time hook, every platform (security list 2026-11-26, item 10): when the program
was started as the update's installing step (as administrator or SYSTEM), the environment
variables that tell its libraries what to load are taken out before anything else runs
(``planetpdf/core/clean_environment.py``). First in the spec's list: nothing may load the
encryption library before it.
"""

import sys

try:
    from planetpdf.core.clean_environment import scrub

    scrub(sys.argv)
except Exception:  # noqa: BLE001 (never in the way of starting the program)
    pass
