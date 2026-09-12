"""Per-channel trust policy. A high-trust surface (web/voice/cli) can collect a PIN and complete money/consent inline;
a low-trust surface (telegram/sms, and mcp until its session authenticates) may only read and propose, so anything
that moves money, sends, or files must escalate to an authenticated web approval surface via a signed one-time link.

This module governs WHERE approval may be collected. It does not decide or execute anything: authority.decide /
executor.execute / execute_approved remain the sole decider. Adapters (later packets) call into here; nothing is
wired into the pipeline yet.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Literal

from ..identity import sign_hmac

Channel = Literal["web", "voice", "cli", "mcp", "telegram", "sms"]
TrustTier = Literal["high", "low"]

ESCALATION_TTL_SECONDS = 900


class EscalationError(Exception):
    """A malformed, tampered, or expired escalation token."""


# mcp is listed low here and promoted to high by trust_for() only when its session is authenticated.
TRUST: dict[str, TrustTier] = {
    "web": "high",
    "voice": "high",
    "cli": "high",
    "mcp": "low",
    "telegram": "low",
    "sms": "low",
}


def trust_for(channel: str, *, authenticated: bool = False) -> TrustTier:
    """The trust tier for `channel`. mcp resolves by session state (high only when authenticated); unknown -> low."""
    if channel == "mcp":
        return "high" if authenticated else "low"
    return TRUST.get(channel, "low")


def may_complete_inline(channel: str, *, authenticated: bool = False) -> bool:
    """True when this surface may collect a PIN and complete money/consent inline (i.e. it is high-trust)."""
    return trust_for(channel, authenticated=authenticated) == "high"


def requires_escalation(
    decision_outcome: str, channel: str, *, authenticated: bool = False, moves_value: bool = False
) -> bool:
    """Escalate when authority asked for approval, or when a money/send/file action lands on a low-trust surface.

    `moves_value` is the packet's "the action moves money / sends / files" flag, supplied by the caller from the
    proposal's action type (the signature is otherwise as specified; this keyword is the silent-point decision).
    """
    if decision_outcome == "needs-approval":
        return True
    return moves_value and trust_for(channel, authenticated=authenticated) == "low"


def _escalation_payload(action_id: str, member_id: str, expires: int, nonce: str) -> str:
    # Domain-separated ("escalation") so this token can never be redeemed as an enrollment code.
    return "escalation|" + "|".join([action_id, member_id, str(expires), nonce])


def escalation_link(
    action_id: str,
    member_id: str,
    *,
    secret: bytes,
    now: float,
    ttl_seconds: int = ESCALATION_TTL_SECONDS,
    base_url: str,
) -> str:
    """A signed, single-use URL to the authenticated web approval surface. `now` is epoch seconds (as SessionSigner).

    Token shape: `action_id.member_id.expires.nonce.signature`, carried as `?token=`. Single-use enforcement (recording
    the nonce) is the adapter's job when it consumes the link; this only mints and signs it.
    """
    expires = int(now) + ttl_seconds
    nonce = secrets.token_hex(16)
    signature = sign_hmac(secret, _escalation_payload(action_id, member_id, expires, nonce))
    token = ".".join([action_id, member_id, str(expires), nonce, signature])
    return f"{base_url.rstrip('/')}/approve?token={token}"


def verify_escalation_link(token_or_url: str, *, secret: bytes, now: float) -> tuple[str, str]:
    """Verify an escalation token (or full URL) and return (action_id, member_id). Raises EscalationError otherwise."""
    token = token_or_url.rsplit("token=", 1)[-1]
    parts = token.split(".")
    if len(parts) != 5 or not parts[2].isdigit():
        raise EscalationError("malformed escalation token")
    action_id, member_id, expires_raw, nonce, signature = parts
    expires = int(expires_raw)
    expected = sign_hmac(secret, _escalation_payload(action_id, member_id, expires, nonce))
    if not hmac.compare_digest(expected, signature):
        raise EscalationError("escalation token signature does not verify")
    if expires <= now:
        raise EscalationError("escalation token has expired")
    return action_id, member_id
