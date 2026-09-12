"""The shared identity seam: handle resolution, and single-use HMAC enrollment codes that bind a channel handle."""

import json

import pytest

from household.config import ROOT
from household.identity import (
    EnrollmentError,
    mint_enrollment_code,
    redeem_enrollment_code,
    resolve,
)
from household.model import Household

DEMO = ROOT / "fixtures" / "households" / "demo.json"
SECRET = b"ch0-enrollment-secret-32-bytes!!"
NOW = 1_000_000.0


def demo() -> Household:
    return Household.model_validate(json.loads(DEMO.read_text(encoding="utf-8")))


def test_bound_handle_resolves_to_the_right_member() -> None:
    hh = demo()
    assert resolve("telegram", "8675309", hh).id == "ama"
    assert resolve("sms", "+16475551234", hh).id == "daniel"


def test_unknown_or_unbound_handle_resolves_to_none() -> None:
    hh = demo()
    assert resolve("telegram", "0000", hh) is None
    assert resolve("sms", "+15550000000", hh) is None
    assert resolve("telegram", "+16475551234", hh) is None  # right id, wrong channel


def test_sms_match_is_normalized() -> None:
    hh = demo()
    assert resolve("sms", "  +16475551234  ", hh).id == "daniel"


def test_mint_then_redeem_binds_the_handle_and_is_single_use() -> None:
    hh = demo()
    code = mint_enrollment_code("kofi", "telegram", secret=SECRET, now=NOW)
    member = redeem_enrollment_code(code, "42424242", hh, secret=SECRET, now=NOW + 1)
    assert member.id == "kofi"
    assert hh.member("kofi").handles["telegram"] == "42424242"
    assert resolve("telegram", "42424242", hh).id == "kofi"
    with pytest.raises(EnrollmentError, match="already been redeemed"):
        redeem_enrollment_code(code, "42424242", hh, secret=SECRET, now=NOW + 2)


def test_expired_code_raises() -> None:
    hh = demo()
    code = mint_enrollment_code("kofi", "sms", secret=SECRET, now=NOW, ttl_seconds=900)
    with pytest.raises(EnrollmentError, match="expired"):
        redeem_enrollment_code(code, "+15551112222", hh, secret=SECRET, now=NOW + 901)


def test_tampered_code_raises() -> None:
    hh = demo()
    code = mint_enrollment_code("kofi", "telegram", secret=SECRET, now=NOW)
    tampered = code[:-1] + ("0" if code[-1] != "0" else "1")
    with pytest.raises(EnrollmentError, match="signature"):
        redeem_enrollment_code(tampered, "42424242", hh, secret=SECRET, now=NOW + 1)


def test_wrong_secret_and_malformed_shape_raise() -> None:
    hh = demo()
    code = mint_enrollment_code("kofi", "telegram", secret=SECRET, now=NOW)
    with pytest.raises(EnrollmentError, match="signature"):
        redeem_enrollment_code(code, "42424242", hh, secret=b"a-different-secret-of-length-32!", now=NOW + 1)
    with pytest.raises(EnrollmentError, match="malformed"):
        redeem_enrollment_code("not.a.valid.code", "42424242", hh, secret=SECRET, now=NOW + 1)


def test_unknown_member_in_code_raises_without_recording_nonce() -> None:
    hh = demo()
    code = mint_enrollment_code("ghost", "telegram", secret=SECRET, now=NOW)
    with pytest.raises(EnrollmentError, match="unknown member"):
        redeem_enrollment_code(code, "42424242", hh, secret=SECRET, now=NOW + 1)
    assert not hh.redeemed_enrollment_nonces
