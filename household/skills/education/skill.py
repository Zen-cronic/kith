"""The education-money skill: RESP contributions with the CESG grant room shown and cited, and tuition instalments
checked against their due date. Both are payments on the internal ledger; authority decides who may make them."""

from __future__ import annotations

from pydantic import Field

from ...config import ROOT
from ...schemas import AgentOutput
from ..base import Skill
from .rules import RULES
from .templates import TEMPLATES
from .tools import TOOLS


class EducationEvidence(AgentOutput):
    beneficiary_member_id: str = Field(description="The child whose RESP it is, or the member whose tuition it is")
    amount_text: str = Field(description="The amount exactly as written")
    payee: str = Field(description="'RESP' or the school or program exactly as written")
    due_text: str = Field(description="The due date exactly as written, or ''")
    purpose: str = Field(description="What it is for, in the member's words")


EDUCATION = Skill(
    id="education",
    name="Education money (RESP grants and tuition)",
    action_types=("payment:transfer",),
    tools=TOOLS,
    rules=RULES,
    evidence_schema=EducationEvidence,
    action_templates=TEMPLATES,
    fixtures_dir=ROOT / "fixtures" / "requests",
    prompt_block=(
        "Money for a child's education. For an RESP contribution: call cesg_room for the beneficiary with the amount\n"
        "exactly as written, then propose_resp_contribution and copy its proposal unchanged; the CESG numbers in\n"
        "grant_room_note and notes come from the tool, never from you. A child asking for RESP money is proposed\n"
        "once (authority routes it to a guardian). For a tuition instalment: call tuition_schedule with the payee\n"
        "and the due date as written; if it says past_due, propose nothing and explain in needs; otherwise call\n"
        "propose_tuition_payment and copy its proposal unchanged. Both templates are payment:transfer on the\n"
        "internal ledger; kind in the payload says which one."
    ),
    matcher_hints=("RESP for", "'s RESP", "my RESP", "RESP contribution", "education savings", "CESG", "tuition", "instalment", "installment"),
)
