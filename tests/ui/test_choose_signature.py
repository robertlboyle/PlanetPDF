"""Pick which saved signature / initials to place (user request)."""

from __future__ import annotations

import io

import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication, QRectF
from PySide6.QtWidgets import QApplication

from planetpdf.core.markup import Kind
from planetpdf.core.signing.visual import ink_color, recolor_ink, trim_and_clear_background
from planetpdf.ui.main_window import MainWindow

RED, BLUE = (0.8, 0.1, 0.1), (0.05, 0.15, 0.55)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _png(color, width=300):
    image = Image.new("RGB", (width, 100), "white")
    for x in range(30, width - 30):
        for y in range(45, 55):
            image.putpixel((x, y), (0, 0, 0))
    out = io.BytesIO()
    image.save(out, "PNG")
    return recolor_ink(trim_and_clear_background(out.getvalue()), color)


@pytest.fixture
def window(qapp, tmp_path, pdf_copy):
    win = MainWindow(tmp_path / "profile")
    win.show()
    win.open_file(pdf_copy("text.pdf"))
    library = win.signature_library
    library.add(_png(BLUE), "signature", "Blue full")
    library.add(_png(RED), "signature", "Red full")  # newest = default
    library.add(_png(BLUE, 160), "initials", "My initials")
    QCoreApplication.processEvents()
    yield win
    win.undo_stack.setClean()
    win.close()


def _listed(menu):
    return [(a.text(), a.data()) for a in menu.actions() if a.data()]


def test_sign_menu_lists_every_saved_signature(window):
    mui = window.markup_ui
    mui.fill_saved_signatures(mui.sign_menu, mui._saved_marker)
    names = [text for text, _ in _listed(mui.sign_menu)]
    assert names == ["Red full   (default)", "Blue full", "My initials   (default)"]
    sections = [a.text() for a in mui.sign_menu.actions() if a.isSeparator() and a.text()]
    assert sections == ["Signatures", "Initials"]
    # opening it again doesn't pile up duplicates; the rest of the menu is still there
    mui.fill_saved_signatures(mui.sign_menu, mui._saved_marker)
    assert len(_listed(mui.sign_menu)) == 3
    texts = [a.text() for a in mui.sign_menu.actions()]
    assert "&Digitally Sign Document…" in texts and texts.count("&New Signature…") == 1
    mui.fill_saved_signatures(mui.choose_menu)
    mui.fill_saved_signatures(mui.choose_menu)
    assert len(_listed(mui.choose_menu)) == 3
    assert [a.text() for a in mui.choose_menu.actions()].count("&Manage Signatures and Initials…") == 1


def test_placing_the_chosen_one_and_it_becomes_the_default(window):
    mui = window.markup_ui
    library = window.signature_library
    blue = next(s for s in library.items if s.name == "Blue full")
    mui.fill_saved_signatures(mui.sign_menu, mui._saved_marker)
    action = next(a for a in mui.sign_menu.actions() if a.data() == blue.sig_id)
    action.trigger()
    assert window.markup.tool is Kind.SIGNATURE
    window.view.shapeDrawn.emit("place", 0, QRectF(100, 600, 1, 1))  # a click: default size
    [placed] = [i for i in window.markup.items if i.kind is Kind.SIGNATURE]
    from planetpdf.core.appearance import read_source
    import pymupdf

    doc = window.session.doc
    source = read_source(doc, placed.xref)
    pix = pymupdf.open(stream=source, filetype="pdf")[0].get_pixmap(dpi=72, alpha=True)
    assert ink_color(pix.tobytes("png")) == pytest.approx(BLUE, abs=0.06)  # the blue one, not the default red
    assert library.default("signature").sig_id == blue.sig_id  # next time: the one you used
    mui.act_place_signature.trigger()
    assert window.markup.tool is Kind.SIGNATURE
