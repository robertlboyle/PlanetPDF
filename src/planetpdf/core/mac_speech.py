"""macOS' own voices for Read Out Loud (2026-10-14): the fallback while Kokoro isn't downloaded
(the user: "prefer and suggest kokoro"), like the Windows voices on Windows.

``say`` (every Mac has it) writes each sentence to a WAVE file, which ``neural_speech.Speaker``
plays like Kokoro's sentences, so pause, stop, speed and volume work the same. The voices are
the Mac's spoken-content voices: the basic ones sound about like Windows' voices; "Enhanced"
and "Premium" ones (downloaded by the user in System Settings ▸ Accessibility ▸ Spoken Content)
are much better and are listed first. Apple's novelty voices (Bells, Zarvox…) are left out.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import wave
from functools import cache
from pathlib import Path

RATE = 22050  # Hz, what say writes
WORDS_PER_MINUTE = 180  # say's normal speed; Read Out Loud's speed multiplies it
NOVELTY = {"albert", "bad news", "bahh", "bells", "boing", "bubbles", "cellos", "good news", "jester", "organ",
           "superstar", "trinoids", "whisper", "wobble", "zarvox"}
_LINE = re.compile(r"^(?P<name>.+?)\s+(?P<locale>[a-z]{2,3}[_-][A-Za-z0-9_]+)\s+#")  # (long names: one space)


def available() -> bool:
    return shutil.which("say") is not None


def parse_voices(listing: str, languages: tuple[str, ...] = ("en",)) -> dict[str, str]:
    """``say -v '?'``'s list -> {voice name: label}, Premium / Enhanced first."""
    found = []
    for line in listing.splitlines():
        match = _LINE.match(line)
        if not match:
            continue
        name, locale = match["name"].strip(), match["locale"]
        if not locale.split("_")[0].split("-")[0] in languages or name.lower() in NOVELTY:
            continue
        rank = 0 if "premium" in name.lower() else 1 if "enhanced" in name.lower() else 2
        region = {"en_US": "US", "en_GB": "UK", "en_AU": "Australia", "en_IE": "Ireland", "en_IN": "India",
                  "en_ZA": "South Africa", "en_SC": "Scotland"}.get(locale.replace("-", "_"), locale)
        base = re.sub(r"\s*\(.*\)\s*$", "", name)
        quality = " Premium" if rank == 0 else " Enhanced" if rank == 1 else ""
        found.append((rank, base.lower(), name, f"{base} ({region}{quality}, macOS)"))
    return {name: label for _rank, _base, name, label in sorted(found)}


@cache
def voices() -> dict[str, str]:
    if not available():
        return {}
    done = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=30)
    return parse_voices(done.stdout)


class MacEngine:
    """Same shape as ``neural_speech.Engine``."""

    name = "mac"

    def __init__(self) -> None:
        if not available():
            raise RuntimeError("This Mac has no speech (the say command is missing).")
        self.voices = voices()
        preferred = [v for v in self.voices if "premium" in v.lower() or "enhanced" in v.lower()]
        self.default_voice = (preferred or [v for v in ("Samantha", "Daniel") if v in self.voices]
                              or list(self.voices) or [""])[0]

    def make(self, text: str, voice: str, speed: float):
        import numpy as np

        voice = voice if voice in self.voices else self.default_voice
        with tempfile.TemporaryDirectory(prefix="planetpdf-say-") as tmp:
            source, out = Path(tmp) / "text.txt", Path(tmp) / "speech.wav"
            source.write_text(text, encoding="utf-8")
            command = ["say", "-r", str(round(WORDS_PER_MINUTE * speed)), "-f", str(source), "-o", str(out),
                       "--file-format=WAVE", f"--data-format=LEI16@{RATE}"]
            if voice:
                command[1:1] = ["-v", voice]
            done = subprocess.run(command, capture_output=True, text=True, timeout=120)
            if done.returncode != 0 or not out.is_file():
                raise RuntimeError(f"macOS couldn't speak ({done.stderr.strip() or done.returncode}).")
            with wave.open(str(out)) as audio:
                frames = audio.readframes(audio.getnframes())
                rate = audio.getframerate()
        return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0, rate
