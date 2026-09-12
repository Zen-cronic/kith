"""The benefits skill: coordinate a dental or health claim between two plans, prepare the secondary claim, and email
the packet to whoever files it."""

from __future__ import annotations

from pydantic import Field

from ...config import ROOT
from ...schemas import AgentOutput
from ..base import Skill
from .form import FORMS
from .rules import RULES
from .templates import TEMPLATES
from .tools import TOOLS


class BenefitsEvidence(AgentOutput):
    claimant_member_id: str = Field(description="The member the service was for")
    insurer: str = Field(description="The insurer named on the statement, exactly as written")
    service_date_text: str = Field(description="The service date exactly as written")
    billed_text: str = Field(description="The amount billed, exactly as written")
    primary_paid_text: str = Field(description="What the first plan paid, exactly as written")
    provider: str = Field(description="The clinic or provider, exactly as written")


BENEFITS = Skill(
    id="benefits",
    name="Benefits (coordination of benefits)",
    action_types=("benefits:claim", "email:send"),
    tools=TOOLS,
    rules=RULES,
    evidence_schema=BenefitsEvidence,
    action_templates=TEMPLATES,
    fixtures_dir=ROOT / "fixtures" / "requests",
    prompt_block=(
        "An explanation of benefits (EOB) or a dental/health statement. Call plan_lookup for the claimant and\n"
        "cob_order with the claimant and the service date; call residual with the billed amount and what the primary\n"
        "plan paid. Propose one benefits:claim on the official-form rail for the secondary plan with the residual as\n"
        "claim_amount (it is prepared for a person to file; never say it was submitted), and one email:send of the\n"
        "packet to the requester's own email when they asked for it. Quote every amount and date from the intake."
    ),
    matcher_hints=("EOB", "dental", "benefits", "Sun Life", "Manulife", "claim"),
    form_builders=FORMS,
)
