"""Build PlanetPDF for Linux: the AppImage, the .deb and the .rpm (2026-10-26).

    .venv/bin/python scripts/build_linux.py --docker --arch arm64   # from a Mac or any Docker host
    .venv/bin/python scripts/build_linux.py --docker --arch x86_64
    python scripts/build_linux.py                                    # inside the build image itself

``--docker`` builds the build computer from packaging/linux/Dockerfile (Ubuntu 24.04 with
Python 3.14, Ghostscript and the pinned packages: the first time takes a while, then it's
cached) and runs this script inside it for that processor. On an Apple Silicon Mac, ARM64
runs at full speed; x86-64 runs through Rosetta (Colima started with ``--vz-rosetta``).
PyInstaller can't build for another kind of processor, so each one is built on its own.

Steps (inside the image): PyInstaller (the one-folder program) -> Ghostscript, PortAudio
(sound, ALSA only) and the X11 cursor libraries into it -> the built program's own self-test -> the AppImage -> the .deb ->
the .rpm. Results go to dist/linux/ (and, unless ``--no-update``, the signed update files for
each package to dist/update-site/, made on the host where the signing key is):

* ``PlanetPDF-<version>-<aarch64|x86_64>.AppImage``: runs on most distributions without
  installing; make it executable and start it.
* ``planetpdf_<version>_<arm64|amd64>.deb``: Ubuntu 24.04 / Debian 13 or newer
  (``sudo apt install ./planetpdf_….deb``).
* ``planetpdf-<version>-1.<aarch64|x86_64>.rpm``: Fedora 40 / openSUSE or newer
  (``sudo dnf install ./planetpdf-….rpm``).

The packages install into /opt/planetpdf with a ``planetpdf`` command, a menu entry and
"Open With" for PDF files. Each knows how it was installed (``package-kind``), and PlanetPDF
updates it that way: the AppImage replaces itself; a .deb / .rpm is installed with an
administrator's password (core/update.py).
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
DOCKERFILE = ROOT / "packaging" / "linux" / "Dockerfile"
IMAGE = "planetpdf-linux-build"
STEPS = 6
ARCHES = {  # our name -> (Docker, AppImage / rpm, deb)
    "arm64": ("linux/arm64", "aarch64", "arm64"),
    "x86_64": ("linux/amd64", "x86_64", "amd64"),
}
GHOSTSCRIPT = Path("/opt/gs")
RUNTIME = Path("/opt/appimage-runtime")
PORTAUDIO = Path("/opt/portaudio")
INSTALL_DIR = "/opt/planetpdf"
PUBLIC_KEY = ROOT / "packaging" / "linux" / "planetpdf-linux.asc"  # (the repositories' key: scripts/linux_repo.py)
REPO_KEY = Path("/key/linux-repo-key.asc")  # (its private half, mounted by --docker when this computer has it)
APT_KEYRING = "/usr/share/keyrings/planetpdf-archive-keyring.gpg"
RPM_KEY = "/usr/share/pki/rpm-gpg/RPM-GPG-KEY-planetpdf"
REPO_URL = "https://software.planet.net/linux"
KIND_FILE = "package-kind"  # (how this copy was installed: core/update.py updates each kind its own way)
# Qt's X11 window support needs libxcb-cursor (and these few it uses), which desktops often lack:
# bundled with the program
XCB_LIBS = ("libxcb-cursor.so.0", "libxcb-image.so.0", "libxcb-util.so.1", "libxcb-render-util.so.0",
            "libxcb-icccm.so.4", "libxcb-keysyms.so.1")
DEB_DEPENDS = ("libgl1, libegl1, libfontconfig1, libfreetype6, libxkbcommon0, libxkbcommon-x11-0, "
               "libdbus-1-3, libx11-xcb1, libxcb1, libxcb-randr0, libxcb-shape0, libxcb-xinerama0, "
               "libxcb-xkb1, libsm6, libice6, libcups2t64 | libcups2")
RPM_REQUIRES = ("libGL.so.1", "libEGL.so.1", "libfontconfig.so.1", "libfreetype.so.6", "libxkbcommon.so.0",
                "libxkbcommon-x11.so.0", "libdbus-1.so.3", "libX11-xcb.so.1", "libxcb.so.1", "libSM.so.6", "libICE.so.6")
SUMMARY = "PDF editor: view, fill and sign, comment, edit, redact, OCR, and convert"
DESCRIPTION = ("PlanetPDF views, edits, fills in, signs, comments on, redacts, recognizes, and converts PDF "
               "files. Free to use, with no ads and no data collection. From Planet Networks.")
HOMEPAGE = "https://software.planet.net/planetpdf.html"
DESKTOP = """[Desktop Entry]
Type=Application
Name=PlanetPDF
GenericName=PDF Editor
Comment=View, edit, fill in, sign, and convert PDF files
Exec={exec} %F
Icon=planetpdf
Terminal=false
Categories=Office;Graphics;Viewer;
MimeType=application/pdf;
StartupWMClass=PlanetPDF
"""


def step(n: int, text: str) -> None:
    print(f"\n=== Step {n} of {STEPS}: {text} ===", flush=True)


def run(*args, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in args], check=True, **kw)


def version() -> str:
    text = (ROOT / "src" / "planetpdf" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def this_arch() -> str:
    return "arm64" if platform.machine().lower() in ("aarch64", "arm64") else "x86_64"


# --- on the Docker host -----------------------------------------------------------------------------

def in_docker(arch: str, out: Path, extra: list[str]) -> None:
    """Make (or reuse) the build image for that processor, then run this script inside it with the
    project mounted read-only and the results written to ``out``."""
    docker_platform = ARCHES[arch][0]
    tag = f"{IMAGE}:{arch}"
    print(f"Preparing the {arch} build image (cached after the first time)…", flush=True)
    run("docker", "buildx", "build", "--platform", docker_platform, "--load", "-t", tag,
        "-f", DOCKERFILE, ROOT)
    out.mkdir(parents=True, exist_ok=True)
    user = f"{os.getuid()}:{os.getgid()}"
    from linux_repo import private_key

    key = private_key()
    mount_key = ["-v", f"{key}:{REPO_KEY}:ro"] if key.is_file() else []
    run("docker", "run", "--rm", "--platform", docker_platform, "--user", user, "-e", "HOME=/tmp",
        "-v", f"{ROOT}:/src:ro", "-v", f"{out}:/out", *mount_key, tag,
        "python", "/src/scripts/build_linux.py", "--out", "/out", *extra)


# --- inside the build image -------------------------------------------------------------------------

def bundle_ghostscript(program: Path) -> None:
    """``ghostscript/bin/gs`` beside the program (core.ghostscript.bundled_ghostscript), with its
    data under ``share/ghostscript/<version>`` and its licenses."""
    dest = program / "ghostscript"
    shutil.rmtree(dest, ignore_errors=True)
    (dest / "bin").mkdir(parents=True)
    shutil.copy2(GHOSTSCRIPT / "bin" / "gs", dest / "bin" / "gs")
    for share in (GHOSTSCRIPT / "share" / "ghostscript").iterdir():
        for folder in ("lib", "Resource", "iccprofiles"):
            if (share / folder).is_dir():
                shutil.copytree(share / folder, dest / "share" / "ghostscript" / share.name / folder)
    shutil.copytree(GHOSTSCRIPT / "licenses", dest / "licenses")
    needs = run("ldd", dest / "bin" / "gs", capture_output=True, text=True).stdout
    outside = [line.split()[0] for line in needs.splitlines()
               if "=>" in line and not re.match(r"\s*(libc|libm|libdl|libpthread|librt|ld-linux)", line.strip())]
    if outside:
        print("Note: Ghostscript also uses " + ", ".join(outside))


def bundle_xcb(program: Path) -> None:
    internal = program / "_internal"
    found = run("ldconfig", "-p", capture_output=True, text=True).stdout
    for name in XCB_LIBS:
        match = re.search(rf"^\s*{re.escape(name)} .*=> (\S+)$", found, re.M)
        if not match:
            sys.exit(f"{name} isn't installed in the build image")
        shutil.copy2(match.group(1), internal / name)


def bundle_portaudio(program: Path) -> None:
    """PortAudio built with ALSA only (the Dockerfile), found by packaging/linux/rthook_portaudio.py
    when the computer has none of its own."""
    shutil.copy2((PORTAUDIO / "lib" / "libportaudio.so.2").resolve(), program / "_internal" / "libportaudio.so.2")
    (program / "_internal" / "portaudio-license.txt").write_bytes((PORTAUDIO / "licenses" / "LICENSE.txt").read_bytes())


def self_test(program: Path, work: Path) -> None:
    report = work / "self-test.txt"
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PLANETPDF_PROFILE_DIR": str(work / "profile"),
           "PLANETPDF_UPDATE_URL": ""}
    code = subprocess.run([program / "PlanetPDF", "--self-test", report], env=env).returncode
    print(report.read_text(encoding="utf-8") if report.is_file() else "(no report)")
    if code != 0:
        sys.exit("The self-test failed: see above.")


def icon_png(target: Path) -> None:
    from PIL import Image

    with Image.open(ROOT / "src" / "planetpdf" / "resources" / "icons" / "planetpdf.ico") as ico:
        ico.size = max(ico.ico.sizes())
        ico.convert("RGBA").resize((256, 256)).save(target)


def stage_install(program: Path, stage: Path) -> None:
    """The files as they're installed by the .deb and .rpm."""
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(program, stage / INSTALL_DIR.lstrip("/"), symlinks=True)
    (stage / "usr" / "bin").mkdir(parents=True)
    (stage / "usr" / "bin" / "planetpdf").symlink_to(f"{INSTALL_DIR}/PlanetPDF")
    apps = stage / "usr" / "share" / "applications"
    apps.mkdir(parents=True)
    (apps / "planetpdf.desktop").write_text(DESKTOP.format(exec="planetpdf"), encoding="utf-8")
    icons = stage / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    icons.mkdir(parents=True)
    icon_png(icons / "planetpdf.png")
    # the repositories' public key, for apt (binary keyring) and dnf / zypper (armored)
    keyring = stage / APT_KEYRING.lstrip("/")
    keyring.parent.mkdir(parents=True)
    run("gpg", "--batch", "--yes", "--dearmor", "--output", keyring, PUBLIC_KEY)
    keyring.chmod(0o644)
    rpm_key = stage / RPM_KEY.lstrip("/")
    rpm_key.parent.mkdir(parents=True)
    shutil.copy2(PUBLIC_KEY, rpm_key)


# Installing the .deb / .rpm the first time adds PlanetPDF's repository (user 2026-10-27: "Add
# automatically"), so the system's updater offers new versions; upgrades never add it again (an
# administrator who removed it keeps it removed); removing the package removes it.
APT_SOURCE = "/etc/apt/sources.list.d/planetpdf.sources"
DEB_POSTINST = f"""#!/bin/sh
set -e
command -v update-desktop-database >/dev/null && update-desktop-database -q || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q /usr/share/icons/hicolor || true
if [ "$1" = configure ] && [ -z "$2" ] && [ -d /etc/apt/sources.list.d ] && [ ! -e {APT_SOURCE} ]; then
    cat > {APT_SOURCE} <<'END'
# PlanetPDF's package repository, added when PlanetPDF was installed: Software Updater and
# "apt upgrade" then offer new versions. Delete this file to stop it.
Types: deb
URIs: {REPO_URL}/apt
Suites: stable
Components: main
Architectures: @ARCH@
Signed-By: {APT_KEYRING}
END
fi
"""
DEB_POSTRM = f"""#!/bin/sh
set -e
command -v update-desktop-database >/dev/null && update-desktop-database -q || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q /usr/share/icons/hicolor || true
if [ "$1" = remove ] || [ "$1" = purge ]; then
    rm -f {APT_SOURCE}
fi
"""
RPM_REPOS = {"/etc/yum.repos.d": "", "/etc/zypp/repos.d": "type=rpm-md\nautorefresh=1\n"}
RPM_POST = "\n".join(f"""if [ $1 -eq 1 ] && [ -d {folder} ] && [ ! -e {folder}/planetpdf.repo ]; then
cat > {folder}/planetpdf.repo <<'END'
# PlanetPDF's package repository, added when PlanetPDF was installed: the system's updates then
# offer new versions. Delete this file to stop it.
[planetpdf]
name=PlanetPDF
baseurl={REPO_URL}/rpm/$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=file://{RPM_KEY}
{extra}END
fi""" for folder, extra in RPM_REPOS.items())
RPM_POSTUN = "\n".join(f"[ $1 -eq 0 ] && rm -f {folder}/planetpdf.repo || :" for folder in RPM_REPOS)


def make_appimage(program: Path, work: Path, target: Path) -> None:
    appdir = work / "AppDir"
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.copytree(program, appdir / "usr" / "lib" / "planetpdf", symlinks=True)
    (appdir / "usr" / "lib" / "planetpdf" / KIND_FILE).write_text("appimage\n", encoding="utf-8")
    apprun = appdir / "AppRun"
    apprun.write_text('#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\n'
                      'exec "$HERE/usr/lib/planetpdf/PlanetPDF" "$@"\n', encoding="utf-8")
    apprun.chmod(0o755)
    (appdir / "planetpdf.desktop").write_text(DESKTOP.format(exec="PlanetPDF"), encoding="utf-8")
    icon_png(appdir / "planetpdf.png")
    (appdir / ".DirIcon").symlink_to("planetpdf.png")
    squash = work / "planetpdf.squashfs"
    squash.unlink(missing_ok=True)
    run("mksquashfs", appdir, squash, "-root-owned", "-noappend", "-comp", "zstd", "-quiet")
    with open(target, "wb") as out:
        for part in (RUNTIME, squash):
            with open(part, "rb") as f:
                shutil.copyfileobj(f, out)
    target.chmod(0o755)
    squash.unlink()


def make_deb(stage: Path, work: Path, v: str, arch: str, target: Path) -> None:
    root = work / "deb"
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(stage, root, symlinks=True)
    (root / INSTALL_DIR.lstrip("/") / KIND_FILE).write_text("deb\n", encoding="utf-8")
    size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink()) // 1024
    (root / "DEBIAN").mkdir()
    (root / "DEBIAN" / "control").write_text(
        f"Package: planetpdf\nVersion: {v}\nArchitecture: {ARCHES[arch][2]}\n"
        f"Maintainer: Planet Networks <software@planet.net>\nInstalled-Size: {size}\n"
        f"Depends: {DEB_DEPENDS}\nSection: graphics\nPriority: optional\n"
        f"Homepage: {HOMEPAGE}\nDescription: {SUMMARY}\n {DESCRIPTION}\n", encoding="utf-8")
    for name, text in (("postinst", DEB_POSTINST.replace("@ARCH@", ARCHES[arch][2])), ("postrm", DEB_POSTRM)):
        script = root / "DEBIAN" / name
        script.write_text(text, encoding="utf-8")
        script.chmod(0o755)
    run("dpkg-deb", "--root-owner-group", "-Zxz", "--build", root, target, capture_output=True)
    shutil.rmtree(root)


def make_rpm(stage: Path, work: Path, v: str, arch: str, target: Path) -> None:
    top = work / "rpm"
    shutil.rmtree(top, ignore_errors=True)
    rpm_arch = ARCHES[arch][1]
    (stage / INSTALL_DIR.lstrip("/") / KIND_FILE).write_text("rpm\n", encoding="utf-8")
    requires = ", ".join(f"{name}()(64bit)" for name in RPM_REQUIRES)
    refresh = ("command -v update-desktop-database >/dev/null && update-desktop-database -q || :\n"
               "command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q /usr/share/icons/hicolor || :\n")
    spec = top / "planetpdf.spec"
    (top / "SPECS").mkdir(parents=True)
    spec.write_text(f"""\
Name: planetpdf
Version: {v}
Release: 1
Summary: {SUMMARY}
License: Freeware
URL: {HOMEPAGE}
BuildArch: {rpm_arch}
AutoReqProv: no
Requires: {requires}
%define debug_package %{{nil}}
%define __os_install_post %{{nil}}
%define _build_id_links none

%description
{DESCRIPTION}

%install
cp -a {stage}/. %{{buildroot}}/

%post
{refresh}{RPM_POST}

%postun
{refresh}{RPM_POSTUN}

%files
%defattr(-,root,root,-)
{INSTALL_DIR}
/usr/bin/planetpdf
/usr/share/applications/planetpdf.desktop
/usr/share/icons/hicolor/256x256/apps/planetpdf.png
{APT_KEYRING}
{RPM_KEY}
""", encoding="utf-8")
    run("rpmbuild", "-bb", "--define", f"_topdir {top}", "--target", rpm_arch, spec, capture_output=True)
    built = next((top / "RPMS").rglob("*.rpm"))
    shutil.move(built, target)
    shutil.rmtree(top)


def build(out: Path) -> None:
    arch, v = this_arch(), version()
    names = ARCHES[arch]
    print(f"Building PlanetPDF {v} for Linux ({arch})")
    work = Path("/tmp/planetpdf-build")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    step(1, "PyInstaller (the program)")
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", work / "dist",
        "--workpath", work / "work", ROOT / "packaging" / "planetpdf.spec")
    program = work / "dist" / "PlanetPDF"

    step(2, "Ghostscript, PortAudio, and the X11 cursor libraries into the program")
    bundle_ghostscript(program)
    bundle_xcb(program)
    bundle_portaudio(program)
    shutil.copy2(ROOT / "THIRD-PARTY-NOTICES.txt", program / "THIRD-PARTY-NOTICES.txt")
    print(f"Program: {sum(p.stat().st_size for p in program.rglob('*') if p.is_file()) / 1e6:.0f} MB")

    step(3, "The built program's self-test")
    self_test(program, work)

    step(4, "The AppImage")
    appimage = out / f"PlanetPDF-{v}-{names[1]}.AppImage"
    make_appimage(program, work, appimage)
    print(f"{appimage.name} ({appimage.stat().st_size / 1e6:.0f} MB)")

    stage = work / "stage"
    stage_install(program, stage)
    step(5, "The .deb")
    deb = out / f"planetpdf_{v}_{names[2]}.deb"
    make_deb(stage, work, v, arch, deb)
    print(f"{deb.name} ({deb.stat().st_size / 1e6:.0f} MB)")

    step(6, "The .rpm")
    rpm = out / f"planetpdf-{v}-1.{names[1]}.rpm"
    make_rpm(stage, work, v, arch, rpm)
    if REPO_KEY.is_file():
        from linux_repo import sign_rpm

        sign_rpm(rpm, REPO_KEY)
        print("Signed with the repositories' key")
    else:
        print("Not signed: the repositories' key isn't on this computer (the dnf repository needs signed packages)")
    print(f"{rpm.name} ({rpm.stat().st_size / 1e6:.0f} MB)")
    print(f"\nDone: PlanetPDF {v} for Linux ({arch}) in {out}")


def packages(out: Path, arch: str) -> list[Path]:
    v, names = version(), ARCHES[arch]
    return [out / f"PlanetPDF-{v}-{names[1]}.AppImage", out / f"planetpdf_{v}_{names[2]}.deb",
            out / f"planetpdf-{v}-1.{names[1]}.rpm"]


def publish(out: Path, arches: list[str], notes: str) -> None:
    """The signed update files for each package (on the host: the signing key stays there)."""
    for arch in arches:
        for package in packages(out, arch):
            run(sys.executable, ROOT / "scripts" / "release_update.py", "publish", "--build", package,
                "--out", ROOT / "dist" / "update-site", "--notes", notes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--docker", action="store_true", help="build inside the Docker build image")
    parser.add_argument("--arch", choices=[*ARCHES, "both"], default=None,
                        help="with --docker: the processor to build for (default: this computer's)")
    parser.add_argument("--out", default=str(ROOT / "dist" / "linux"), help="where the packages go")
    parser.add_argument("--no-update", action="store_true", help="with --docker: don't make the signed update files")
    parser.add_argument("--notes", default="", help="what's new (shown when the update is offered)")
    args = parser.parse_args()
    out = Path(args.out)
    if args.docker:
        arches = list(ARCHES) if args.arch == "both" else [args.arch or this_arch()]
        for n, arch in enumerate(arches, 1):
            print(f"\n##### Processor {n} of {len(arches)}: {arch} #####", flush=True)
            in_docker(arch, out, [])
        if not args.no_update:
            publish(out, arches, args.notes)
        return 0
    if sys.platform != "linux":
        sys.exit("Build for Linux on Linux, or use --docker.")
    if not GHOSTSCRIPT.is_dir() or not RUNTIME.is_file():
        sys.exit("Run this inside the build image (packaging/linux/Dockerfile), or use --docker.")
    out.mkdir(parents=True, exist_ok=True)
    build(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
