"""Allowance rules come from the ledger, not from a statute: each child's allowance account carries `auto_limit`
(what the child may take without asking) and `weekly` (a rolling 7-day cap). authority.decide enforces them."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from ...model import Household, money
from ..base import RulesTable

RULES = RulesTable(
    title="Allowance rule set by the parents in the ledger (per child)",
    params={
        "auto_limit": "the amount a child may take from their own allowance without asking (account.rules.auto_limit)",
        "weekly": "a rolling 7-day cap on what the child takes without asking (account.rules.weekly)",
        "above either": "the request waits for a guardian; nothing moves until one approves",
    },
)


def allowance_rule(household: Household, member_id: str, now: datetime) -> dict[str, Any] | None:
    """The child's allowance account and rule, plus what they have already taken this week."""
    account = household.allowance_account(member_id)
    member = household.member(member_id)
    if account is None or member is None:
        return None
    since = (now - timedelta(days=7)).isoformat()
    spent = sum((household.amount_of(r) for r in household.receipts_for(member_id, "allowance:transfer", since)), Decimal("0"))
    weekly = account.rules.get("weekly")
    auto_limit = account.rules.get("auto_limit")
    return {
        "member_id": member_id,
        "member_name": member.name,
        "account_id": account.id,
        "balance": account.balance,
        "currency": account.currency,
        "auto_limit": auto_limit,
        "weekly": weekly,
        "spent_this_week": f"{spent:.2f}",
        "left_this_week": f"{money(weekly) - spent:.2f}" if weekly is not None else None,
    }
