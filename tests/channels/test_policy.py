"""Per-channel trust policy: where money/consent may complete inline, when it must escalate, and signed links."""

import pytest

from household.channels.policy import (
    EscalationError,
    escalation_link,
    may_complete_inline,
    requires_escalation,
    trust_for,
    verify_escalation_link,
)

SECRET = b"ch0-escalation-secret-32-bytes!!"
NOW = 1_000_000.0
BASE = "https://home.example.test"


def test_trust_tiers_and_inline_completion() -> None:
    for channel in ("web", "voice", "cli"):
        assert trust_for(channel) == "high"
        assert may_complete_inline(channel) is True
    for channel in ("telegram", "sms"):
        assert trust_for(channel) == "low"
        assert may_complete_inline(channel) is False
    assert may_complete_inline("mcp") is False
    assert may_complete_inline("mcp", authenticated=True) is True
    assert trust_for("mcp", authenticated=True) == "high"
    assert trust_for("unheard-of-channel") == "low"


def test_requires_escalation_rules() -> None:
    # needs-approval always escalates, whatever the surface.
    assert requires_escalation("needs-approval", "web") is True
    assert requires_escalation("needs-approval", "telegram") is True
    # A money/send/file action escalates on a low-trust surface but completes inline on a high-trust one.
    assert requires_escalation("allow", "telegram", moves_value=True) is True
    assert requires_escalation("allow", "sms", moves_value=True) is True
    assert requires_escalation("allow", "web", moves_value=True) is False
    assert requires_escalation("allow", "mcp", moves_value=True) is True
    assert requires_escalation("allow", "mcp", authenticated=True, moves_value=True) is False
    # A pure read/propose (no value moved) never escalates on an allow.
    assert requires_escalation("allow", "telegram", moves_value=False) is False


def test_escalation_link_round_trips() -> None:
    link = escalation_link("act-1", "ama", secret=SECRET, now=NOW, base_url=BASE)
    assert link.startswith(f"{BASE}/approve?token=")
    assert verify_escalation_link(link, secret=SECRET, now=NOW + 1) == ("act-1", "ama")
    token = link.rsplit("token=", 1)[-1]
    assert verify_escalation_link(token, secret=SECRET, now=NOW + 1) == ("act-1", "ama")


def test_escalation_link_rejects_tamper_and_expiry() -> None:
    link = escalation_link("act-1", "ama", secret=SECRET, now=NOW, ttl_seconds=900, base_url=BASE)
    with pytest.raises(EscalationError, match="expired"):
        verify_escalation_link(link, secret=SECRET, now=NOW + 901)
    tampered = link[:-1] + ("0" if link[-1] != "0" else "1")
    with pytest.raises(EscalationError, match="signature"):
        verify_escalation_link(tampered, secret=SECRET, now=NOW + 1)
    with pytest.raises(EscalationError, match="signature"):
        verify_escalation_link(link, secret=b"a-different-secret-of-length-32!", now=NOW + 1)
