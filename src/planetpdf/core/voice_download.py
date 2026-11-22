"""Read Out Loud's natural voices (Kokoro), downloaded on first use (2026-10-14; user: "prefer and
suggest kokoro", and not carried in every Mac update: 354 MB).

On the update server, beside the updates (``<update address>voices/``):

* ``voices.json``: ``{"app": "PlanetPDF", "voices": "kokoro", "files": [{"path": "kokoro/kokoro-v1.0.onnx",
  "size": …, "sha256": "…"}, …]}``, and ``voices.json.sig``: signed with the update key
  (``scripts/release_update.py voices``), so a changed server can't hand out other files;
* the files themselves, at those paths.

They go into the downloaded-voices folder (``neural_speech.USER_MODELS``, outside the app and the
roaming profile, so updates keep them), each as ``.part`` until its size and SHA-256 match.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from planetpdf import APP_NAME
from planetpdf.core import update as U
from planetpdf.core.document import PdfError
from planetpdf.core.progress import report

FOLDER = "voices"
MANIFEST = "voices.json"
SIGNATURE = MANIFEST + ".sig"
CHUNK = 256 * 1024
MB = 1_000_000


class VoiceDownloadCanceled(PdfError):
    pass


@dataclass(frozen=True)
class VoiceFile:
    path: str  # e.g. "kokoro/kokoro-v1.0.onnx", inside the models folder
    size: int
    sha256: str


def voices_url(update_url: str) -> str:
    return update_url.rstrip("/") + f"/{FOLDER}/"


def read_manifest(manifest: bytes, signature: bytes, key=None) -> list[VoiceFile]:
    """The files the signed manifest lists; UpdateError when it isn't signed with the update key."""
    from cryptography.exceptions import InvalidSignature

    try:
        (key or U.public_key()).verify(base64.b64decode(signature.strip()), manifest)
    except (InvalidSignature, ValueError):
        raise U.UpdateError("The voices on the server aren't signed with PlanetPDF's update key, so they "
                            "weren't downloaded.") from None
    try:
        data = json.loads(manifest.decode("utf-8"))
        if data.get("app") != APP_NAME:
            raise ValueError("not PlanetPDF's")
        files = [VoiceFile(str(f["path"]), int(f["size"]), str(f["sha256"]).lower()) for f in data["files"]]
    except (ValueError, KeyError, TypeError) as exc:
        raise U.UpdateError(f"The voice list on the server can't be read ({exc}).") from exc
    for f in files:
        parts = Path(f.path).parts
        if not parts or ".." in parts or Path(f.path).is_absolute() or parts[0] != "kokoro":
            raise U.UpdateError(f"The voice list names an odd file ({f.path}).")
    return files


def fetch_manifest(update_url: str, *, key=None, opener=U._open) -> list[VoiceFile]:
    base = voices_url(update_url)
    manifest = U._read(opener, base + MANIFEST)
    signature = U._read(opener, base + SIGNATURE, 4096)
    return read_manifest(manifest, signature, key)


def _matches(path: Path, wanted: VoiceFile) -> bool:
    if not path.is_file() or path.stat().st_size != wanted.size:
        return False
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest() == wanted.sha256


def download(update_url: str, target: Path, progress=None, *, key=None, opener=U._open) -> Path:
    """Download the natural voices into ``target`` (the models folder). ``progress(done, total, stage)``
    counts megabytes; returning False cancels."""
    files = fetch_manifest(update_url, key=key, opener=opener)
    total = sum(f.size for f in files)
    done = 0
    stage = "Downloading the natural voices (MB):"
    for wanted in files:
        out = target / wanted.path
        if _matches(out, wanted):
            done += wanted.size
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        part = out.with_name(out.name + ".part")
        digest = hashlib.sha256()
        got = 0
        try:
            with opener(voices_url(update_url) + wanted.path, U.TIMEOUT) as response, open(part, "wb") as sink:
                while chunk := response.read(CHUNK):
                    got += len(chunk)
                    if got > wanted.size:
                        raise U.UpdateError("A voice file is larger than the server's list says: it was thrown away.")
                    digest.update(chunk)
                    sink.write(chunk)
                    if not report(progress, (done + got) // MB, max(total // MB, 1), stage):
                        raise VoiceDownloadCanceled("Canceled.")
        except (U.UpdateError, VoiceDownloadCanceled):
            part.unlink(missing_ok=True)
            raise
        except Exception as exc:
            part.unlink(missing_ok=True)
            raise U.UpdateError(f"The voices didn't download ({U._reason(exc)}).") from exc
        if got != wanted.size or digest.hexdigest() != wanted.sha256:
            part.unlink(missing_ok=True)
            raise U.UpdateError("A downloaded voice file doesn't match its checksum: it was thrown away. Try again later.")
        os.replace(part, out)
        done += wanted.size
    return target


def make_manifest(models: Path, private_key) -> tuple[bytes, bytes, list[Path]]:
    """For the release script: (voices.json, its signature, the files to upload) from a models folder."""
    from planetpdf.core.neural_speech import KOKORO_MODEL, KOKORO_VOICES

    files = [models / "kokoro" / KOKORO_MODEL, models / "kokoro" / KOKORO_VOICES]
    entries = []
    for path in files:
        if not path.is_file():
            raise PdfError(f"{path} is missing: the voices can't be published.")
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(CHUNK), b""):
                digest.update(chunk)
        entries.append({"path": path.relative_to(models).as_posix(), "size": path.stat().st_size,
                        "sha256": digest.hexdigest()})
    manifest = json.dumps({"app": APP_NAME, "voices": "kokoro", "files": entries}, indent=2).encode("utf-8")
    return manifest, U.sign(manifest, private_key), files
