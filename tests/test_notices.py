"""The third-party license inventory (scripts/update_notices.py) stays complete and current.
If a test here fails after a dependency change, run:

    .venv\\Scripts\\python.exe scripts\\update_notices.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LICENSES = ROOT / "src" / "planetpdf" / "resources" / "licenses"
REGENERATE = "out of date: run scripts/update_notices.py"


def _components() -> list[dict]:
    return json.loads((LICENSES / "components.json").read_text(encoding="utf-8"))


def _key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_every_runtime_package_is_listed_with_its_pinned_version():
    listed = {_key(c["name"]): c["version"] for c in _components() if c["kind"] == "Python package"}
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].split(";", 1)[0].strip()
        if "==" in line:
            name, version = line.split("==", 1)
            name, version = name.strip(), version.split()[0]  # (hashes follow the version)
            assert listed.get(_key(name)) == version, f"{name} {version}: {REGENERATE}"
    dev_only = {"pytest", "pip-licenses", "pygments"}
    assert not dev_only & set(listed), "dev-only packages aren't shipped and shouldn't be listed"


def test_notices_have_a_section_and_license_for_every_component():
    notices = (LICENSES / "THIRD-PARTY-NOTICES.txt").read_text(encoding="utf-8")
    for c in _components():
        assert c["license"] and c["license"] != "UNKNOWN", c["name"]
        assert f"\n{c['name']} {c['version']}".rstrip() + "\n" in notices, f"{c['name']}: {REGENERATE}"


def test_lgpl_texts_ship_for_qt_and_the_root_copy_matches():
    notices = (LICENSES / "THIRD-PARTY-NOTICES.txt").read_text(encoding="utf-8")
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in notices and "Version 3, 29 June 2007" in notices
    assert "TERMS AND CONDITIONS" in notices  # (the GPL-3.0 text the LGPL-3.0 builds on)
    assert len((LICENSES / "GPL-3.0.txt").read_text(encoding="utf-8").splitlines()) == 674  # the official text
    assert (ROOT / "THIRD-PARTY-NOTICES.txt").read_text(encoding="utf-8") == notices, REGENERATE
    names = {c["name"] for c in _components()}
    assert {"Qt", "Python", "MuPDF", "FFmpeg"} <= names
    fonts = {p.stem.removeprefix("OFL-") for p in (ROOT / "src/planetpdf/resources/fonts").glob("OFL-*.txt")}
    listed_fonts = {c["name"].removesuffix(" (font)").replace(" ", "") for c in _components() if c["name"].endswith("(font)")}
    assert fonts == listed_fonts, f"bundled fonts changed: {REGENERATE}"
