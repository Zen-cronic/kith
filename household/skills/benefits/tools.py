"""Benefits tools: read-only plan lookup, the deterministic COB order, and the residual arithmetic."""

from __future__ import annotations

import json
from typing import Any

from strands import tool

from ..base import SkillTool, ToolContext, error_json
from . import rules


def build_plan_lookup(ctx: ToolContext) -> Any:
    @tool
    def plan_lookup(member_id: str) -> str:
        """Look up a member's benefits plan on file: insurer, plan id and the plan holder's birthday.

        Args:
            member_id: the member id, e.g. "daniel".
        """
        plan = rules.plan_for(ctx.household, member_id)
        ctx.note("plan_lookup", member_id=member_id, found=plan is not None)
        if plan is None:
            return error_json(f"{member_id!r} has no benefits plan on file")
        return json.dumps(plan, ensure_ascii=False)

    return plan_lookup


def build_cob_order(ctx: ToolContext) -> Any:
    @tool
    def cob_order(subject_member_id: str, service_date: str) -> str:
        """Which plan pays first for a claim, and why: the claimant's own plan for an adult; for a dependant child, the
        parent whose birthday falls earlier in the year. Returns primary, secondary and the reasons.

        Args:
            subject_member_id: the member the service was for.
            service_date: the service date exactly as written on the statement.
        """
        order = rules.cob_order(ctx.household, subject_member_id, service_date)
        ctx.note("cob_order", subject_member_id=subject_member_id, primary=(order.primary or {}).get("plan_id"))
        return json.dumps(order.as_dict(), ensure_ascii=False)

    return cob_order


def build_residual(ctx: ToolContext) -> Any:
    @tool
    def residual(amount: str, primary_paid: str) -> str:
        """The amount left to claim from the secondary plan: eligible expense minus what the primary plan paid.

        Args:
            amount: the eligible expense as a decimal string, e.g. "180.00".
            primary_paid: what the primary plan paid, e.g. "144.00".
        """
        try:
            left = rules.residual(amount, primary_paid)
        except ValueError as exc:
            ctx.note("residual", ok=False)
            return error_json(str(exc))
        ctx.note("residual", amount=amount, primary_paid=primary_paid, residual=left)
        return json.dumps({"amount": amount, "primary_paid": primary_paid, "residual": left})

    return residual


TOOLS = (
    SkillTool("plan_lookup", build_plan_lookup),
    SkillTool("cob_order", build_cob_order),
    SkillTool("residual", build_residual),
)
