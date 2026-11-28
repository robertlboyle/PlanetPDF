"""Office copies are cleaned by reading their parts, not by patterns (security review 2026-11-27,
finding 5: Word reached a server through a .rels part written with spaces around the =, with an
entity in the value, or in UTF-16, and through an RTF's template)."""

from __future__ import annotations

import sys

import pytest

from planetpdf.core import office_safety as S

TEMPLATE = (r'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            r'attachedTemplate" Target="file:///\\SERVER\share\t.dotx" {mode}/>')
WRAP = '<?xml version="1.0" encoding="{enc}" standalone="yes"?>\n<Relationships xmlns="{ns}">{rel}</Relationships>'


@pytest.mark.parametrize("mode", [
    'TargetMode = "External"',  # (spaces around the =)
    'TargetMode="Extern&#97;l"',  # (an entity in the value)
    'TargetMode="external"',
    "",  # (no TargetMode at all, but a target outside the file)
])
def test_every_spelling_of_an_outside_link_is_found(mode):
    part = WRAP.format(enc="UTF-8", ns=S.RELATIONSHIPS, rel=TEMPLATE.format(mode=mode))
    data, count = S.unlinked_rels(part.encode("utf-8"))
    assert count == 1 and b"SERVER" not in data


def test_a_relationship_part_in_utf_16_is_read_too():
    part = WRAP.format(enc="UTF-16", ns=S.RELATIONSHIPS, rel=TEMPLATE.format(mode='TargetMode="External"'))
    data, count = S.unlinked_rels(part.encode("utf-16"))
    assert count == 1 and "SERVER" not in data.decode("utf-8")


def test_inside_targets_and_hyperlinks_stay():
    rel = ('<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
           'Target="media/image1.png"/><Relationship Id="rId3" '
           'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
           'Target="https://www.example.com/" TargetMode="External"/>')
    part = WRAP.format(enc="UTF-8", ns=S.RELATIONSHIPS, rel=rel).encode("utf-8")
    assert S.unlinked_rels(part) == (part, 0)


def test_a_relationship_part_that_isnt_xml_is_refused():
    with pytest.raises(S.OfficeRefused):
        S.unlinked_rels(b"<Relationships><Relationship Target='x' ")


RTF = (rb"{\rtf1\ansi{\*\template \\SERVER\s\r7-template.dotx}"
       rb"Before {\field{\*\fldinst { INCLUDEPICTURE \\\\SERVER\\s\\p.png \\d }}{\fldrslt shown}} after "
       rb"{\object\objautlink{\*\objclass Word.Document}{\*\objdata 0102}} "
       rb"{\pict\pngblip\bin3 {}} end}")


def test_an_rtfs_template_fields_and_linked_objects_are_taken_out(tmp_path):
    cleaned, count = S.unlinked_rtf(RTF)
    assert count == 3 and b"SERVER" not in cleaned
    assert b"shown" in cleaned and b"Before" in cleaned and cleaned.endswith(b"end}")
    assert rb"\bin3 {}" in cleaned  # (binary data is skipped over, never read as braces)
    source = tmp_path / "share.rtf"
    source.write_bytes(RTF)
    folder = tmp_path / "private"
    folder.mkdir()
    assert b"SERVER" not in S.safe_copy(source, folder).read_bytes()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sessions")
def test_office_isnt_started_without_a_desktop(tmp_path, monkeypatch):
    from planetpdf.core import office, windows_office

    monkeypatch.setattr(windows_office, "in_services_session", lambda: True)
    monkeypatch.setattr(office, "_backend", lambda: object())
    with pytest.raises(Exception, match="without a desktop"):
        office.to_pdf(tmp_path / "a.docx", tmp_path / "a.pdf")
