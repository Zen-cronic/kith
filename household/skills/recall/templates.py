"""Product-recall action templates. The lookup is read-only; the claim email is written from the record's own words."""

from ..base import ActionTemplate

REMEDY = ActionTemplate(
    "recall:remedy",
    "external-api-readonly",
    "COMPLETE for a live CPSC lookup; SIMULATED-replay from the recorded record; SIMULATED when neither is available",
    ("recall_number", "product"),
    "Looks up the CPSC recall record (read-only, never writes) so the remedy can be quoted word for word.",
)
CLAIM_EMAIL = ActionTemplate(
    "email:send",
    "ses-email",
    "COMPLETE when SES is live and the recipient is verified; SIMULATED otherwise",
    ("subject", "body"),
    "Emails the manufacturer's consumer contact to claim the remedy. The body quotes the record's remedy text and "
    "the recall URL verbatim; the recipient is the ConsumerContact email, else the member's own email with a needs entry.",
)

TEMPLATES = {REMEDY.action_type: REMEDY, CLAIM_EMAIL.action_type: CLAIM_EMAIL}
