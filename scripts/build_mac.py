"""Build the Mac app, its disk image and its update (Mac app, 2026-10-10).

    .venv/bin/python scripts/build_mac.py               # everything
    .venv/bin/python scripts/build_mac.py --no-update   # without the signed update files

Steps: PyInstaller (PlanetPDF.app, Apple Silicon) -> Ghostscript into the app and the scanner
helper (mac/planetpdf-scan.swift, compiled with swiftc) -> code signing -> the built app's own
self-test -> notarizing -> the disk image (PlanetPDF-<version>.dmg: drag PlanetPDF into
Applications) -> the signed update for the update server (latest-macos.json).

**Runs on macOS 15 and later, whatever this Mac runs** (2026-11-05: 0.1.14 was built from
Homebrew's Python and Ghostscript, which Homebrew builds for the Mac they're installed on: the
app needed macOS 26.0, its PDF/A 26.6.2, and crashed at start on macOS 26.2, where the system's
XML library lacks what Homebrew's Python linked against). So nothing in the app comes from
Homebrew any more:

- **Python** = python-build-standalone (``PYTHON_*``, made to be shipped inside apps: built for
  macOS 11, its own expat, OpenSSL, SQLite), downloaded into build/mac-python with its checksum
  checked, and a build environment build/mac-venv from requirements-dev.txt. The script starts
  itself again in that environment (whichever Python it was started with).
- **Ghostscript** is compiled from its source (``GS_*``, the same file as the Linux build) with
  ``MACOSX_DEPLOYMENT_TARGET`` = ``MIN_MACOS``, Homebrew kept off the PATH so it uses its own
  libraries (it needs only libSystem and libiconv), without Tesseract (PlanetPDF reads text
  with MuPDF's own Tesseract).
- **Every piece of code in the app is checked** (``check_minimum``, before signing): none may
  need a macOS newer than ``MIN_MACOS`` or load anything from Homebrew. 15.0 is what PySide6's
  own modules need; Info.plist's LSMinimumSystemVersion says the same.

**Signing:** with a "Developer ID Application" certificate in the keychain the app is signed
with it (hardened runtime, packaging/entitlements.plist) and, with a notarytool keychain
profile (``--notary-profile``, default "PlanetNotary", shared with Planet Networks' other Mac
programs; a Mac that still has only the old "PlanetPDF" profile uses that and says so; made once
with ``xcrun notarytool store-credentials PlanetNotary``), notarized by Apple and stapled: it
opens on any Mac without warnings.
Without a certificate it's signed "ad hoc": it runs on this Mac; elsewhere macOS blocks
the first open until it's allowed in System Settings ▸ Privacy & Security.

Work files go to build/mac/; the results to dist/: PlanetPDF-<version>.dmg and update-site/.
Needs: Xcode's command-line tools (clang, swiftc), Homebrew's tesseract for its language
files (tessdata: data only, no code), internet for the first build, and for the update files the update-signing private key (PLANETPDF_SIGNING_KEY or
scripts/release_update.py's usual places).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build" / "mac"
DIST = ROOT / "dist"
APP = DIST / "PlanetPDF.app"
ENTITLEMENTS = ROOT / "packaging" / "entitlements.plist"
BREW = Path(os.environ.get("HOMEBREW_PREFIX", "/opt/homebrew"))
STEPS = 7
MACHO_MAGIC = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xcf"}
MIN_MACOS = "15.0"
PYTHON_RELEASE = "20261020"
PYTHON_FILE = "cpython-3.14.8+20261020-aarch64-apple-darwin-install_only.tar.gz"
PYTHON_SHA256 = "54eafeb1703dd5d150d45d07bbccb650fab4d2ee75e6928862597a8676cf9105"
PYTHON_HOME = ROOT / "build" / "mac-python"
BUILD_VENV = ROOT / "build" / "mac-venv"
GS_VERSION = "10.08.0"
GS_SHA256 = "c20492bc8ebb96c87fa2e52a0926e1cda8cde95d66145e018ac713fed5da38cf"
GS_WORK = ROOT / "build" / "mac-gs"
# configure / make see only the system's tools: Homebrew's libraries must not be picked up
CLEAN_ENV = {"HOME": os.environ.get("HOME", ""), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
             "MACOSX_DEPLOYMENT_TARGET": MIN_MACOS, "LANG": "C"}


def step(n: int, text: str) -> None:
    print(f"\n=== Step {n} of {STEPS}: {text} ===", flush=True)


def run(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in args], check=True, **kw)


def version() -> str:
    text = (ROOT / "src" / "planetpdf" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def download(url: str, target: Path, sha256: str) -> None:
    """Download once; the checksum is checked every time it's used."""
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        run("curl", "-fsSL", "-o", target.with_suffix(".part"), url)
        target.with_suffix(".part").rename(target)
    if hashlib.sha256(target.read_bytes()).hexdigest() != sha256:
        target.unlink()
        sys.exit(f"{target.name} didn't match its checksum: removed, try again.")


# --- the build's own Python ------------------------------------------------------------------------

def build_python() -> Path:
    """build/mac-venv's Python (made from python-build-standalone when missing, its packages
    installed again whenever requirements*.txt change)."""
    python = BUILD_VENV / "bin" / "python"
    if not (PYTHON_HOME / "bin" / "python3").exists():
        archive = ROOT / "build" / "downloads" / PYTHON_FILE
        download("https://github.com/astral-sh/python-build-standalone/releases/download/"
                 f"{PYTHON_RELEASE}/{PYTHON_FILE.replace('+', '%2B')}", archive, PYTHON_SHA256)
        unpack = ROOT / "build" / "mac-python-unpack"
        shutil.rmtree(unpack, ignore_errors=True)
        unpack.mkdir(parents=True)
        run("tar", "-xzf", archive, "-C", unpack)
        shutil.rmtree(PYTHON_HOME, ignore_errors=True)
        (unpack / "python").rename(PYTHON_HOME)
        shutil.rmtree(unpack)
        shutil.rmtree(BUILD_VENV, ignore_errors=True)
    if not python.exists():
        run(PYTHON_HOME / "bin" / "python3", "-m", "venv", BUILD_VENV)
    wanted = hashlib.sha256(b"".join((ROOT / n).read_bytes() for n in ("requirements.txt", "requirements-dev.txt")))
    marker = BUILD_VENV / "requirements.sha256"
    if not marker.is_file() or marker.read_text().strip() != wanted.hexdigest():
        print("Installing the packages into the build environment (build/mac-venv)…", flush=True)
        run(python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--require-hashes", "-r",
            ROOT / "requirements-dev.txt")  # (exactly the files pinned: scripts/hash_requirements.py)
        marker.write_text(wanted.hexdigest() + "\n")
    return python


# --- Ghostscript ---------------------------------------------------------------------------------

def build_ghostscript() -> Path:
    """Ghostscript compiled for MIN_MACOS (cached in build/mac-gs); returns its install folder."""
    install = GS_WORK / f"install-{GS_VERSION}-{MIN_MACOS}"
    if (install / "bin" / "gs").is_file():
        return install
    archive = ROOT / "build" / "downloads" / f"ghostscript-{GS_VERSION}.tar.xz"
    download("https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/download/"
             f"gs{GS_VERSION.replace('.', '')}/ghostscript-{GS_VERSION}.tar.xz", archive, GS_SHA256)
    source = GS_WORK / f"ghostscript-{GS_VERSION}"
    shutil.rmtree(source, ignore_errors=True)
    source.mkdir(parents=True)
    run("tar", "-xJf", archive, "-C", source, "--strip-components=1")
    print(f"Compiling Ghostscript {GS_VERSION} for macOS {MIN_MACOS} (a few minutes)…", flush=True)
    log = GS_WORK / "build.log"
    with open(log, "w") as out:
        try:
            run("./configure", f"--prefix={install}", "--without-x", "--disable-cups", "--disable-dbus",
                "--disable-gtk", "--disable-fontconfig", "--without-libidn", "--without-libpaper",
                "--without-tesseract", "--without-pdftoraster", cwd=source, env=CLEAN_ENV, stdout=out, stderr=out)
            run("make", f"-j{os.cpu_count() or 4}", cwd=source, env=CLEAN_ENV, stdout=out, stderr=out)
            run("make", "install", cwd=source, env=CLEAN_ENV, stdout=out, stderr=out)
        except subprocess.CalledProcessError:
            shutil.rmtree(install, ignore_errors=True)
            sys.exit(f"Compiling Ghostscript failed: see {log}")
    home = install / "share" / "ghostscript" / GS_VERSION
    for folder in ("iccprofiles", "Resource"):  # (PlanetPDF hands Ghostscript the profiles by path)
        shutil.copytree(source / folder, home / folder, dirs_exist_ok=True)
    (install / "licenses").mkdir(exist_ok=True)
    for name in ("LICENSE", "doc/COPYING"):
        shutil.copy2(source / name, install / "licenses" / Path(name).name)
    shutil.rmtree(source)
    return install


def _deps(binary: Path) -> list[str]:
    out = run("otool", "-L", binary, capture_output=True, text=True).stdout.splitlines()[1:]
    return [line.strip().split(" (", 1)[0] for line in out if line.strip()]


def bundle_ghostscript(app: Path, install: Path) -> int:
    """gs, its data (share/ghostscript: lib, Resource, iccprofiles) and its licenses into
    Contents/Resources/ghostscript. Returns how many files."""
    dest = app / "Contents" / "Resources" / "ghostscript"
    shutil.rmtree(dest, ignore_errors=True)
    (dest / "bin").mkdir(parents=True)
    shutil.copy2(install / "bin" / "gs", dest / "bin" / "gs")
    shutil.copytree(install / "share", dest / "share")
    shutil.copytree(install / "licenses", dest / "licenses")
    return sum(1 for f in dest.rglob("*") if f.is_file())


def _min_macos(binary: Path) -> str | None:
    out = run("vtool", "-show-build", binary, capture_output=True, text=True).stdout
    match = re.search(r"minos (\S+)", out)
    return match.group(1) if match else None


def _version_key(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in text.split("."))


def check_minimum(app: Path) -> int:
    """Every piece of code in the app runs on MIN_MACOS and loads nothing from Homebrew (or
    another non-system place outside the app). Returns how many were checked."""
    code = [p for p in (app / "Contents").rglob("*") if p.is_file() and not p.is_symlink() and _is_macho(p)]
    problems = []
    for n, path in enumerate(code, 1):
        print(f"\rChecking file {n} of {len(code)}", end="", flush=True)
        name = path.relative_to(app)
        minimum = _min_macos(path)
        if minimum and _version_key(minimum) > _version_key(MIN_MACOS):
            problems.append(f"{name} needs macOS {minimum}")
        for dep in _deps(path):
            if dep.startswith("/") and not dep.startswith(("/usr/lib/", "/System/")):
                problems.append(f"{name} loads {dep}")
    print()
    if problems:
        sys.exit(f"The app wouldn't run on macOS {MIN_MACOS}:\n  " + "\n  ".join(problems))
    return len(code)


# --- signing ---------------------------------------------------------------------------------------

def developer_id() -> tuple[str, str] | None:
    """(SHA-1 fingerprint, name) of the keychain's Developer ID Application certificate. Signing
    by fingerprint: the same certificate in two keychains makes its name ambiguous to codesign."""
    out = run("security", "find-identity", "-v", "-p", "codesigning", capture_output=True, text=True).stdout
    match = re.search(r'([0-9A-F]{40}) "(Developer ID Application: [^"]+)"', out)
    return (match.group(1), match.group(2)) if match else None


def _is_macho(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) in MACHO_MAGIC
    except OSError:
        return False


def sign(app: Path, identity: str | None) -> None:
    """Every piece of code inside first (deepest first), frameworks, then the app."""
    base = ["codesign", "--force", "--sign", identity or "-"]
    if identity:
        base += ["--options", "runtime", "--timestamp"]
    contents = app / "Contents"
    main = contents / "MacOS" / "PlanetPDF"
    code = [p for p in contents.rglob("*") if p.is_file() and not p.is_symlink() and p != main and _is_macho(p)]
    code.sort(key=lambda p: len(p.parts), reverse=True)
    for n, path in enumerate(code, 1):
        extra = ["--entitlements", ENTITLEMENTS] if identity and os.access(path, os.X_OK) and path.suffix == "" else []
        run(*base, *extra, path, capture_output=True)
        print(f"\rSigning file {n} of {len(code)}", end="", flush=True)
    print()
    frameworks = sorted((p for p in contents.rglob("*.framework") if p.is_dir() and not p.is_symlink()),
                        key=lambda p: len(p.parts), reverse=True)
    for framework in frameworks:
        run(*base, framework, capture_output=True)
    run(*base, "--entitlements", ENTITLEMENTS, app, capture_output=True)
    run("codesign", "--verify", "--deep", "--strict", app)


def notarize(path: Path, profile: str) -> None:
    """Send to Apple's notary service and wait; then staple the ticket on."""
    target = path
    if path.suffix == ".app":
        target = Path(tempfile.mkdtemp()) / f"{path.stem}.zip"
        run("ditto", "-c", "-k", "--keepParent", path, target)
    run("xcrun", "notarytool", "submit", target, "--keychain-profile", profile, "--wait")
    run("xcrun", "stapler", "staple", path)


def has_notary_profile(profile: str) -> bool:
    done = subprocess.run(["xcrun", "notarytool", "history", "--keychain-profile", profile],
                          capture_output=True, text=True)
    return done.returncode == 0


# One notarytool profile for every Planet Networks Mac program (user 2026-11-21: the name is only a
# label in this Mac's Keychain, so a generic one); the old per-program name still works until each
# Mac has the new one.
NOTARY_PROFILE = "PlanetNotary"
OLD_NOTARY_PROFILE = "PlanetPDF"


def notary_profile(chosen: str | None) -> str:
    """The profile to notarize with: the one asked for exactly, else PlanetNotary, else the old one."""
    if chosen:
        return chosen
    if not has_notary_profile(NOTARY_PROFILE) and has_notary_profile(OLD_NOTARY_PROFILE):
        print(f"No notarytool profile “{NOTARY_PROFILE}” on this Mac: using “{OLD_NOTARY_PROFILE}”")
        return OLD_NOTARY_PROFILE
    return NOTARY_PROFILE


# --- the disk image --------------------------------------------------------------------------------

# Finder's "has a custom icon" flag (kHasCustomIcon in the FinderInfo's flags)
CUSTOM_ICON_FINDER_INFO = "00" * 8 + "0400" + "00" * 22


def app_icon(app: Path) -> Path:
    import plistlib

    with open(app / "Contents" / "Info.plist", "rb") as f:
        name = plistlib.load(f)["CFBundleIconFile"]
    return app / "Contents" / "Resources" / (name if name.endswith(".icns") else name + ".icns")


def make_dmg(app: Path, target: Path) -> None:
    """The disk image: the app and an Applications link; the mounted disk shows PlanetPDF's
    icon (.VolumeIcon.icns + Finder's custom-icon flag on the disk), set on a writable image
    that is then compressed."""
    stage = WORK / "dmg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    run("ditto", app, stage / app.name)
    (stage / "Applications").symlink_to("/Applications")
    writable = WORK / "dmg-rw.dmg"
    mount = WORK / "dmg-mount"
    writable.unlink(missing_ok=True)
    run("hdiutil", "create", "-volname", "PlanetPDF", "-srcfolder", stage, "-fs", "HFS+", "-format", "UDRW",
        "-ov", writable, capture_output=True)
    mount.mkdir(parents=True, exist_ok=True)
    run("hdiutil", "attach", writable, "-nobrowse", "-noautoopen", "-mountpoint", mount, capture_output=True)
    try:
        shutil.copy2(app_icon(app), mount / ".VolumeIcon.icns")
        run("xattr", "-wx", "com.apple.FinderInfo", CUSTOM_ICON_FINDER_INFO, mount)
    finally:
        run("hdiutil", "detach", mount, "-quiet")
    target.unlink(missing_ok=True)
    run("hdiutil", "convert", writable, "-format", "UDZO", "-imagekey", "zlib-level=9", "-o", target,
        capture_output=True)
    writable.unlink()


def set_file_icon(path: Path, icns: Path) -> bool:
    """Give the file in Finder the app's icon (NSWorkspace setIcon:forFile:options:). It's kept
    in the file's extended attributes: copying between Macs keeps it, a web download doesn't."""
    import ctypes
    import ctypes.util

    ctypes.cdll.LoadLibrary(ctypes.util.find_library("AppKit"))
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value

    def send(restype, *argtypes):
        return ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(address)

    def sel(name: str) -> int:
        return objc.sel_registerName(name.encode())

    def nsstring(text: str) -> int:
        return send(ctypes.c_void_p, ctypes.c_char_p)(objc.objc_getClass(b"NSString"), sel("stringWithUTF8String:"),
                                                       text.encode())

    image = send(ctypes.c_void_p)(objc.objc_getClass(b"NSImage"), sel("alloc"))
    image = send(ctypes.c_void_p, ctypes.c_void_p)(image, sel("initWithContentsOfFile:"), nsstring(str(icns)))
    workspace = send(ctypes.c_void_p)(objc.objc_getClass(b"NSWorkspace"), sel("sharedWorkspace"))
    return bool(image) and send(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(
        workspace, sel("setIcon:forFile:options:"), image, nsstring(str(path)), 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-update", action="store_true", help="don't make the signed update files")
    parser.add_argument("--identity", help="signing certificate (default: the keychain's Developer ID Application)")
    parser.add_argument("--notary-profile", help=f"notarytool keychain profile (default {NOTARY_PROFILE})")
    parser.add_argument("--notes", default="", help="what's new (shown when the update is offered)")
    args = parser.parse_args()
    if sys.platform != "darwin":
        sys.exit("Build the Mac app on a Mac.")
    python = build_python()
    if Path(sys.prefix).resolve() != BUILD_VENV.resolve():  # (start again in the build environment)
        os.execv(python, [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])
    tessdata = BREW / "share" / "tessdata"
    if not (tessdata / "eng.traineddata").is_file():
        sys.exit("Tesseract's language files aren't installed (brew install tesseract).")
    v = version()
    print(f"Building PlanetPDF {v} for Mac (Apple Silicon, macOS {MIN_MACOS} and later)")
    ghostscript = build_ghostscript()

    step(1, "PyInstaller (PlanetPDF.app)")
    shutil.rmtree(APP, ignore_errors=True)
    env = {**os.environ, "PLANETPDF_TESSDATA": str(tessdata), "PLANETPDF_MIN_MACOS": MIN_MACOS}
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", DIST, "--workpath", WORK / "work",
        ROOT / "packaging" / "planetpdf.spec", env=env)
    shutil.rmtree(DIST / "PlanetPDF", ignore_errors=True)  # (the folder BUNDLE was made from)

    step(2, "Ghostscript and the scanner helper into the app")
    print(f"Ghostscript copied ({bundle_ghostscript(APP, ghostscript)} files)")
    helper = APP / "Contents" / "MacOS" / "planetpdf-scan"  # (ImageCaptureCore: USB and network scanners)
    run("swiftc", "-O", "-swift-version", "5", "-target", f"arm64-apple-macos{MIN_MACOS}", "-o", helper,
        ROOT / "mac" / "planetpdf-scan.swift", "-framework", "ImageCaptureCore")
    print(f"Scanner helper built ({helper.stat().st_size / 1e3:.0f} KB)")
    print(f"Every one of {check_minimum(APP)} code files runs on macOS {MIN_MACOS}")

    step(3, "Code signing")
    found = None if args.identity else developer_id()
    identity = args.identity or (found[0] if found else None)
    print(f"Signing with {found[1] if found else identity}" if identity else
          "No Developer ID certificate: signing ad hoc (runs here; other Macs ask to allow it once)")
    sign(APP, identity)

    step(4, "The built app's self-test")
    report = WORK / "self-test.txt"
    code = subprocess.run([APP / "Contents" / "MacOS" / "PlanetPDF", "--self-test", report]).returncode
    print(report.read_text(encoding="utf-8") if report.is_file() else "(no report)")
    if code != 0:
        sys.exit("The self-test failed: see above.")

    step(5, "Notarizing")
    notarized = False
    args.notary_profile = notary_profile(args.notary_profile)
    if identity and has_notary_profile(args.notary_profile):
        notarize(APP, args.notary_profile)
        notarized = True
    else:
        print("Skipped: " + ("no Developer ID certificate" if not identity else
                             f"no notarytool profile “{args.notary_profile}” (xcrun notarytool store-credentials)"))

    step(6, "The disk image")
    dmg = DIST / f"PlanetPDF-{v}.dmg"
    make_dmg(APP, dmg)
    if identity:
        run("codesign", "--force", "--sign", identity, "--timestamp", dmg)
        if notarized:
            notarize(dmg, args.notary_profile)
    set_file_icon(dmg, app_icon(APP))  # (after signing and stapling: it's outside the signed data)
    print(f"{dmg} ({dmg.stat().st_size / 1e6:.0f} MB)")

    step(7, "The signed update")
    if args.no_update:
        print("Skipped (--no-update)")
    else:
        run(sys.executable, ROOT / "scripts" / "release_update.py", "publish", "--build", APP,
            "--out", DIST / "update-site", "--notes", args.notes)
    print(f"\nDone: PlanetPDF {v}" + ("" if notarized else " (not notarized)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
