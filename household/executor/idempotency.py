"""Idempotency keys are computed in code, never by the model: the same request on the same day is one action."""

from __future__ import annotations

import hashlib

from ..model import ActionProposal, Household, Receipt


def key(proposal: ActionProposal, household_id: str, day_iso: str) -> str:
    """sha256 of household|action_type|subject|recipient|amount|sorted evidence refs|day."""
    parts = [
        household_id,
        proposal.action_type,
        proposal.subject_member_id,
        proposal.recipient or "",
        proposal.amount or "",
        ",".join(sorted(proposal.evidence_refs)),
        day_iso,
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def is_duplicate(idempotency_key: str, household: Household) -> Receipt | None:
    """The receipt already issued for this key, if any. Records without a receipt are not duplicates."""
    if not idempotency_key:
        return None
    for record in household.actions:
        if record.proposal.idempotency_key == idempotency_key and record.receipt is not None:
            return record.receipt
    return None
