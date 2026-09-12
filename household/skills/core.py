"""The core "skill": plain payments between household accounts and plain emails. It has no tools of its own; the
planner uses it when no domain skill matches. Authority still decides every action in code."""

from __future__ import annotations

from pydantic import Field

from ..config import ROOT
from ..schemas import AgentOutput
from .base import ActionTemplate, RulesTable, Skill


class CoreEvidence(AgentOutput):
    subject_member_id: str = Field(description="Whose affairs the payment or email concerns")
    amount_text: str = Field(description="The amount exactly as written, or '' for an email")
    payee: str = Field(description="Who is paid or emailed, exactly as written")
    purpose: str = Field(description="What it is for, in the requester's words")
    due_text: str = Field(description="A due date exactly as written, or ''")


PAYMENT = ActionTemplate(
    "payment:transfer",
    "internal-ledger",
    "COMPLETE (internal household ledger, no bank rail)",
    ("payee", "purpose", "due"),
    "Moves money from the household account inside this app's ledger. No bank is touched.",
)
EMAIL = ActionTemplate(
    "email:send",
    "ses-email",
    "COMPLETE when SES is live and the recipient is verified; SIMULATED otherwise",
    ("subject", "body"),
    "Sends one email on the member's behalf. The recipient must be given in the request.",
)

RULES = RulesTable(
    title="Household authority rules (decided in code by authority.decide)",
    params={
        "self_confirm_limit": "an adult acting for themself confirms above the household self-confirm limit",
        "spouse grants": "an adult acting for the other adult needs an active, in-scope, under-limit grant from them",
        "minors": "a minor's payment or email always waits for a guardian",
    },
)

HOUSEHOLD = Skill(
    id="household",
    name="Household payments and email (core)",
    action_types=("payment:transfer", "email:send"),
    tools=(),
    rules=RULES,
    evidence_schema=CoreEvidence,
    action_templates={"payment:transfer": PAYMENT, "email:send": EMAIL},
    fixtures_dir=ROOT / "fixtures" / "requests",
    prompt_block=(
        "Plain household payments and emails. Propose exactly what was asked, with the amount and payee quoted from\n"
        "the intake. If the request cites a grant, put its id in claimed_grant_id; never invent one. When the\n"
        "authority's reasons say a limit was exceeded, you may split the amount: one action within the limit and one\n"
        "for the remainder, each with its own rationale, so the remainder can wait for approval."
    ),
    matcher_hints=("pay", "payment", "deposit", "fee", "tuition", "transfer", "e-transfer", "email", "e-mail", "message"),
)
