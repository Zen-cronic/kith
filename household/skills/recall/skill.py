"""The product-recall skill: money owed to the household. A recalled product becomes a read-only CPSC lookup and a
claim email to the manufacturer that quotes the published remedy word for word. No money moves in this ledger."""

from __future__ import annotations

from pydantic import Field

from ...config import ROOT
from ...schemas import AgentOutput
from ..base import Skill
from .rules import RULES
from .templates import TEMPLATES
from .tools import TOOLS


class RecallEvidence(AgentOutput):
    member_id: str = Field(description="The member whose product it is (the subject)")
    product: str = Field(description="The product exactly as the member described it")
    recall_number_text: str = Field(description="The recall number exactly as written, or ''")
    purchase_text: str = Field(description="Where and when it was bought, exactly as written, or ''")


RECALL = Skill(
    id="recall",
    name="Product recall (money owed)",
    action_types=("recall:remedy", "email:send"),
    tools=TOOLS,
    rules=RULES,
    evidence_schema=RecallEvidence,
    action_templates=TEMPLATES,
    fixtures_dir=ROOT / "fixtures" / "skills" / "recall",
    prompt_block=(
        "A member says a product they own was recalled, or asks whether it was. Call lookup_recall with the recall\n"
        "number or words from the product; if nothing matches, say so in needs and propose nothing. Then call\n"
        "propose_recall_claim and copy both of its actions unchanged: one recall:remedy lookup (read-only) and one\n"
        "email:send to the manufacturer's consumer contact. The email body quotes the remedy and the recall URL\n"
        "word for word; never paraphrase a remedy or promise an amount the record does not state. Nothing\n"
        "money-moving is proposed: the refund is the manufacturer's to pay."
    ),
    matcher_hints=("recall", "recalled", "CPSC", "safety notice", "product safety"),
)
