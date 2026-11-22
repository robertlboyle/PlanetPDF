"""Help ▸ Report a Problem: a mailto link with the version filled in; nothing is sent."""

from PySide6.QtCore import QUrlQuery
from PySide6.QtWidgets import QApplication

from planetpdf import APP_NAME, FEEDBACK_EMAIL, __version__
from planetpdf.ui.main_window import report_problem_url


def test_report_problem_url():
    url = report_problem_url()
    assert url.scheme() == "mailto"
    assert url.path() == FEEDBACK_EMAIL
    query = QUrlQuery(url)
    assert query.queryItemValue("subject") == f"{APP_NAME} {__version__}: "
    body = query.queryItemValue("body")
    assert "What happened:" in body and f"{APP_NAME} {__version__}" in body


def test_report_problem_opens_the_mail_program(tmp_path, monkeypatch):
    from planetpdf.ui import main_window as mw

    QApplication.instance() or QApplication([])
    opened = []
    monkeypatch.setattr(mw.QDesktopServices, "openUrl", lambda url: opened.append(url) or True)
    w = mw.MainWindow(tmp_path / "profile")
    try:
        help_menu = next(a.menu() for a in w.menuBar().actions() if a.text() == "&Help")
        assert w.act_report_problem in help_menu.actions()
        w.act_report_problem.trigger()
        assert opened and opened[0].scheme() == "mailto"
    finally:
        w.close()
