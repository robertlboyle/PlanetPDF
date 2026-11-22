"""Office opened for converting never runs a file's macros (security review 2026-11-21)."""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Office through COM: Windows only")


class FakeOffice:
    def __init__(self, open_count=0, refuses=False):
        self.Documents = type("D", (), {"Count": open_count})()
        self.DisplayAlerts = -1
        self._security, self.refuses, self.history, self.quit = 1, refuses, [], False

    @property
    def AutomationSecurity(self):
        return self._security

    @AutomationSecurity.setter
    def AutomationSecurity(self, value):
        self.history.append(value)
        if not self.refuses:
            self._security = value

    def Quit(self):
        self.quit = True


@pytest.fixture
def office(monkeypatch):
    import comtypes.client

    from planetpdf.core import windows_office as W

    made = {}
    monkeypatch.setattr(W, "available", lambda app: True)
    monkeypatch.setattr(comtypes.client, "CreateObject", lambda prog_id, dynamic: made["office"])
    return W, made


def test_macros_are_forced_off_while_converting(office):
    W, made = office
    made["office"] = FakeOffice()
    with W._office("word") as word:
        assert word.AutomationSecurity == W.MSO_AUTOMATION_SECURITY_FORCE_DISABLE
    assert made["office"].quit


def test_the_users_own_office_gets_its_setting_back(office):
    W, made = office
    made["office"] = FakeOffice(open_count=2)
    with W._office("word"):
        pass
    assert made["office"].history == [3, 1] and not made["office"].quit


def test_an_office_that_wont_turn_macros_off_opens_nothing(office):
    W, made = office
    made["office"] = FakeOffice(refuses=True)
    with pytest.raises(W.PdfError, match="macros"):
        with W._office("word"):
            pytest.fail("the file must not be opened")
