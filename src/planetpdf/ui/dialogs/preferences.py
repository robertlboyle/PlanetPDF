"""Preferences: pages listed down the left (General, Appearance, Performance, Documents,
Signatures, Read Out Loud, Scripting & Plugins, Updates), small enough for any screen (user 2026-10-07)."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.document import PdfError

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

DATE_FORMATS = ["%b %d, %Y", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d", "%B %d, %Y", "%d %b %Y"]
TIME_FORMATS = ["%I:%M %p", "%H:%M"]


class PreferencesDialog(QDialog):
    # Pages, listed down the left (user 2026-10-07: "tabs to the left … small enough that it
    # will fit on almost any screen. Organize by performance, appearance, updates").
    PAGES = [("general", "General", "user"), ("appearance", "Appearance", "palette"),
             ("performance", "Performance", "gauge-high"), ("documents", "Documents", "floppy-disk"),
             ("signatures", "Signatures", "file-signature"), ("reading", "Read Out Loud", "volume"),
             ("scripting", "Scripting & Plugins", "puzzle-piece"), ("updates", "Updates", "arrows-rotate")]
    THEMES = [("system", "Follow System Setting"), ("light", "Light"), ("dark", "Dark")]
    _last_page = 0  # (the page shown last, while PlanetPDF runs)

    def __init__(self, window: MainWindow, parent: QWidget | None = None) -> None:
        super().__init__(parent or window)
        from planetpdf.ui import fa

        self._window = window
        s = window.settings
        self.setWindowTitle("Preferences")
        now = datetime.now()

        # General: you, and where your settings live
        self.user_name = QLineEdit(s.user_name, self)
        self.user_name.setPlaceholderText("For stamps ({user}) and comments")
        self.user_name.setToolTip("Shown on stamps ({user}) and as the author of comments; empty = your Windows name")
        self.company_name = QLineEdit(s.company_name, self)
        self.company_name.setPlaceholderText("For stamps ({company}); blank = none")
        self.company_name.setToolTip("Shown on the Company Confidential stamp and on stamps with {company}; "
                                     "nothing is written when it's blank")
        self.date_format = QComboBox(self)
        for fmt in DATE_FORMATS:
            self.date_format.addItem(now.strftime(fmt), fmt)
        self.date_format.setCurrentIndex(max(self.date_format.findData(s.date_format), 0))
        self.time_format = QComboBox(self)
        for fmt in TIME_FORMATS:
            self.time_format.addItem(now.strftime(fmt).lstrip("0"), fmt)
        self.time_format.setCurrentIndex(max(self.time_format.findData(s.time_format), 0))
        you = QGroupBox("You", self)
        form = QFormLayout(you)
        form.addRow("Your name:", self.user_name)
        form.addRow("Company name:", self.company_name)
        form.addRow("Date format:", self.date_format)
        form.addRow("Time format:", self.time_format)
        where = QLabel(str(window.profile.root), self)
        where.setWordWrap(True)
        where.setTextInteractionFlags(where.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse)
        open_folder = QPushButton("Open Folder", self)
        open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(window.profile.root))))
        profile = QGroupBox("Profile folder", self)
        profile.setToolTip("Your settings, signatures, and stamps; it roams with your Windows profile")
        prow = QVBoxLayout(profile)
        prow.addWidget(where)
        prow.addWidget(open_folder, 0, Qt.AlignmentFlag.AlignLeft)
        general = (you, profile)
        from planetpdf.ui import default_app

        if default_app.available():  # (the installed program only: from source it would be Python)
            self.make_default = QPushButton("Make PlanetPDF the Default App for PDF Files…" if sys.platform == "win32"
                                            else "Make PlanetPDF the Default App for PDF Files", self)
            self.make_default.clicked.connect(lambda: default_app.make_default(window))
            if default_app.is_default():
                self.make_default.setText("PlanetPDF Opens PDF Files")
                self.make_default.setEnabled(False)
            pdf_app = QGroupBox("PDF files", self)
            QVBoxLayout(pdf_app).addWidget(self.make_default, 0, Qt.AlignmentFlag.AlignLeft)
            general = (you, profile, pdf_app)

        # Appearance
        self.theme = QComboBox(self)
        for key, text in self.THEMES:
            self.theme.addItem(text, key)
        self.theme.setCurrentIndex(max(self.theme.findData(s.theme), 0))
        self.theme.setToolTip("A Windows contrast theme (or the Mac's Increase contrast) always wins: "
                              "PlanetPDF then uses its colors")
        self.text_size = QComboBox(self)
        from planetpdf.ui.theme import TEXT_SIZES

        for percent in TEXT_SIZES:
            self.text_size.addItem("Normal" if percent == 100 else f"{percent}%", percent)
        self.text_size.setCurrentIndex(max(self.text_size.findData(s.text_size), 0))
        self.text_size.setToolTip("Menus, toolbars, panels and dialogs; the pages zoom on their own")
        look = QGroupBox("Display", self)
        lform = QFormLayout(look)
        lform.addRow("Theme:", self.theme)
        lform.addRow("Text size:", self.text_size)

        # Performance: memory and speed
        self.page_cache = QSpinBox(self)
        self.page_cache.setRange(50, 4000)
        self.page_cache.setSingleStep(50)
        self.page_cache.setSuffix(" MB")
        self.page_cache.setValue(s.page_cache_mb)
        self.page_cache.setToolTip("Pages already drawn are kept so going back to them is instant. More keeps more "
                                   "pages (useful at high zoom); less uses less memory. The pages on screen always stay.")
        self.font_previews = QCheckBox("Show fonts in their own typeface in font lists", self)
        self.font_previews.setChecked(s.font_previews)
        self.font_previews.setToolTip("Off: the font lists show just the names (quicker, uses less memory). "
                                      "Text on the page always shows in its own font.")
        speed = QGroupBox("Memory and speed", self)
        pform = QFormLayout(speed)
        pform.addRow("Keep rendered pages up to:", self.page_cache)
        pform.addRow(self.font_previews)
        # AI text reading for exports (Unlimited-OCR; the optional add-on and an NVIDIA card; 2026-10-21)
        from planetpdf.core import ai_ocr

        ai = ai_ocr.status()
        ai_box = QGroupBox("AI text reading for exports (Unlimited-OCR)", self)
        aform = QFormLayout(ai_box)
        self.ai_status = QLabel(("Ready. " if ai.ready else "Not available here. ") + ai.reason, self)
        self.ai_status.setWordWrap(True)
        aform.addRow(self.ai_status)
        needs = QLabel("Needs the optional <b>PlanetPDF AI Text Reading</b> add-on and an <b>NVIDIA graphics card "
                       "with about 8 GB of memory</b> (Windows).", self)
        needs.setWordWrap(True)
        aform.addRow(needs)
        self.ai_idle = QSpinBox(self)
        self.ai_idle.setRange(1, 60)
        self.ai_idle.setSuffix(" minutes")
        self.ai_idle.setValue(s.ai_ocr_idle_minutes)
        self.ai_idle.setToolTip("After an export the model stays on the graphics card this long, so the next one starts "
                                "at once; then its memory is freed.")
        aform.addRow("Keep the AI loaded for:", self.ai_idle)

        # Documents
        self.autosave = QSpinBox(self)
        self.autosave.setRange(0, 60)
        self.autosave.setSuffix(" min")
        self.autosave.setSpecialValueText("Off")
        self.autosave.setValue(s.autosave_minutes)
        self.autosave.setToolTip("While a document has unsaved changes, keep a recovery copy on this computer, "
                                 "offered back if PlanetPDF closes unexpectedly.")
        recovery = QGroupBox("Recovery", self)
        dform = QFormLayout(recovery)
        dform.addRow("Auto-save for recovery every:", self.autosave)
        printer = self._printer_group(s)
        adding = self._adding_text_group(s)

        # Signatures
        self.tsa = QLineEdit(s.timestamp_url, self)
        self.tsa.setPlaceholderText("e.g. http://timestamp.digicert.com (empty = none)")
        self.certs = QListWidget(self)
        self.certs.setMaximumHeight(90)
        import_button = QPushButton("Import Certificate…", self)
        import_button.clicked.connect(self._import_cert)
        signing = QGroupBox("Digital signatures", self)
        sform = QFormLayout(signing)
        sform.addRow("Timestamp server:", self.tsa)
        trusted = QLabel("Trusted certificates (e.g. your company's certificate authority):", self)
        trusted.setWordWrap(True)
        sform.addRow(trusted)
        sform.addRow(self.certs)
        import_row = QHBoxLayout()
        import_row.addWidget(import_button)
        import_row.addStretch(1)
        sform.addRow(import_row)
        self.check_revocation = QCheckBox("Check online whether signers' certificates were revoked", self)
        self.check_revocation.setChecked(s.check_revocation)
        self.check_revocation.setToolTip("When a signed document opens, PlanetPDF asks the certificate authority "
                                         "named in the signer's certificate whether it was revoked. This goes "
                                         "online and tells that authority which certificate you're checking.")
        sform.addRow(self.check_revocation)
        mac = sys.platform == "darwin"
        self.trust_windows = QCheckBox(f"Also trust the certificates {'macOS' if mac else 'Windows'} trusts", self)
        self.trust_windows.setChecked(s.trust_windows_roots)
        self.trust_windows.setToolTip("The root certificates macOS trusts, and the certificates in your Keychain"
                                      if mac else "The root certificates in Windows' certificate store, e.g. your "
                                                  "company's CA")
        self.trust_windows.setVisible(sys.platform in ("win32", "darwin"))
        sform.addRow(self.trust_windows)

        # Read Out Loud
        reading = self._read_aloud_group(s)

        # Updates (user 2026-10-07)
        self.check_on_start = QCheckBox("Check for updates on startup", self)
        self.check_on_start.setChecked(s.check_updates_on_start)
        self.check_on_start.setToolTip("A quiet check in the background soon after PlanetPDF starts")
        self.update_days = QSpinBox(self)
        self.update_days.setRange(0, 365)
        self.update_days.setSuffix(" days")
        self.update_days.setSpecialValueText("Never")
        self.update_days.setValue(s.update_every_days)
        self.update_days.setToolTip("While PlanetPDF is running. Help ▸ Check for Updates works any time.")
        check_now = QPushButton("Check Now", self)
        check_now.clicked.connect(lambda: window.check_for_updates())
        updates = QGroupBox("Updates", self)
        uform = QFormLayout(updates)
        uform.addRow(self.check_on_start)
        uform.addRow("Check for updates every:", self.update_days)
        uform.addRow(check_now)

        # Scripting & Plugins (2026-10-18)
        from planetpdf.ui.dialogs.scripting_page import ScriptingPage

        self.scripting = ScriptingPage(window, self)

        # The pages, and the list down the left that picks one
        self.pages = QStackedWidget(self)
        self.page_list = QListWidget(self)
        self.page_list.setAccessibleName("Preference pages")
        self.page_list.setObjectName("preferencePages")
        self.page_list.setIconSize(QSize(16, 16))
        self.page_list.setSpacing(1)
        contents = {"general": general, "appearance": (look,), "performance": (speed, ai_box), "documents": (recovery, adding, printer),
                    "signatures": (signing,), "reading": (reading,), "scripting": (self.scripting,),
                    "updates": (updates,)}
        for key, label, icon in self.PAGES:
            page = QWidget(self)
            page.setObjectName(f"preferences_{key}")
            column = QVBoxLayout(page)
            column.setContentsMargins(0, 0, 0, 0)
            for box in contents[key]:
                column.addWidget(box)
            column.addStretch(1)
            self.pages.addWidget(page)
            QListWidgetItem(fa.icon(icon), label, self.page_list)
        self.page_list.setFixedWidth(self.page_list.sizeHintForColumn(0) + 28)
        self.page_list.currentRowChanged.connect(self._show_page)
        self.page_list.setCurrentRow(min(PreferencesDialog._last_page, len(self.PAGES) - 1))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        body = QHBoxLayout()
        body.addWidget(self.page_list)
        body.addWidget(self.pages, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addWidget(buttons)
        from planetpdf.ui import accessibility

        accessibility.put_first(self, self.page_list)  # (made after the pages: Tab reached it last)
        self.resize(640, 440)  # (fits a 1366 x 768 screen at 125 % with room to spare)
        self._refresh_certs()
        self.policy_note = QLabel("", self)
        self.policy_note.setStyleSheet("color: gray;")
        self.policy_note.setWordWrap(True)
        layout.insertWidget(1, self.policy_note)
        self._show_policy(s, check_now)

    # What an administrator set for everyone on this computer (core/policy.py): shown, grayed.
    LOCKED_CONTROLS = {
        "check_updates_on_start": ("check_on_start",), "update_every_days": ("update_days",),
        "check_revocation": ("check_revocation",), "trust_windows_roots": ("trust_windows",),
        "timestamp_url": ("tsa",), "autosave_minutes": ("autosave",), "company_name": ("company_name",),
        "remember_text": ("remember_text",), "remember_private_numbers": ("remember_private",),
        "print_folder": ("print_ask", "print_to_folder", "print_folder"), "print_name_template": ("print_name",),
    }
    BY_ADMINISTRATOR = "Set by your administrator for everyone on this computer."

    def _lock(self, widget: QWidget, why: str = "") -> None:
        widget.setEnabled(False)
        widget.setToolTip(why or self.BY_ADMINISTRATOR)

    def _show_policy(self, s, check_now: QPushButton) -> None:
        from planetpdf.core import policy
        from planetpdf.core import update as U

        current = policy.current()
        locked = [name for name in self.LOCKED_CONTROLS if s.locked(name)]
        for name in locked:
            for attribute in self.LOCKED_CONTROLS[name]:
                self._lock(getattr(self, attribute))
        if "remember_private_numbers" in locked:  # (ticking "Remember the text" would turn it back on)
            self.remember_text.toggled.disconnect(self.remember_private.setEnabled)
        if s.locked("api_enabled"):
            self._lock(self.scripting.api_enabled)
        updates_off = U.checks_off(U.install_root())
        if updates_off:
            for widget in (self.check_on_start, self.update_days, check_now):
                self._lock(widget, updates_off)
        from planetpdf.core import voice_download

        button = getattr(self, "download_voices", None)  # (there only while the voices aren't downloaded)
        if voice_download.turned_off() and button is not None:
            self._lock(button, voice_download.turned_off())
        if locked or s.locked("api_enabled") or updates_off or current.off:
            self.policy_note.setText("Some settings here are set by your administrator for everyone on this "
                                     "computer; they are grayed.")
        self.policy_note.setVisible(bool(self.policy_note.text()))

    def _show_page(self, row: int) -> None:
        self.pages.setCurrentIndex(row)
        PreferencesDialog._last_page = row

    def show_page(self, key: str) -> None:
        """Opens on a page ("performance", "updates", …)."""
        keys = [k for k, _l, _i in self.PAGES]
        self.page_list.setCurrentRow(keys.index(key))

    # --- View ▸ Read Out Loud ---------------------------------------------------------------------

    ENGINES = [
        ("auto", "Automatic: Kokoro (the most natural voices) when installed and smooth here"),
        ("kokoro", "Kokoro: the most natural voices (needs a fast computer)"),
        ("piper", "Piper: very good voices, fast on any computer"),
        ("windows", "Windows voices"),
        ("mac", "macOS voices (better: download Premium voices in System Settings ▸ Accessibility)"),
    ]

    def _read_aloud_group(self, s) -> QGroupBox:
        from planetpdf.core import neural_speech as NS

        group = QGroupBox("Read Out Loud", self)
        form = QFormLayout(group)
        self.read_engine = QComboBox(self)
        # (its long descriptions mustn't widen the dialog: they show in full when it opens)
        self.read_engine.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.read_engine.setMinimumContentsLength(22)
        mac = NS.mac_voices_available()
        engines = [(k, t) for k, t in self.ENGINES
                   if k == "windows" and sys.platform == "win32" or k == "mac" and mac
                   or k == "auto" and (NS.kokoro_available() and (NS.piper_voices() or mac))
                   or k == "kokoro" and NS.kokoro_available() or k == "piper" and NS.piper_voices()]
        for key, text in engines:
            self.read_engine.addItem(text, key)
        self.read_engine.setCurrentIndex(max(self.read_engine.findData(s.read_aloud_engine), 0))
        form.addRow("Voice engine:", self.read_engine)
        self.voice_boxes: dict[str, QComboBox] = {}
        choices = {
            "kokoro": list(NS.KOKORO_VOICE_NAMES.items()) if NS.kokoro_available() else [],
            "piper": [(v, NS.PIPER_VOICE_NAMES.get(v, v)) for v in NS.piper_voices()],
            "windows": [(v, v) for v in self._windows_voices()],
            "mac": list(self._mac_voices().items()),
        }
        for key, label in (("kokoro", "Kokoro voice:"), ("piper", "Piper voice:"), ("windows", "Windows voice:"),
                           ("mac", "macOS voice:")):
            box = QComboBox(self)
            for code, name in choices[key]:
                box.addItem(name, code)
            box.setCurrentIndex(max(box.findData(s.read_aloud_voices.get(key, "")), 0))
            self.voice_boxes[key] = box
            form.addRow(label, box)
        self.read_speed = QSpinBox(self)
        self.read_speed.setRange(-10, 10)
        self.read_speed.setValue(s.read_aloud_rate)
        self.read_speed.setToolTip("-10 slowest, 0 normal, 10 fastest")
        try_it = QPushButton("Try Voice", self)
        try_it.clicked.connect(self.try_voice)
        speed_row = QHBoxLayout()
        speed_row.addWidget(self.read_speed)
        speed_row.addStretch(1)
        speed_row.addWidget(try_it)
        form.addRow("Speed:", speed_row)
        self.download_voices = None
        if not NS.kokoro_available() and NS.USER_MODELS is not None:  # (2026-10-14: offered, not bundled)
            self.download_voices = QPushButton("Download Natural Voices (354 MB)…", self)
            self.download_voices.setToolTip("Kokoro: the most natural reading voices, a one-time download")
            self.download_voices.clicked.connect(self._download_voices)
            form.addRow("", self.download_voices)
        self._form_reading = form
        self._trial = None
        self.read_engine.currentIndexChanged.connect(lambda _i: self._show_voice_rows())
        self._show_voice_rows()
        return group

    def _download_voices(self) -> None:
        if self._window.extras.download_natural_voices():
            self.download_voices.setEnabled(False)
            self.download_voices.setText("Natural voices downloaded: choose Kokoro after reopening Preferences")

    @staticmethod
    def _mac_voices() -> dict[str, str]:
        if sys.platform != "darwin":
            return {}
        from planetpdf.core import mac_speech

        return mac_speech.voices()

    def _windows_voices(self) -> list[str]:
        if sys.platform != "win32":
            return []
        try:
            from planetpdf.core.windows_speech import Speaker

            return Speaker().voices
        except Exception:
            return []

    def _show_voice_rows(self) -> None:
        engine = self.read_engine.currentData()
        wanted = {"auto": ("kokoro", "piper"), "kokoro": ("kokoro",), "piper": ("piper",), "windows": ("windows",),
                  "mac": ("mac",)}
        for key, box in self.voice_boxes.items():
            self._form_reading.setRowVisible(box, key in wanted.get(engine, ()) and box.count() > 0)

    def _reading_choices(self) -> dict:
        return {key: box.currentData() for key, box in self.voice_boxes.items() if box.currentData()}

    def try_voice(self) -> None:
        """Read a sentence with the chosen engine, voice and speed."""
        import platform

        from planetpdf.core import neural_speech as NS

        if self._trial is not None:
            self._trial.stop()
        engine = self.read_engine.currentData()
        text = "This is how PlanetPDF reads your documents aloud."
        if engine == "windows":
            from planetpdf.core.windows_speech import Speaker

            self._trial = Speaker()
            self._trial.rate = self.read_speed.value()
            if self.voice_boxes["windows"].currentData():
                self._trial.set_voice(self.voice_boxes["windows"].currentData())
        else:
            known = self._window.settings.read_aloud_measured.get(platform.node(), "")
            self._trial = NS.Speaker(engine, voices=self._reading_choices(), rate=self.read_speed.value(), known=known)
        self._trial.speak(text)

    def done(self, result: int) -> None:
        if self._trial is not None:
            self._trial.stop()
        super().done(result)

    def _refresh_certs(self) -> None:
        self.certs.clear()
        names = self._window.trust_store.names()
        self.certs.addItems(names or ["(none: signatures will show as “signer not trusted”)"])

    def _import_cert(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Trusted Certificate", "", "Certificates (*.cer *.crt *.pem *.der)")
        if not path:
            return
        try:
            self._window.trust_store.import_certificate(Path(path))
        except (PdfError, OSError) as exc:
            self._window.show_message("Import Certificate", str(exc))
            return
        self._refresh_certs()

    def _adding_text_group(self, s) -> QGroupBox:
        """Click to add text, and the remembered text offered while typing (user 2026-11-16)."""
        group = QGroupBox("Adding text", self)
        box = QVBoxLayout(group)
        self.click_adds_text = QCheckBox("Clicking the page with no tool chosen adds text there", self)
        self.click_adds_text.setChecked(s.click_adds_text)
        self.click_adds_text.setToolTip("Also right after placing a signature, initials, or a stamp. "
                                        "Dragging over text still selects it.")
        self.remember_text = QCheckBox("Remember the text I add and suggest it as I type", self)
        self.remember_text.setChecked(s.remember_text)
        self.remember_text.setToolTip("Kept only in your profile folder. Tab, Right, or End takes a suggestion; "
                                      "Up and Down show the others; Shift+Delete forgets it.")
        self.remember_private = QCheckBox("Also remember text with Social Security, card, or account numbers", self)
        self.remember_private.setChecked(s.remember_private_numbers)
        self.remember_private.setToolTip("Off: text holding what looks like one of those numbers is never remembered, "
                                         "and switching this off forgets any that was.")
        self.remember_private.setStyleSheet("margin-left: 22px;")
        self.remember_private.setEnabled(s.remember_text)
        self.remember_text.toggled.connect(self.remember_private.setEnabled)
        self.clear_text = QPushButton(self, clicked=self._clear_remembered_text)
        self._show_remembered_count()
        box.addWidget(self.click_adds_text)
        box.addWidget(self.remember_text)
        box.addWidget(self.remember_private)
        box.addWidget(self.clear_text, 0, Qt.AlignmentFlag.AlignLeft)
        return group

    def _show_remembered_count(self) -> None:
        count = len(self._window.text_history.entries)
        self.clear_text.setText(f"Forget Remembered Text ({count:,})")
        self.clear_text.setEnabled(count > 0)

    def _clear_remembered_text(self) -> None:
        self._window.text_history.clear()
        self._show_remembered_count()

    def _printer_group(self, s) -> QGroupBox:
        """Where printouts from the PlanetPDF printer go (user 2026-10-18): asked each time
        (the Save dialog starts in the folder used last), or one folder; and their name."""
        import os

        from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QRadioButton

        from planetpdf.core import printouts as PR

        group = QGroupBox("PlanetPDF printer", self)
        box = QVBoxLayout(group)
        self.print_ask = QRadioButton("Ask where to save each printout (starts in the folder used last)", self)
        self.print_to_folder = QRadioButton("Save printouts in this folder without asking:", self)
        self.print_folder = QLineEdit(s.print_folder, self)
        self.print_folder.setPlaceholderText("a folder")
        browse = QPushButton("Browse…", self)

        def choose() -> None:
            start = self.print_folder.text() or s.print_last_folder or ""
            folder = QFileDialog.getExistingDirectory(self, "Folder for Printouts", start)
            if folder:
                self.print_folder.setText(folder)
                self.print_to_folder.setChecked(True)

        browse.clicked.connect(choose)
        (self.print_to_folder if s.print_folder else self.print_ask).setChecked(True)
        self.print_folder.textEdited.connect(lambda _t: self.print_to_folder.setChecked(True))
        row = QHBoxLayout()
        row.addWidget(self.print_folder, 1)
        row.addWidget(browse)
        box.addWidget(self.print_ask)
        box.addWidget(self.print_to_folder)
        box.addLayout(row)
        if os.environ.get(PR.ENV_FOLDER, "").strip() and not s.print_folder:
            it = QLabel(f"IT has set a printout folder for this computer: {os.environ[PR.ENV_FOLDER]}. "
                        "Printouts go there unless you choose another folder here.", self)
            it.setWordWrap(True)
            it.setStyleSheet("color: gray;")
            self.print_ask.setText("Use this computer's printout folder (set by IT)")
            box.addWidget(it)
        self.print_name = QLineEdit(s.print_name_template, self)
        self.print_name.setPlaceholderText(PR.template(""))
        self.print_name.setToolTip("\n".join(f"{{{k}}}: {v}" for k, v in PR.TOKENS.items()))
        form = QFormLayout()
        form.addRow("File name:", self.print_name)
        box.addLayout(form)
        hint = QLabel("You can use " + ", ".join(f"{{{k}}}" for k in PR.TOKENS) + ".", self)
        hint.setStyleSheet("color: gray;")
        hint.setWordWrap(True)
        box.addWidget(hint)
        self.print_open = QComboBox(self)
        for text, key in (("Ask each time", "ask"), ("Open the PDF", "open"), ("Don't open it", "keep")):
            self.print_open.addItem(text, key)
        self.print_open.setCurrentIndex(0 if s.print_open_ask else (1 if s.print_open_after else 2))
        self.print_open.setToolTip("After a printout is saved: open it in PlanetPDF? (Asking offers to remember "
                                   "the answer.)")
        form.addRow("After saving:", self.print_open)
        return group

    def _save(self) -> None:
        s = self._window.settings
        s.user_name = self.user_name.text().strip()
        s.company_name = self.company_name.text().strip()
        s.date_format = self.date_format.currentData()
        s.time_format = self.time_format.currentData()
        s.timestamp_url = self.tsa.text().strip()
        s.autosave_minutes = self.autosave.value()
        s.click_adds_text = self.click_adds_text.isChecked()
        s.remember_text = self.remember_text.isChecked()
        s.remember_private_numbers = self.remember_private.isChecked()
        if not s.remember_private_numbers:
            self._window.text_history.forget_private()
        s.print_folder = self.print_folder.text().strip() if self.print_to_folder.isChecked() else ""
        s.print_name_template = self.print_name.text().strip()
        s.print_open_ask = self.print_open.currentData() == "ask"
        if not s.print_open_ask:
            s.print_open_after = self.print_open.currentData() == "open"
        s.trust_windows_roots = self.trust_windows.isChecked()
        s.check_revocation = self.check_revocation.isChecked()
        s.check_updates_on_start = self.check_on_start.isChecked()
        s.update_every_days = self.update_days.value()
        s.page_cache_mb = self.page_cache.value()
        s.ai_ocr_idle_minutes = self.ai_idle.value()
        from planetpdf.core import ai_ocr

        ai_ocr.IDLE_SECONDS = s.ai_ocr_idle_minutes * 60
        self.scripting.save()
        from planetpdf.ui.main_window import MainWindow

        for window in MainWindow.open_windows():
            window.view.set_cache_limit(s.page_cache_mb)  # (at once: a smaller limit frees memory now)
        if self.text_size.currentData() != s.text_size:
            from planetpdf.ui.theme import apply_text_size

            s.text_size = self.text_size.currentData()
            apply_text_size(s.text_size)  # (every window at once)
        theme = self.theme.currentData()
        if theme != s.theme:
            for window in MainWindow.open_windows():  # (the same as View ▸ Display Theme)
                window.set_theme(theme)
        if self.font_previews.isChecked() != s.font_previews:
            s.font_previews = self.font_previews.isChecked()
            for window in MainWindow.open_windows():  # (every window's font lists follow at once)
                window.text_format.apply_font_previews()
        reading = (self.read_engine.currentData() or s.read_aloud_engine, self._reading_choices(), self.read_speed.value())
        if reading != (s.read_aloud_engine, s.read_aloud_voices, s.read_aloud_rate):
            s.read_aloud_engine, s.read_aloud_voices, s.read_aloud_rate = reading
            from planetpdf.ui.phase_d_ui import ExtrasUi

            ExtrasUi.reset_speaker()  # (the next reading uses them)
        if self._window.autosave is not None:
            self._window.autosave.apply_settings()
        self._window.save_settings_now()
        self.accept()
