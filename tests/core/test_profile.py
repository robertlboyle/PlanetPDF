from __future__ import annotations

from planetpdf.core.signing.visual import SignatureLibrary
from planetpdf.core.stamps import StampLibrary
from planetpdf.core.storage.profile import Profile, migrate_legacy
from planetpdf.core.storage.settings import Settings, load_settings, save_settings


def test_everything_lives_under_one_root(tmp_path):
    profile = Profile(tmp_path / "roaming" / "PlanetPDF").ensure()
    for sub in (profile.settings_dir, profile.signatures_dir, profile.stamps_dir, profile.trusted_certs_dir):
        assert sub.is_dir()
        assert profile.root in (sub, *sub.parents)
    save_settings(Settings(user_name="R. Boyle"), profile.settings_dir)
    StampLibrary.load(profile.stamps_dir).add_text_stamp("Checked", ["CHECKED"])
    SignatureLibrary.load(profile.signatures_dir)
    files = {p.relative_to(profile.root).as_posix() for p in profile.root.rglob("*") if p.is_file()}
    assert files == {"settings.json", "stamps/stamps.json"}


def test_same_profile_on_another_machine(tmp_path):
    """A roaming profile is the same folder wherever the user logs in."""
    shared = tmp_path / "roaming-share" / "PlanetPDF"
    machine_a = Profile(shared).ensure()
    save_settings(Settings(user_name="Robert"), machine_a.settings_dir)
    StampLibrary.load(machine_a.stamps_dir).add_text_stamp("Filed", ["FILED"])
    machine_b = Profile(shared).ensure()
    assert load_settings(machine_b.settings_dir).user_name == "Robert"
    assert [s.name for s in StampLibrary.load(machine_b.stamps_dir).custom] == ["Filed"]


def test_legacy_settings_migrated_once(tmp_path):
    legacy = tmp_path / "local-config"
    save_settings(Settings(user_name="Old"), legacy)
    profile = Profile(tmp_path / "roaming").ensure()
    assert [p.name for p in migrate_legacy(profile, [legacy])] == ["settings.json"]
    assert load_settings(profile.settings_dir).user_name == "Old"
    save_settings(Settings(user_name="New"), profile.settings_dir)
    assert migrate_legacy(profile, [legacy]) == []  # never overwrites
    assert load_settings(profile.settings_dir).user_name == "New"
