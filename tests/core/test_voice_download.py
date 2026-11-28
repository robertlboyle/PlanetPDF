"""Read Out Loud's natural voices downloaded on first use (2026-10-14), from a stand-in server."""

from __future__ import annotations

import io
import json
import sys

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from planetpdf.core import neural_speech as NS
from planetpdf.core import update as U
from planetpdf.core import voice_download as V

BASE = "https://software.example/PDF/"


@pytest.fixture
def server(tmp_path):
    key = Ed25519PrivateKey.generate()
    models = tmp_path / "published"
    (models / "kokoro").mkdir(parents=True)
    (models / "kokoro" / NS.KOKORO_MODEL).write_bytes(b"m" * 2_500_000)
    (models / "kokoro" / NS.KOKORO_VOICES).write_bytes(b"v" * 300_000)
    manifest, signature, files = V.make_manifest(models, key)
    served = {V.MANIFEST: manifest, V.SIGNATURE: signature}
    served.update({p.relative_to(models).as_posix(): p.read_bytes() for p in files})
    asked = []

    def opener(url, _timeout):
        assert url.startswith(BASE + "voices/")
        name = url[len(BASE + "voices/"):]
        asked.append(name)
        if name not in served:
            raise OSError("404")
        return io.BytesIO(served[name])

    return {"key": key.public_key(), "opener": opener, "served": served, "asked": asked}


def test_downloads_checks_and_counts_megabytes(server, tmp_path):
    stages = []
    target = tmp_path / "models"
    V.download(BASE, target, lambda done, total, stage="": stages.append((done, total)) or True,
               key=server["key"], opener=server["opener"])
    assert NS.kokoro_available(target)
    assert stages[-1] == (2, 2)  # (2.8 MB counted in whole MB)
    assert not list(target.rglob("*.part"))
    server["asked"].clear()
    V.download(BASE, target, key=server["key"], opener=server["opener"])  # (already there: only the list)
    assert server["asked"] == [V.MANIFEST, V.SIGNATURE]


def test_a_tampered_list_or_file_is_refused(server, tmp_path):
    served = server["served"]
    good = served[V.MANIFEST]
    served[V.MANIFEST] = good.replace(b"kokoro-v1.0", b"kokoro-v6.6")
    with pytest.raises(U.UpdateError, match="aren't signed"):
        V.download(BASE, tmp_path / "a", key=server["key"], opener=server["opener"])
    served[V.MANIFEST] = good
    served["kokoro/" + NS.KOKORO_VOICES] = b"x" * 300_000  # (same size, other bytes)
    with pytest.raises(U.UpdateError, match="checksum"):
        V.download(BASE, tmp_path / "b", key=server["key"], opener=server["opener"])
    assert not list((tmp_path / "b").rglob("*.part"))


def test_cancel_and_odd_paths(server, tmp_path):
    with pytest.raises(V.VoiceDownloadCanceled):
        V.download(BASE, tmp_path / "c", lambda *a: False, key=server["key"], opener=server["opener"])
    key = Ed25519PrivateKey.generate()
    manifest = json.dumps({"app": "PlanetPDF", "files": [{"path": "../evil", "size": 1, "sha256": "0" * 64}]}).encode()
    with pytest.raises(U.UpdateError, match="odd file"):
        V.read_manifest(manifest, U.sign(manifest, key), key.public_key())


def test_without_voice_files_a_mac_reads_with_its_own_voices(monkeypatch, tmp_path):
    monkeypatch.setattr(NS, "models_dir", lambda: None)
    monkeypatch.setattr(NS, "mac_voices_available", lambda: True)

    class FakeMac:
        name = "mac"

    monkeypatch.setattr(NS, "load_engine", lambda name, root=None: FakeMac() if name == "mac" else None)
    engine, speed = NS.choose_engine("auto", None)
    assert engine.name == "mac" and speed is None


def test_mac_voice_list_skips_novelty_voices_and_puts_premium_first():
    from planetpdf.core import mac_speech as M

    listing = """Albert              en_US    # Hello! My name is Albert.
Ava (Premium)       en_US    # Hello! My name is Ava.
Bells               en_US    # Hello! My name is Bells.
Daniel              en_GB    # Hello! My name is Daniel.
Eddy (English (US)) en_US    # Hello! My name is Eddy.
Amélie              fr_CA    # Bonjour! Je m’appelle Amélie.
Zoe (Enhanced)      en_US    # Hello! My name is Zoe.
"""
    voices = M.parse_voices(listing)
    assert list(voices) == ["Ava (Premium)", "Zoe (Enhanced)", "Daniel", "Eddy (English (US))"]
    assert voices["Ava (Premium)"] == "Ava (US Premium, macOS)" and voices["Daniel"] == "Daniel (UK, macOS)"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS' own voices")
def test_macos_speaks_a_sentence():
    from planetpdf.core.mac_speech import MacEngine

    engine = MacEngine()
    samples, rate = engine.make("Hello from PlanetPDF.", engine.default_voice, 1.2)
    assert rate == 22050 and 0.5 < len(samples) / rate < 5 and abs(samples).max() > 0.1


def test_a_voice_file_that_isnt_the_pinned_one_isnt_loaded(tmp_path):
    """Security review 2026-11-25: model files were fetched and loaded unchecked."""
    import pytest

    from planetpdf.core import neural_speech as NS

    (tmp_path / "piper").mkdir()
    (tmp_path / "piper" / "en_US-joe-medium.onnx").write_bytes(b"not the model")
    with pytest.raises(NS.ModelNotAsMade):
        NS.check_model(tmp_path, "piper/en_US-joe-medium.onnx")
    with pytest.raises(NS.ModelNotAsMade):
        NS.check_model(tmp_path, "piper/some-other-voice.onnx")
    root = NS.models_dir()
    if root is not None and (root / "piper" / "en_US-joe-medium.onnx").is_file():
        assert NS.check_model(root, "piper/en_US-joe-medium.onnx").is_file()  # (the real one passes)


def test_models_dir_prefers_a_folder_that_holds_voices(tmp_path, monkeypatch):
    """A user's own voices folder left empty (a canceled download) mustn't hide the voices that
    their own installer put in PlanetPDF's folder for everyone (2026-11-27)."""
    from planetpdf.core import neural_speech as NS

    empty, voices = tmp_path / "empty", tmp_path / "voices"
    empty.mkdir()
    (voices / "piper").mkdir(parents=True)
    (voices / "piper" / "en_US-joe-medium.onnx").write_bytes(b"x")
    (voices / "piper" / "en_US-joe-medium.onnx.json").write_text("{}")
    monkeypatch.setenv("PLANETPDF_MODELS", str(empty))  # (the first place looked)
    monkeypatch.setattr(NS, "USER_MODELS", voices)
    assert NS.models_dir() == voices

