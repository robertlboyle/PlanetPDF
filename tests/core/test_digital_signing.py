

# --- revocation and timestamps (security review 2026-11-21) -------------------------------------------


def _status(**kw):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from pyhanko.sign.validation.status import SignatureCoverageLevel

    subject = SimpleNamespace(native={"common_name": "Pat Signer"}, human_friendly="Pat Signer")
    values = dict(signing_cert=SimpleNamespace(subject=subject), intact=True, valid=True, trusted=True,
                  coverage=SignatureCoverageLevel.ENTIRE_FILE, modification_level=None, docmdp_ok=True,
                  timestamp_validity=None, signer_reported_dt=datetime(2026, 1, 2, tzinfo=timezone.utc),
                  revoked=False, revocation_details=None)
    values.update(kw)
    return SimpleNamespace(**values)


def test_a_revoked_certificate_makes_the_signature_invalid():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from planetpdf.core.signing.digital import Verdict, _summarize

    revoked = _status(revoked=True, revocation_details=SimpleNamespace(
        revocation_date=datetime(2026, 3, 4, tzinfo=timezone.utc)))
    s = _summarize("Sig1", revoked, revocation_checked=True)
    assert s.verdict is Verdict.INVALID and s.revoked and "revoked on March 04, 2026" in s.summary


def test_each_signature_says_whether_revocation_was_checked():
    from planetpdf.core.signing.digital import Verdict, _summarize

    offline = _summarize("Sig1", _status())
    assert offline.verdict is Verdict.VALID and "wasn't checked" in offline.summary
    online = _summarize("Sig1", _status(), revocation_checked=True)
    assert online.revocation_checked and "checked online" in online.summary


def test_a_timestamp_that_doesnt_check_out_proves_no_time():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from planetpdf.core.signing.digital import _summarize

    bad = SimpleNamespace(intact=False, valid=False, trusted=False, timestamp=datetime(2020, 1, 1, tzinfo=timezone.utc))
    s = _summarize("Sig1", _status(timestamp_validity=bad))
    assert not s.timestamped and s.signing_time.year == 2026 and "timestamp doesn't check out" in s.summary
    untrusted = SimpleNamespace(intact=True, valid=True, trusted=False, timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc))
    s = _summarize("Sig1", _status(timestamp_validity=untrusted))
    # (an untrusted server proves no time: the time shown is the signer's own; security review 2026-11-25)
    assert not s.timestamped and s.signing_time.year == 2026 and "isn't proven" in s.summary
    trusted = SimpleNamespace(intact=True, valid=True, trusted=True, timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc))
    s = _summarize("Sig1", _status(timestamp_validity=trusted))
    assert s.timestamped and s.signing_time.year == 2025


def test_revocation_is_checked_only_when_a_server_answered(pdf_copy, test_certs, tmp_path):
    """Security review 2026-11-27, finding 15: with nothing listening at the signer's revocation
    address, or no address at all, the signature said "Revocation was checked online"."""
    from planetpdf.core.signing.digital import SignOptions, sign_pdf, validate_signatures
    from planetpdf.core.signing.providers.pkcs12 import FileTrustStore, Pkcs12Provider
    from tests.fixtures.certs import PFX_PASSWORD

    target = tmp_path / "signed.pdf"
    sign_pdf(pdf_copy("text.pdf"), target, Pkcs12Provider(test_certs.pfx, PFX_PASSWORD), 0, (72, 600, 300, 680),
             SignOptions())
    [status] = validate_signatures(target, FileTrustStore(tmp_path / "t"), check_revocation=True)
    assert not status.revocation_checked
    assert "checked online" not in status.summary and "couldn't be checked" in status.summary


def test_the_command_line_fails_the_check_for_a_signature_that_isnt_valid(pdf_copy, test_certs, tmp_path, capsys):
    """A revoked (invalid) signature ended with exit code 0."""
    from planetpdf import cli
    from planetpdf.core.signing.digital import SignOptions, sign_pdf
    from planetpdf.core.signing.providers.pkcs12 import Pkcs12Provider
    from tests.fixtures.certs import PFX_PASSWORD

    target = tmp_path / "signed.pdf"
    sign_pdf(pdf_copy("text.pdf"), target, Pkcs12Provider(test_certs.pfx, PFX_PASSWORD), 0, (72, 600, 300, 680),
             SignOptions())
    assert cli.main(["planetpdf", "signatures", str(target), "--quiet"]) == cli.OK
    data = bytearray(target.read_bytes())
    at = data.find(b"stream") + 20
    data[at] ^= 0x01
    target.write_bytes(bytes(data))
    assert cli.main(["planetpdf", "signatures", str(target), "--quiet"]) == cli.CHECK_FAILED
    assert "Traceback" not in capsys.readouterr().err
