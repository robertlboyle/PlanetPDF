"""PDF from Scanner on the Mac and Linux (2026-10-14): choose a scanner and how to scan.

Mac: every scanner Apple's ImageCaptureCore sees, USB, network (AirScan) and shared
(``core/mac_scanner.py``). Linux: network scanners found with Bonjour (AirScan / eSCL,
``core/escl_scanner.py``). On both, a scanner can be added by its address (eSCL). Choosing one
asks it what it can do, so only its sources, color modes and resolutions are offered. The
scanner and choices used last are remembered, and the last scanner stays listed when it didn't
answer this time (Bonjour answers come and go on a busy network).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

from planetpdf.core import escl_scanner as E
from planetpdf.core import mac_scanner as M
from planetpdf.core.document import PdfError

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

DPIS = (150, 200, 300, 600)
NO_SCANNERS = ("No scanners were found on the network. Check that the scanner is on and on the same "
               "network, or add it by its address.")
MAC_PERMISSION = ("\n\nOn a Mac, PlanetPDF also needs permission to use the local network: System Settings ▸ "
                  "Privacy & Security ▸ Local Network.")


def remember_certificates(window: MainWindow) -> None:
    """Where scanners' certificates are remembered, and who's asked the first time (security
    review 2026-11-25): the user, only from the window's own thread (a scan running in the
    background never asks: the scanner window, which reads its abilities first, already did)."""
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication, QMessageBox

    E.PINS_FILE = window.profile.root / "scanner-certificates.json"

    def ask(address: str, fingerprint: str) -> bool:
        if QThread.currentThread() is not QApplication.instance().thread():
            return False
        answer = QMessageBox.question(
            window, "Scanner Certificate",
            f"PlanetPDF hasn't used the scanner at {address} before. It identifies itself with a certificate "
            f"of its own, which nothing can vouch for:\n\n{fingerprint}\n\nUse this scanner? PlanetPDF then "
            "remembers the certificate and refuses a different one at this address later.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
        return answer == QMessageBox.StandardButton.Yes

    E.first_use = ask


def find_scanners() -> list:
    """The scanners on this computer's network (and, on a Mac, its USB ones)."""
    if sys.platform == "darwin":
        return M.list_scanners()
    return E.discover()


def capabilities(scanner):
    return M.capabilities(scanner) if isinstance(scanner, M.MacScanner) else E.capabilities(scanner)


def scan(scanner, folder, **options) -> list:
    return M.scan(scanner, folder, **options) if isinstance(scanner, M.MacScanner) else E.scan(scanner, folder, **options)


@dataclass
class ScanChoice:
    scanner: E.Scanner
    caps: E.Capabilities
    source: str
    color: str
    dpi: int


class ScanDialog(QDialog):
    def __init__(self, window: MainWindow, scanners: list[E.Scanner], parent: QWidget | None = None) -> None:
        super().__init__(parent or window)
        self._window = window
        self.setWindowTitle("PDF from Scanner")
        s = window.settings
        self.caps: E.Capabilities | None = None
        self.scanner = QComboBox(self)
        self.scanner.setAccessibleName("Scanner")
        self.scanner.setMinimumWidth(320)
        find = QPushButton("Find Again", self)
        find.clicked.connect(self.find_again)
        add = QPushButton("Add by Address…", self)
        add.clicked.connect(self.add_by_address)
        forget = QPushButton("Forget Certificates", self)
        forget.setToolTip("Forget the scanners' certificates PlanetPDF remembered: each scanner is asked about "
                          "again the next time (after a scanner was reset or replaced).")
        forget.clicked.connect(self.forget_certificates)
        remember_certificates(window)
        row = QHBoxLayout()
        row.addWidget(self.scanner, 1)
        row.addWidget(find)
        row.addWidget(add)
        row.addWidget(forget)
        self.source = QComboBox(self)
        self.color = QComboBox(self)
        self.dpi = QComboBox(self)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        form = QFormLayout()
        form.addRow("Scanner:", row)
        form.addRow("Scan from:", self.source)
        form.addRow("Colors:", self.color)
        form.addRow("Resolution:", self.dpi)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.scan_button = self.buttons.addButton("Scan", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.status)
        layout.addWidget(self.buttons)
        self.scanner.currentIndexChanged.connect(self._scanner_chosen)
        self.source.currentIndexChanged.connect(self._source_chosen)
        self._fill_scanners(scanners, prefer=s.scan_device)

    # --- scanners ------------------------------------------------------------------------------

    def _fill_scanners(self, found: list[E.Scanner], prefer: str = "") -> None:
        s = self._window.settings
        known = {sc.url: sc for sc in found}
        if s.scan_device.startswith("ica:") and s.scan_device not in known:  # (didn't answer just now)
            known[s.scan_device] = M.MacScanner(s.scan_device_name or "Scanner", s.scan_device[4:])
        for address in s.scan_addresses:
            manual = E.scanner_at(address)
            known.setdefault(manual.url, manual)
        if s.scan_device and s.scan_device not in known and s.scan_device in s.scan_addresses:
            known[s.scan_device] = E.scanner_at(s.scan_device)
        self.scanner.blockSignals(True)
        self.scanner.clear()
        for sc in known.values():
            self.scanner.addItem(sc.name, sc)
        self.scanner.blockSignals(False)
        index = next((i for i in range(self.scanner.count()) if self.scanner.itemData(i).url == prefer), 0)
        if self.scanner.count():
            self.scanner.setCurrentIndex(index)
            self._scanner_chosen()
        else:
            self._show_caps(None, NO_SCANNERS + (MAC_PERMISSION if sys.platform == "darwin" else ""))

    def find_again(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            found = find_scanners()
        except (OSError, PdfError) as exc:
            found = []
            self.status.setText(f"Couldn't look for scanners ({exc}).")
        finally:
            QApplication.restoreOverrideCursor()
        current = self.scanner.currentData()
        self._fill_scanners(found, prefer=current.url if current else "")

    def forget_certificates(self) -> None:
        E.forget_certificates()
        self.status.setText("The scanners' certificates were forgotten: each is asked about again.")

    def add_by_address(self) -> None:
        text, ok = QInputDialog.getText(self, "Add Scanner", "The scanner's address (for example 192.168.1.40):")
        if not ok or not text.strip():
            return
        scanner = E.scanner_at(text)
        addresses = self._window.settings.scan_addresses
        if scanner.url not in addresses:
            addresses.append(scanner.url)
        self.scanner.addItem(scanner.name, scanner)
        self.scanner.setCurrentIndex(self.scanner.count() - 1)

    def _scanner_chosen(self) -> None:
        scanner = self.scanner.currentData()
        if scanner is None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._show_caps(capabilities(scanner))
        except PdfError as exc:
            self._show_caps(None, str(exc) + (MAC_PERMISSION if sys.platform == "darwin" else ""))
        finally:
            QApplication.restoreOverrideCursor()

    def _show_caps(self, caps: E.Capabilities | None, problem: str = "") -> None:
        s = self._window.settings
        self.caps = caps
        self.status.setText(problem)
        for box in (self.source, self.color, self.dpi):
            box.clear()
        if caps is not None:
            for key in ("glass", "feeder", "feeder-duplex"):
                if key in caps.sources:
                    self.source.addItem(E.SOURCE_NAMES[key], key)
            self._select(self.source, s.scan_source)
            self._source_chosen()
        self.scan_button.setEnabled(caps is not None and bool(caps.sources))

    def _source_chosen(self) -> None:
        if self.caps is None or self.source.currentData() is None:
            return
        s = self._window.settings
        source = self.caps.sources.get(self.source.currentData(), {}) if self.caps else {}
        self.color.clear()
        for key in source.get("colors") or list(E.COLORS):
            self.color.addItem(E.COLOR_NAMES[key], key)
        self._select(self.color, s.scan_color)
        self.dpi.clear()
        offered = [d for d in DPIS if not source.get("dpis") or d in source["dpis"]] or source.get("dpis", [300])
        for dpi in offered:
            self.dpi.addItem(f"{dpi} dpi", dpi)
        self._select(self.dpi, s.scan_dpi if s.scan_dpi in offered else 300)

    @staticmethod
    def _select(box: QComboBox, data) -> None:
        index = box.findData(data)
        box.setCurrentIndex(index if index >= 0 else 0)

    def choice(self) -> ScanChoice | None:
        scanner = self.scanner.currentData()
        if scanner is None or self.caps is None or self.source.currentData() is None:
            return None
        choice = ScanChoice(scanner, self.caps, self.source.currentData(), self.color.currentData() or "color",
                            self.dpi.currentData() or 300)
        s = self._window.settings
        s.scan_device, s.scan_device_name = scanner.url, scanner.name
        s.scan_source, s.scan_color, s.scan_dpi = choice.source, choice.color, choice.dpi
        return choice
