"""Publish PlanetPDF's public source on GitHub (user 2026-11-22: "push the latest public code to github",
then "script it").

    .venv\\Scripts\\python.exe scripts\\publish_source.py               # copy, check, commit, push
    .venv\\Scripts\\python.exe scripts\\publish_source.py --dry-run     # copy and check; show what would change
    .venv\\Scripts\\python.exe scripts\\publish_source.py -m "Message"  # your own commit message

The private repository (GitLab, full history) is where work happens. The public one,
https://github.com/robertlboyle/PlanetPDF, started from a fresh snapshot of 0.1.17, because the
Font Awesome Pro icons are in every old private commit. Each run makes the public copy again from
this repository's last commit (``git archive HEAD``: uncommitted changes never go out):

1. the public clone, ``PlanetPDF-public`` beside this project (``PLANETPDF_PUBLIC_REPO`` overrides;
   cloned from GitHub when missing), must have no changes of its own, and takes GitHub's latest;
2. its files are replaced by this commit's, leaving out ``EXCLUDED`` (the Pro icons, Claude's notes,
   internal setup and planning documents, the upload script that names the server account);
3. every added or changed line is scanned for keys, tokens, and private server names (``LEAKS``), and
   no Pro icon may be in it: anything found stops the run, and nothing is committed;
4. one commit (author Robert Boyle <software@planet.net>, so no personal address goes public), pushed
   with the GitHub sign-in (``gh auth login`` once per computer).

Nothing changed = nothing committed.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PUBLIC_URL = "https://github.com/robertlboyle/PlanetPDF.git"
AUTHOR_NAME = "Robert Boyle"
AUTHOR_EMAIL = "software@planet.net"
ATTRIBUTION = "Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>"

# Paths (relative, forward slashes) never published; a folder covers everything inside it.
EXCLUDED = (
    "src/planetpdf/resources/icons/fa/",  # Font Awesome Pro: company license (the free set stands in)
    "CLAUDE.md",
    "docs/PLAN.md",
    "docs/CHANGELOG.md",
    "docs/BUILD-SETUP.md",
    "docs/SECURITY-PLAN-2026-11-25.md",  # (the fixes aren't out yet)
    "docs/SECURITY-REVIEW-REPLY-2026-11-25.md",  # (a draft, sent once the fixes are out)
    "docs/SECURITY-FINDINGS-2026-11-26.md",  # (our own list: until its items are fixed)
    "docs/icons-pro-vs-free.pdf",
    "docs/icons-pro-vs-free.png",
    "docs/menu_layout.csv",
    "docs/menu_layout.xlsx",
    "scripts/upload_update.py",
)

# What must never appear in an added line. Written so this file doesn't match itself.
LEAKS = re.compile("|".join((
    r"PRIVATE[ ]KEY-----",
    r"planet[w]eb",
    r"planet[s]oftware",
    r"web[h]osting",
    r"69\.172\.142",
    r"Robert[B]oyle",
    r"robert[@]",
    r"\bgh[pousr]_[A-Za-z0-9]{20,}",
    r"\bglpat-[A-Za-z0-9_-]{16,}",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bxox[abp]-[A-Za-z0-9-]{10,}",
)), re.IGNORECASE)


class Stop(Exception):
    pass


def git(repo: Path, *args: str, binary: bool = False) -> str | bytes:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if done.returncode:
        raise Stop(f"git {' '.join(args)} failed in {repo}:\n{done.stderr.decode(errors='replace').strip()}")
    return done.stdout if binary else done.stdout.decode("utf-8", errors="replace")


def excluded(path: str) -> bool:
    return any(path == e or (e.endswith("/") and path.startswith(e)) for e in EXCLUDED)


def public_folder() -> Path:
    env = os.environ.get("PLANETPDF_PUBLIC_REPO")
    return Path(env) if env else PROJECT.parent / "PlanetPDF-public"


def prepare(public: Path) -> None:
    if not (public / ".git").is_dir():
        if public.exists() and any(public.iterdir()):
            raise Stop(f"{public} exists but isn't a Git clone: move it away first.")
        print(f"Cloning {PUBLIC_URL} into {public}")
        subprocess.run(["git", "clone", "-q", PUBLIC_URL, str(public)], check=True)
    if git(public, "status", "--porcelain").strip():
        raise Stop(f"{public} has changes of its own: commit or discard them there first.")
    git(public, "pull", "-q", "--ff-only")
    git(public, "config", "user.name", AUTHOR_NAME)
    git(public, "config", "user.email", AUTHOR_EMAIL)
    git(public, "config", "core.autocrlf", "input")


def copy_files(public: Path) -> int:
    for path in git(public, "ls-files", "-z").split("\0"):
        if path:
            (public / path).unlink(missing_ok=True)
    for folder in sorted((p for p in public.rglob("*") if p.is_dir() and ".git" not in p.parts),
                         key=lambda p: len(p.parts), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()
    archive = tarfile.open(fileobj=io.BytesIO(git(PROJECT, "archive", "--format=tar", "HEAD", binary=True)))
    members = [m for m in archive.getmembers() if m.isfile() and not excluded(m.name)]
    for n, member in enumerate(members, 1):
        if n % 100 == 0 or n == len(members):
            print(f"Copying file {n} of {len(members)}", end="\r" if n < len(members) else "\n")
        archive.extract(member, public, filter="data")
    return len(members)


def check(public: Path) -> list[str]:
    git(public, "add", "-A")
    problems = [f"{p}: is excluded but present" for p in git(public, "diff", "--cached", "--name-only").splitlines()
                if excluded(p)]
    current = None
    for line in git(public, "diff", "--cached", "-U0", "--no-color").splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++") and LEAKS.search(line):
            problems.append(f"{current}: {line[1:].strip()[:120]}")
    return problems


def version() -> str:
    text = (PROJECT / "src" / "planetpdf" / "__init__.py").read_text(encoding="utf-8")
    found = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return found.group(1) if found else "?"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish PlanetPDF's public source on GitHub.")
    parser.add_argument("--dry-run", action="store_true", help="copy and check, then undo; commit nothing")
    parser.add_argument("-m", "--message", help="the commit message's first line")
    parser.add_argument("--no-push", action="store_true", help="commit but don't push")
    args = parser.parse_args(argv)
    public = public_folder()
    try:
        head = git(PROJECT, "rev-parse", "--short", "HEAD").strip()
        if git(PROJECT, "status", "--porcelain").strip():
            print("Note: this project has uncommitted changes; only its last commit is published.")
        print(f"Step 1 of 4: getting the public clone ready ({public})")
        prepare(public)
        print(f"Step 2 of 4: copying commit {head}")
        count = copy_files(public)
        print(f"Step 3 of 4: checking the changes ({count} files in the public copy)")
        problems = check(public)
        changed = git(public, "diff", "--cached", "--name-status").splitlines()
        if problems:
            git(public, "reset", "-q", "--hard")
            raise Stop("Not published; private details found:\n  " + "\n  ".join(problems))
        if not changed:
            print("Nothing changed since the last public commit.")
            return 0
        print(f"{len(changed)} file(s) changed:")
        for line in changed[:50]:
            print("  " + line.replace("\t", "  "))
        if len(changed) > 50:
            print(f"  … and {len(changed) - 50} more")
        if args.dry_run:
            git(public, "reset", "-q", "--hard")
            git(public, "clean", "-q", "-fd")
            print("Dry run: nothing committed (the public clone is back as it was).")
            return 0
        title = args.message or f"PlanetPDF {version()}: changes up to {head}"
        print("Step 4 of 4: committing and pushing")
        git(public, "commit", "-q", "-m", f"{title}\n\n{ATTRIBUTION}")
        if not args.no_push:
            git(public, "push", "-q")
        print(f"Published: {git(public, 'log', '--oneline', '-1').strip()}"
              + ("" if args.no_push else " (pushed)"))
        return 0
    except Stop as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
