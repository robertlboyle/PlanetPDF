"""Who can change PlanetPDF's install folder (security list 2026-11-26, item 3): the same rule as
the print service's (printer/src/acl.rs), on security descriptors as text, so it runs anywhere."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from planetpdf.core import update_helper as H
from planetpdf.core import windows_acl as A

PROGRAM_FILES = "O:BAD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)(A;OICI;0x1200a9;;;AC)"
TI = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"


def test_a_folder_as_program_files_keeps_it_is_closed():
    assert A.others_who_can_change(PROGRAM_FILES) == []
    windows = (f"O:{TI}D:PAI(A;;0x1301bf;;;{TI})(A;CIIO;GA;;;{TI})(A;;0x1301bf;;;SY)(A;OICIIO;GA;;;SY)"
               "(A;;0x1301bf;;;BA)(A;OICIIO;GA;;;BA)(A;;0x1200a9;;;BU)(A;OICIIO;GXGR;;;BU)(A;OICIIO;GA;;;CO)"
               "(A;;0x1200a9;;;AC)")
    assert A.others_who_can_change(windows) == []
    assert A.others_who_can_change("O:SYD:P(A;OICI;FA;;;SY)(A;OICI;FRFX;;;WD)") == []
    assert A.others_who_can_change("O:S-1-5-21-1-2-3-512D:(A;;FA;;;S-1-5-21-1-2-3-500)") == []
    assert A.others_who_can_change("O:BAG:BAD:(A;;FA;;;BA)S:(AU;SAFA;FA;;;WD)") == []  # (group and audit parts)


def test_a_folder_users_can_write_is_open():
    data_drive = "O:BAD:AI(A;OICIID;FA;;;BA)(A;OICIID;FA;;;SY)(A;OICIID;0x1301bf;;;AU)(A;OICIID;0x1200a9;;;BU)"
    assert A.others_who_can_change(data_drive) == ["AU"]
    assert A.others_who_can_change("O:BAD:(A;;FA;;;BA)(A;;FW;;;BU)(A;;GA;;;WD)(A;;FW;;;BU)") == ["BU", "WD"]
    assert A.others_who_can_change("O:BAD:(A;;FA;;;BA)(A;;SD;;;BU)") == ["BU"]  # (delete alone)
    assert A.others_who_can_change("O:BAD:(A;;FA;;;BA)(A;;WD;;;S-1-5-21-1-2-3-1001)") == ["S-1-5-21-1-2-3-1001"]
    assert A.others_who_can_change("O:BAD:NO_ACCESS_CONTROL") == ["everyone (no permissions are set)"]


def test_an_owner_who_is_not_an_administrator_counts():
    assert A.others_who_can_change("O:S-1-5-21-1-2-3-1001D:PAI(A;OICI;FA;;;SY)") == ["owner S-1-5-21-1-2-3-1001"]
    assert A.others_who_can_change("O:BUG:BUD:(A;;FA;;;BA)") == ["owner BU"]
    assert A.in_words("owner BU") == "its owner, Users (BU)" and A.in_words("AU") == "Authenticated Users (AU)"


def test_refusals_and_entries_for_later_give_nothing():
    assert A.others_who_can_change("O:BAD:(D;;FA;;;BU)(A;OICIIO;FA;;;BU)(A;;FA;;;CO)(A;;FR;;;BU)") == []


@pytest.mark.parametrize("sddl", ["", "D:(A;;FA;;;BA)", "O:BA", "O:BAD:(A;;??;;;BU)", "O:BAD:(A;;FA)", "O:D:(A;;FA;;;BA)"])
def test_what_cannot_be_read_is_not_taken_for_closed(sddl):
    with pytest.raises(ValueError):
        A.others_who_can_change(sddl)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows permissions")
def test_real_files_and_folders_are_read(tmp_path):
    from planetpdf.core import windows_elevate as E

    system = Path(E.system_dir())
    assert A.open_to(system) == [] and A.open_to(system / "kernel32.dll") == []
    assert A.closed_to_others(system) and not A.closed_to_others(tmp_path)  # (the user's own: theirs to change)
    assert not A.closed_to_others(system / "no-such-file-here.dll")
    with pytest.raises(OSError):
        A.open_to(system / "no-such-file-here.dll")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows permissions")
def test_closing_a_folder_leaves_only_administrators_able_to_change_it(tmp_path):
    """The installer's and the updater's icacls steps, on a folder of the test's own. Its owner
    stays the test's user (making Administrators the owner needs an administrator), so only the
    permissions are judged."""
    folder = tmp_path / "PlanetPDF"
    (folder / "printer").mkdir(parents=True)
    (folder / "PlanetPDF.exe").write_bytes(b"x")
    subprocess.run(["icacls", str(folder), "/grant", "*S-1-5-11:(OI)(CI)M", "/Q"], check=True, capture_output=True)
    assert "AU" in A.open_to(folder) and "AU" in A.open_to(folder / "PlanetPDF.exe")  # (every user may modify)
    set_owner, reset, grant = A.protect_commands(folder)
    assert Path(set_owner[0]).name == "icacls.exe" and Path(set_owner[0]).is_absolute() and "/setowner" in set_owner
    try:
        for command in (reset, grant):
            subprocess.run(command, check=True, capture_output=True)
        for path in (folder, folder / "printer", folder / "PlanetPDF.exe"):
            assert [who for who in A.open_to(path) if not who.startswith("owner ")] == []
        assert "D:PAI" in A.sddl_of(folder)  # (nothing inherited from the folder above)
    finally:  # (the test's user is the owner: give the folder back so it can be removed)
        subprocess.run(["icacls", str(folder), "/reset", "/T", "/Q"], capture_output=True)


def test_the_updates_administrator_step_closes_an_open_install_folder(tmp_path, monkeypatch):
    from planetpdf.core import windows_elevate as E

    root = tmp_path / "PlanetPDF"
    root.mkdir()
    closed, asked = [False], []
    monkeypatch.setattr(H.sys, "platform", "win32")
    monkeypatch.setattr(A, "closed_to_others", lambda path: closed[0])
    monkeypatch.setattr(A, "protect_folder", lambda path: asked.append(path) or True)
    monkeypatch.setattr(E, "is_admin", lambda: False)
    assert H.close_install_folder(root) == "" and asked == []  # (not an administrator: nothing is tried)
    monkeypatch.setattr(E, "is_admin", lambda: True)
    said = H.close_install_folder(root, tmp_path)
    assert asked == [root] and "now closed to everyone but administrators" in said
    assert said in (tmp_path / H.LOG_FILE).read_text(encoding="utf-8")
    monkeypatch.setattr(A, "protect_folder", lambda path: False)  # (a drive without permissions)
    assert H.close_install_folder(root).startswith("WARNING:")
    closed[0] = True
    assert H.close_install_folder(root) == ""  # (already closed: left alone)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows permissions")
def test_a_tree_is_open_if_anything_in_it_is(tmp_path):
    """Security review 2026-11-27, finding 3: the whole install folder counts, every file and
    folder in it, and a junction anywhere in it is never PlanetPDF's."""
    closed = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc"
    assert A.open_in_tree(closed) == []
    (tmp_path / "versions" / "1.0").mkdir(parents=True)
    (tmp_path / "versions" / "1.0" / "x.dll").write_bytes(b"x")
    found = A.open_in_tree(tmp_path, most=50)
    assert any("x.dll" in line and "its owner" in line for line in found)  # (the user's own file)
    link = tmp_path / "jtop"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(closed)], check=True, capture_output=True)
    assert any(line.startswith(str(link)) and "a link to another place" in line
               for line in A.open_in_tree(tmp_path, most=50))
