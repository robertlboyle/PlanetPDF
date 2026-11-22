

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
    assert s.timestamped and "don't trust" in s.summary
