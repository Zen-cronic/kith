"""Benefits action templates. The claim is prepared for a human to file; it is never submitted by this app."""

from ..base import ActionTemplate

CLAIM = ActionTemplate(
    "benefits:claim",
    "official-form",
    "PREPARE-ONLY, always",
    ("insurer", "plan_id", "service_date", "billed", "primary_paid", "claim_amount", "provider"),
    "Renders the secondary (coordination of benefits) claim with source quotes for the member to review and file.",
)
PACKET_EMAIL = ActionTemplate(
    "email:send",
    "ses-email",
    "COMPLETE when SES is live and the recipient is verified; SIMULATED otherwise",
    ("subject", "body"),
    "Emails the prepared claim packet to the member who will file it.",
)

TEMPLATES = {CLAIM.action_type: CLAIM, PACKET_EMAIL.action_type: PACKET_EMAIL}
