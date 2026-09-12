"""Recall tools: one read-only search of the recorded CPSC records and one proposal builder. Neither reaches the
network; the executor's external-api-readonly rail does the live or replayed lookup."""

from __future__ import annotations

import json
from typing import Any

from strands import tool

from ..base import SkillTool, ToolContext, error_json, proposal_json
from . import rules
from .templates import CLAIM_EMAIL, REMEDY


def build_lookup_recall(ctx: ToolContext) -> Any:
    @tool
    def lookup_recall(query: str) -> str:
        """Find a product recall in the recorded CPSC records by recall number or by words from the product or its
        title. Returns the record's title, URL, remedy (verbatim), consumer contact, hazards, products, retailers and
        the date the record was fetched. Read-only; nothing is sent anywhere.

        Args:
            query: a recall number such as "26639", or words such as "Peony Design bibs".
        """
        entry = rules.find_recall(query)
        ctx.note("lookup_recall", query=query, found=entry is not None)
        if entry is None:
            numbers = [str(e["record"].get("RecallNumber")) for e in rules.recorded_recalls()]
            return error_json(f"no recorded recall matches {query!r}", recorded_recall_numbers=numbers)
        return json.dumps(rules.summary(entry), ensure_ascii=False)

    return lookup_recall


def build_propose_recall_claim(ctx: ToolContext) -> Any:
    @tool
    def propose_recall_claim(member_id: str, recall_number: str, product: str, purchase_text: str = "") -> str:
        """Build the two proposals for a recall claim: the read-only recall:remedy lookup and the email:send to the
        manufacturer's consumer contact, whose body quotes the remedy and the recall URL verbatim. Returns
        {"actions": [...], "needs": [...]}; copy the actions into the plan unchanged. It proposes only; authority decides.

        Args:
            member_id: the member the recalled product belongs to (the subject), e.g. "ama".
            recall_number: the CPSC recall number, e.g. "26639".
            product: the product as the member described it.
            purchase_text: where and when it was bought, exactly as written, or "".
        """
        member = ctx.household.member(member_id)
        if member is None:
            ctx.note("propose_recall_claim", member_id=member_id, ok=False)
            return error_json(f"{member_id!r} is not in this household")
        entry = rules.find_recall(recall_number)
        if entry is None:
            ctx.note("propose_recall_claim", recall_number=recall_number, ok=False)
            return error_json(f"no recorded recall matches {recall_number!r}")
        record = entry["record"]
        email = rules.contact_email(record)
        needs: list[str] = []
        recipient = email or member.email or ""
        if email is None:
            needs.append("manufacturer contact email not in record; the claim email is addressed to the member's own email "
                         "for them to forward")
            if not member.email:
                needs.append(f"{member.name} has no email on file, so the claim email has no recipient")
        summary = rules.summary(entry)
        evidence = [f"CPSC recall {summary['recall_number']}: {summary['title']} ({summary['url']})"]
        lookup = proposal_json(
            REMEDY,
            subject_member_id=member.id,
            payload={"recall_number": summary["recall_number"], "product": product},
            evidence_refs=evidence,
            rationale=f"Read-only lookup of CPSC recall {summary['recall_number']} so the remedy can be quoted word for word.",
        )
        claim = proposal_json(
            CLAIM_EMAIL,
            subject_member_id=member.id,
            recipient=recipient,
            payload={"subject": rules.claim_subject(record, product), "body": rules.claim_body(record, product, member.name, purchase_text)},
            evidence_refs=evidence + [f"Remedy: {summary['remedy']}"],
            rationale=(f"{member.name} claims the remedy the recall promises ({', '.join(o for o in summary['remedy_options'] if o) or 'see remedy'}) "
                       f"from {rules.manufacturer(record)} at the consumer contact in the record."),
        )
        ctx.note("propose_recall_claim", recall_number=summary["recall_number"], recipient=recipient, ok=True)
        return json.dumps({"actions": [json.loads(lookup), json.loads(claim)], "needs": needs}, ensure_ascii=False)

    return propose_recall_claim


TOOLS = (
    SkillTool("lookup_recall", build_lookup_recall),
    SkillTool("propose_recall_claim", build_propose_recall_claim),
)
