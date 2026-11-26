"""PDF from Scanner / Web Page (Create and Insert Pages), Edit ▸ Check Spelling, and View ▸
Read Out Loud (backlog Phase D, built 2026-10-02)."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.document import PdfError

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow


SCAN_PREFIX = "planetpdf-scan-"


def _forget_scans(folder: Path) -> None:
    """Deletes a folder of scanned pages (only one PlanetPDF made for a scan)."""
    if folder.name.startswith(SCAN_PREFIX):
        shutil.rmtree(folder, ignore_errors=True)

class ExtrasUi:
    def __init__(self, window: MainWindow) -> None:
        self.w = window
        self._speaker = None
        self._reading_to_end = False
        self._stopped = True  # (Stop pressed / nothing started: the button is Play)
        self._read_timer = QTimer(window, interval=300)
        self._read_timer.timeout.connect(self._read_tick)
        self._speller = None

    # --- PDF from Scanner -----------------------------------------------------------------------

    def scan_pages(self) -> list[Path]:
        """Windows: its own scan dialog (WIA), page after page until "no more". Mac and Linux: a
        network scanner over AirScan / eSCL (``_network_scan``)."""
        if sys.platform != "win32":
            return self._network_scan()
        from planetpdf.core import windows_scanner

        w = self.w
        if not windows_scanner.scanners():
            w.show_message("Scanner", "No scanner was found. Check that it's on, connected, and installed in Windows.")
            return []
        folder = Path(tempfile.mkdtemp(prefix=SCAN_PREFIX))
        pages: list[Path] = []
        try:
            self._scan_windows(folder, pages)
        except BaseException:
            _forget_scans(folder)
            raise
        if not pages:
            _forget_scans(folder)
        return pages

    def _scan_windows(self, folder: Path, pages: list[Path]) -> None:
        w = self.w
        while True:
            try:
                page = self.scan_one(folder)
            except PdfError as exc:
                w.show_message("Scanner", str(exc))
                break
            if page is None:
                break
            pages.append(page)
            if not w.ask("Scanner", f"{len(pages)} page{'s' if len(pages) != 1 else ''} scanned.\n\nScan another page?"):
                break

    def scan_one(self, folder: Path) -> Path | None:  # (tests replace this)
        from planetpdf.core import windows_scanner

        return windows_scanner.scan_page(folder)

    def choose_scanner(self):  # (tests replace this)
        """The scanner and settings to use (``ScanDialog``), or None when canceled."""
        from planetpdf.ui.dialogs.scan import ScanDialog, find_scanners

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            found = find_scanners()
        except (OSError, PdfError):
            found = []
        finally:
            QApplication.restoreOverrideCursor()
        dialog = ScanDialog(self.w, found)
        return dialog.choice() if dialog.exec() == QDialog.DialogCode.Accepted else None

    def _network_scan(self) -> list[Path]:
        """Scan with a Mac (ImageCaptureCore: USB, network, shared) or network (AirScan) scanner:
        the glass gives a page at a time ("Scan another page?"), the feeder all its pages at once
        ("Scan more pages?")."""
        choice = self.choose_scanner()
        if choice is None:
            return []
        folder = Path(tempfile.mkdtemp(prefix=SCAN_PREFIX))
        pages: list[Path] = []
        try:
            self._scan_network(choice, folder, pages)
        except BaseException:
            _forget_scans(folder)
            raise
        if not pages:
            _forget_scans(folder)
        return pages

    def _scan_network(self, choice, folder: Path, pages: list[Path]) -> None:
        from planetpdf.core import escl_scanner
        from planetpdf.ui.dialogs.scan import scan
        from planetpdf.ui.tasks import TaskCanceled, run_in_background

        w = self.w
        while True:
            try:
                pages += run_in_background(
                    w, "Scanner", f"Scanning with {choice.scanner.name}",
                    lambda progress: scan(choice.scanner, folder, source=choice.source, color=choice.color,
                                          dpi=choice.dpi, caps=choice.caps, progress=progress,
                                          first_number=len(pages) + 1))
            except (TaskCanceled, escl_scanner.ScanCanceled):
                break
            except PdfError as exc:
                w.show_message("Scanner", str(exc))
                break
            more = "Scan another page?" if choice.source == "glass" else "Scan more pages from the feeder?"
            if not w.ask("Scanner", f"{len(pages)} page{'s' if len(pages) != 1 else ''} scanned.\n\n{more}"):
                break

    def _scans_as_pdf(self, pages: list[Path]) -> bytes | None:
        """The scanned pages as one PDF; the scans themselves are deleted, whatever happens
        (security review 2026-11-25: they stayed in the temporary folder)."""
        from planetpdf.core.combine import CombineOptions, combine_files

        if not pages:
            return None
        try:
            out = pages[0].parent / "scanned.pdf"
            combine_files(pages, out, CombineOptions(image_page="letter" if self.w.settings.scan_letter_pages
                                                     else "image"))
            return out.read_bytes()
        finally:
            for folder in {page.parent for page in pages}:
                _forget_scans(folder)

    def create_from_scanner(self) -> bool:
        data = self._scans_as_pdf(self.scan_pages())
        if data is None:
            return False
        if self.w._save_new_pdf(data, "Scanned.pdf"):
            self._offer_ocr()
            return True
        return False

    def insert_from_scanner(self) -> bool:
        from planetpdf.ui.commands import InsertPdfPagesCommand

        data = self._scans_as_pdf(self.scan_pages())
        if data is None:
            return False
        self.w._push(InsertPdfPagesCommand(self.w, data, self.w._insert_position(), "Scanner", None))
        return True

    def _offer_ocr(self) -> None:
        from planetpdf.core import ocr

        if ocr.available_languages() and self.w.ask("Recognize Text", "Make the scanned pages searchable (OCR) now?"):
            self.w.act_ocr.trigger()

    # --- PDF from Web Page ------------------------------------------------------------------------

    def ask_address(self) -> str | None:  # (tests replace this)
        text, ok = QInputDialog.getText(self.w, "PDF from Web Page", "Web page address (or an .html file):",
                                        QLineEdit.EchoMode.Normal, self.w.settings.last_web_address)
        return text.strip() if ok and text.strip() else None

    def web_capture_off(self) -> bool:
        """Said before asking for an address: an administrator turned this off (core/policy.py)."""
        from planetpdf.core import policy

        if not policy.reason("web_capture"):
            return False
        self.w.show_message("PDF from Web Page", policy.reason("web_capture"))
        return True

    def _capture(self, address: str) -> bytes | None:
        from planetpdf.core.web_capture import WebCaptureCanceled, web_page_to_pdf
        from planetpdf.ui.tasks import TaskCanceled, run_in_background

        folder = Path(tempfile.mkdtemp(prefix="planetpdf-web-"))
        out = folder / "page.pdf"
        try:
            run_in_background(self.w, "PDF from Web Page", f"Capturing {address}",
                              lambda progress: web_page_to_pdf(address, out, progress=progress))
            data = out.read_bytes()
        except (TaskCanceled, WebCaptureCanceled):
            return None
        except PdfError as exc:
            self.w.show_message("PDF from Web Page", str(exc))
            return None
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        self.w.settings.last_web_address = address
        return data

    def create_from_web(self, address: str | None = None) -> bool:
        if self.web_capture_off():
            return False
        address = address or self.ask_address()
        if not address:
            return False
        data = self._capture(address)
        if data is None:
            return False
        name = address.rstrip("/").split("/")[-1].split("?")[0] or "Web Page"
        name = "".join(c if c.isalnum() or c in " -_." else "_" for c in name)[:60]
        return self.w._save_new_pdf(data, Path(name).stem + ".pdf")

    def insert_from_web(self, address: str | None = None) -> bool:
        from planetpdf.ui.commands import InsertPdfPagesCommand

        if self.web_capture_off():
            return False
        address = address or self.ask_address()
        if not address:
            return False
        data = self._capture(address)
        if data is None:
            return False
        self.w._push(InsertPdfPagesCommand(self.w, data, self.w._insert_position(), "Web Page", None))
        return True

    # --- Check Spelling ---------------------------------------------------------------------------

    @property
    def speller(self):
        if self._speller is None:
            from planetpdf.core.spelling import Speller

            folder = self.w.profile.root / "spelling"
            self._speller = Speller(folder / "user_words.txt", user_lists=folder / "dictionaries",
                                    off=self.w.settings.spelling_off)
        return self._speller

    def check_spelling(self) -> SpellingDialog:
        dialog = SpellingDialog(self.w, self.speller)
        dialog.show()
        return dialog

    def edit_dictionary(self) -> None:
        DictionaryDialog(self.w, self.speller).exec()

    def correct_ocr(self):
        """Scan & OCR ▸ Recognize Text ▸ Correct Recognized Text (Acrobat)."""
        from planetpdf.core import ocr

        session = self.w.session
        if session is None:
            return None
        if not any(ocr.page_has_ocr(session.doc, i) for i in range(session.page_count)):
            if self.w.ask("Correct Recognized Text",
                          "This document has no recognized (OCR) text to correct yet.\n\nRecognize the text now?"):
                self.w.act_ocr.trigger()
            return None
        dialog = CorrectOcrDialog(self.w, self.speller)
        dialog.show()
        return dialog

    # --- Read Out Loud ----------------------------------------------------------------------------

    # One speaker for every window, so a voice model is loaded once (Kokoro is ~300 MB).
    _shared_speaker = None

    @property
    def speaker(self):
        """Kokoro or Piper (core/neural_speech.py: "auto" picks Kokoro only where it runs
        smoothly), or the Windows voices when chosen or when the voice files are missing."""
        if ExtrasUi._shared_speaker is None:
            ExtrasUi._shared_speaker = self._make_speaker()
        self._speaker = ExtrasUi._shared_speaker
        return self._speaker

    def _make_speaker(self):
        import platform

        from planetpdf.core import neural_speech as NS

        s = self.w.settings
        if s.read_aloud_engine != "windows" and (NS.available() or NS.mac_voices_available()):
            host = platform.node()

            def chosen(engine, speed) -> None:  # (from the loading thread)
                if s.read_aloud_engine == "auto" and speed is not None:
                    s.read_aloud_measured[host] = engine.name  # timed once per computer

            speaker = NS.Speaker(s.read_aloud_engine, voices=s.read_aloud_voices, rate=s.read_aloud_rate,
                                 known=s.read_aloud_measured.get(host, ""), on_engine=chosen)
            speaker.volume = s.read_aloud_volume
            return speaker
        from planetpdf.core.windows_speech import Speaker

        speaker = Speaker()
        speaker.rate = s.read_aloud_rate
        speaker.volume = s.read_aloud_volume
        if s.read_aloud_voices.get("windows"):
            speaker.set_voice(s.read_aloud_voices["windows"])
        return speaker

    # --- the toolbar's voice menu and volume slider (user 2026-10-02) -------------------------------

    def set_volume(self, volume: int) -> None:
        """The volume slider: at once while reading, remembered for next time."""
        self.w.settings.read_aloud_volume = max(0, min(100, int(volume)))
        if ExtrasUi._shared_speaker is not None:
            ExtrasUi._shared_speaker.volume = self.w.settings.read_aloud_volume

    def voice_choices(self) -> tuple[str, list[tuple[str, str]], str]:
        """(engine, [(voice code, name with male / female)], the current voice) for the
        engine reading now (or about to: "auto" not timed yet guesses Piper)."""
        import platform

        from planetpdf.core import neural_speech as NS

        s = self.w.settings
        speaker = self.speaker
        if isinstance(speaker, NS.Speaker):
            if speaker.engine is not None:
                engine = speaker.engine.name
            elif s.read_aloud_engine in ("kokoro", "piper", "mac"):
                engine = s.read_aloud_engine
            elif not NS.available() and NS.mac_voices_available():
                engine = "mac"
                speaker.prepare()
            else:
                engine = s.read_aloud_measured.get(platform.node()) or ("piper" if NS.piper_voices() else "kokoro")
                speaker.prepare()  # (loads, and times Kokoro, meanwhile)
            if engine == "kokoro":
                voices = list(NS.KOKORO_VOICE_NAMES.items())
                default = "af_heart"
            elif engine == "mac":
                from planetpdf.core import mac_speech

                voices = list(mac_speech.voices().items())
                default = voices[0][0] if voices else ""
            else:
                voices = [(v, NS.PIPER_VOICE_NAMES.get(v, v)) for v in NS.piper_voices()]
                default = "en_US-kristin-medium"
            current = s.read_aloud_voices.get(engine) or default
            return engine, voices, current
        voices = [(name, label) for name, label in speaker.voice_labels()]
        return "windows", voices, s.read_aloud_voices.get("windows") or (voices[0][0] if voices else "")

    def choose_voice(self, engine: str, code: str) -> None:
        """A voice picked on the toolbar: used from the next sentence, remembered."""
        from planetpdf.core import neural_speech as NS

        self.w.settings.read_aloud_voices[engine] = code
        speaker = ExtrasUi._shared_speaker
        if isinstance(speaker, NS.Speaker):
            speaker.voice_for[engine] = code
        elif speaker is not None:
            speaker.set_voice(code)
        self.w.save_settings_now()
        self._show_state()  # (the button shows what's really happening after the change)
        QTimer.singleShot(400, self._show_state)

    @classmethod
    def reset_speaker(cls) -> None:
        """New Read Out Loud preferences: the next reading starts with them."""
        if cls._shared_speaker is not None:
            cls._shared_speaker.stop()
        cls._shared_speaker = None

    # --- the natural voices, downloaded on first use (2026-10-14; user: "prefer and suggest kokoro") ----

    _suggested = False  # (asked once per run)

    def suggest_natural_voices(self) -> bool:
        """Read Out Loud without Kokoro: suggest downloading it, once per run. True when downloaded."""
        from planetpdf.core import neural_speech as NS
        from planetpdf.core import voice_download as V

        if NS.kokoro_available() or ExtrasUi._suggested or NS.USER_MODELS is None or V.turned_off():
            return False
        ExtrasUi._suggested = True
        if not self.ask_download():
            return False
        return self.download_natural_voices()

    def ask_download(self) -> bool:  # (tests replace this)
        box = QMessageBox(self.w)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Read Out Loud")
        box.setText("Read Out Loud sounds most natural with PlanetPDF's natural voices.")
        box.setInformativeText(f"They're a one-time download of about 354 MB. Download them now?\n\n"
                               f"Until then, PlanetPDF reads with the {fallback_voices()}.")
        download = box.addButton("Download", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(download)
        box.exec()
        return box.clickedButton() is download

    def download_natural_voices(self) -> bool:
        """Download Kokoro into the downloaded-voices folder (counted in MB, Cancel); then read with it."""
        from planetpdf import UPDATE_URL
        from planetpdf.core import neural_speech as NS
        from planetpdf.core import update as U
        from planetpdf.core import voice_download as V
        from planetpdf.ui.tasks import TaskCanceled, run_in_background

        w = self.w
        if V.turned_off():
            w.show_message("Natural Voices", f"{V.turned_off()} Read Out Loud uses the {fallback_voices()}.")
            return False
        url = U.configured_url(U.install_root(), UPDATE_URL, even_if_off=True)  # (its own switch, not the updates')
        if not url:
            w.show_message("Natural Voices", f"No update server is set, so the natural voices can't be downloaded. "
                                             f"Read Out Loud uses the {fallback_voices()}.")
            return False
        try:
            run_in_background(w, "Natural Voices", "Downloading the natural voices",
                              lambda progress: V.download(url, NS.USER_MODELS, progress))
        except (TaskCanceled, V.VoiceDownloadCanceled):
            return False
        except (U.UpdateError, PdfError, OSError) as exc:
            w.show_message("Natural Voices", f"{exc}\n\nRead Out Loud uses the {fallback_voices()} meanwhile.")
            return False
        ExtrasUi.reset_speaker()  # (the next reading uses Kokoro)
        w.statusBar().showMessage("The natural voices are ready: Read Out Loud uses them now.", 8000)
        return True

    def prepare_speaker(self) -> None:
        """Activate Read Out Loud: load the voice in the background meanwhile."""
        self.suggest_natural_voices()
        prepare = getattr(self.speaker, "prepare", None)
        if prepare is not None:
            prepare()
            self._read_timer.start()  # (shows "Please wait…" while it checks / loads)

    def _page_text(self, index: int) -> str:
        from planetpdf.core.export_formats import _paragraphs

        return "\n".join(p for p in _paragraphs(self.w.session.doc[index]) if p != "[Picture]")

    def read_page(self) -> None:
        """Read This Page Only."""
        if self.w.session is None:
            return
        self._reading_to_end = False
        self._stopped = False
        self._read_timer.stop()
        page = max(self.w.view.current_page, 0)
        self.speaker.speak(self._page_text(page) or "This page has no text.")
        self._show_state()
        self._read_timer.start()  # (watches for a problem with the voice)
        if not self._show_wait():
            self.w.statusBar().showMessage(f"Reading page {page + 1} aloud. Shift+Ctrl+E stops.", 5000)

    def read_to_end(self) -> None:
        """Read To End of Document: page after page, the view following."""
        if self.w.session is None:
            return
        self._reading_to_end = True
        self._stopped = False
        self._reading_page = max(self.w.view.current_page, 0)
        self.speaker.speak(self._page_text(self._reading_page) or " ")
        self._show_state()
        self._read_timer.start()
        if not self._show_wait():
            self.w.statusBar().showMessage("Reading aloud to the end. Shift+Ctrl+C pauses, Shift+Ctrl+E stops.", 6000)

    WAIT_MESSAGES = {
        "checking": "Checking this computer's capabilities for the best reading voice (only the first time). "
                    "Please wait…",
        "loading": "Loading the reading voice. Please wait…",
        "preparing": "Getting ready to read. Please wait…",
    }

    def _show_wait(self) -> bool:
        """Say what the voice is doing before any sound (user 2026-10-02: Play seemed to do
        nothing for several seconds). True while there is something to wait for."""
        status = getattr(self._speaker, "status", "") if self._speaker is not None else ""
        message = self.WAIT_MESSAGES.get(status, "")
        bar = self.w.statusBar()
        if message:
            if bar.currentMessage() != message:
                bar.showMessage(message)
            self._waiting = True
        elif getattr(self, "_waiting", False):
            bar.clearMessage()
            self._waiting = False
        return bool(message)

    def _read_tick(self) -> None:
        if self._show_wait() and not self.speaker.speaking:
            return  # (still checking / loading after Activate: keep watching)
        error = getattr(self.speaker, "error", "")
        if error:  # (the voice couldn't be loaded or played: say so, never silently nothing)
            self.speaker.error = ""
            self.stop_reading()
            self.w.show_message("Read Out Loud", f"The reading voice didn't work:\n\n{error}")
            return
        if not self._reading_to_end:
            if not self.speaker.speaking:
                self._read_timer.stop()
                self._show_state()  # (finished: back to Pause, ready for the next reading)
            return
        if self.speaker.paused or self.speaker.speaking:
            return
        nxt = self._reading_page + 1
        if self.w.session is None or nxt >= self.w.session.page_count:
            self._reading_to_end = False
            self._read_timer.stop()
            self._show_state()
            return
        self._reading_page = nxt
        self.w.view.go_to_page(nxt)
        self.speaker.speak(self._page_text(nxt) or " ")

    def pause_or_resume(self) -> None:
        """Play / Pause: pauses while reading, resumes when paused, and when nothing is
        being read (after Stop, or at the end) starts reading from this page."""
        if self.reading_state() == "stopped":
            self.read_to_end()
            return
        if self.speaker.paused:
            self.speaker.resume()
        else:
            self.speaker.pause()
        self._show_state()

    def stop_reading(self) -> None:
        self._stopped = True
        self._reading_to_end = False
        self._read_timer.stop()
        if self._speaker is not None:
            self.speaker.stop()
        self._show_state()

    def reading_state(self) -> str:
        """ "playing", "paused" or "stopped" (nothing being read). Stop counts at once: the
        Windows voice still says it's speaking for a moment after being stopped (the button
        stayed Pause, user bug 2026-10-04)."""
        speaker = self._speaker
        if speaker is None or self._stopped or not (speaker.speaking or self._reading_to_end):
            return "stopped"
        return "paused" if speaker.paused else "playing"

    def _show_state(self) -> None:
        """Pause / Play on the toolbar follows the reading (standard media buttons)."""
        from planetpdf.ui.toolbars import show_reading_state

        show_reading_state(self.w, self.reading_state())


class SpellingDialog(QDialog):
    """Edit ▸ Check Spelling: each misspelled word with suggestions. Change fixes it in a
    comment, a form field or the page text (each undoable). Page text is rewritten the way
    Edit PDF does it, after a warning that says whether the line keeps its font (user
    2026-10-02); invisible OCR text over a scan is shown but can't be changed."""

    def __init__(self, window: MainWindow, speller) -> None:
        super().__init__(window)
        self._window = window
        self._speller = speller
        self.setWindowTitle("Check Spelling")
        self.setMinimumSize(520, 460)
        self.include_pages = QCheckBox("Also check the page text", self)
        self.include_pages.setChecked(True)
        self.include_pages.toggled.connect(lambda _on: self.start())
        self.issues_list = QListWidget(self)
        self.issues_list.currentRowChanged.connect(self._show)
        self.context = QLabel(self)
        self.context.setWordWrap(True)
        self.suggestions = QListWidget(self)
        self.suggestions.itemDoubleClicked.connect(lambda _i: self.change())
        self.replacement = QLineEdit(self)
        self.suggestions.currentTextChanged.connect(self.replacement.setText)
        buttons = QHBoxLayout()
        self.buttons = {}
        for text, slot in (("Ignore", self.ignore), ("Ignore All", self.ignore_all),
                           ("Add to Dictionary", self.add_to_dictionary), ("Change", self.change), ("Go To", self.go_to)):
            b = QPushButton(text, self)
            b.clicked.connect(slot)
            buttons.addWidget(b)
            self.buttons[text] = b
        self.status = QLabel(self)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close.rejected.connect(self.reject)
        lists = close.addButton("Dictionaries…", QDialogButtonBox.ButtonRole.ActionRole)
        lists.setToolTip("Word lists (medical, legal, technical, names…) and your own words")
        lists.clicked.connect(self.edit_dictionaries)
        layout = QVBoxLayout(self)
        layout.addWidget(self.include_pages)
        layout.addWidget(QLabel("Misspelled:", self))
        layout.addWidget(self.issues_list, 1)
        layout.addWidget(self.context)
        layout.addWidget(QLabel("Suggestions:", self))
        layout.addWidget(self.suggestions)
        layout.addWidget(self.replacement)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        layout.addWidget(close)
        self.issues = []
        self.start()

    def start(self) -> None:
        from planetpdf.core.spelling import find_issues

        session = self._window.session
        self.issues = find_issues(session, self._speller, page_text=self.include_pages.isChecked()) if session else []
        self._fill()

    def edit_dictionaries(self) -> None:
        DictionaryDialog(self._window, self._speller).exec()
        self.start()  # (words in a newly switched-on list are fine now)

    def _fill(self) -> None:
        where = {"comment": "comment", "field": "form field", "page": "page text", "ocr": "OCR text"}
        self.issues_list.clear()
        for issue in self.issues:
            QListWidgetItem(f"{issue.word}  —  page {issue.page + 1}, {where[issue.where]}", self.issues_list)
        self.status.setText("No spelling mistakes found." if not self.issues
                            else f"{len(self.issues)} word{'s' if len(self.issues) != 1 else ''} to check.")
        if self.issues:
            self.issues_list.setCurrentRow(0)
        else:
            self._show(-1)

    def current(self):
        row = self.issues_list.currentRow()
        return self.issues[row] if 0 <= row < len(self.issues) else None

    def _show(self, _row: int) -> None:
        issue = self.current()
        self.suggestions.clear()
        self.replacement.clear()
        self.context.setText(issue.context if issue else "")
        for name in ("Ignore", "Ignore All", "Add to Dictionary", "Change", "Go To"):
            self.buttons[name].setEnabled(issue is not None)
        self.buttons["Change"].setToolTip("")
        if issue is None:
            return
        from planetpdf.core import ocr

        if issue.where == "ocr" and not ocr.page_has_ocr(self._window.session.doc, issue.page):
            self.buttons["Change"].setEnabled(False)
            self.buttons["Change"].setToolTip("This word is in invisible text over a scanned picture that other "
                                              "software recognized; PlanetPDF can only correct its own OCR text.")
        elif issue.where == "ocr":
            self.buttons["Change"].setToolTip("This word is in the invisible recognized (OCR) text over a scanned "
                                              "picture: Change corrects that text, so search and copy find the right "
                                              "word. The picture stays as it is.")
        self.suggestions.addItems(self._speller.suggestions(issue.word))
        if self.suggestions.count():
            self.suggestions.setCurrentRow(0)
        else:
            self.replacement.setText(issue.word)

    def _drop(self, matching=None) -> None:
        row = self.issues_list.currentRow()
        if matching is None:
            del self.issues[row]
        else:
            self.issues = [i for i in self.issues if i.word.lower() != matching.lower()]
            row = min(row, len(self.issues) - 1)
        self._fill()
        if self.issues:
            self.issues_list.setCurrentRow(max(0, min(row, len(self.issues) - 1)))

    def ignore(self) -> None:
        if self.current() is not None:
            self._drop()

    def ignore_all(self) -> None:
        issue = self.current()
        if issue is not None:
            self._speller.ignore_all(issue.word)
            self._drop(issue.word)

    def add_to_dictionary(self) -> None:
        issue = self.current()
        if issue is not None:
            self._speller.add_word(issue.word)
            self._drop(issue.word)

    def change(self) -> None:
        from planetpdf.core.spelling import replace_word

        issue = self.current()
        new = self.replacement.text().strip()
        if issue is None or not new or not issue.changeable:
            return
        text = replace_word(issue.text, issue, new)
        if issue.where == "comment":
            self._window.markup.set_text(issue.target, text)
        elif issue.where == "field":
            self._window.forms.set_value(issue.target, text)
        elif issue.where == "ocr":
            from planetpdf.core.ocr_correct import correct_at

            if not correct_recognized_text(self._window, issue.page,
                                           lambda s: correct_at(s, issue.page, issue.rect, issue.word, new)):
                return
        elif not self._change_page_text(issue, text):
            return
        # (the other words in the same text: offsets moved; check again)
        self.start()

    def _change_page_text(self, issue, text: str) -> bool:
        """Rewrite the line in the page's content, after saying how invasive that is."""
        from planetpdf.core import content_edit as E
        from planetpdf.ui.commands import PageContentCommand

        line = issue.line
        session = self._window.session
        other_font = E.font_change(session.doc, line, text)
        if other_font is None:
            look = ("It keeps its font, size, and color, but the words after the change may shift a little "
                    "(the spacing comes from the font, not from the original layout).")
        else:
            look = (f"The line's own font ({_font_label(line.font)}) doesn't have every letter of the correction "
                    f"(PDFs often hold only the letters they use), so the whole line will be drawn in {other_font} "
                    "instead, at the same size and color. Its look will change; you can restyle it with Edit PDF.")
        if not self._window.ask("Change Page Text",
                                f"This edits the PDF's own text: the line \"{_short(line.text)}\" on page {issue.page + 1} "
                                f"is taken out and written again with \"{issue.word}\" changed to "
                                f"\"{self.replacement.text().strip()}\".\n\n{look}\n\nYou can undo it (Ctrl+Z). Change it?"):
            return False
        try:
            command = PageContentCommand.applied(self._window, [issue.page], lambda s: E.replace_line(s, line, text),
                                                 "Correct Spelling")
        except PdfError as exc:
            self._window.show_message("Change Page Text", str(exc))
            return False
        except Exception as exc:  # (unexpected, e.g. an unusual file: nothing changed)
            self._window.report_error("Change Page Text", exc)
            return False
        self._window.push_command(command)
        return True

    def go_to(self) -> None:
        issue = self.current()
        if issue is None:
            return
        self._window.view.go_to_page(issue.page)
        if issue.rect is not None:
            from PySide6.QtCore import QRectF
            from PySide6.QtGui import QColor

            from planetpdf.core.redaction.marks import to_display

            shown = to_display(self._window.session.doc[issue.page], issue.rect)
            self._window.view.set_highlights("spelling", {issue.page: [QRectF(shown.x0, shown.y0, shown.width, shown.height)]},
                                             QColor(230, 40, 40))

    def done(self, result: int) -> None:
        self._window.view.clear_highlights("spelling")
        super().done(result)


class CorrectOcrDialog(QDialog):
    """Correct Recognized Text: each word the OCR may have misread ("suspects": not in the
    dictionary, or letters mixed with digits and odd marks), with the scan under it, so it
    can be compared and retyped. Correct rewrites it in the page's OCR text (one undo step
    each); Not a Mistake skips it; Ignore All keeps the word for this session."""

    def __init__(self, window: MainWindow, speller) -> None:
        from PySide6.QtWidgets import QComboBox

        super().__init__(window)
        self._window = window
        self._speller = speller
        self.setWindowTitle("Correct Recognized Text")
        self.setMinimumSize(480, 480)
        self.suspects_list = QListWidget(self)
        self.suspects_list.currentRowChanged.connect(self._show)
        self.picture = QLabel(self)
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.picture.setMinimumHeight(90)
        self.picture.setStyleSheet("background: white; border: 1px solid #c8c8c8;")
        self.replacement = QComboBox(self)
        self.replacement.setEditable(True)
        self.replacement.lineEdit().returnPressed.connect(self.correct)
        buttons = QHBoxLayout()
        self.buttons = {}
        for text, slot in (("Correct", self.correct), ("Not a Mistake", self.skip), ("Ignore All", self.ignore_all)):
            b = QPushButton(text, self)
            b.clicked.connect(lambda _checked=False, slot=slot: slot())
            buttons.addWidget(b)
            self.buttons[text] = b
        self.buttons["Correct"].setDefault(True)
        self.status = QLabel(self)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Words that may be misread:", self))
        layout.addWidget(self.suspects_list, 1)
        layout.addWidget(QLabel("On the page:", self))
        layout.addWidget(self.picture)
        layout.addWidget(QLabel("Recognized as (type the right word, or leave it empty to remove it):", self))
        layout.addWidget(self.replacement)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        layout.addWidget(close)
        self.suspects = []
        self._skipped: set[tuple[int, str]] = set()  # Not a Mistake (page, word)
        self._ignored: set[str] = set()  # Ignore All
        self.start()

    def start(self, row: int = 0) -> None:
        from planetpdf.core import ocr
        from planetpdf.core.ocr_correct import find_suspects

        session = self._window.session
        pages = [i for i in range(session.page_count) if ocr.page_has_ocr(session.doc, i)] if session else []
        found = find_suspects(session.doc, pages, self._speller) if session else []
        self.suspects = [s for s in found if (s.page, s.text) not in self._skipped and s.text.lower() not in self._ignored]
        self.suspects_list.clear()
        for s in self.suspects:
            QListWidgetItem(f"{s.text}  —  page {s.page + 1}", self.suspects_list)
        self.status.setText("No more words to check." if not self.suspects
                            else f"{len(self.suspects)} word{'s' if len(self.suspects) != 1 else ''} to check.")
        if self.suspects:
            self.suspects_list.setCurrentRow(max(0, min(row, len(self.suspects) - 1)))
        else:
            self._show(-1)

    def current(self):
        row = self.suspects_list.currentRow()
        return self.suspects[row] if 0 <= row < len(self.suspects) else None

    def _show(self, _row: int) -> None:
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QPixmap

        from planetpdf.core.ocr_correct import word_picture
        from planetpdf.core.redaction.marks import to_display

        suspect = self.current()
        self.replacement.clear()
        self.picture.clear()
        for b in self.buttons.values():
            b.setEnabled(suspect is not None)
        if suspect is None:
            self._window.view.clear_highlights("ocr-correct")
            return
        doc = self._window.session.doc
        picture = QPixmap()
        picture.loadFromData(word_picture(doc, suspect.page, suspect.rect))
        if picture.width() > 440:
            picture = picture.scaledToWidth(440, Qt.TransformationMode.SmoothTransformation)
        self.picture.setPixmap(picture)
        self.replacement.addItem(suspect.text)
        self.replacement.addItems([w for w in suspect.suggestions if w != suspect.text])
        self.replacement.setCurrentIndex(1 if self.replacement.count() > 1 else 0)
        self.replacement.lineEdit().selectAll()
        self.replacement.setFocus()
        self._window.view.go_to_page(suspect.page)
        shown = to_display(doc[suspect.page], suspect.rect)
        self._window.view.set_highlights("ocr-correct", {suspect.page: [QRectF(shown.x0, shown.y0, shown.width,
                                                                               shown.height)]}, QColor(230, 40, 40))

    def correct(self) -> None:
        from planetpdf.core.ocr_correct import correct_words

        suspect = self.current()
        if suspect is None:
            return
        new = self.replacement.currentText().strip()
        row = self.suspects_list.currentRow()
        if new == suspect.text:
            self.skip()
            return
        if correct_recognized_text(self._window, suspect.page,
                                   lambda s: correct_words(s, suspect.page, {suspect.index: new})):
            self.start(row)  # (word places on that page may have moved: look again)

    def skip(self) -> None:
        row = self.suspects_list.currentRow()
        if 0 <= row < len(self.suspects):
            self._skipped.add((self.suspects[row].page, self.suspects[row].text))
            del self.suspects[row]
            self.suspects_list.takeItem(row)
            self.status.setText(f"{len(self.suspects)} word{'s' if len(self.suspects) != 1 else ''} to check."
                                if self.suspects else "No more words to check.")
            if not self.suspects:
                self._show(-1)

    def ignore_all(self) -> None:
        suspect = self.current()
        if suspect is not None:
            self._speller.ignore_all(suspect.text.strip(".,;:!?()\"'“”’"))
            row = self.suspects_list.currentRow()
            same = suspect.text.lower()
            self._ignored.add(same)
            keep = [s for s in self.suspects if s.text.lower() != same]
            self.suspects = keep
            self.suspects_list.clear()
            for s in keep:
                QListWidgetItem(f"{s.text}  —  page {s.page + 1}", self.suspects_list)
            if keep:
                self.suspects_list.setCurrentRow(min(row, len(keep) - 1))
            else:
                self._show(-1)
                self.status.setText("No more words to check.")

    def done(self, result: int) -> None:
        self._window.view.clear_highlights("ocr-correct")
        super().done(result)


def correct_recognized_text(window: MainWindow, page: int, apply) -> bool:
    """Rewrite a page's OCR text (``apply(session)`` returns the words changed) as one
    "Correct Recognized Text" undo step."""
    from planetpdf.ui.commands import PageContentCommand

    changed = []
    try:
        command = PageContentCommand.applied(window, [page], lambda s: changed.append(apply(s)),
                                             "Correct Recognized Text")
    except Exception as exc:  # (nothing changed)
        window.report_error("Correct Recognized Text", exc)
        return False
    if not changed or not changed[0]:
        return False
    window.push_command(command)
    window.text_layer_changed({page})
    return True


def _font_label(name: str) -> str:
    """A PDF font name as people know it: no subset tag ("ABCDEF+Calibri-Bold")."""
    return name.split("+", 1)[1] if len(name) > 7 and name[6] == "+" else name


def _short(text: str, n: int = 60) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


class DictionaryDialog(QDialog):
    """Edit ▸ Check Spelling ▸ Edit Dictionary: the word lists (bundled: medical, legal,
    technical, names…; plus lists you import), each can be switched off, and the words
    you added yourself (all kept in your profile)."""

    def __init__(self, window: MainWindow, speller) -> None:
        super().__init__(window)
        self._window = window
        self._speller = speller
        self.setWindowTitle("Edit Dictionary")
        self.setMinimumSize(460, 560)
        self.lists = QListWidget(self)
        self.lists.itemChanged.connect(self._list_toggled)
        import_button = QPushButton("Import Word List…", self)
        import_button.setToolTip("A text file with one word per line (or a Hunspell .dic file)")
        import_button.clicked.connect(lambda: self.import_list())
        self.remove_list_button = QPushButton("Remove List", self)
        self.remove_list_button.clicked.connect(self.remove_list)
        self.lists.currentRowChanged.connect(lambda _r: self._update_buttons())
        list_buttons = QHBoxLayout()
        list_buttons.addWidget(import_button)
        list_buttons.addWidget(self.remove_list_button)
        list_buttons.addStretch(1)
        self.list = QListWidget(self)
        self.word = QLineEdit(self)
        self.word.setPlaceholderText("A word to add")
        add = QPushButton("Add", self)
        add.clicked.connect(self.add)
        remove = QPushButton("Remove", self)
        remove.clicked.connect(self.remove)
        row = QHBoxLayout()
        row.addWidget(self.word, 1)
        row.addWidget(add)
        row.addWidget(remove)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        intro = QLabel("Word lists: words in a checked list are never marked as misspelled. Names keep their "
                       "capitals (\"JavaScript\", \"iPhone\"): written another way they are marked.", self)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addWidget(self.lists, 1)
        layout.addLayout(list_buttons)
        layout.addWidget(QLabel("Your own words:", self))
        layout.addWidget(self.list, 1)
        layout.addLayout(row)
        layout.addWidget(close)
        self._fill_lists()
        self._fill()

    def _fill_lists(self) -> None:
        self.lists.blockSignals(True)
        self.lists.clear()
        for wl in self._speller.word_lists():
            item = QListWidgetItem(wl.name if wl.builtin else f"{wl.name} (imported)", self.lists)
            item.setData(Qt.ItemDataRole.UserRole, wl)
            item.setToolTip(wl.about)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked if wl.key in self._speller.off else Qt.CheckState.Checked)
        self.lists.blockSignals(False)
        self._update_buttons()

    def _update_buttons(self) -> None:
        item = self.lists.currentItem()
        self.remove_list_button.setEnabled(item is not None and not item.data(Qt.ItemDataRole.UserRole).builtin)

    def _list_toggled(self, _item) -> None:
        off = [self.lists.item(i).data(Qt.ItemDataRole.UserRole).key for i in range(self.lists.count())
               if self.lists.item(i).checkState() != Qt.CheckState.Checked]
        self._speller.set_off(off)
        self._window.settings.spelling_off = off
        self._window.save_settings_now()

    def import_list(self, path: Path | None = None, name: str | None = None) -> bool:
        from PySide6.QtWidgets import QFileDialog

        from planetpdf.core.spelling import import_word_list

        if path is None:
            chosen, _ = QFileDialog.getOpenFileName(self, "Import Word List", self._window.settings.last_directory or "",
                                                    "Word lists (*.txt *.dic);;All files (*)")
            if not chosen:
                return False
            path = Path(chosen)
        if name is None:
            name, ok = QInputDialog.getText(self, "Import Word List", "Name of the list:", text=path.stem.replace("_", " "))
            if not ok:
                return False
        try:
            import_word_list(path, self._speller.user_lists, name)
        except (OSError, ValueError) as exc:
            self._window.show_message("Import Word List", str(exc))
            return False
        self._speller.reload_lists()
        self._fill_lists()
        return True

    def remove_list(self) -> None:
        from planetpdf.core.spelling import remove_word_list

        item = self.lists.currentItem()
        if item is None:
            return
        wl = item.data(Qt.ItemDataRole.UserRole)
        if wl.builtin or not self._window.ask("Remove List", f"Remove the word list \"{wl.name}\"?"):
            return
        remove_word_list(wl)
        self._speller.reload_lists()
        self._fill_lists()

    def _fill(self) -> None:
        self.list.clear()
        self.list.addItems(self._speller.user_words)

    def add(self) -> None:
        word = self.word.text().strip()
        if word and " " not in word:
            self._speller.add_word(word)
            self.word.clear()
            self._fill()

    def remove(self) -> None:
        for item in self.list.selectedItems():
            self._speller.remove_word(item.text())
        self._fill()


def fallback_voices() -> str:
    """What reads when the natural voices aren't there."""
    return "macOS voices" if sys.platform == "darwin" else "Windows voices"


def read_out_loud_available() -> bool:
    from planetpdf.core import neural_speech

    if neural_speech.available() or neural_speech.mac_voices_available():  # (voice files: any system; the Mac's own)
        return True
    if sys.platform != "win32":
        return False
    try:
        from planetpdf.core import windows_speech  # noqa: F401
    except Exception:
        return False
    return True
