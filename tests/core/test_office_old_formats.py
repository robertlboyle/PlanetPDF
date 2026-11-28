r"""Older Office files reach Word or Excel only as a clean newer-format copy, and whatever can't be
made clean is refused (Robert 2026-11-28: "err on the side of not converting vs. creating a
security issue")."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

from planetpdf.core import office_safety as S

OFFICE = Path(r"C:\Program Files\Microsoft Office\root\Office16")
SAMPLE_DOC = OFFICE / "1033" / "PROTTPLN.DOC"
SAMPLE_XLS = OFFICE / "Visio Content" / "1033" / "ORGDATA.XLS"
# (SOLVSAMP.XLS is refused: it names a printer on a share, prn-corp1, in its print settings)
NAMES_A_SHARE = OFFICE / "SAMPLES" / "SOLVSAMP.XLS"


@pytest.fixture
def folder(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    return private


@pytest.mark.skipif(sys.platform != "win32" or not SAMPLE_DOC.is_file(), reason="Office's sample files and converter")
def test_an_old_word_file_reaches_word_as_a_clean_docx(tmp_path, folder):
    source = tmp_path / "letter.doc"
    source.write_bytes(SAMPLE_DOC.read_bytes())
    copy = S.safe_copy(source, folder)
    assert copy == folder / "letter.docx"
    with zipfile.ZipFile(copy) as z:
        assert "word/document.xml" in z.namelist()
    assert not (folder / "letter.doc").exists()  # (the old file itself is never given to Word)


@pytest.mark.skipif(sys.platform != "win32" or not SAMPLE_XLS.is_file(), reason="Office's sample files and converter")
def test_an_old_excel_file_reaches_excel_as_a_clean_xlsx(tmp_path, folder):
    source = tmp_path / "book.xls"
    source.write_bytes(SAMPLE_XLS.read_bytes())
    copy = S.safe_copy(source, folder)
    assert copy.suffix == ".xlsx"
    with zipfile.ZipFile(copy) as z:
        assert "xl/workbook.xml" in z.namelist()


@pytest.mark.parametrize("text", [
    "\\\\SERVER\\share\\template.dot",  # (a share)
    "file:///C:/x.dot",
    "\x13 INCLUDEPICTURE \"x.png\" \\d",  # (a field that fetches)
    "\x13DDEAUTO cmd /c calc",
])
@pytest.mark.parametrize("encoding", ["latin-1", "utf-16-le"])
def test_an_old_file_naming_something_outside_is_refused_before_anything_opens_it(tmp_path, folder, text, encoding,
                                                                                   monkeypatch):
    source = tmp_path / "report.doc"
    source.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64 + text.encode(encoding) + b"\x00" * 64)
    from planetpdf.core import windows_office

    monkeypatch.setattr(windows_office, "convert_old", lambda *a: pytest.fail("the converter must not run"),
                        raising=False)
    with pytest.raises(S.OfficeRefused, match="names something outside"):
        S.safe_copy(source, folder)


def test_ordinary_words_are_not_taken_for_fields():
    assert S.fetching_text(b"Click the link below to import the data from our database") == ""
    assert S.fetching_text("A plain letter: see the link.".encode("utf-16-le")) == ""


@pytest.mark.parametrize("name", ["slides.ppt", "notes.odt", "sheet.ods", "talk.odp", "memo.wpd"])
def test_formats_that_cant_be_cleaned_are_refused(tmp_path, folder, name):
    source = tmp_path / name
    source.write_bytes(b"not looked at")
    with pytest.raises(S.OfficeRefused, match="wasn't converted"):
        S.safe_copy(source, folder)


def test_a_csvs_formulas_never_reach_excel(tmp_path, folder):
    from openpyxl import load_workbook

    source = tmp_path / "data.csv"
    source.write_text('Name,Amount,Note\nA,12.5,=WEBSERVICE("http://198.18.0.1/x")\nB,-3,@SUM(1)\n', encoding="utf-8")
    copy = S.safe_copy(source, folder)
    assert copy == folder / "data.xlsx"
    sheet = load_workbook(copy).active
    assert sheet["B2"].value == 12.5 and sheet["B3"].value == -3
    assert sheet["C2"].value == '=WEBSERVICE("http://198.18.0.1/x")' and sheet["C2"].data_type == "s"
    with zipfile.ZipFile(copy) as z:
        assert b"<f>" not in z.read("xl/worksheets/sheet1.xml")  # (no formula anywhere)


def _package(path: Path, parts: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        for name, text in parts.items():
            z.writestr(name, text)
    return path


@pytest.mark.parametrize("name, part, text", [
    ("book.xlsx", "xl/connections.xml",
     '<connections xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
     '<connection id="1" name="q" refreshOnLoad="1"><webPr url="http://198.18.0.1/data"/></connection></connections>'),
    ("book.xlsx", "xl/queryTables/queryTable1.xml",
     '<queryTable xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" name="q" connectionId="1" '
     'refreshOnLoad="true"/>'),
    ("letter.docx", "word/settings.xml",
     '<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:mailMerge>'
     '<w:dataType w:val="odbc"/><w:query w:val="SELECT * FROM x"/></w:mailMerge></w:settings>'),
])
def test_data_that_refreshes_or_a_mail_merge_is_refused(tmp_path, folder, name, part, text):
    source = _package(tmp_path / name, {part: text})
    with pytest.raises(S.OfficeRefused, match="wasn't converted"):
        S.safe_copy(source, folder)
    assert not (folder / name).exists()


def test_data_that_refreshes_only_when_asked_is_fine(tmp_path, folder):
    source = _package(tmp_path / "book.xlsx", {"xl/connections.xml": (
        '<connections xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<connection id="1" name="q" refreshOnLoad="0"/></connections>')})
    assert S.safe_copy(source, folder).exists()


def test_an_rtf_object_keeps_only_its_picture():
    rtf = (rb"{\rtf1 Before {\object\objemb\objupdate{\*\objclass Package}{\*\objdata 0102}"
           rb"{\result {\pict\pngblip 89504e47}}} after}")
    cleaned, count = S.unlinked_rtf(rtf)
    assert count == 1
    assert b"objdata" not in cleaned and b"objupdate" not in cleaned and b"objclass" not in cleaned
    assert rb"{\result {\pict\pngblip 89504e47}}" in cleaned and cleaned.startswith(rb"{\rtf1 Before ")


@pytest.mark.skipif(not NAMES_A_SHARE.is_file(), reason="Office's sample file")
def test_a_real_workbook_naming_a_share_is_refused(tmp_path, folder):
    source = tmp_path / "solver.xls"
    source.write_bytes(NAMES_A_SHARE.read_bytes())
    with pytest.raises(S.OfficeRefused, match="prn-corp1"):
        S.safe_copy(source, folder)
