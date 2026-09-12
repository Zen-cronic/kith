"""Executor boundary: the only entry point is `execute`, which is idempotent and dispatches to one of five rails.

Rail functions are never model tools. A receipt's mode is decided in code from the environment (`receipts.label_for`):
COMPLETE only when a real side effect happened, PREPARE-ONLY for official forms, SIMULATED-replay for a recorded
response, SIMULATED otherwise. The same proposal on the same day returns the receipt already issued rather than
acting twice.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from ..config import Settings, load_settings
from ..model import ActionProposal, Household, Rail, Receipt, as_utc
from . import forms, idempotency, rails, receipts
from .receipts import (
    FORM_LABEL,
    LEDGER_INSUFFICIENT_LABEL,
    LEDGER_LABEL,
    LIVE_LOOKUP_LABEL,
    NO_RECORD_LABEL,
    REPLAY_LABEL,
    SES_LIVE_LABEL,
    SES_NO_FROM_LABEL,
    SES_NO_RECIPIENT_LABEL,
    SES_UNVERIFIED_LABEL,
    SIMULATED_LABEL,
    STRIPE_LIVE_LABEL,
    STRIPE_NO_KEY_LABEL,
)

__all__ = [
    "RAILS", "RAIL_FUNCTIONS", "ExecutionReport", "SIMULATED_LABEL", "STUB_LABEL", "execute", "execute_many",
    "forms", "idempotency", "rails", "receipts", "record", "request_digest",
]

RailFunction = Callable[[ActionProposal, Household, Settings, datetime, dict[str, Any] | None], receipts.RailResult]

RAIL_FUNCTIONS: dict[Rail, RailFunction] = {
    "ses-email": rails.ses_email,
    "internal-ledger": rails.internal_ledger,
    "stripe-test": rails.stripe_test,
    "official-form": rails.form_render,
    "external-api-readonly": rails.external_readonly,
}

# Metadata for the README "What is real" table (`household rails --write-readme`) and the UI panel. `reasons` are the
# exact label_reason strings a receipt on that rail can carry.
RAILS: dict[Rail, dict[str, Any]] = {
    "ses-email": {
        "name": "Amazon SES email",
        "label": "COMPLETE when EXECUTION_MODE=live and the recipient is a verified identity; SIMULATED otherwise",
        "real": "A real email leaves the account through SES; the MessageId is the provider_ref. The sandbox only delivers to verified identities, listed once at startup.",
        "reasons": (SES_LIVE_LABEL, SES_UNVERIFIED_LABEL, SES_NO_FROM_LABEL, SES_NO_RECIPIENT_LABEL, SIMULATED_LABEL),
    },
    "internal-ledger": {
        "name": "Internal household ledger",
        "label": "COMPLETE when EXECUTION_MODE=live (internal household ledger, no bank rail); SIMULATED otherwise",
        "real": "A double-entry posting in this app's own ledger; household and allowance balances never go negative, and an outside payee is an external counterparty account. No bank is touched.",
        "reasons": (LEDGER_LABEL, LEDGER_INSUFFICIENT_LABEL, SIMULATED_LABEL),
    },
    "stripe-test": {
        "name": "Stripe (test mode)",
        "label": "COMPLETE (test mode) when EXECUTION_MODE=live and an sk_test_ key is present; SIMULATED otherwise",
        "real": "A Stripe test-mode PaymentIntent created over HTTPS with the test key; no real money moves, and a live key is refused.",
        "reasons": (STRIPE_LIVE_LABEL, STRIPE_NO_KEY_LABEL, SIMULATED_LABEL),
    },
    "official-form": {
        "name": "Official form (prepared, not filed)",
        "label": "PREPARE-ONLY, always",
        "real": "Renders the skill's field schema with verbatim source quotes to runs/forms/<action_id>.md for a human to review and file. Never submits.",
        "reasons": (FORM_LABEL,),
    },
    "external-api-readonly": {
        "name": "External read-only lookup",
        "label": "COMPLETE for a live lookup; SIMULATED-replay from a recorded response; SIMULATED when neither is available",
        "real": "Reads public data (CPSC recalls, AeroAPI flight status). Never writes anywhere; a replayed receipt names the date the record was fetched.",
        "reasons": (
            LIVE_LOOKUP_LABEL.format(source="CPSC"), REPLAY_LABEL.format(source="CPSC", fetched="<date>"),
            NO_RECORD_LABEL.format(source="CPSC"),
        ),
    },
}

STUB_LABEL = SIMULATED_LABEL  # P1 name, kept so callers written against the stub keep importing


def request_digest(proposal: ActionProposal) -> str:
    """P1 name: sha256 of the canonical proposal JSON, excluding the day-scoped idempotency key."""
    return receipts.proposal_digest(proposal)


def resolve_settings(settings: Settings | None, execution_mode: str | None) -> Settings:
    if settings is None:
        return load_settings(execution_mode=execution_mode)
    if execution_mode and execution_mode != settings.execution_mode:
        return replace(settings, execution_mode=execution_mode)
    return settings


def execute(
    proposal: ActionProposal,
    household: Household,
    settings: Settings | None = None,
    *,
    execution_mode: str | None = None,
    now: datetime | None = None,
    grant_id: str | None = None,
    outputs: dict[str, Any] | None = None,
) -> Receipt:
    """Return the receipt for this proposal, executing its rail if none exists yet.

    Fills `proposal.idempotency_key` when empty. A key that already has a receipt returns that receipt untouched
    (the caller logs it as skipped=["duplicate"]). `settings` defaults to the environment; `execution_mode` overrides
    it. Rails may put non-receipt outputs (a looked-up record, a form path) under `outputs[proposal.id]`.
    """
    at = as_utc(now) if now is not None else datetime.now(UTC)
    if not proposal.idempotency_key:
        proposal.idempotency_key = idempotency.key(proposal, household.id, at.date().isoformat())
    prior = idempotency.is_duplicate(proposal.idempotency_key, household)
    if prior is not None:
        return prior
    resolved = resolve_settings(settings, execution_mode)
    result = RAIL_FUNCTIONS[proposal.rail](proposal, household, resolved, at, outputs)
    return receipts.build(proposal, result, at, grant_id)


@dataclass
class ExecutionReport:
    receipts: list[Receipt] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (action_id, reason)


def execute_many(
    proposals: Iterable[ActionProposal],
    household: Household,
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
    grant_ids: Mapping[str, str | None] | None = None,
    outputs: dict[str, Any] | None = None,
) -> ExecutionReport:
    """Execute allowed proposals in order. Each receipt is recorded on its action record when one exists, so a
    duplicate later in the same batch returns the earlier receipt and is listed under `skipped`."""
    at = as_utc(now) if now is not None else datetime.now(UTC)
    report = ExecutionReport()
    for proposal in proposals:
        if not proposal.idempotency_key:
            proposal.idempotency_key = idempotency.key(proposal, household.id, at.date().isoformat())
        duplicate = idempotency.is_duplicate(proposal.idempotency_key, household) is not None
        receipt = execute(proposal, household, settings, now=at, grant_id=(grant_ids or {}).get(proposal.id), outputs=outputs)
        report.receipts.append(receipt)
        if duplicate:
            report.skipped.append((proposal.id, "duplicate"))
        elif household.action(proposal.id) is not None:
            record(household, receipt)
    return report


def record(household: Household, receipt: Receipt) -> None:
    """Attach the receipt to its action record and the household receipt list, exactly once."""
    action = household.action(receipt.action_id)
    if action is None:
        raise KeyError(f"no action record for {receipt.action_id!r}; append the ActionRecord before recording")
    action.receipt = receipt
    if not any(r.id == receipt.id for r in household.receipts):
        household.receipts.append(receipt)
