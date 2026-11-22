"""Built-in search presets (US formats only, per CLAUDE.md)."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    pattern: re.Pattern[str]
    example: str


PRESETS: dict[str, Preset] = {
    p.key: p
    for p in (
        Preset(
            "ssn",
            "SSN",
            # 123-45-6789 or 123 45 6789; excludes never-issued 000/666/9xx areas, 00 group, 0000 serial.
            re.compile(r"(?<!\d)(?!000|666|9\d\d)\d{3}([- ])(?!00)\d{2}\1(?!0000)\d{4}(?!\d)"),
            "123-45-6789",
        ),
        Preset(
            "phone",
            "Phone number",
            # NANP: optional +1, (555) 867-5309 / 555-867-5309 / 555.867.5309 / 555 867 5309.
            re.compile(
                r"(?<![\d-])(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\d-])"
            ),
            "(555) 867-5309",
        ),
        Preset(
            "email",
            "Email address",
            re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
            "name@example.com",
        ),
        Preset(
            "account",
            "Account number (8-17 digits)",
            re.compile(r"(?<![\d-])\d{8,17}(?![\d-])"),
            "0012345678901",
        ),
    )
}
