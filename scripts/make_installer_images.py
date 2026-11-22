"""Draw the Windows installer's wizard pictures from PlanetPDF's icon (2026-10-23).

Inno Setup shows a tall picture on the Welcome and Finished pages (WizardImageFile) and a small
one at the top right of the other pages (WizardSmallImageFile); without them it shows its own
artwork. It picks the size that fits the screen's scaling from the list it's given, so each
picture is made at every size Inno Setup's documentation lists. Output: packaging\\images\\*.png
(Inno Setup 6 reads PNG, transparency included). Re-run after changing the icon.

    .venv\\Scripts\\python.exe scripts\\make_installer_images.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
RESOURCES = ROOT / "src" / "planetpdf" / "resources"
ICON = RESOURCES / "icons" / "planetpdf.ico"
FONT = RESOURCES / "fonts" / "NotoSans.ttf"
OUT = ROOT / "packaging" / "images"

# Inno Setup 6's sizes for 100 % to 250 % scaling
WIZARD_SIZES = [(164, 314), (192, 386), (246, 459), (273, 556), (328, 604), (355, 700), (410, 797)]
SMALL_SIZES = [(55, 55), (64, 68), (69, 69), (83, 80), (92, 97), (110, 106), (119, 123), (138, 140)]

TOP = (0x1E, 0x5F, 0xB4)       # the icon's blue, darker at the top
BOTTOM = (0x3A, 0x8E, 0xE6)


def icon(size: int) -> Image.Image:
    """The app icon at `size` pixels (the largest picture in the .ico, scaled)."""
    image = Image.open(ICON)
    image.size = max(image.info["sizes"])
    image.load()
    return image.convert("RGBA").resize((size, size), Image.LANCZOS)


def wizard_image(width: int, height: int) -> Image.Image:
    """A blue panel: the icon on a white tile, "PlanetPDF" under it."""
    panel = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(panel)
    for y in range(height):
        t = y / max(height - 1, 1)
        draw.line([(0, y), (width, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    tile = round(width * 0.56)
    left, top = (width - tile) // 2, round(height * 0.22)
    draw.rounded_rectangle([left, top, left + tile, top + tile], radius=tile // 6, fill="white")
    picture = icon(round(tile * 0.78))
    offset = (left + (tile - picture.width) // 2, top + (tile - picture.height) // 2)
    panel.paste(picture, offset, picture)
    font = ImageFont.truetype(str(FONT), round(width * 0.15))
    font.set_variation_by_name("Bold")      # Noto Sans is a variable font
    text_top = top + tile + round(height * 0.05)
    draw.text((width / 2, text_top), "PlanetPDF", font=font, fill="white", anchor="mt")
    return panel


def small_image(width: int, height: int) -> Image.Image:
    """The icon on a transparent background (the wizard's header band shows through)."""
    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    side = min(width, height)
    picture = icon(side)
    panel.paste(picture, ((width - side) // 2, (height - side) // 2), picture)
    return panel


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in [*OUT.glob("*.png"), *OUT.glob("*.bmp")]:
        old.unlink()
    for n, (w, h) in enumerate(WIZARD_SIZES, 1):
        wizard_image(w, h).save(OUT / f"wizard-{w}x{h}.png", optimize=True)
        print(f"Wizard picture {n} of {len(WIZARD_SIZES)}: {w} x {h}")
    for n, (w, h) in enumerate(SMALL_SIZES, 1):
        small_image(w, h).save(OUT / f"small-{w}x{h}.png", optimize=True)
        print(f"Small picture {n} of {len(SMALL_SIZES)}: {w} x {h}")


if __name__ == "__main__":
    main()
