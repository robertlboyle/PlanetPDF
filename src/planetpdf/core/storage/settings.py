"""Application settings, stored as JSON in a directory chosen by the caller.

The UI passes the per-user config location; tests pass a temp dir. Unknown keys found
in the file are preserved on save so newer builds don't lose older builds' data
(and vice versa) when the user switches between computers.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from planetpdf.core.storage.files import write_bytes_atomic

log = logging.getLogger(__name__)

SETTINGS_FILENAME = "settings.json"
MAX_RECENT_FILES = 10


@dataclass
class Settings:
    user_name: str = ""
    company_name: str = ""
    asked_default_pdf_app: bool = False  # Mac app: the first start asked whether PlanetPDF opens PDFs  # stamps' {company} (Company Confidential); blank by default
    timestamp_url: str = ""
    last_directory: str = ""
    recent_files: list[str] = field(default_factory=list)
    theme: str = "system"  # View ▸ Display Theme: "system", "light" or "dark"
    text_size: int = 100  # Preferences ▸ Appearance ▸ Text size: percent of the system's (larger for low vision)
    last_session: list[str] = field(default_factory=list)  # open when PlanetPDF last closed (File ▸ Reopen)
    show_thumbnails: bool = True  # the Pages / Bookmarks side panel is open
    side_panel: str = "pages"  # which one: "pages" or "bookmarks"
    side_panel_width: int = 190
    side_panel_side: str = "left"  # "left" (with a rail of buttons) or "right" (buttons on the tool strip)
    view_layout: str = "single"  # ui LayoutMode value: single / two_up / grid_2x2
    cover_page_separate: bool = False
    # View ▸ Page Display / Show/Hide (Acrobat; 2026-10-02)
    show_page_gaps: bool = True
    show_rulers: bool = False
    show_grid: bool = False
    snap_to_grid: bool = False
    snap_to_objects: bool = True  # (dotted guides while dragging, user 2026-11-24)
    show_guides: bool = True
    cursor_coordinates: bool = False
    button_labels: str = "default"  # "none" / "default" / "all"
    page_controls: bool = False  # the floating page / zoom bar at the bottom of the page view
    tools_pane: bool = True  # the tool strip (Shift+F4)
    tool_order: list[str] = field(default_factory=list)  # the tool strip's icons as the user dragged them (keys)
    recent_tools: list[str] = field(default_factory=list)  # tools opened last, newest first: their bars are built at start
    check_revocation: bool = False  # signatures: ask the certificate authorities online (security review 2026-11-21)
    trust_windows_roots: bool = True  # signatures: also trust what the system trusts (Windows, or macOS + the Keychain)
    last_windows_certificate: str = ""  # thumbprint of the Windows-store certificate last used to sign
    pkcs11_module: str = ""  # the smart card / USB token's PKCS#11 driver (DLL) last used
    scan_letter_pages: bool = False  # PDF from Scanner: fit scans on Letter pages (else the scan's own size)
    # network scanning (AirScan / eSCL; Mac and Linux): the scanner and choices used last
    scan_device: str = ""  # its eSCL address
    scan_device_name: str = ""
    scan_addresses: list[str] = field(default_factory=list)  # scanners added by address (not found by Bonjour)
    scan_source: str = "glass"  # glass / feeder / feeder-duplex
    scan_color: str = "color"  # color / gray / bw
    scan_dpi: int = 300
    last_web_address: str = ""
    read_aloud_voice: str = ""
    read_aloud_rate: int = 0  # -10 (slow) to 10 (fast)
    read_aloud_volume: int = 100  # 0-100 (the toolbar's slider)
    read_aloud_engine: str = "auto"  # auto (Kokoro when fast enough here, else Piper) / kokoro / piper / windows
    read_aloud_voices: dict = field(default_factory=dict)  # engine -> voice code
    read_aloud_measured: dict = field(default_factory=dict)  # computer name -> the engine "auto" chose there
    show_document_tabs: bool = True  # a tab per open document (View ▸ Show/Hide ▸ Document Tabs)
    spelling_off: list[str] = field(default_factory=list)  # Check Spelling word lists switched off (all on by default)
    last_printer: str = ""
    # the PlanetPDF printer's printouts (user 2026-10-18; core/printouts.py): a folder to save them in
    # without asking ("" = ask, or PLANETPDF_PRINT_FOLDER), their name template ("" = {document}, or
    # PLANETPDF_PRINT_NAME), and the folder the Save dialog used last
    print_folder: str = ""
    print_name_template: str = ""
    print_last_folder: str = ""
    print_open_ask: bool = True  # ask "Open it after it's saved?" before the Save dialog (user 2026-10-18)
    print_open_after: bool = True  # the answer, once remembered (Preferences ▸ Documents)
    # scripting (2026-10-18): the local API for other programs and plugins; the plugins turned on
    api_enabled: bool = False  # (off until the user turns it on: security review 2026-11-25, user 2026-11-26)
    api_off_by_default: bool = True  # (settings from before then: their "on" was the old default; see load_settings)
    plugins_enabled: dict[str, str] = field(default_factory=dict)  # plugin id -> its fingerprint when turned on
    # Updates (Preferences, user 2026-10-07; Help ▸ Check for Updates any time)
    check_updates_on_start: bool = True  # a quiet check 20 s after PlanetPDF starts
    update_every_days: int = 14  # and while it runs, once this many days have passed; 0 = never
    update_last_check: float = 0.0  # time.time() of the last check
    font_previews: bool = True  # font lists show each font in its own typeface (off: just the names)
    page_cache_mb: int = 200  # rendered pages kept per window (pages on screen always stay)
    update_skipped: str = ""  # "Skip This Version"
    update_noticed: str = ""  # a version this user was told about but can't install (said once)
    # the version in which this user was told (or asked) about an install whose own programs are
    # older than it (once per version; security list 2026-11-26, item 6)
    install_parts_noticed: str = ""
    autosave_minutes: int = 5  # recovery copy of unsaved changes; 0 = off
    redact_fill: str = "#000000"
    redact_overlay_enabled: bool = False
    redact_overlay_text: str = "REDACTED"
    redact_remove_touching: bool = False
    highlight_fields: bool = True
    date_format: str = "%b %d, %Y"  # stamps' {date}
    time_format: str = "%I:%M %p"  # stamps' {time}
    last_stamp: str = "builtin-approved"  # Stamp ▾ shows it at the top (Acrobat)
    stamp_favorites: list[str] = field(default_factory=list)
    show_stamp_names: bool = False  # Stamp ▾ ▸ Show Stamp Names
    comments_in_panel: bool = False  # comments in the Comments panel on the right, not in boxes on the page
    collapse_drawing_tools: bool = False  # Comment bar: the drawing tools as one drop-down (Acrobat)
    markup_color: str = "#d11a1a"
    markup_colors: dict = field(default_factory=dict)  # per-tool color overrides
    markup_fill_enabled: bool = False
    markup_fill: str = "#fff3b0"
    markup_width: float = 2.0
    markup_opacity: float = 1.0
    markup_font_size: float = 12.0
    # Fill & Sign symbols (user 2026-10-26): the size of the last one placed or resized, per symbol
    # (name -> [width, height] as displayed; the next one is the same), and the circle's look
    symbol_sizes: dict = field(default_factory=dict)
    circle_filled: bool = False
    circle_line: float = 1.5
    form_auto_detect: bool = True  # Prepare Form ▸ Start: blanks and boxes become fields (Acrobat)
    # Add Text's font for the next text (font, bold, italic, underline, strikeout; core.fonts)
    text_format: dict = field(default_factory=dict)
    textbox_format: dict = field(default_factory=dict)  # the same for Comment text boxes and callouts
    # user 2026-11-16: a click on the page with no tool on adds text there; text added is
    # remembered (<profile>/text-history.json) and suggested while typing
    click_adds_text: bool = True
    remember_text: bool = True
    # ...including text holding what looks like an SSN, a card, or an account number (user
    # 2026-11-26: "allow it to be configured to store cc numbers, ssn, etc. as an option. By
    # default, that can be off")
    remember_private_numbers: bool = False
    last_certificate: str = ""  # path of the last .pfx/.p12 used (the password is never stored)
    sign_reason: str = ""
    sign_location: str = ""
    ocr_language: str = "eng"
    ocr_dpi: int = 300
    export_format: str = "pdf"
    export_dpi: int = 150
    export_jpeg_quality: int = 85
    export_compress: bool = True
    export_ocr: bool = True
    export_ai_reading: bool = False  # Export: read the pages with the AI (Unlimited-OCR), when it can run here
    ai_ocr_idle_minutes: int = 5  # the AI stays on the graphics card this long after an export
    window_geometry: str = ""  # base64 of QMainWindow.saveGeometry(); opaque to core
    window_state: str = ""
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)
    # An administrator's policy (core/policy.py): the settings it sets for everyone on this
    # computer, and what this user's own file says for them (kept for when the policy is gone)
    _locked: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    _own: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __setattr__(self, name: str, value: Any) -> None:
        """A setting an administrator's policy sets keeps the policy's value, whatever sets it."""
        locked = self.__dict__.get("_locked")
        if locked and name in locked:
            value = locked[name]
        object.__setattr__(self, name, value)

    def locked(self, name: str) -> bool:
        """Set by the administrator's policy: Preferences shows it grayed."""
        return name in self._locked

    def author(self) -> str:
        """The name comments are signed with: "Your name" from Preferences, else the
        computer login name, as Acrobat does (user 2026-10-04: notes had no author)."""
        if self.user_name.strip():
            return self.user_name.strip()
        import getpass

        try:
            return getpass.getuser()
        except Exception:  # (no login name to be found)
            return ""

    def add_recent_file(self, path: Path) -> None:
        entry = str(path)
        self.recent_files = [entry] + [p for p in self.recent_files if p != entry]
        del self.recent_files[MAX_RECENT_FILES:]


def _known_fields() -> dict[str, Any]:
    return {f.name: f for f in fields(Settings) if not f.name.startswith("_")}


def _under_policy(values: dict[str, Any], extra: dict[str, Any] | None = None) -> Settings:
    """The settings with the administrator's policy in force (core/policy.py)."""
    from planetpdf.core import policy

    locked = dict(policy.current().settings)
    own = {name: values[name] for name in locked if name in values}
    return Settings(**{**values, **locked}, _extra=extra or {}, _locked=locked, _own=own)


def load_settings(directory: Path) -> Settings:
    path = directory / SETTINGS_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _under_policy({})
    except (OSError, ValueError) as exc:
        log.warning("Ignoring unreadable settings file %s: %s", path, exc)
        return _under_policy({})
    if not isinstance(raw, dict):
        return _under_policy({})

    known = _known_fields()
    defaults = Settings()
    values: dict[str, Any] = {}
    for name in known:
        if name in raw and isinstance(raw[name], type(getattr(defaults, name))):
            values[name] = raw[name]
    if "recent_files" in values:
        values["recent_files"] = [p for p in values["recent_files"] if isinstance(p, str)]
    if "api_off_by_default" not in raw:
        # Saved before the local API was off by default: its "on" was the old default, not a
        # choice, so it goes off once, unless plugins are on (they work through it).
        values["api_enabled"] = bool(values.get("api_enabled")) and bool(values.get("plugins_enabled"))
    extra = {k: v for k, v in raw.items() if k not in known}
    return _under_policy(values, extra)


def save_settings(settings: Settings, directory: Path) -> None:
    data = asdict(settings)
    extra = data.pop("_extra")
    locked, own = data.pop("_locked"), data.pop("_own")
    for name in locked:  # (the user's own choice is what's kept, not the policy's value)
        if name in own:
            data[name] = own[name]
        else:
            data.pop(name, None)
    payload = {**extra, **data}
    write_bytes_atomic(
        directory / SETTINGS_FILENAME,
        json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"),
    )
