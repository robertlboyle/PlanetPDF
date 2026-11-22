"""Phase D in the window (2026-10-02): scanner, web page, Check Spelling, Read Out Loud,
the token option when signing."""

from __future__ import annotations

import sys

import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog

from planetpdf.core import markup as K
from planetpdf.core import web_capture
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, pdf_copy, monkeypatch):
    w = MainWindow(tmp_path / "profile")
    w.open_file(pdf_copy("text.pdf"))
    w.messages = []
    w.show_message = lambda title, text: w.messages.append(text)
    w.answers = []
    w.ask = lambda title, text: (w.answers.append(text), w.answer)[1]
    w.answer = False
    yield w
    w.extras.stop_reading()
    for other in MainWindow.open_windows():
        other.undo_stack.setClean()
        other.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows scanner")
def test_insert_from_scanner_two_pages(win, tmp_path, monkeypatch):
    from planetpdf.core import windows_scanner

    monkeypatch.setattr(windows_scanner, "scanners", lambda: ["Test Scanner"])
    shots = iter([("red", True), ("blue", False)])

    def fake_scan(folder):
        color, _more = next(shots)
        path = folder / f"{color}.png"
        Image.new("RGB", (850, 1100), color).save(path)
        return path

    monkeypatch.setattr(win.extras, "scan_one", fake_scan)
    answers = iter([True, False])  # "Scan another page?" yes, then no
    win.ask = lambda title, text: next(answers)
    before = win.session.page_count
    assert win.extras.insert_from_scanner()
    assert win.session.page_count == before + 2 and win.undo_stack.undoText().startswith("Insert Pages from Scanner")


def test_no_scanner_says_so(win, monkeypatch):
    if sys.platform != "win32":
        pytest.skip("Windows scanner")
    from planetpdf.core import windows_scanner

    monkeypatch.setattr(windows_scanner, "scanners", lambda: [])
    assert not win.extras.insert_from_scanner()
    assert "No scanner was found" in win.messages[-1]


@pytest.mark.skipif(web_capture.find_browser() is None, reason="Edge / Chrome not installed")
def test_create_and_insert_from_web_page(win, tmp_path, monkeypatch):
    page = tmp_path / "offer.html"
    page.write_text("<h1>Gigabit Offer</h1><p>Sign up today.</p>", encoding="utf-8")
    target = tmp_path / "offer.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    assert win.extras.create_from_web(str(page)), win.messages
    with pymupdf.open(target) as doc:
        assert "Gigabit Offer" in doc[0].get_text()
    win.open_file(target)
    count = win.session.page_count
    assert win.extras.insert_from_web(str(page)), win.messages
    assert win.session.page_count == count * 2


def test_check_spelling_changes_a_comment_and_learns_words(win):
    K.add_markup(win.session, K.MarkupSpec(K.new_id(), 0, K.Kind.NOTE, (400, 400, 420, 420), text="Plese chek Planetnet"))
    win.markup.changed({0})
    dialog = win.extras.check_spelling()
    assert dialog.include_pages.isChecked()  # (page text is checked too, by default)
    dialog.include_pages.setChecked(False)
    words = [i.word for i in dialog.issues]
    assert words == ["Plese", "chek", "Planetnet"]
    dialog.issues_list.setCurrentRow(0)
    assert "Please" in [dialog.suggestions.item(i).text() for i in range(dialog.suggestions.count())]
    dialog.replacement.setText("Please")
    dialog.change()
    note = next(i for i in K.list_markup(win.session) if i.kind is K.Kind.NOTE)
    assert note.spec.text == "Please chek Planetnet" and win.undo_stack.canUndo()
    assert [i.word for i in dialog.issues] == ["chek", "Planetnet"]  # (checked again)
    dialog.ignore_all()
    dialog.add_to_dictionary()
    assert dialog.issues == [] and "No spelling mistakes" in dialog.status.text()
    assert "planetnet" in win.extras.speller.user_words
    dialog.close()


def _page_pdf(path, fontfile=None):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    kwargs = {"fontname": "noto", "fontfile": str(fontfile)} if fontfile else {"fontname": "helv"}
    page.insert_text((72, 100), "The contrct is ready", fontsize=14, **kwargs)
    page.insert_text((72, 300), "Scaned wrods", render_mode=3)  # (an OCR layer)
    doc.save(path)
    return path


def test_change_page_text_warns_then_rewrites_the_line(win, tmp_path):
    """User 2026-10-02: Change was grayed for page text, even in Edit PDF."""
    from planetpdf.core.resources import font_path

    win.open_file(_page_pdf(tmp_path / "embedded.pdf", font_path("sans")))
    dialog = win.extras.check_spelling()
    dialog.issues_list.setCurrentRow(next(n for n, i in enumerate(dialog.issues) if i.word == "contrct"))
    assert dialog.buttons["Change"].isEnabled()
    dialog.replacement.setText("contract")
    win.answer = False
    dialog.change()
    assert "keeps its font" in win.answers[-1] and "contrct" in win.session.doc[0].get_text()
    win.answer = True
    dialog.change()
    assert "The contract is ready" in win.session.doc[0].get_text()
    assert win.undo_stack.undoText() == "Correct Spelling"
    assert "contract" not in [i.word for i in dialog.issues]
    win.undo_stack.undo()
    assert "contrct" in win.session.doc[0].get_text()
    dialog.close()


def test_change_page_text_says_when_the_font_changes_and_ocr_stays_gray(win, tmp_path):
    win.open_file(_page_pdf(tmp_path / "base14.pdf"))  # (Helvetica, not embedded: written again as Helvetica)
    dialog = win.extras.check_spelling()
    words = {i.word: n for n, i in enumerate(dialog.issues)}
    dialog.issues_list.setCurrentRow(words["wrods"])
    # (invisible text another program recognized: not ours to rewrite)
    assert not dialog.buttons["Change"].isEnabled() and "other software" in dialog.buttons["Change"].toolTip()
    dialog.issues_list.setCurrentRow(words["contrct"])
    dialog.replacement.setText("contract")
    win.answer = True
    dialog.change()
    assert "will be drawn in Helvetica" in win.answers[-1] and "look will change" in win.answers[-1]
    assert "The contract is ready" in win.session.doc[0].get_text()
    dialog.close()


def _ocr_pdf(path):
    from planetpdf.core import ocr

    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    ocr.insert_ocr_text_doc(doc, 0, [ocr.OcrWord((72, 200, 140, 214), "c0mpany"),
                                     ocr.OcrWord((150, 200, 200, 214), "Scaned")])
    doc.save(path)
    return path


def test_correct_recognized_text(win, tmp_path):
    from planetpdf.core.ocr_correct import ocr_words

    win.open_file(_ocr_pdf(tmp_path / "ocr.pdf"))
    dialog = win.extras.correct_ocr()
    assert [s.text for s in dialog.suspects] == ["c0mpany", "Scaned"]
    assert not dialog.picture.pixmap().isNull()
    dialog.replacement.setEditText("company")
    dialog.correct()
    assert [w.text for w in ocr_words(win.session.doc, 0)] == ["company", "Scaned"]
    assert win.undo_stack.undoText() == "Correct Recognized Text"
    assert [s.text for s in dialog.suspects] == ["Scaned"]
    dialog.skip()
    assert dialog.suspects == [] and "No more" in dialog.status.text()
    win.undo_stack.undo()
    assert [w.text for w in ocr_words(win.session.doc, 0)] == ["c0mpany", "Scaned"]
    dialog.close()


def test_check_spelling_changes_ocr_text(win, tmp_path):
    from planetpdf.core.ocr_correct import ocr_words

    win.open_file(_ocr_pdf(tmp_path / "ocr.pdf"))
    dialog = win.extras.check_spelling()
    dialog.issues_list.setCurrentRow(next(n for n, i in enumerate(dialog.issues) if i.word == "Scaned"))
    assert dialog.buttons["Change"].isEnabled()
    dialog.replacement.setText("Scanned")
    dialog.change()
    assert [w.text for w in ocr_words(win.session.doc, 0)][1] == "Scanned"
    dialog.close()


def test_correct_ocr_without_ocr_offers_recognition(win):
    win.answer = False
    assert win.extras.correct_ocr() is None and "no recognized" in win.answers[-1]


def test_dictionaries_dialog_switches_lists_and_imports(win, tmp_path, monkeypatch):
    from planetpdf.ui.phase_d_ui import DictionaryDialog

    monkeypatch.setattr(win, "save_settings_now", lambda: None)
    speller = win.extras.speller
    dialog = DictionaryDialog(win, speller)
    names = [dialog.lists.item(i).text() for i in range(dialog.lists.count())]
    assert "Medical" in names and "Computing & Programming" in names
    medical = dialog.lists.item(names.index("Medical"))
    assert not speller.is_wrong("lisinopril")
    medical.setCheckState(Qt.CheckState.Unchecked)
    assert speller.is_wrong("lisinopril") and win.settings.spelling_off == ["medical"]
    medical.setCheckState(Qt.CheckState.Checked)
    assert win.settings.spelling_off == [] and not speller.is_wrong("lisinopril")
    words = tmp_path / "products.txt"
    words.write_text("Gigafibr\nPlanetLink\n", encoding="utf-8")
    assert dialog.import_list(words, "Our Products")
    assert not speller.is_wrong("Gigafibr") and speller.is_wrong("Planetlink")
    assert any(dialog.lists.item(i).text() == "Our Products (imported)" for i in range(dialog.lists.count()))
    dialog.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows voices")
def test_read_out_loud_needs_activating(win):
    from planetpdf.ui.phase_d_ui import ExtrasUi

    win.settings.read_aloud_engine = "windows"  # (quick to load; the natural voices: tests/core)
    ExtrasUi.reset_speaker()
    assert not win.act_read_page.isEnabled()
    assert not any(a.isVisible() for a in win.read_toolbar_actions)  # (toolbar buttons only once activated)
    win.act_read_activate.trigger()
    assert win.act_read_page.isEnabled() and win.act_read_page.shortcut().toString() == "Ctrl+Shift+V"
    assert all(a.isVisible() for a in win.read_toolbar_actions) and not win.act_read_page.icon().isNull()
    win.extras.speaker.volume = 0  # (silent)
    win.act_read_page.trigger()
    assert win.extras.speaker.speaking
    win.act_read_pause.trigger()  # (standard media buttons: Pause becomes Play while paused)
    assert win.act_read_pause.toolTip().startswith("Resume")
    win.act_read_pause.trigger()
    assert win.act_read_pause.toolTip().startswith("Pause")
    # user 2026-10-04: after Stop the button is Play again and starts reading from this page
    win.act_read_stop.trigger()
    assert win.extras.reading_state() == "stopped" and win.act_read_pause.toolTip().startswith("Read aloud")
    win.act_read_pause.trigger()
    assert win.extras.speaker.speaking and win.extras.reading_state() == "playing"
    assert win.act_read_pause.toolTip().startswith("Pause")
    voice = win.extras.voice_choices()  # (changing the voice keeps the button truthful)
    if voice[1]:
        win.extras.choose_voice(voice[0], voice[1][0][0])
        assert win.act_read_pause.toolTip().startswith("Pause" if win.extras.speaker.speaking else "Read aloud")
    win.act_read_stop.trigger()
    assert win.act_read_pause.toolTip().startswith("Read aloud")
    win.act_read_activate.trigger()  # (off: stops, the commands gray out, the buttons go)
    assert not win.act_read_page.isEnabled() and not any(a.isVisible() for a in win.read_toolbar_actions)


def test_token_option_needs_a_driver(win):
    from planetpdf.ui.dialogs.digital_sign import DigitalSignDialog

    dialog = DigitalSignDialog(win)
    dialog.source.setCurrentIndex(dialog.source.findData("pkcs11"))
    assert dialog.pin.isVisibleTo(dialog) and not dialog.password.isVisibleTo(dialog)
    dialog.driver.setText("")
    assert not dialog.unlock() and "driver" in dialog.error.text()
    dialog.driver.setText(r"C:\nowhere\token.dll")
    dialog.pin.setText("1234")
    assert not dialog.unlock() and "wasn't found" in dialog.error.text() and dialog.pin.text() == ""


def test_natural_voice_is_used_and_chosen_in_preferences(win, monkeypatch):
    """User 2026-10-02: Kokoro when the computer is fast enough, else Piper; both in Preferences."""
    from planetpdf.core import neural_speech as NS
    from planetpdf.ui.dialogs.preferences import PreferencesDialog
    from planetpdf.ui.phase_d_ui import ExtrasUi

    if not NS.available() or not NS.piper_voices():
        pytest.skip("voice models (with Piper) not downloaded")
    monkeypatch.setattr(win, "save_settings_now", lambda: None)
    win.settings.read_aloud_engine = "piper"
    ExtrasUi.reset_speaker()
    speaker = win.extras.speaker
    assert isinstance(speaker, NS.Speaker) and speaker.preference == "piper"
    dialog = PreferencesDialog(win)
    dialog.show_page("reading")  # (Preferences shows one page at a time since 2026-10-07)
    dialog.read_engine.setCurrentIndex(dialog.read_engine.findData("auto"))
    assert dialog.voice_boxes["kokoro"].isVisibleTo(dialog) == NS.kokoro_available()
    dialog.voice_boxes["piper"].setCurrentIndex(dialog.voice_boxes["piper"].findData("en_US-joe-medium"))
    dialog._save()
    assert win.settings.read_aloud_engine == "auto" and win.settings.read_aloud_voices["piper"] == "en_US-joe-medium"
    assert ExtrasUi._shared_speaker is None  # (the next reading starts with the new choice)


def test_voice_menu_and_volume_slider_on_the_toolbar(win, monkeypatch):
    """User 2026-10-02: "Are there different voices? male and female? A volume slider would
    be nice on the top too."""
    from planetpdf.core import neural_speech as NS
    from planetpdf.ui.phase_d_ui import ExtrasUi

    if not NS.piper_voices():
        pytest.skip("Piper voices not downloaded")
    monkeypatch.setattr(win, "save_settings_now", lambda: None)
    win.settings.read_aloud_engine = "piper"
    ExtrasUi.reset_speaker()
    win.act_read_activate.trigger()
    assert all(a.isVisible() for a in win.read_toolbar_actions)  # (the voice menu and slider included)
    menu = win.read_voice_button.menu()
    menu.aboutToShow.emit()
    names = [a.text() for a in menu.actions() if a.isCheckable()]
    assert any("female" in n for n in names) and any("male" in n and "female" not in n for n in names)
    next(a for a in menu.actions() if a.text().startswith("Joe")).trigger()
    assert win.settings.read_aloud_voices["piper"] == "en_US-joe-medium"
    assert win.extras.speaker.voice_for["piper"] == "en_US-joe-medium"  # (from the next sentence)
    win.read_volume_slider.setValue(35)
    assert win.settings.read_aloud_volume == 35 and win.extras.speaker.volume == 35
    win.act_read_activate.trigger()
    assert not any(a.isVisible() for a in win.read_toolbar_actions)
