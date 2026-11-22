"""Create ▸ PDF from Scanner / Insert Pages ▸ From Scanner (Windows only; backlog Phase D,
built 2026-10-02): Windows Image Acquisition (WIA), through comtypes. Windows' own scan
dialog chooses the scanner, color, resolution and area, so every WIA scanner works with
no driver of ours. Each scan is one page; the caller asks for more.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from planetpdf.core.document import PdfError

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("Scanning is only available on Windows.")

WIA_SCANNER = 1  # WiaDeviceType.ScannerDeviceType
WIA_FORMAT_PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"
WIA_ERROR_CANCELED = -2145320860  # 0x80210064: the user closed the scan dialog
WIA_ERROR_OFFLINE = -2145320954  # 0x80210005: the scanner is off / not connected


def _comtypes():
    import comtypes
    import comtypes.client

    comtypes.CoInitialize()
    return comtypes.client


def scanners() -> list[str]:
    """The names of the WIA scanners Windows knows."""
    client = _comtypes()
    try:
        manager = client.CreateObject("WIA.DeviceManager", dynamic=True)
        names = []
        for i in range(1, manager.DeviceInfos.Count + 1):
            info = manager.DeviceInfos.Item(i)
            if info.Type == WIA_SCANNER:
                names.append(str(info.Properties.Item("Name").Value))
        return names
    except Exception:
        return []


def scan_page(folder: Path | None = None) -> Path | None:
    """Show Windows' scan dialog; the scanned page as a PNG file, or None if canceled."""
    client = _comtypes()
    try:
        dialog = client.CreateObject("WIA.CommonDialog", dynamic=True)
        image = dialog.ShowAcquireImage(WIA_SCANNER, 0, 0, WIA_FORMAT_PNG, False, True, False)
    except Exception as exc:
        code = getattr(exc, "args", [None])[0]
        if code == WIA_ERROR_CANCELED:
            return None
        if code == WIA_ERROR_OFFLINE:
            raise PdfError("The scanner isn't responding. Check that it's on and connected.") from exc
        raise PdfError(f"Scanning failed: {exc}") from exc
    if image is None:  # (canceled)
        return None
    folder = Path(folder or tempfile.mkdtemp(prefix="planetpdf-scan-"))
    n = 1
    while (folder / f"scan-{n:03d}.png").exists():
        n += 1
    path = folder / f"scan-{n:03d}.png"
    image.SaveFile(str(path))
    return path
