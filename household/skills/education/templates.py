"""Education-money action templates. Both move money on the internal ledger as payment:transfer, the only ledger
action type for a payee outside the household; the template name (resp:contribute, tuition:pay) says which story it
tells and which payload the planner fills, and `kind` in the payload carries that name onto the receipt."""

from ..base import ActionTemplate

RESP_CONTRIBUTE = ActionTemplate(
    "payment:transfer",
    "internal-ledger",
    "COMPLETE (internal household ledger, no bank rail)",
    ("kind", "beneficiary_member_id", "year_contributed_so_far", "grant_room_note"),
    "resp:contribute, a contribution to a child's RESP: recipient is 'RESP for <child first name>' (the ledger keeps "
    "it as ext-resp-for-<child>), kind is 'resp:contribute', and grant_room_note is copied from cesg_room, never "
    "computed by you.",
)
TUITION_PAY = ActionTemplate(
    "payment:transfer",
    "internal-ledger",
    "COMPLETE (internal household ledger, no bank rail)",
    ("kind", "payee", "due", "instalment"),
    "tuition:pay, one tuition instalment to a school or program: kind is 'tuition:pay', due is the due date exactly "
    "as written, instalment says which one (e.g. '2 of 3') or ''. An instalment whose due date is already past is "
    "never proposed; it goes to needs.",
)

TEMPLATES = {"resp:contribute": RESP_CONTRIBUTE, "tuition:pay": TUITION_PAY}
