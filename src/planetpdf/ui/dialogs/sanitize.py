from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

from planetpdf.core.redaction.sanitize import LABELS, SanitizeOptions


class SanitizeDialog(QDialog):
    """Last step before applying: what else to remove. Everything is ticked by default."""

    def __init__(self, mark_count: int, page_count: int, signed: bool, parent: QWidget | None = None,
                 sanitize_only: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sanitize Document" if sanitize_only else "Apply Redactions")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Removes hidden information from this document. You can undo it; "
            "<b>save</b> afterwards to keep the change.",
            self,
        ) if sanitize_only else QLabel(
            f"<b>{mark_count} mark{'s' if mark_count != 1 else ''} on {page_count} "
            f"page{'s' if page_count != 1 else ''}</b> will be permanently removed: text, "
            "images, and graphics under them, plus any comments, links, and form fields they "
            "overlap. The result is saved as a <b>new file</b> and checked automatically.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        if signed and not sanitize_only:
            warn = QLabel("⚠ This document is digitally signed. The redacted copy will not keep the signatures.", self)
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #b35900;")
            layout.addWidget(warn)

        layout.addWidget(QLabel("<br>Remove:" if sanitize_only else "<br>Also remove hidden information:", self))
        self.checks: dict[str, QCheckBox] = {}
        for name in SanitizeOptions.__dataclass_fields__:
            title, description = LABELS[name]
            box = QCheckBox(title, self)
            box.setChecked(True)
            box.setToolTip(description)
            self.checks[name] = box
            layout.addWidget(box)
            hint = QLabel(description, self)
            hint.setWordWrap(True)
            hint.setStyleSheet("color: gray; margin-left: 22px;")
            layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.ok = buttons.addButton("Sanitize" if sanitize_only else "Choose File and Apply…", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self) -> SanitizeOptions:
        return SanitizeOptions(**{name: box.isChecked() for name, box in self.checks.items()})
