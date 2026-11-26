"""Plugins in the window (``core/plugins.py``): each plugin that is turned on (and unchanged since)
puts its items at the end of the menus it names; choosing one starts the plugin with its own token
for the local API."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from planetpdf.core import plugins

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow


def api_server():
    """The running local API (``ui/api_server.attach`` keeps it on the application)."""
    return getattr(QApplication.instance(), "planetpdf_api", None)


class PluginMenus:
    def __init__(self, window: MainWindow) -> None:
        self.w = window
        self._added: list[tuple[QMenu, QAction]] = []
        self._running: list = []

    def _top_menu(self, title: str) -> QMenu | None:
        for action in self.w.menuBar().actions():
            if action.menu() is not None and action.text().replace("&", "") == title:
                return action.menu()
        return None

    def install(self) -> None:
        """(Again after Preferences turns plugins on or off.)"""
        for menu, action in self._added:
            menu.removeAction(action)
        self._added.clear()
        enabled = self.w.settings.plugins_enabled
        for plugin in plugins.discover(self.w.profile.root):
            if not plugins.is_enabled(plugin, enabled):
                continue
            for item in plugin.menu_items:
                menu = self._top_menu(item.menu)
                if menu is None:
                    continue
                if not any(m is menu for m, _a in self._added):
                    self._added.append((menu, menu.addSeparator()))
                action = QAction(item.title, menu)
                action.setToolTip(f"{plugin.label} (a plugin{', ' + plugin.publisher if plugin.publisher else ''})")
                action.setObjectName(f"plugin:{plugin.id}:{item.command}")
                action.triggered.connect(lambda _checked=False, p=plugin, i=item: self.run(p, i))
                menu.addAction(action)
                self._added.append((menu, action))

    def actions(self) -> list[QAction]:
        return [a for _m, a in self._added if not a.isSeparator()]

    def run(self, plugin: plugins.Plugin, item: plugins.MenuItem) -> None:
        title = f"Plugin: {plugin.name}"
        if not plugins.is_enabled(plugin, self.w.settings.plugins_enabled):
            QMessageBox.warning(self.w, title, f"{plugin.label} changed since it was turned on, so it's off until "
                                               "you turn it on again in Preferences ▸ Scripting & Plugins.")
            self.install()
            return
        server = api_server()
        if server is None or not server.listening:
            QMessageBox.warning(self.w, title, "Plugins work through PlanetPDF's local API, which is turned off "
                                               "(Preferences ▸ Scripting & Plugins).")
            return
        session = self.w.session
        if item.needs_document and session is None:
            QMessageBox.information(self.w, title, f"Open a document first: “{item.title}” works on it.")
            return
        _client, token = server.service.clients.register(plugin.name, plugin.publisher, plugin=plugin.id,
                                                        permissions=plugin.permissions)
        try:
            process = plugins.start(plugin, item, address=server.address, token=token, key=server.key,
                                    log_folder=self.w.profile.root / "logs" / "plugins",
                                    document=session.path if session is not None else None,
                                    page=self.w.view.current_page + 1 if session is not None else None)
        except (OSError, plugins.PluginError) as e:
            QMessageBox.warning(self.w, title, f"{plugin.label} couldn't be started: {e}")
            return
        self.w.statusBar().showMessage(f"{plugin.name}: {item.title}…", 5000)
        self._watch(plugin, item, process)

    def _watch(self, plugin, item, process) -> None:
        timer = QTimer(self.w)
        self._running.append((timer, process))

        def check() -> None:
            code = process.poll()
            if code is None:
                return
            timer.stop()
            self._running[:] = [(t, p) for t, p in self._running if p is not process]
            if code:
                log = self.w.profile.root / "logs" / "plugins" / f"{plugin.id}.log"
                self.w.statusBar().showMessage(f"{plugin.name}: “{item.title}” stopped with code {code} "
                                               f"(its log: {log})", 15000)

        timer.timeout.connect(check)
        timer.start(500)


def log_path(window: MainWindow, plugin_id: str) -> Path:
    return window.profile.root / "logs" / "plugins" / f"{plugin_id}.log"
