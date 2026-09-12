"""Receipts: canonical digests, the honest label decided in code, and the builder every rail shares.

A receipt's `mode` is decided here from the environment and the rail's own checks, never by the model:
COMPLETE only when a real side effect happened, PREPARE-ONLY for official forms, SIMULATED-replay for a
recorded response, SIMULATED otherwise (carrying the digest of the exact request that would have been sent).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..config import Settings
from ..model import ActionProposal, Rail, Receipt, ReceiptMode

# Exact label_reason strings. tests/test_rails.py and the README table cross-check these against RAILS.
SIMULATED_LABEL = "EXECUTION_MODE=simulated: nothing sent; the receipt carries the digest of the exact request"
SES_LIVE_LABEL = "SES sandbox: verified identities only"
SES_UNVERIFIED_LABEL = "recipient not a verified SES identity (sandbox)"
SES_NO_FROM_LABEL = "SES_FROM is not set"
SES_NO_RECIPIENT_LABEL = "no recipient email address"
LEDGER_LABEL = "internal household ledger (no bank rail)"
LEDGER_INSUFFICIENT_LABEL = "insufficient balance"
STRIPE_LIVE_LABEL = "Stripe test mode (sk_test_ key): no real money moves"
STRIPE_NO_KEY_LABEL = "no Stripe test key (STRIPE_SECRET_KEY must start with sk_test_)"
FORM_LABEL = "PREPARE-ONLY: rendered for a human to review and file; never submitted"
LIVE_LOOKUP_LABEL = "live {source} lookup (read-only)"
REPLAY_LABEL = "replayed {source} record fetched {fetched}"
NO_RECORD_LABEL = "no recorded {source} response to replay"


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(value: Any) -> str:
    """sha256 of the canonical JSON of any request or response."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def proposal_digest(proposal: ActionProposal) -> str:
    """sha256 of the canonical proposal, excluding the day-scoped idempotency key (P1's `request_digest`)."""
    return digest(proposal.model_dump(mode="json", exclude={"idempotency_key"}))


@dataclass(frozen=True)
class Label:
    mode: ReceiptMode
    reason: str


@dataclass
class RailResult:
    """What a rail did, or would have done. `request` is the exact provider request (digested onto the receipt whether
    or not it was sent); `response` is the provider's reply when something real happened; `provider_ref` is the
    provider's own reference (SES MessageId, ledger entry id, form path, record URL)."""

    label: Label
    request: Any
    response: Any = None
    provider_ref: str | None = None


def label_for(rail: Rail, settings: Settings, context: Mapping[str, Any] | None = None) -> Label:
    """The label a rail earns in this environment. Context keys are the rail's own checks (`verified`, `problem`,
    `test_key`, `fetched`, `network_ok`, `source`); a missing key assumes the happy path, so `label_for(rail,
    settings)` states what this environment can do at best."""
    ctx = dict(context or {})
    live = settings.execution_mode == "live"
    if rail == "official-form":
        return Label("PREPARE-ONLY", FORM_LABEL)
    if ctx.get("problem"):
        return Label("SIMULATED", str(ctx["problem"]))
    if rail == "external-api-readonly":
        source = str(ctx.get("source", "CPSC"))
        if live and ctx.get("network_ok", True):
            return Label("COMPLETE", LIVE_LOOKUP_LABEL.format(source=source))
        if ctx.get("fetched"):
            return Label("SIMULATED-replay", REPLAY_LABEL.format(source=source, fetched=ctx["fetched"]))
        return Label("SIMULATED", NO_RECORD_LABEL.format(source=source))
    if not live:
        return Label("SIMULATED", SIMULATED_LABEL)
    if rail == "internal-ledger":
        return Label("COMPLETE", LEDGER_LABEL)
    if rail == "ses-email":
        if not settings.ses_from:
            return Label("SIMULATED", SES_NO_FROM_LABEL)
        if not ctx.get("verified", bool(settings.ses_verified_identities)):
            return Label("SIMULATED", SES_UNVERIFIED_LABEL)
        return Label("COMPLETE", SES_LIVE_LABEL)
    if rail == "stripe-test":
        if not ctx.get("test_key", settings.stripe_secret_key_present):
            return Label("SIMULATED", STRIPE_NO_KEY_LABEL)
        return Label("COMPLETE", STRIPE_LIVE_LABEL)
    raise ValueError(f"unknown rail {rail!r}")


def build(proposal: ActionProposal, result: RailResult, at: datetime, grant_id: str | None) -> Receipt:
    key = proposal.idempotency_key or proposal_digest(proposal)
    return Receipt(
        id=f"rcpt-{key[:12]}",
        action_id=proposal.id,
        rail=proposal.rail,
        mode=result.label.mode,
        provider_ref=result.provider_ref,
        request_digest=digest(result.request),
        response_digest=digest(result.response) if result.response is not None else "",
        at=at.isoformat(),
        executed_under_grant=grant_id,
        label_reason=result.label.reason,
    )
