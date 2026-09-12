"""The secondary (coordination of benefits) claim form the benefits skill owns: the seven payload fields of the
`benefits:claim` template with member-readable labels, the intake line each value was quoted from, and the CLHIA
rules quoted verbatim. The official-form rail renders it for a person to review and file; it is never submitted."""

from __future__ import annotations

from ...model import ActionProposal
from ..base import FormField, FormSpec, SourceQuote
from .rules import CLHIA_URL, RULES
from .templates import CLAIM

FORM_ID = "cob-secondary-claim"
LABELS: dict[str, str] = {
    "insurer": "Insurer (secondary plan)",
    "plan_id": "Plan ID",
    "service_date": "Date of service",
    "billed": "Amount billed",
    "primary_paid": "Paid by the primary plan",
    "claim_amount": "Amount claimed (residual)",
    "provider": "Provider",
}


def field_source(proposal: ActionProposal, key: str, value: str) -> str:
    """The verbatim intake line the value was quoted from; the residual cites the rule it was computed by."""
    if key == "claim_amount":
        return RULES.params["residual"]
    return next((ref for ref in proposal.evidence_refs if value and value in ref), "")


def claim_form(proposal: ActionProposal) -> FormSpec:
    payload = proposal.payload
    fields = tuple(
        FormField(label, payload.get(key, ""), field_source(proposal, key, payload.get(key, "")))
        for key, label in LABELS.items()
    )
    quotes = tuple(SourceQuote(c.quote, f"{c.label}, {c.url}") for c in RULES.citations)
    title = f"{payload.get('insurer') or 'Secondary plan'} coordination-of-benefits claim (secondary plan)"
    return FormSpec(FORM_ID, title, CLHIA_URL, fields, quotes)


FORMS = {CLAIM.action_type: claim_form}
