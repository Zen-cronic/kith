"""Shared identity seam: turn a channel-native id into an authenticated Member, and bind new handles.

Resolution is a thin delegate to `Household.member_by_handle`. Enrollment codes are HMAC-signed (the same pattern as
the web `SessionSigner`): a member authorises binding a channel id from an already-trusted surface with their PIN, gets
a short-lived code, and redeems it once on the new channel. This module only resolves and binds identity; it never
decides or executes an action — `authority.decide` / `executor.execute` / `execute_approved` stay the sole decider.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from .model import Household, Member

ENROLLMENT_TTL_SECONDS = 900


class EnrollmentError(Exception):
    """A bad, expired, replayed, or wrong-shape enrollment code."""


def resolve(channel: str, native_id: str, household: Household) -> Member | None:
    """The authenticated member bound to `native_id` on `channel`, or None when the handle is unknown/unbound."""
    return household.member_by_handle(channel, native_id)


def sign_hmac(secret: bytes, payload: str) -> str:
    """HMAC-SHA256 hex digest over `payload`, mirroring web SessionSigner._sign."""
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def _enrollment_payload(member_id: str, channel: str, expires: int, nonce: str) -> str:
    # Domain-separated ("enroll") so an enrollment code can never be replayed as an escalation link, or vice versa.
    return "enroll|" + "|".join([member_id, channel, str(expires), nonce])


def mint_enrollment_code(
    member_id: str, channel: str, *, secret: bytes, now: float, ttl_seconds: int = ENROLLMENT_TTL_SECONDS
) -> str:
    """A short-lived, single-use code binding `channel` to `member_id`. `now` is epoch seconds (as SessionSigner).

    Shape: `member_id.channel.expires.nonce.signature` (dot-joined, like the session token). The caller has already
    authorised this on a trusted surface (a PIN check); this only issues the code.
    """
    expires = int(now) + ttl_seconds
    nonce = secrets.token_hex(16)
    signature = sign_hmac(secret, _enrollment_payload(member_id, channel, expires, nonce))
    return ".".join([member_id, channel, str(expires), nonce, signature])


def redeem_enrollment_code(code: str, native_id: str, household: Household, *, secret: bytes, now: float) -> Member:
    """Verify signature + expiry + single-use, bind `member.handles[channel] = native_id`, and return the member.

    Raises EnrollmentError on a malformed, tampered, expired, replayed code, or an unknown member.
    """
    parts = (code or "").split(".")
    if len(parts) != 5 or not parts[2].isdigit():
        raise EnrollmentError("malformed enrollment code")
    member_id, channel, expires_raw, nonce, signature = parts
    expires = int(expires_raw)
    expected = sign_hmac(secret, _enrollment_payload(member_id, channel, expires, nonce))
    if not hmac.compare_digest(expected, signature):
        raise EnrollmentError("enrollment code signature does not verify")
    if expires <= now:
        raise EnrollmentError("enrollment code has expired")
    if nonce in household.redeemed_enrollment_nonces:
        raise EnrollmentError("enrollment code has already been redeemed")
    member = household.member(member_id)
    if member is None:
        raise EnrollmentError(f"unknown member {member_id!r} in household {household.id!r}")
    member.handles[channel] = native_id
    household.redeemed_enrollment_nonces.add(nonce)
    return member
