"""PlanetPDF's apt and dnf repositories (2026-10-27; user: "Both", "Add automatically").

    .venv/bin/python scripts/linux_repo.py keygen    # once: the repositories' signing key
    .venv/bin/python scripts/linux_repo.py build     # after build_linux.py: dist/linux-repo/
    .venv/bin/python scripts/upload_update.py --linux-repo   # when the user says: -> /linux/

**The key** (GPG, RSA 4096, no expiry, no passphrase like the update key): the private half is
kept beside the update-signing key in Dropbox (``Dev/PlanetPDF-Key/planetpdf-release/
linux-repo-key.asc``), never in the project; the public half is ``packaging/linux/
planetpdf-linux.asc``, which every package carries (``/usr/share/keyrings/planetpdf-archive-keyring.gpg``
for apt, ``/usr/share/pki/rpm-gpg/RPM-GPG-KEY-planetpdf`` for dnf / zypper). build_linux.py signs
each .rpm with it when the key is there.

**The repositories** (served from https://software.planet.net/linux/, the current version only)::

    planetpdf-linux.asc                              the public key
    apt/pool/main/planetpdf_<v>_<amd64|arm64>.deb
    apt/dists/stable/{InRelease,Release,Release.gpg}
    apt/dists/stable/main/binary-<amd64|arm64>/Packages(.gz)
    rpm/<x86_64|aarch64>/planetpdf-<v>-1.<arch>.rpm  (signed)
    rpm/<x86_64|aarch64>/repodata/…  (repomd.xml signed: repomd.xml.asc)

Installing the .deb / .rpm the first time adds the repository (``build_linux.py``'s package
scripts), so the system's updater offers new versions; removing the package removes it.
Everything here runs inside the build image (``--docker`` is implied from the host).
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
PUBLIC_KEY = ROOT / "packaging" / "linux" / "planetpdf-linux.asc"
KEY_NAME = "linux-repo-key.asc"
KEY_UID = "PlanetPDF Linux packages <software@planet.net>"
IMAGE = "planetpdf-linux-build:arm64"  # (any processor's image will do: nothing here is compiled)
REPO_URL = "https://software.planet.net/linux"
DEB_ARCHES = ("amd64", "arm64")
RPM_ARCHES = ("x86_64", "aarch64")
IN_IMAGE_KEY = Path("/key") / KEY_NAME


def private_key() -> Path:
    """Beside the update-signing key in Dropbox (``PLANETPDF_LINUX_REPO_KEY`` overrides)."""
    if os.environ.get("PLANETPDF_LINUX_REPO_KEY"):
        return Path(os.environ["PLANETPDF_LINUX_REPO_KEY"])
    from dropbox_dev import dev_path

    return dev_path("PlanetPDF-Key", "planetpdf-release", KEY_NAME)


def run(*args, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in args], check=True, **kw)


def version() -> str:
    text = (ROOT / "src" / "planetpdf" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


# --- gpg inside the image ------------------------------------------------------------------------

class Gpg:
    """A throwaway GnuPG home holding the private key (imported from the mounted key file)."""

    def __init__(self, key_file: Path) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="planetpdf-gpg-"))
        self.home.chmod(0o700)
        self("--import", key_file)
        listing = self("--list-secret-keys", "--with-colons", capture=True)
        self.fingerprint = re.search(r"^fpr:+([0-9A-F]{40}):", listing, re.M).group(1)

    def __call__(self, *args, capture: bool = False) -> str:
        done = run("gpg", "--homedir", self.home, "--batch", "--yes", "--pinentry-mode", "loopback",
                   "--passphrase", "", *args, capture_output=True, text=True)
        return done.stdout if capture else ""

    def close(self) -> None:
        shutil.rmtree(self.home, ignore_errors=True)


def sign_rpm(package: Path, key_file: Path = IN_IMAGE_KEY) -> None:
    gpg = Gpg(key_file)
    try:
        run("rpmsign", "--addsign", "--define", f"_gpg_name {gpg.fingerprint}", "--define", f"_gpg_path {gpg.home}",
            "--define", "__gpg /usr/bin/gpg", package, capture_output=True)
    finally:
        gpg.close()


def build_repo(packages: Path, out: Path, key_file: Path = IN_IMAGE_KEY) -> None:
    """dist/linux's packages of this version -> the apt and dnf repositories in ``out``."""
    v = version()
    out.mkdir(parents=True, exist_ok=True)
    for old in out.iterdir():  # (``out`` itself may be a mounted folder: emptied, not removed)
        shutil.rmtree(old) if old.is_dir() else old.unlink()
    gpg = Gpg(key_file)
    try:
        shutil.copy2(PUBLIC_KEY, out / "planetpdf-linux.asc")
        apt = out / "apt"
        pool = apt / "pool" / "main"
        pool.mkdir(parents=True)
        debs = [packages / f"planetpdf_{v}_{arch}.deb" for arch in DEB_ARCHES]
        rpms = [packages / f"planetpdf-{v}-1.{arch}.rpm" for arch in RPM_ARCHES]
        missing = [p.name for p in debs + rpms if not p.is_file()]
        if missing:
            sys.exit(f"Build both processors first (build_linux.py --docker --arch both): missing {', '.join(missing)}")
        for deb in debs:
            shutil.copy2(deb, pool / deb.name)
        dist = apt / "dists" / "stable"
        for n, arch in enumerate(DEB_ARCHES, 1):
            folder = dist / "main" / f"binary-{arch}"
            folder.mkdir(parents=True)
            listing = run("apt-ftparchive", "--arch", arch, "packages", "pool/main", cwd=apt,
                          capture_output=True, text=True).stdout
            (folder / "Packages").write_text(listing, encoding="utf-8")
            (folder / "Packages.gz").write_bytes(gzip.compress(listing.encode("utf-8"), 9))
            print(f"apt: index {n} of {len(DEB_ARCHES)} ({arch})")
        options = [part for k, val in (
            ("Origin", "PlanetPDF"), ("Label", "PlanetPDF"), ("Suite", "stable"), ("Codename", "stable"),
            ("Architectures", " ".join(DEB_ARCHES)), ("Components", "main"),
            ("Description", "PlanetPDF, the free PDF editor from Planet Networks"))
            for part in ("-o", f"APT::FTPArchive::Release::{k}={val}")]
        release = run("apt-ftparchive", *options, "release", "dists/stable", cwd=apt,
                      capture_output=True, text=True).stdout
        (dist / "Release").write_text(release, encoding="utf-8")
        gpg("--local-user", gpg.fingerprint, "--clearsign", "--output", dist / "InRelease", dist / "Release")
        gpg("--local-user", gpg.fingerprint, "--armor", "--detach-sign", "--output", dist / "Release.gpg",
            dist / "Release")
        print("apt: signed (InRelease, Release.gpg)")
        for n, (arch, rpm) in enumerate(zip(RPM_ARCHES, rpms), 1):
            folder = out / "rpm" / arch
            folder.mkdir(parents=True)
            shutil.copy2(rpm, folder / rpm.name)
            signed = run("rpm", "-qp", "--qf", "%{SIGPGP:pgpsig}%{RSAHEADER:pgpsig}", folder / rpm.name,
                         capture_output=True, text=True).stdout
            if "(none)(none)" in signed:
                sys.exit(f"{rpm.name} isn't signed: build it with the repository key there.")
            run("createrepo_c", "--quiet", folder)
            repomd = folder / "repodata" / "repomd.xml"
            gpg("--local-user", gpg.fingerprint, "--armor", "--detach-sign", "--output",
                repomd.with_name("repomd.xml.asc"), repomd)
            print(f"dnf: repository {n} of {len(RPM_ARCHES)} ({arch}), signed")
    finally:
        gpg.close()
    print(f"Repositories for PlanetPDF {v} in {out}")


def _quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


# --- on the host ---------------------------------------------------------------------------------

def keygen(args) -> None:
    secret = private_key()
    if secret.exists() and not args.replace:
        sys.exit(f"{secret} already exists. Every installed copy trusts it: never replace it lightly.")
    secret.parent.mkdir(parents=True, exist_ok=True)
    work = ROOT / "build" / "linux-repo-keygen"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    work.chmod(0o700)
    script = ("export GNUPGHOME=$(mktemp -d) && "
              f"gpg --batch --passphrase '' --quick-gen-key {_quote(KEY_UID)} rsa4096 sign never && "
              "gpg --batch --armor --export > /work/public.asc && "
              "gpg --batch --pinentry-mode loopback --passphrase '' --armor --export-secret-keys > /work/secret.asc && "
              "gpg --batch --with-colons --list-keys | grep '^fpr' | head -1 | cut -d: -f10 > /work/fingerprint")
    run("docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
        "-v", f"{work}:/work", IMAGE, "sh", "-c", script)
    shutil.move(work / "secret.asc", secret)
    secret.chmod(0o600)
    shutil.copy2(work / "public.asc", PUBLIC_KEY)
    fingerprint = (work / "fingerprint").read_text().strip()
    shutil.rmtree(work)
    print(f"Private key: {secret}  (back it up; never share it)\nPublic key:  {PUBLIC_KEY}\nFingerprint: {fingerprint}")


def build_on_host(args) -> None:
    secret = private_key()
    if not secret.is_file():
        sys.exit(f"No repository key at {secret}: run keygen first (once).")
    out = ROOT / "dist" / "linux-repo"
    out.mkdir(parents=True, exist_ok=True)
    run("docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
        "-v", f"{ROOT}:/src:ro", "-v", f"{ROOT / 'dist' / 'linux'}:/packages:ro", "-v", f"{out}:/out",
        "-v", f"{secret}:{IN_IMAGE_KEY}:ro", IMAGE, "python", "/src/scripts/linux_repo.py", "build-here")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    k = sub.add_parser("keygen", help="make the repositories' signing key (once)")
    k.add_argument("--replace", action="store_true")
    sub.add_parser("build", help="build dist/linux-repo/ from dist/linux/ (in Docker)")
    sub.add_parser("build-here", help=argparse.SUPPRESS)  # (inside the image)
    args = parser.parse_args()
    if args.command == "keygen":
        keygen(args)
    elif args.command == "build":
        build_on_host(args)
    else:
        build_repo(Path("/packages"), Path("/out"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
