"""Read Out Loud with natural-sounding voices (user 2026-10-02: the Windows voices "sound
like speech synthesis from 20 years ago").

Two offline engines, both run with onnxruntime on this computer (the text never leaves it):

* **Kokoro** (``kokoro-onnx``, Apache-2.0): the most natural, but heavy: on the dev
  laptop's i7-1260P it only just keeps up with real time on the CPU, and Windows'
  DirectML can't run its model on the graphics chip (2026-10-02).
* **Piper** (``piper-tts``, GPL): very good and about 4-5x faster than real time on the
  same CPU. Its voices here are public domain / CC0 (``kristin``, ``joe``).

``choose_engine("auto")`` times Kokoro on this computer and uses it only when it runs
comfortably faster than real time (``KOKORO_MIN_SPEED``); otherwise Piper (user: "if
performance is bad, fall back to the other one"). ``Speaker`` has the same interface as
the Windows voice (``core/windows_speech.py``): speak / pause / resume / stop return at
once; sentences are made one or two ahead while the one before plays.

Models live in a ``models`` folder (``kokoro/``, ``piper/``): ``PLANETPDF_MODELS``, the
PyInstaller bundle, next to the program, or the project root (development).
"""

from __future__ import annotations

import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

KOKORO_MIN_SPEED = 1.6  # Kokoro is used when it makes speech this many times faster than it plays
KOKORO_MODEL = "kokoro-v1.0.onnx"  # (the int8 one is smaller but slower on CPUs; fp16 is for GPUs)
KOKORO_VOICES = "voices-v1.0.bin"
TEST_SENTENCE = "This is a short test of the reading voice, to see how fast it runs on this computer."

# Kokoro's English voices, as people see them (code -> name); "a" = US, "b" = UK
KOKORO_VOICE_NAMES = {
    "af_heart": "Heart (US, female)", "af_bella": "Bella (US, female)", "af_nicole": "Nicole (US, female)",
    "af_sarah": "Sarah (US, female)", "af_sky": "Sky (US, female)", "af_nova": "Nova (US, female)",
    "am_michael": "Michael (US, male)", "am_adam": "Adam (US, male)", "am_eric": "Eric (US, male)",
    "am_liam": "Liam (US, male)", "am_onyx": "Onyx (US, male)",
    "bf_emma": "Emma (UK, female)", "bf_isabella": "Isabella (UK, female)",
    "bm_george": "George (UK, male)", "bm_daniel": "Daniel (UK, male)",
}
PIPER_VOICE_NAMES = {"en_US-kristin-medium": "Kristin (US, female)", "en_US-joe-medium": "Joe (US, male)"}


# Where downloaded voices go (2026-10-14: Kokoro downloaded on first use instead of carried in every
# Mac update): set by the UI (``ui/paths.models_dir``), outside the app and the roaming profile.
USER_MODELS: Path | None = None


# The voice files PlanetPDF was made with: (file in the models folder) -> SHA-256. A model file is
# in effect code for ONNX Runtime, so one that doesn't match isn't loaded (security review
# 2026-11-25). Kokoro: release model-files-v1.0; Piper: rhasspy/piper-voices at commit
# 5ec43b18 (2026-11-24). scripts/download_voices.py fetches exactly these.
MODEL_SHA256 = {
    "kokoro/kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "kokoro/voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
    "piper/en_US-kristin-medium.onnx": "5849957f929cbf720c258f8458692d6103fff2f0e3d3b19c8259474bb06a18d4",
    "piper/en_US-kristin-medium.onnx.json": "5681426d4aead22195de70531eeeeddb46493cfaffc5764b2ea3db73428b651c",
    "piper/en_US-kristin-medium.MODEL_CARD.txt": "8c181b1d5f7d8152b914faab941fcbe6b8f0495e63e5189d6f27b8c68c8393fc",
    "piper/en_US-joe-medium.onnx": "abf4b998e34045cbd00eaf050de2de620f81ded6eb8b85a24b022a1c2b3f08d2",
    "piper/en_US-joe-medium.onnx.json": "9b91a652090ffa8a49e36569afa9acc8ead2b3a22a950e8e6ca11914f3e85d56",
    "piper/en_US-joe-medium.MODEL_CARD.txt": "4b319248135916e8f5433ce7a9fe5271026b05cb598bab05807b22cb17fd129b",
}
_checked: dict[tuple[str, int, int], bool] = {}


class ModelNotAsMade(RuntimeError):
    """A voice file isn't the one PlanetPDF was made with (changed or damaged)."""


def check_model(root: Path, relative: str) -> Path:
    """The voice file ``relative`` in ``root``, if it's exactly the one PlanetPDF was made with
    (checked once per run while the file is unchanged); else ``ModelNotAsMade``."""
    import hashlib

    path = Path(root) / relative
    expected = MODEL_SHA256.get(relative)
    if expected is None:
        raise ModelNotAsMade(f"{relative} isn't one of the voice files PlanetPDF knows.")
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if not _checked.get(key):
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                digest.update(block)
        if digest.hexdigest() != expected:
            raise ModelNotAsMade(f"The voice file {relative} isn't the one PlanetPDF was made with, so it isn't "
                                 "used. Install the voices again (Preferences ▸ Read Out Loud).")
        _checked[key] = True
    return path


def quiet_onnxruntime():
    """onnxruntime with its telemetry switched off. Microsoft's Windows builds write TraceLogging
    events (the model's file name, hashes, the hardware) that Windows' diagnostic data may pass on
    (security review 2026-11-25). ONNX Runtime turns them back on when a trace session enables its
    provider, so this is called again right before every model is loaded."""
    import onnxruntime as ort

    ort.disable_telemetry_events()
    return ort


def models_dir() -> Path | None:
    candidates = []
    if os.environ.get("PLANETPDF_MODELS"):
        candidates.append(Path(os.environ["PLANETPDF_MODELS"]))
    if USER_MODELS is not None:
        candidates.append(USER_MODELS)
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.append(Path(bundle) / "models")
    exe_dir = Path(sys.executable).resolve().parent
    candidates.append(exe_dir / "models")
    if exe_dir.parent.name == "versions":  # (installed: shared by every version, beside the launcher)
        candidates.append(exe_dir.parent.parent / "models")
    candidates.append(Path(__file__).resolve().parents[3] / "models")  # (src/planetpdf/core -> project)
    return next((c for c in candidates if c.is_dir()), None)


def kokoro_available(root: Path | None = None) -> bool:
    root = root or models_dir()
    return root is not None and (root / "kokoro" / KOKORO_MODEL).is_file() and (root / "kokoro" / KOKORO_VOICES).is_file()


def piper_voices(root: Path | None = None) -> list[str]:
    root = root or models_dir()
    if root is None or not (root / "piper").is_dir():
        return []
    return sorted(p.name[:-5] for p in (root / "piper").glob("*.onnx") if p.with_suffix(".onnx.json").is_file())


def mac_voices_available() -> bool:
    """macOS' own voices (``mac_speech``): every Mac has them."""
    if sys.platform != "darwin":
        return False
    from planetpdf.core import mac_speech

    return mac_speech.available()


def available(root: Path | None = None) -> bool:
    return kokoro_available(root) or bool(piper_voices(root))


def sentences(text: str, limit: int = 280) -> list[str]:
    """The text cut into sentences (long ones at commas), so speech can start while the
    rest is still being made."""
    out = []
    for paragraph in re.split(r"\n\s*\n|\f", text):
        paragraph = " ".join(paragraph.split())
        for sentence in re.split(r"(?<=[.!?;:])\s+(?=[\"'(A-Z0-9])", paragraph):
            while len(sentence) > limit:
                cut = sentence.rfind(", ", 0, limit)
                cut = cut + 1 if cut > limit // 3 else sentence.rfind(" ", 0, limit)
                if cut <= 0:
                    cut = limit
                out.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if sentence.strip():
                out.append(sentence.strip())
    return out


def speed_from_rate(rate: int) -> float:
    """The Windows-voice rate (-10 slow .. 10 fast) as a speed factor (0.5 .. 2)."""
    return 2 ** (max(-10, min(10, rate)) / 10)


# --- engines -------------------------------------------------------------------------------------


class Engine:
    name = ""
    voices: dict[str, str] = {}
    default_voice = ""

    def make(self, text: str, voice: str, speed: float) -> tuple[object, int]:
        """(float32 samples, sample rate) for one sentence."""
        raise NotImplementedError


class KokoroEngine(Engine):
    name = "kokoro"
    default_voice = "af_heart"

    def __init__(self, root: Path) -> None:
        from kokoro_onnx import Kokoro

        model = check_model(root, f"kokoro/{KOKORO_MODEL}")
        voices = check_model(root, f"kokoro/{KOKORO_VOICES}")
        ort = quiet_onnxruntime()

        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(8, (os.cpu_count() or 4) // 2))  # (more was slower)
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = ort.InferenceSession(str(model), options, providers=["CPUExecutionProvider"])
        self._kokoro = Kokoro.from_session(session, str(voices))
        have = set(self._kokoro.get_voices())
        self.voices = {code: label for code, label in KOKORO_VOICE_NAMES.items() if code in have}

    def make(self, text: str, voice: str, speed: float):
        voice = voice if voice in self.voices else self.default_voice
        samples, rate = self._kokoro.create(text, voice=voice, speed=speed, lang="en-gb" if voice[0] == "b" else "en-us")
        return samples, rate


class PiperEngine(Engine):
    name = "piper"

    def __init__(self, root: Path) -> None:
        self._root = root
        self.voices = {v: PIPER_VOICE_NAMES.get(v, v) for v in piper_voices(root)}
        self.default_voice = "en_US-kristin-medium" if "en_US-kristin-medium" in self.voices else next(iter(self.voices), "")
        self._loaded: dict[str, object] = {}

    def _voice(self, voice: str):
        from piper import PiperVoice

        voice = voice if voice in self.voices else self.default_voice
        if voice not in self._loaded:
            check_model(self._root, f"piper/{voice}.onnx.json")
            model = check_model(self._root, f"piper/{voice}.onnx")
            quiet_onnxruntime()  # (Piper makes its own ONNX Runtime session)
            self._loaded[voice] = PiperVoice.load(model)
        return self._loaded[voice]

    def make(self, text: str, voice: str, speed: float):
        import numpy as np
        from piper import SynthesisConfig

        model = self._voice(voice)
        config = SynthesisConfig(length_scale=1.0 / speed)
        parts, rate = [], 22050
        for chunk in model.synthesize(text, syn_config=config):
            rate = chunk.sample_rate
            data = getattr(chunk, "audio_float_array", None)
            parts.append(data if data is not None else chunk.audio_int16_array.astype(np.float32) / 32768.0)
        return (np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)), rate


def measure(engine: Engine, voice: str = "") -> float:
    """How many times faster than real time the engine makes speech here (after a warm-up)."""
    engine.make("Hello.", voice or engine.default_voice, 1.0)
    start = time.perf_counter()
    samples, rate = engine.make(TEST_SENTENCE, voice or engine.default_voice, 1.0)
    took = max(time.perf_counter() - start, 1e-6)
    return (len(samples) / rate) / took


def load_engine(name: str, root: Path | None = None) -> Engine:
    if name == "mac":  # (macOS' own voices: no voice files needed)
        from planetpdf.core.mac_speech import MacEngine

        return MacEngine()
    root = root or models_dir()
    if root is None:
        raise RuntimeError("The voice files (models folder) weren't found.")
    if name == "kokoro":
        return KokoroEngine(root)
    if name == "piper":
        if not piper_voices(root):
            raise RuntimeError("No Piper voice was found in the models folder.")
        return PiperEngine(root)
    raise ValueError(name)


def choose_engine(preference: str = "auto", root: Path | None = None, known: str = "") -> tuple[Engine, float | None]:
    """The engine to read with. ``preference``: "auto", "kokoro", "piper" or "mac". ``known``:
    the choice already measured on this computer (skips timing it again). Returns (engine,
    Kokoro's measured speed or None). Without voice files a Mac reads with its own voices."""
    root = root or models_dir()
    if preference in ("kokoro", "piper", "mac"):
        return load_engine(preference, root), None
    if not available(root) and mac_voices_available():
        return load_engine("mac"), None
    if known in ("kokoro", "piper"):
        return load_engine(known, root), None
    if kokoro_available(root):
        kokoro = KokoroEngine(root)
        speed = measure(kokoro)
        if speed >= KOKORO_MIN_SPEED or not piper_voices(root):
            return kokoro, speed
        del kokoro  # (too slow here: Piper)
        return load_engine("piper", root), speed
    return load_engine("piper", root), None


# --- speaking ------------------------------------------------------------------------------------


@dataclass
class _Piece:
    samples: object
    rate: int


class Speaker:
    """Reads text aloud in the background, like ``windows_speech.Speaker``: ``speak``,
    ``pause``, ``resume`` and ``stop`` return at once; ``speaking`` stays True until the
    last sentence has played. The engine is loaded (and timed, for "auto") on first use,
    in the background too; ``on_engine(engine, speed)`` is called (from that thread) once
    it's known."""

    def __init__(self, preference: str = "auto", voices: dict[str, str] | None = None, rate: int = 0, known: str = "",
                 on_engine=None, root: Path | None = None) -> None:
        """``voices``: the voice to use per engine ({"kokoro": "af_heart", "piper": …})."""
        self.preference, self.voice_for, self.rate = preference, dict(voices or {}), rate
        self.volume = 100
        self.paused = False
        self.engine: Engine | None = None
        self.error: str = ""
        # what it's doing before any sound, for the window to say (user 2026-10-02: tell the
        # user it's checking the computer and to please wait): "checking" (timing Kokoro on
        # this computer), "loading" (the voice), "preparing" (the first sentence), "" (talking
        # or idle)
        self.status = ""
        self._known, self._on_engine, self._root = known, on_engine, root
        self._job = 0  # every speak/stop starts a new job; older threads notice and quit
        self._lock = threading.Lock()
        self._busy = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._engine_lock = threading.Lock()

    @property
    def voices(self) -> list[str]:
        return list(self.engine.voices.values()) if self.engine else []

    @property
    def speaking(self) -> bool:
        return self._busy.is_set()

    def _ensure_engine(self) -> Engine:
        with self._engine_lock:
            if self.engine is None:
                timing = self.preference == "auto" and self._known not in ("kokoro", "piper") \
                    and kokoro_available(self._root)
                self.status = "checking" if timing else "loading"
                try:
                    engine, speed = choose_engine(self.preference, self._root, self._known)
                finally:
                    self.status = "preparing" if self.speaking else ""
                self.engine = engine
                if self._on_engine is not None:
                    self._on_engine(engine, speed)
            return self.engine

    def prepare(self) -> None:
        """Load the engine in the background now (Activate Read Out Loud), so the first
        Read starts quicker."""
        if self.engine is None and not getattr(self, "_preparing", False):
            self._preparing = True
            threading.Thread(target=self._prepare_quietly, daemon=True).start()

    def _prepare_quietly(self) -> None:
        try:
            self._ensure_engine()
        except Exception as exc:  # (shown when reading is tried)
            self.error = str(exc)

    def speak(self, text: str) -> None:
        self.stop()
        parts = sentences(text)
        if not parts:
            return
        if len(parts[0]) > 60:  # (a short first piece: the first words are heard sooner)
            cut = parts[0].find(", ", 25, 70)
            cut = cut + 1 if cut > 0 else parts[0].find(" ", 40)
            if 0 < cut < len(parts[0]) - 10:
                parts[0:1] = [parts[0][:cut].strip(), parts[0][cut:].strip()]
        with self._lock:
            self._job += 1
            job = self._job
        if self.status not in ("checking", "loading"):
            self.status = "preparing"
        self._busy.set()
        self.paused = False
        self._resume.set()
        pieces: queue.Queue = queue.Queue(maxsize=3)
        threading.Thread(target=self._make, args=(job, parts, pieces), daemon=True).start()
        threading.Thread(target=self._play, args=(job, pieces), daemon=True).start()

    def _current(self, job: int) -> bool:
        return job == self._job

    def _make(self, job: int, parts: list[str], pieces: queue.Queue) -> None:
        try:
            engine = self._ensure_engine()
            for text in parts:
                if not self._current(job):
                    return
                samples, rate = engine.make(text, self.voice_for.get(engine.name, ""), speed_from_rate(self.rate))
                while self._current(job):
                    try:
                        pieces.put(_Piece(samples, rate), timeout=0.2)
                        break
                    except queue.Full:
                        continue
        except Exception as exc:
            self.error = str(exc)
        finally:
            pieces.put(None)

    def _play(self, job: int, pieces: queue.Queue) -> None:
        import numpy as np
        import sounddevice as sd

        stream = None
        try:
            while self._current(job):
                try:
                    piece = pieces.get(timeout=0.2)
                except queue.Empty:
                    continue
                if piece is None:
                    break
                if stream is None or stream.samplerate != piece.rate:
                    if stream is not None:
                        stream.close()
                    stream = sd.OutputStream(samplerate=piece.rate, channels=1, dtype="float32")
                    stream.start()
                data = np.asarray(piece.samples, dtype=np.float32)
                block = max(1, piece.rate // 10)  # 0.1 s at a time: pause / stop / volume act quickly
                for start in range(0, len(data), block):
                    while not self._resume.wait(0.1):
                        if not self._current(job):
                            return
                    if not self._current(job):
                        return
                    gain = max(0, min(100, self.volume)) / 100.0  # (the toolbar's slider, at once)
                    stream.write((data[start:start + block] * gain).reshape(-1, 1))
                    if self.status == "preparing":
                        self.status = ""  # (talking now)
        except Exception as exc:
            self.error = str(exc)
        finally:
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
            if self._current(job):
                self._busy.clear()
                if self.status == "preparing":
                    self.status = ""

    def pause(self) -> None:
        if self.speaking and not self.paused:
            self.paused = True
            self._resume.clear()

    def resume(self) -> None:
        if self.paused:
            self.paused = False
            self._resume.set()

    def stop(self) -> None:
        with self._lock:
            self._job += 1
        self.paused = False
        self._resume.set()
        self._busy.clear()
        if self.status == "preparing":
            self.status = ""

    def wait(self, ms: int) -> bool:
        """Block up to ``ms`` milliseconds; True if it finished (tests)."""
        end = time.perf_counter() + ms / 1000
        while self.speaking and time.perf_counter() < end:
            time.sleep(0.05)
        return not self.speaking
