"""Allowance action templates."""

from ..base import ActionTemplate

TRANSFER = ActionTemplate(
    "allowance:transfer",
    "internal-ledger",
    "COMPLETE (internal household ledger, no bank rail)",
    ("memo",),
    "Moves money from the child's allowance account to the household account for a stated purpose.",
)

TEMPLATES = {TRANSFER.action_type: TRANSFER}
