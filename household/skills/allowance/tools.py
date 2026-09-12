"""Allowance tools: one read-only lookup and one proposal builder. Neither moves money."""

from __future__ import annotations

import json
from typing import Any

from strands import tool

from ...model import money
from ..base import SkillTool, ToolContext, error_json, proposal_json
from .rules import allowance_rule
from .templates import TRANSFER


def build_allowance_balance(ctx: ToolContext) -> Any:
    @tool
    def allowance_balance(member_id: str) -> str:
        """Look up a child's allowance account: balance, the amount they may take without asking, the weekly cap,
        and what they have already taken this week.

        Args:
            member_id: the child's member id, e.g. "kofi".
        """
        rule = allowance_rule(ctx.household, member_id, ctx.now)
        ctx.note("allowance_balance", member_id=member_id, found=rule is not None)
        if rule is None:
            return error_json(f"{member_id!r} has no allowance account in this household")
        return json.dumps(rule, ensure_ascii=False)

    return allowance_balance


def build_propose_allowance(ctx: ToolContext) -> Any:
    @tool
    def propose_allowance(member_id: str, amount: str, memo: str) -> str:
        """Build a well-formed allowance:transfer proposal for the plan. It proposes only; authority decides.

        Args:
            member_id: the child whose allowance is used.
            amount: decimal amount as written in the request, e.g. "8.00".
            memo: what it is for, in the child's words.
        """
        rule = allowance_rule(ctx.household, member_id, ctx.now)
        if rule is None:
            ctx.note("propose_allowance", member_id=member_id, ok=False)
            return error_json(f"{member_id!r} has no allowance account in this household")
        try:
            money(amount)
        except ValueError as exc:
            ctx.note("propose_allowance", member_id=member_id, ok=False)
            return error_json(str(exc))
        ctx.note("propose_allowance", member_id=member_id, amount=amount, ok=True)
        household_account = next((a.id for a in ctx.household.accounts if a.kind == "household"), "")
        return proposal_json(
            TRANSFER,
            subject_member_id=member_id,
            recipient=household_account,
            amount_text=amount,
            currency=rule["currency"],
            payload={"memo": memo},
            rationale=f"{rule['member_name']} asked for {amount} {rule['currency']} from their own allowance for: {memo}",
        )

    return propose_allowance


TOOLS = (
    SkillTool("allowance_balance", build_allowance_balance),
    SkillTool("propose_allowance", build_propose_allowance),
)
