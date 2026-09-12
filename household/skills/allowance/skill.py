"""The allowance skill: a child spends their own allowance within the rule their parents set."""

from __future__ import annotations

from pydantic import Field

from ...config import ROOT
from ...schemas import AgentOutput
from ..base import Skill
from .rules import RULES
from .templates import TEMPLATES
from .tools import TOOLS


class AllowanceEvidence(AgentOutput):
    member_id: str = Field(description="The child whose allowance is used")
    amount_text: str = Field(description="The amount exactly as the child wrote or said it")
    purpose: str = Field(description="What it is for, in the child's words")
    when_text: str = Field(description="When it is needed, exactly as written, or ''")


ALLOWANCE = Skill(
    id="allowance",
    name="Allowance",
    action_types=("allowance:transfer",),
    tools=TOOLS,
    rules=RULES,
    evidence_schema=AllowanceEvidence,
    action_templates=TEMPLATES,
    fixtures_dir=ROOT / "fixtures" / "requests",
    prompt_block=(
        "A child asks to use their own allowance. Call allowance_balance for the child first, then propose_allowance\n"
        "with the amount exactly as written and a memo in the child's words. Propose one allowance:transfer only;\n"
        "if the amount is above the child's auto_limit or weekly cap, still propose it once (authority routes it to\n"
        "a guardian) and say so in notes. Never propose more than the child asked for."
    ),
    matcher_hints=("allowance", "pocket money", "book fair", "chores"),
)
