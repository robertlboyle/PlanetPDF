"""Preferences ▸ Scripting & Plugins (2026-10-18): the local API, the programs that have used it
(block one, stop it signing, remove it), what each did (the audit log), and the plugins (turn one
on after seeing what it asks for)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core import api, commands, plugins

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow


def _when(text: str) -> str:
    try:
        return datetime.fromisoformat(text).astimezone().strftime("%b %d, %Y %I:%M %p")
    except ValueError:
        return text or ""


class ScriptingPage(QWidget):
    def __init__(self, window: MainWindow, parent=None) -> None:
        super().__init__(parent)
        self.w = window
        self.store = api.ClientStore(window.profile.root / "api")
        self.audit = api.AuditLog(window.profile.root / "logs" / "api-audit.log")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # the API itself
        box = QGroupBox("Command line and local API", self)
        column = QVBoxLayout(box)
        self.api_enabled = QCheckBox("Let other programs and plugins use PlanetPDF (its local API)", box)
        self.api_enabled.setChecked(window.settings.api_enabled)
        self.api_enabled.setToolTip("Off until you turn it on. Only programs on this computer, running as you, and "
                                    "each one is asked about first; signing always asks you. Takes effect the next "
                                    "time PlanetPDF starts (turning a plugin on starts it at once).")
        column.addWidget(self.api_enabled)
        note = QLabel("Scripts can also run <b>planetpdf help</b> in a command prompt. Programs may use every "
                      "function; <b>signing always asks you</b>.", box)
        note.setWordWrap(True)
        column.addWidget(note)
        layout.addWidget(box)

        # the programs that have used it
        box = QGroupBox("Programs that have used PlanetPDF", self)
        column = QVBoxLayout(box)
        self.programs = QTreeWidget(box)
        self.programs.setHeaderLabels(["Program", "Last used", "Signing"])
        self.programs.setRootIsDecorated(False)
        self.programs.setMinimumHeight(90)
        self.programs.itemSelectionChanged.connect(self._update_buttons)
        column.addWidget(self.programs)
        row = QHBoxLayout()
        self.block = QPushButton("Block", box)
        self.block.clicked.connect(self._toggle_block)
        self.stop_signing = QPushButton("Stop Signing", box)
        self.stop_signing.setToolTip("It will be asked again the next time it signs")
        self.stop_signing.clicked.connect(self._stop_signing)
        self.remove = QPushButton("Remove", box)
        self.remove.setToolTip("Forget it: its token stops working (it can introduce itself again)")
        self.remove.clicked.connect(self._remove)
        log = QPushButton("Activity Log…", box)
        log.clicked.connect(self.show_log)
        for button in (self.block, self.stop_signing, self.remove):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(log)
        column.addLayout(row)
        layout.addWidget(box)

        # plugins
        box = QGroupBox("Plugins", self)
        column = QVBoxLayout(box)
        self.plugin_list = QTreeWidget(box)
        self.plugin_list.setHeaderLabels(["Plugin", "Asks to"])
        self.plugin_list.setRootIsDecorated(False)
        self.plugin_list.setMinimumHeight(70)
        self.plugin_list.itemChanged.connect(self._plugin_ticked)
        column.addWidget(self.plugin_list)
        where = QLabel(f"Put plugins in {window.profile.root / 'plugins'}. A plugin that changes is turned off "
                       "until you turn it on again.", box)
        where.setWordWrap(True)
        where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(where)
        layout.addWidget(box)
        self.refresh()

    # --- programs ---
    def refresh(self) -> None:
        self.programs.clear()
        for client in self.store.load():
            kind = "plugin: " if client.plugin else ""
            state = "blocked" if client.blocked else ("allowed" if client.can_sign else "asks first")
            item = QTreeWidgetItem([f"{kind}{client.label}", _when(client.last_used), state])
            item.setData(0, Qt.ItemDataRole.UserRole, client.id)
            if client.program:  # (the program file allowed: security review 2026-11-21)
                item.setToolTip(0, client.program)
            self.programs.addTopLevelItem(item)
        self.programs.resizeColumnToContents(0)
        self._update_buttons()
        self._fill_plugins()

    def _selected(self) -> api.Client | None:
        item = self.programs.currentItem()
        if item is None:
            return None
        wanted = item.data(0, Qt.ItemDataRole.UserRole)
        return next((c for c in self.store.load() if c.id == wanted), None)

    def _update_buttons(self) -> None:
        client = self._selected()
        for button in (self.block, self.stop_signing, self.remove):
            button.setEnabled(client is not None)
        self.block.setText("Unblock" if client is not None and client.blocked else "Block")
        self.stop_signing.setEnabled(client is not None and client.can_sign)

    def _toggle_block(self) -> None:
        client = self._selected()
        if client is not None:
            self.store.change(client.id, blocked=not client.blocked)
            self.refresh()

    def _stop_signing(self) -> None:
        client = self._selected()
        if client is not None:
            self.store.change(client.id, can_sign=False)
            self.refresh()

    def _remove(self) -> None:
        client = self._selected()
        if client is not None:
            self.store.remove(client.id)
            self.refresh()

    def show_log(self) -> QDialog:
        dialog = QDialog(self)
        dialog.setWindowTitle("Activity Log: Programs Using PlanetPDF")
        text = QPlainTextEdit(dialog)
        text.setReadOnly(True)
        lines = []
        for e in reversed(self.audit.tail(500)):
            files = ", ".join(e.get("files") or [])
            lines.append(f"{_when(e.get('time', ''))}  {e.get('program', '')}  {e.get('method', '')}"
                         f"{'  ' + files if files else ''}  → {e.get('outcome', '')}")
        text.setPlainText("\n".join(lines) or "Nothing yet.")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(text)
        layout.addWidget(buttons)
        dialog.resize(720, 400)
        dialog.open()
        return dialog

    # --- plugins ---
    def _fill_plugins(self) -> None:
        self.plugin_list.blockSignals(True)
        self.plugin_list.clear()
        enabled = self.w.settings.plugins_enabled
        for plugin in plugins.discover(self.w.profile.root):
            asks = ", ".join(commands.PERMISSIONS[p].split(":")[0].lower() for p in plugin.permissions) or "nothing"
            item = QTreeWidgetItem([plugin.label + (f" ({plugin.publisher})" if plugin.publisher else ""),
                                    plugin.problem or asks])
            item.setData(0, Qt.ItemDataRole.UserRole, plugin.id)
            item.setToolTip(0, f"{plugin.description}\n{plugin.folder}".strip())
            if plugin.problem or self.plugins_off():
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                if not plugin.problem:
                    item.setText(1, "turned off by your administrator")
                    item.setToolTip(0, self.plugins_off())
            else:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                on = plugins.is_enabled(plugin, enabled)
                item.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
                if plugin.id in enabled and not on:
                    item.setText(1, "changed since you turned it on: turn it on again to use it")
            self.plugin_list.addTopLevelItem(item)
        self.plugin_list.resizeColumnToContents(0)
        self.plugin_list.blockSignals(False)

    def plugins_off(self) -> str:
        """Why no plugin can be turned on here ("" = they can): an administrator turned plugins,
        or the local API they work through, off for everyone on this computer (core/policy.py)."""
        if plugins.turned_off():
            return plugins.turned_off()
        s = self.w.settings
        if s.locked("api_enabled") and not s.api_enabled:
            return "Plugins work through the local API, which is turned off on this computer by your administrator."
        return ""

    def _plugin_ticked(self, item: QTreeWidgetItem, _column: int) -> None:
        pid = item.data(0, Qt.ItemDataRole.UserRole)
        plugin = next((p for p in plugins.discover(self.w.profile.root) if p.id == pid), None)
        if plugin is None:
            return
        enabled = dict(self.w.settings.plugins_enabled)
        if item.checkState(0) == Qt.CheckState.Checked and self.plugins_off():
            QMessageBox.information(self, "Plugins", self.plugins_off())
            self._fill_plugins()
            return
        if item.checkState(0) == Qt.CheckState.Checked:
            if not self.confirm_plugin(plugin):
                self._fill_plugins()
                return
            enabled[plugin.id] = plugin.fingerprint()
            if not self.api_enabled.isChecked():  # (plugins work through the local API, off by default)
                from planetpdf.ui import api_server

                self.api_enabled.setChecked(True)
                self.w.settings.api_enabled = True
                api_server.attach(self.w, self.w.profile.root)
        else:
            enabled.pop(plugin.id, None)
        self.w.settings.plugins_enabled = enabled
        self.w.save_settings_now()
        from planetpdf.ui.main_window import MainWindow

        for window in MainWindow.open_windows():
            window.plugin_menus.install()
        self._fill_plugins()

    def confirm_plugin(self, plugin: plugins.Plugin) -> bool:
        asks = "\n".join(f"• {commands.PERMISSIONS[p]}" for p in plugin.permissions) or "• Nothing through PlanetPDF"
        items = "\n".join(f"• {i.menu} ▸ {i.title}" for i in plugin.menu_items) or "• (no menu items)"
        sign = "\n\nIt will still ask you before it signs anything." if commands.SIGN in plugin.permissions else ""
        answer = QMessageBox.question(
            self, "Turn On Plugin?",
            f"{plugin.label}{' by ' + plugin.publisher if plugin.publisher else ''}\n{plugin.folder}\n\n"
            f"It asks to:\n{asks}\n\nIt adds:\n{items}\n\nIt runs as a program of its own, with your rights, "
            f"like any program you start. Turn it on only if you trust where it came from.{sign}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def save(self) -> None:
        self.w.settings.api_enabled = self.api_enabled.isChecked()
