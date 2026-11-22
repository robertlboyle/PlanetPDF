"""Read Out Loud's natural voices (Kokoro / Piper; user 2026-10-02)."""

from __future__ import annotations

import sys
import time
import types

import numpy as np
import pytest

from planetpdf.core import neural_speech as NS


def test_text_is_cut_into_sentences_long_ones_at_commas():
    parts = NS.sentences("Hello there. Is it fiber? Yes!\n\nA new paragraph, " + "with many words, " * 30)
    assert parts[:3] == ["Hello there.", "Is it fiber?", "Yes!"]
    assert all(len(p) <= 280 for p in parts) and len(parts) > 4
    assert NS.sentences("  \n ") == []


def test_speed_follows_the_windows_rate_scale():
    assert NS.speed_from_rate(0) == 1.0
    assert NS.speed_from_rate(10) == pytest.approx(2.0) and NS.speed_from_rate(-10) == pytest.approx(0.5)


class FakeEngine(NS.Engine):
    name = "piper"
    voices = {"v1": "Voice One"}
    default_voice = "v1"

    def __init__(self, seconds_per_sentence=0.3, delay=0.0):
        self.made = []
        self.seconds, self.delay = seconds_per_sentence, delay

    def make(self, text, voice, speed):
        time.sleep(self.delay)
        self.made.append((text, voice, round(speed, 2)))
        return np.zeros(int(16000 * self.seconds), dtype=np.float32), 16000


class FakeStream:
    written = []

    def __init__(self, samplerate, channels, dtype):
        self.samplerate = samplerate

    def start(self):
        pass

    def write(self, data):
        FakeStream.written.append(len(data))
        time.sleep(len(data) / self.samplerate / 20)  # (20x faster than real playback)

    def stop(self):
        pass

    def close(self):
        pass


@pytest.fixture
def fake_audio(monkeypatch):
    FakeStream.written = []
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(OutputStream=FakeStream))


def test_speaker_reads_every_sentence_and_finishes(fake_audio):
    speaker = NS.Speaker("piper", voices={"piper": "v1"}, rate=10)
    speaker.engine = engine = FakeEngine()
    speaker.speak("One. Two. Three.")
    assert speaker.speaking  # (at once: the window doesn't wait)
    assert speaker.wait(10_000)
    assert [t for t, _v, _s in engine.made] == ["One.", "Two.", "Three."]
    assert {(v, s) for _t, v, s in engine.made} == {("v1", 2.0)}
    assert sum(FakeStream.written) == 3 * int(16000 * 0.3) and speaker.error == ""


def test_pause_resume_and_stop(fake_audio):
    speaker = NS.Speaker("piper")
    speaker.engine = FakeEngine(seconds_per_sentence=2.0)
    speaker.speak("A long first sentence. A second one.")
    time.sleep(0.05)
    speaker.pause()
    written = sum(FakeStream.written)
    time.sleep(0.3)
    assert speaker.paused and speaker.speaking and sum(FakeStream.written) - written <= 1600  # (at most a block)
    speaker.resume()
    speaker.stop()
    assert not speaker.speaking
    time.sleep(0.3)
    stopped_at = sum(FakeStream.written)
    time.sleep(0.3)
    assert sum(FakeStream.written) == stopped_at  # nothing more is played


def test_auto_uses_kokoro_only_when_it_is_fast_enough(monkeypatch, tmp_path):
    (tmp_path / "kokoro").mkdir()
    for name in (NS.KOKORO_MODEL, NS.KOKORO_VOICES):
        (tmp_path / "kokoro" / name).write_bytes(b"x")
    (tmp_path / "piper").mkdir()
    for name in ("en_US-joe-medium.onnx", "en_US-joe-medium.onnx.json"):
        (tmp_path / "piper" / name).write_bytes(b"x")

    class Kokoro(FakeEngine):
        name = "kokoro"

    monkeypatch.setattr(NS, "KokoroEngine", lambda root: Kokoro())
    monkeypatch.setattr(NS, "PiperEngine", lambda root: FakeEngine())
    monkeypatch.setattr(NS, "measure", lambda engine, voice="": 0.9)  # (like the dev laptop)
    engine, speed = NS.choose_engine("auto", tmp_path)
    assert engine.name == "piper" and speed == 0.9
    monkeypatch.setattr(NS, "measure", lambda engine, voice="": 3.0)
    assert NS.choose_engine("auto", tmp_path)[0].name == "kokoro"
    assert NS.choose_engine("auto", tmp_path, known="piper") [0].name == "piper"  # (timed before: not again)
    assert NS.choose_engine("kokoro", tmp_path)[0].name == "kokoro"


def test_a_missing_voice_is_reported_not_silent(fake_audio, tmp_path):
    speaker = NS.Speaker("piper", root=tmp_path)  # (no models there)
    speaker.speak("Hello.")
    assert speaker.wait(5000) and "No Piper voice" in speaker.error


@pytest.mark.skipif(not NS.piper_voices(), reason="Piper voices not downloaded (models/piper)")
def test_piper_makes_real_speech():
    engine = NS.load_engine("piper")
    samples, rate = engine.make("Planet Networks.", engine.default_voice, 1.0)
    assert rate > 8000 and len(samples) > rate * 0.3 and float(np.abs(samples).max()) > 0.01


def test_the_speaker_says_it_is_checking_then_loading_then_talking(fake_audio, monkeypatch):
    """User 2026-10-02: Play seemed to do nothing at first; tell the user it's checking the
    computer, and to please wait."""
    seen = []

    def slow_choice(preference, root=None, known=""):
        seen.append(speaker.status)
        time.sleep(0.3)
        return FakeEngine(seconds_per_sentence=0.2), 2.0

    monkeypatch.setattr(NS, "choose_engine", slow_choice)
    monkeypatch.setattr(NS, "kokoro_available", lambda root=None: True)
    speaker = NS.Speaker("auto")
    speaker.speak("Hello there. Second sentence.")
    time.sleep(0.1)
    assert speaker.status == "checking"  # (timing the voice on this computer)
    assert speaker.wait(10_000)
    assert seen == ["checking"] and speaker.status == ""  # (talking cleared it)
    speaker.speak("Again.")
    assert speaker.status in ("preparing", "")  # (no checking the second time)
    assert speaker.wait(10_000) and speaker.status == ""


def test_a_long_first_sentence_starts_with_a_short_piece(fake_audio):
    speaker = NS.Speaker("piper")
    speaker.engine = engine = FakeEngine(seconds_per_sentence=0.05)
    speaker.speak("Planet Networks provides fiber internet to homes and businesses across northern New Jersey.")
    assert speaker.wait(10_000)
    assert len(engine.made[0][0]) < 60 and len(engine.made) == 2
