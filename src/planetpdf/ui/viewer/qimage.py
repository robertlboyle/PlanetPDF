from __future__ import annotations

from PySide6.QtGui import QImage, QPixmap

from planetpdf.core.render import RenderedPage


def to_pixmap(page: RenderedPage) -> QPixmap:
    image = QImage(page.samples, page.width, page.height, page.stride, QImage.Format.Format_RGB888)
    # copy() detaches from the Python bytes buffer before it can be freed.
    return QPixmap.fromImage(image.copy())


def pdf_preview(pdf: bytes, max_width: int, max_height: int) -> QPixmap:
    """Picture of a one-page source PDF (stamps, signatures) with a transparent background."""
    import pymupdf

    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        rect = doc[0].rect
        zoom = min(max_width / max(rect.width, 1), max_height / max(rect.height, 1))
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(image.copy())


def png_preview(png: bytes, max_width: int, max_height: int) -> QPixmap:
    pixmap = QPixmap()
    pixmap.loadFromData(png, "PNG")
    if pixmap.isNull():
        return pixmap
    from PySide6.QtCore import Qt

    return pixmap.scaled(max_width, max_height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
