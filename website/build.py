"""Build software.planet.net (user 2026-10-24: a page per program; all freeware. No category pages since 2026-11-05).

    .venv\\Scripts\\python.exe website\\build.py        # -> website/dist/

Each pages/*.html is a page's content, starting with <!-- title: … -->, <!-- nav: … --> and
<!-- description: … --> comments; this wraps it in the site's header, menu and footer and fills
in {{planetpdf_version}} (Windows: WINDOWS_VERSION), {{planetpdf_mac_version}}, {{planetpdf_linux_version}}, {{eudora_version}} (and its Mac and Linux
forms), {{eleonora_version}} (likewise), {{eleonora_logo}}, and {{download_icon}}; Eleonora's page shows its
<!-- coming-soon --> parts until ELEONORA_RELEASED, then its <!-- released --> parts; within them the Mac's
and Linux's <!-- eleonora-mac --> / <!-- eleonora-linux --> parts once ELEONORA_MAC_RELEASED / ELEONORA_LINUX_RELEASED,
else their <!-- eleonora-mac-pending --> / <!-- eleonora-linux-pending --> parts.
PlanetPDF's release notes page is made from the program's own What's New, so it stays current.
Upload with scripts/upload_update.py --website. The pictures (images/) live on the server only;
the fonts (fonts/, Barlow under the OFL) are kept here and served by the site, never from Google.
"""

from __future__ import annotations

import hashlib
import html
import os
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PAGES = HERE / "pages"
DIST = HERE / "dist"
WHATS_NEW = ROOT / "src" / "planetpdf" / "resources" / "help" / "whats-new.html"
# Eleonora (formerly Eudora), from its own clone beside this one (C:\code\Eudora, ~/code/Eudora;
# ELEONORA_ROOT, or the older EUDORA_ROOT, says where else): its What's New makes the release notes
# pages, as PlanetPDF's does, and its falcon icon goes into the Eleonora page
ELEONORA_ROOT = Path(os.environ.get("ELEONORA_ROOT") or os.environ.get("EUDORA_ROOT") or ROOT.parent / "Eudora")
ELEONORA_WHATS_NEW = ELEONORA_ROOT / "crates" / "eleonora-app" / "ui" / "help" / "whats-new.html"
ELEONORA_ICON = ELEONORA_ROOT / "crates" / "eleonora-app" / "icons" / "eleonora.svg"
# (Eleonora 2.0.0 isn't published yet: its page says "coming soon" and links no downloads until this is
# True; set it, and the versions below, when the user publishes it)
ELEONORA_RELEASED = True  # (Windows 2.0.0 published 2026-11-18, 2.0.2 the same day)
# (the Mac and Linux follow from the Mac mini: until each is uploaded, its rows offer Eudora, which
# updates itself to Eleonora; set True when that system's Eleonora is on the server)
ELEONORA_MAC_RELEASED = True  # (Mac 2.0.0 published 2026-11-18, 2.0.2 the same day)
ELEONORA_LINUX_RELEASED = True  # (Linux 2.0.0 published 2026-11-18, 2.0.1 and 2.0.2 the same day)
ELEONORA_VERSION = "2.0.9"  # (Windows 2.0.9, 2026-11-22)
ELEONORA_ANDROID_VERSION = "2.0.9"  # (the Android beta, 2026-11-22; 2.0.2 crashed at start)
ELEONORA_MAC_VERSION = "2.0.8"  # (Mac 2.0.8, 2026-11-22, from the MacBook Pro)
ELEONORA_LINUX_VERSION = "2.0.8"  # (Linux 2.0.8, 2026-11-22, from the MacBook Pro)
# (Eudora 1.x: its pages now send people to Eleonora's, see MOVED; 1.x copies update themselves to Eleonora)
WINDOWS_VERSION = "1.0.0"  # (Windows' last release, 2026-11-22; raise it when a Windows build is uploaded)
MAC_VERSION = "1.0.0"  # (the Mac's last release, 2026-11-22; raise it when a Mac build is uploaded)
EUDORA_VERSION = "1.0.3"  # (Eudora's last Windows release, x64 and ARM64, 2026-11-17; raise it when Eudora uploads a new one)
EUDORA_MAC_VERSION = "1.0.2"  # (Eudora's last Mac release, 2026-11-17)
EUDORA_LINUX_VERSION = "1.0.2"  # (Eudora's last Linux release, 2026-11-17)
LINUX_VERSION = "1.0.0"  # (Linux's last release, 2026-11-22; raise it when the Linux packages are uploaded)

# (no category pages: user 2026-11-05, "an artificial extra layer … It is all useful productivity
# software"; the old category addresses forward to the home page, see MOVED)
MENU = [("home", "index.html", "Home"), ("planetpdf", "planetpdf.html", "PlanetPDF"), ("eleonora", "eleonora.html", "Eleonora"),
        ("principles", "principles.html", "Our Principles"), ("freeware", "freeware.html", "Freeware Terms")]
DOWNLOAD_ICON = ('<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
                 'stroke-linejoin="round" aria-hidden="true"><path d="M10 3v10M5.5 8.5 10 13l4.5-4.5M3.5 16.5h13"/></svg>')

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="icon" href="images/favicon.ico" sizes="any">
<link rel="apple-touch-icon" href="images/apple-touch-icon.png">
<link rel="preload" href="fonts/Barlow-Regular.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="style.css?v={style_version}">
</head>
<body>
<header class="top">
  <div class="wrap">
    <a class="brand" href="index.html"><picture><source srcset="images/planet-networks-logo-white.png" media="(prefers-color-scheme: dark)"><img src="images/planet-networks-logo.png" height="40" alt="Planet Networks"></picture> <span>Software</span></a>
    <nav aria-label="Site">
      <ul>
{menu}
      </ul>
    </nav>
  </div>
</header>
<main>
  <div class="wrap">
{body}
  </div>
</main>
<footer>
  <div class="wrap">
    <p><strong>All software on this site is freeware:</strong> free to use, provided as is, with no warranty and no
    support. See the <a href="freeware.html">freeware terms</a>. Feedback and bug reports:
    <a href="mailto:software@planet.net">software@planet.net</a>. Privacy:
    <a href="eleonora-privacy.html">Eleonora privacy policy</a>.</p>
    <p>&copy; 2026 Planet Networks, Inc., Newton, NJ.</p>
  </div>
</footer>
</body>
</html>
"""


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version))


def planetpdf_version() -> str:
    text = (ROOT / "src" / "planetpdf" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def _meta(text: str, key: str) -> str:
    found = re.search(rf"<!--\s*{key}:\s*(.*?)\s*-->", text)
    return found.group(1) if found else ""


def render(fragment: str) -> str:
    title, nav, description = _meta(fragment, "title"), _meta(fragment, "nav"), _meta(fragment, "description")
    body = re.sub(r"<!--\s*(title|nav|description):.*?-->\s*", "", fragment)
    # (what says the Mac or Linux is behind Windows shows only while it is: user 2026-10-26; a
    # Mac-only fix can put the Mac ahead, as 0.1.15 did on 2026-11-05)
    for system, version in (("mac", MAC_VERSION), ("linux", LINUX_VERSION)):
        behind = _key(version) < _key(WINDOWS_VERSION)
        body = re.sub(rf"<!-- {system}-behind -->(.*?)<!-- /{system}-behind -->\n?", r"\1" if behind else "", body,
                      flags=re.S)
    # (Eleonora's page: "coming soon" until it's published, then its downloads)
    for part, shown in (("coming-soon", not ELEONORA_RELEASED), ("released", ELEONORA_RELEASED),
                        ("eleonora-mac", ELEONORA_MAC_RELEASED), ("eleonora-mac-pending", not ELEONORA_MAC_RELEASED),
                        ("eleonora-linux", ELEONORA_LINUX_RELEASED), ("eleonora-linux-pending", not ELEONORA_LINUX_RELEASED)):
        body = re.sub(rf"<!-- {part} -->(.*?)<!-- /{part} -->\n?", r"\1" if shown else "", body, flags=re.S)
    body = (body.replace("{{planetpdf_version}}", WINDOWS_VERSION)
                .replace("{{planetpdf_mac_version}}", MAC_VERSION)
                .replace("{{planetpdf_linux_version}}", LINUX_VERSION)
                .replace("{{eudora_version}}", EUDORA_VERSION)
                .replace("{{eudora_mac_version}}", EUDORA_MAC_VERSION)
                .replace("{{eudora_linux_version}}", EUDORA_LINUX_VERSION)
                .replace("{{eleonora_version}}", ELEONORA_VERSION)
                .replace("{{eleonora_mac_version}}", ELEONORA_MAC_VERSION)
                .replace("{{eleonora_android_version}}", ELEONORA_ANDROID_VERSION)
                .replace("{{eleonora_linux_version}}", ELEONORA_LINUX_VERSION)
                .replace("{{download_icon}}", DOWNLOAD_ICON))
    if "{{eleonora_logo}}" in body:
        body = body.replace("{{eleonora_logo}}", eleonora_logo())
    if "{{eleonora_card_logo}}" in body:
        body = body.replace("{{eleonora_card_logo}}", eleonora_logo(56))
    if "{{" in body:
        raise SystemExit(f"Unknown placeholder in {title}: {re.search(r'{{[^}]*}}', body).group(0)}")
    menu = "\n".join(f'        <li><a href="{href}"{" aria-current=\"page\"" if key == nav else ""}>{label}</a></li>'
                     for key, href, label in MENU)
    full_title = "Planet Networks Software" if nav == "home" else f"{title} | Planet Networks Software"
    # (the stylesheet's name changes with its contents, so a browser never pairs new pages with an
    # old cached stylesheet: user 2026-11-05, the new logo squeezed into the old 30-pixel square)
    style_version = hashlib.sha256((HERE / "style.css").read_bytes()).hexdigest()[:10]
    return TEMPLATE.format(title=html.escape(full_title), description=html.escape(description, quote=True),
                           menu=menu, body=body.strip(), style_version=style_version)


def release_notes() -> str:
    """The PlanetPDF release notes page: What's New's version sections, without its links to
    manual pages (not on the site) and its index comments."""
    source = WHATS_NEW.read_text(encoding="utf-8")
    sections = source[source.index("<h2"):source.rindex("</body>")]
    sections = re.sub(r"<!--.*?-->\s*", "", sections, flags=re.S)
    # "See <manual section>." and "(see <manual section>)" point into the program's manual: dropped
    sections = re.sub(r"\s*\(see\s+<a\b[^>]*>.*?</a>\)", "", sections, flags=re.S | re.I)
    sections = re.sub(r"\s*See\s+<a\b[^>]*>.*?</a>\.?", "", sections, flags=re.S)
    sections = re.sub(r"</?a\b[^>]*>", "", sections)  # (any other link keeps its words)
    return f"""<!-- title: PlanetPDF Release Notes -->
<!-- nav: planetpdf -->
<!-- description: What's new in each version of PlanetPDF, the free PDF editor from Planet Networks. -->
<div class="crumbs"><a href="index.html">Home</a> › <a href="planetpdf.html">PlanetPDF</a> › Release notes</div>
<h1>PlanetPDF release notes</h1>
<p class="lede">What changed in each version, newest first.<!-- mac-behind --> The Windows version is {{{{planetpdf_version}}}};
the Mac version is {{{{planetpdf_mac_version}}}}, so the newer changes reach the Mac with its next release.<!-- /mac-behind --><!-- linux-behind -->
The Linux version is {{{{planetpdf_linux_version}}}}; the newer changes reach Linux with its next release.<!-- /linux-behind --></p>
<p class="cta"><a class="btn" href="planetpdf.html#install">{{{{download_icon}}}} Download PlanetPDF</a></p>
<div class="notes">
{sections.strip()}
</div>
"""


def eleonora_logo(size: int = 96) -> str:
    """Eleonora's falcon icon, put into the page itself from Eleonora's own icon (the site's pictures
    live only on the server, so a new picture there would need uploading by hand)."""
    if not ELEONORA_ICON.is_file():
        raise SystemExit(f"Eleonora's icon isn't at {ELEONORA_ICON}: clone Eudora beside PlanetPDF, or set ELEONORA_ROOT.")
    svg = re.sub(r"<!--.*?-->\s*", "", ELEONORA_ICON.read_text(encoding="utf-8"), flags=re.S).strip()
    return svg.replace("<svg ", f'<svg width="{size}" height="{size}" role="img" aria-label="Eleonora\'s falcon icon" ', 1)


def eleonora_sections() -> str:
    """The version sections of Eleonora's What's New, without the links into its manual (not on the site)."""
    if not ELEONORA_WHATS_NEW.is_file():
        raise SystemExit(f"Eleonora's What's New isn't at {ELEONORA_WHATS_NEW}: clone Eudora beside PlanetPDF, or set ELEONORA_ROOT.")
    source = ELEONORA_WHATS_NEW.read_text(encoding="utf-8")
    sections = source[source.index("<h2"):source.rindex("</section>")]
    sections = re.sub(r"<!--.*?-->\s*", "", sections, flags=re.S)
    sections = re.sub(r"\s*\((?:<a\b[^>]*>more</a>)\)", "", sections)
    sections = re.sub(r"\s*See\s+<a\b[^>]*>.*?</a>(?:\s+and\s+<a\b[^>]*>.*?</a>)?\.?", "", sections, flags=re.S)
    sections = re.sub(r"</?a\b[^>]*>", "", sections)
    return sections.strip()


def eleonora_release_notes() -> str:
    """Eleonora's release notes page: its manual's What's New, Eudora's versions included."""
    download = '<a class="btn" href="eleonora.html#install">{{download_icon}} Download Eleonora</a>'
    return f"""<!-- title: Eleonora Release Notes -->
<!-- nav: eleonora -->
<!-- description: What's new in each version of Eleonora (formerly Eudora), the free email program from Planet Networks. -->
<div class="crumbs"><a href="index.html">Home</a> › <a href="eleonora.html">Eleonora</a> › Release notes</div>
<h1>Eleonora release notes</h1>
<p class="lede">What changed in each version, newest first. Versions before 2.0.0 were called Eudora. Windows, the
Mac, and Linux get the same version number for the same Eleonora.</p>
<p class="cta">{download}</p>
<div class="notes">
{eleonora_sections()}
</div>
"""


# addresses that used to be pages: a small page that sends the browser on (links people kept still work)
# (Eudora became Eleonora in 2.0.0, 2026-11-18: its pages send people to Eleonora's)
MOVED = {"email.html": "index.html", "productivity.html": "index.html", "tools.html": "index.html",
         "eudora.html": "eleonora.html", "eudora-release-notes.html": "eleonora-release-notes.html"}
MOVED_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Planet Networks Software</title>
<script>location.replace("{to}" + location.hash)</script>
<meta http-equiv="refresh" content="0; url={to}"><link rel="canonical" href="https://software.planet.net/{to}"></head>
<body><p>This page has moved: <a href="{to}">software.planet.net/{to}</a>.</p></body></html>
"""


def build() -> list[Path]:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    pages = {p.name: p.read_text(encoding="utf-8") for p in sorted(PAGES.glob("*.html"))}
    pages["planetpdf-release-notes.html"] = release_notes()
    pages["eleonora-release-notes.html"] = eleonora_release_notes()
    out = []
    for name, fragment in pages.items():
        path = DIST / name
        path.write_text(render(fragment), encoding="utf-8", newline="\n")
        out.append(path)
    for name, to in MOVED.items():
        (DIST / name).write_text(MOVED_PAGE.format(to=to), encoding="utf-8", newline="\n")
        out.append(DIST / name)
    shutil.copy2(HERE / "style.css", DIST / "style.css")
    out.append(DIST / "style.css")
    shutil.copytree(HERE / "fonts", DIST / "fonts")  # (Barlow, OFL: served by the site itself)
    out += sorted((DIST / "fonts").iterdir())
    return out


if __name__ == "__main__":
    files = build()
    for n, path in enumerate(files, 1):
        print(f"Built file {n} of {len(files)}: {path.name}")
    print(f"PlanetPDF {planetpdf_version()} (Windows {WINDOWS_VERSION}, Mac {MAC_VERSION}, Linux {LINUX_VERSION}); site in {DIST}")
