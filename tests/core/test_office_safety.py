"""Office files are converted from a copy that can't reach the network (security review
2026-11-25: a .docx naming a template on a server made Word fetch it when PlanetPDF converted it)."""

from __future__ import annotations

import sys
import zipfile

import pytest

from planetpdf.core import office_safety as S

RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'attachedTemplate" Target="file:///\\\\SERVER\\share\\r2-tpl.dotx" TargetMode="External"/>'
        "<Relationship Id='rId2' TargetMode='External' Target='http://example.com/pic.png' "
        "Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/image'/>"
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'hyperlink" Target="https://www.planet.net/" TargetMode="External"/>'
        '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'styles" Target="styles.xml"/></Relationships>')


def test_outside_links_are_pointed_nowhere_but_hyperlinks_stay():
    data, count = S.unlinked_rels(RELS)
    text = data.decode("utf-8")
    assert count == 2
    assert "SERVER" not in text and "example.com/pic.png" not in text
    assert text.count(S.REMOVED_TARGET) == 2
    assert 'Target="https://www.planet.net/"' in text and 'Target="styles.xml"' in text
    assert 'TargetMode="External"' in text  # (the rest of each tag as it was)


def _docx(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
        z.writestr("word/_rels/settings.xml.rels", RELS)
    return path


def test_the_copy_office_gets_has_no_outside_links(tmp_path):
    source = _docx(tmp_path / "letter.docx")
    folder = tmp_path / "private"
    folder.mkdir()
    copy = S.safe_copy(source, folder)
    assert copy == folder / "letter.docx"
    with zipfile.ZipFile(copy) as z:
        assert sorted(z.namelist()) == sorted(zipfile.ZipFile(source).namelist())
        rels = z.read("word/_rels/settings.xml.rels").decode("utf-8")
    assert "SERVER" not in rels and "planet.net" in rels
    assert "SERVER" in zipfile.ZipFile(source).read("word/_rels/settings.xml.rels").decode("utf-8")  # (untouched)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' mark of the web")
def test_a_downloaded_older_format_file_is_refused(tmp_path):
    old = tmp_path / "report.doc"
    old.write_bytes(b"\xd0\xcf\x11\xe0 not really a Word file")
    folder = tmp_path / "private"
    folder.mkdir()
    with pytest.raises(S.OfficeRefused):  # (the user's own file goes through Office's converter, which can't read this)
        S.safe_copy(old, folder)
    with open(f"{old}:Zone.Identifier", "w", encoding="utf-8") as mark:
        mark.write("[ZoneTransfer]\nZoneId=3\n")
    assert S.downloaded(old)
    with pytest.raises(S.OfficeRefused, match="Protected View"):
        S.safe_copy(old, folder)
    docx = _docx(tmp_path / "report.docx")
    with open(f"{docx}:Zone.Identifier", "w", encoding="utf-8") as mark:
        mark.write("[ZoneTransfer]\nZoneId=3\n")
    assert S.safe_copy(docx, folder).exists()  # (the newer format: cleaned, then converted)
