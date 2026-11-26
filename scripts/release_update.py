"""Publish a PlanetPDF update (core/update.py).

Once, make the signing key (the private key is kept beside the project, in
..\\PlanetPDF-Key\\planetpdf-release\\, where Dropbox syncs it to your other computers; never
inside the project, never on the server; keep a backup: losing it means installed copies
can't be updated without reinstalling, and anyone who gets it can sign updates):

    .venv\\Scripts\\python.exe scripts\\release_update.py keygen

It writes the public half to src/planetpdf/resources/update/update_key.pub, which goes
into every build. For each release (after building the one-folder program, with the new
__version__ in src/planetpdf/__init__.py):

    .venv\\Scripts\\python.exe scripts\\release_update.py publish --build dist\\PlanetPDF --notes "What's new"

That makes update-site/: PlanetPDF-<version>.zip, latest.json and latest.json.sig. Upload
all three to the update server's folder (the zip first, latest.json + .sig last).

The Mac app (2026-10-10; scripts/build_mac.sh does this): point --build at PlanetPDF.app (or
the folder holding it). That makes PlanetPDF-<version>-macos.zip (packed with ditto, which
keeps the app's links and signature), latest-macos.json and latest-macos.json.sig, in the
same server folder as the Windows files:

    .venv/bin/python scripts/release_update.py publish --build dist/PlanetPDF.app --notes "What's new"

Linux (2026-10-27; scripts/build_linux.py does this): point --build at the AppImage, .deb or
.rpm. It goes up as it is, with latest-linux-<x86_64|arm64>-<appimage|deb|rpm>.json (+ .sig):
each installed copy updates itself with its own kind.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from planetpdf import APP_NAME, __version__  # noqa: E402
from planetpdf.core import update as U  # noqa: E402

# The private key syncs with Dropbox (user 2026-10-07: "make the update-signing private key sync
# too"; nobody else can open the Dev folder): beside the project, never inside it (the project
# may become a Git repository). Then the old per-computer place; PLANETPDF_SIGNING_KEY overrides.
# The folder has no leading dot (user 2026-10-10): on a Mac a dot folder is hidden and
# Dropbox wouldn't sync it. The Mac clone isn't beside Dropbox's Dev folder, so Dropbox's own
# folder on a Mac is looked in too; the dotted folders are still read (older copies).
KEY_FOLDER = "planetpdf-release"
KEY_FILE = "update_signing_key.pem"
sys.path.insert(0, str(ROOT / "scripts"))
from dropbox_dev import dev_path  # noqa: E402  (the project is outside Dropbox: 2026-10-14)

SYNCED_KEY = dev_path("PlanetPDF-Key", KEY_FOLDER, KEY_FILE)
HOME_KEY = Path.home() / KEY_FOLDER / KEY_FILE
OLD_KEYS = [dev_path("PlanetPDF-Key", f".{KEY_FOLDER}", KEY_FILE), Path.home() / f".{KEY_FOLDER}" / KEY_FILE]


def default_key() -> Path:
    import os

    if os.environ.get("PLANETPDF_SIGNING_KEY"):
        return Path(os.environ["PLANETPDF_SIGNING_KEY"])
    return next((p for p in (SYNCED_KEY, HOME_KEY, *OLD_KEYS) if p.is_file()), SYNCED_KEY)


DEFAULT_KEY = default_key()
PUBLIC_KEY = ROOT / "src" / "planetpdf" / "resources" / "update" / "update_key.pub"


def _outside_project(path: Path) -> Path:
    path = path.resolve()
    if path.is_relative_to(ROOT):
        sys.exit(f"Keep the private key outside the project folder (it syncs to Dropbox): {path}")
    return path


def load_key(path: Path):
    """The signing key; a passphrase-protected one asks for its passphrase (typed by whoever
    releases: never stored, never on the command line). Security review 2026-11-25 (finding 9):
    whether the key gets a passphrase is Robert's decision; ``protect-key`` adds one."""
    data = _outside_project(path).read_bytes()
    try:
        return serialization.load_pem_private_key(data, password=None)
    except TypeError:  # (encrypted)
        import getpass

        passphrase = getpass.getpass(f"Passphrase for the update-signing key ({path.name}): ").encode("utf-8")
        try:
            return serialization.load_pem_private_key(data, password=passphrase)
        except ValueError:
            sys.exit("That passphrase doesn't open the update-signing key.")


def protect_key(args) -> None:
    """Puts a passphrase on the signing key (the same key: copies already installed keep accepting
    its updates). The passphrase is asked for twice and never stored; the unprotected file is
    replaced. Back up the protected file, and keep the passphrase somewhere safe: without it, no
    update can be signed."""
    import getpass

    path = _outside_project(Path(args.private_key))
    key = load_key(path)
    first = getpass.getpass("New passphrase (at least 12 characters): ")
    if len(first) < 12 or getpass.getpass("The same passphrase again: ") != first:
        sys.exit("The passphrases were too short or didn't match: nothing changed.")
    protected = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.BestAvailableEncryption(first.encode("utf-8")))
    tmp = path.with_suffix(path.suffix + ".new")
    tmp.write_bytes(protected)
    tmp.replace(path)
    print(f"{path} now needs its passphrase. Copies in other places (backups) are still unprotected: "
          "replace or delete them.")


def keygen(args) -> None:
    path = _outside_project(Path(args.private_key))
    if path.exists() and not args.replace:
        sys.exit(f"{path} exists already. Copies built with its public key only accept updates signed by it; "
                 "use --replace only if you really mean to start over.")
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    PUBLIC_KEY.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_KEY.write_text(base64.b64encode(raw).decode("ascii") + "\n", encoding="ascii")
    print(f"Private key: {path}  (back it up; never share it)\nPublic key:  {PUBLIC_KEY}")


def publish(args) -> None:
    build = Path(args.build)
    if build.suffix != ".app" and (build / U.APP_BUNDLE).is_dir():
        build = build / U.APP_BUNDLE
    platform = "macos" if build.suffix == ".app" else "windows"
    if build.suffix.lower() in (".appimage", ".deb", ".rpm"):
        processor, kind = U.linux_target(build)
        platform = f"{processor}-{kind}"
    if platform == "macos" and not (build / "Contents" / "MacOS" / APP_NAME).is_file():
        sys.exit(f"{build} has no Contents/MacOS/{APP_NAME}: point --build at the built PlanetPDF.app.")
    if platform.startswith("linux") and not build.is_file():
        sys.exit(f"{build} isn't there: point --build at the built AppImage, .deb or .rpm.")
    if platform == "windows" and not (build / "PlanetPDF.exe").is_file():
        sys.exit(f"{build} has no PlanetPDF.exe: point --build at the one-folder build.")
    key = load_key(Path(args.private_key))
    public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                            serialization.PublicFormat.Raw)).decode("ascii")
    if PUBLIC_KEY.read_text(encoding="ascii").strip() != public:
        sys.exit("This private key doesn't match the public key in the program: installed copies would refuse it.")
    version = args.version or __version__
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = f"{APP_NAME}-{version}-macos.zip" if platform == "macos" else f"{APP_NAME}-{version}.zip"
    if platform.startswith("linux"):
        name = build.name
    zip_path = out / name
    if platform.startswith("linux"):
        import shutil

        shutil.copy2(build, zip_path)
    elif platform == "macos":
        print("Packing the app with ditto")
        zip_path.unlink(missing_ok=True)
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(build), str(zip_path)], check=True)
    else:
        files = sorted(p for p in build.rglob("*") if p.is_file())
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for n, path in enumerate(files, 1):
                z.write(path, path.relative_to(build).as_posix())
                print(f"\rPacking file {n} of {len(files)}", end="")
        print()
    data = zip_path.read_bytes()
    manifest = json.dumps({"app": APP_NAME, "platform": platform, "version": version, "file": name,
                           "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "notes": args.notes,
                           "published": datetime.date.today().isoformat()}, indent=2).encode("utf-8")
    manifest_file = U.manifest_name(platform)
    (out / manifest_file).write_bytes(manifest)
    (out / (manifest_file + ".sig")).write_bytes(U.sign(manifest, key))
    if platform == U.PLATFORM:  # (as the program will)
        U.verify(manifest, (out / (manifest_file + ".sig")).read_bytes(), U.public_key(PUBLIC_KEY))
    print(f"Ready to upload from {out}: {name} ({len(data) / 1e6:.1f} MB), {manifest_file}, {manifest_file}.sig")


def voices(args) -> None:
    """Read Out Loud's natural voices for downloading on first use (2026-10-14):
    update-site/voices/voices.json (+ .sig) and the Kokoro files beside it."""
    import shutil

    from planetpdf.core import voice_download

    key = load_key(Path(args.private_key))
    manifest, signature, files = voice_download.make_manifest(Path(args.models), key)
    out = Path(args.out) / voice_download.FOLDER
    for path in files:
        target = out / path.relative_to(Path(args.models))
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.stat().st_size != path.stat().st_size:
            shutil.copy2(path, target)
    (out / voice_download.MANIFEST).write_bytes(manifest)
    (out / voice_download.SIGNATURE).write_bytes(signature)
    voice_download.read_manifest(manifest, signature, U.public_key(PUBLIC_KEY))  # (as the program will)
    total = sum(p.stat().st_size for p in files)
    print(f"Ready to upload from {out}: {len(files)} voice files ({total / 1e6:.0f} MB), "
          f"{voice_download.MANIFEST}, {voice_download.SIGNATURE}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    k = sub.add_parser("keygen", help="make the signing key (once)")
    k.add_argument("--private-key", default=str(DEFAULT_KEY))
    k.add_argument("--replace", action="store_true")
    p = sub.add_parser("publish", help="sign a build for the update server")
    p.add_argument("--build", required=True, help="the one-folder build (holds PlanetPDF.exe), PlanetPDF.app, "
                                                     "or the Linux AppImage, .deb or .rpm")
    p.add_argument("--version", help=f"default: __version__ ({__version__})")
    p.add_argument("--notes", default="", help="what's new (shown when the update is offered)")
    p.add_argument("--out", default=str(ROOT / "update-site"))
    p.add_argument("--private-key", default=str(DEFAULT_KEY))
    v = sub.add_parser("voices", help="sign Read Out Loud's natural voices for downloading on first use")
    v.add_argument("--models", default=str(ROOT / "models"), help="the models folder (holds kokoro/)")
    v.add_argument("--out", default=str(ROOT / "dist" / "update-site"))
    v.add_argument("--private-key", default=str(DEFAULT_KEY))
    k2 = sub.add_parser("protect-key", help="put a passphrase on the signing key (asked for at each release)")
    k2.add_argument("--private-key", default=str(DEFAULT_KEY))
    args = parser.parse_args()
    {"keygen": keygen, "publish": publish, "voices": voices, "protect-key": protect_key}[args.command](args)


if __name__ == "__main__":
    main()
