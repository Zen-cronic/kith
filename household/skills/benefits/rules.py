"""Coordination of benefits (COB) rules, as the CLHIA explains them to consumers. Deterministic: given the household
plans and the claimant, the order of plans is a function, not a model opinion."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ...model import Household, money
from ..base import Citation, RulesTable

CLHIA_URL = "https://www.clhia.ca/en-ca/Consumers/Understanding-the-Coordination-of-Benefits"

RULES = RulesTable(
    title="Coordination of benefits (CLHIA consumer guidance)",
    citations=(
        Citation("CLHIA, custody", CLHIA_URL, "In single custody situations, the custodial parent's plan pays first…"),
        Citation("CLHIA, 100% cap", CLHIA_URL, "The combined payments from all plans cannot exceed 100 per cent of the eligible expense"),
    ),
    params={
        "own claim": "the claimant's own plan pays first; the spouse's plan pays second",
        "dependant child": "the plan of the parent whose birthday (month and day) falls earlier in the calendar year pays "
        "first (paraphrased from the CLHIA page; verify the wording there)",
        "residual": "the secondary claim is the eligible expense minus what the primary plan paid, never below zero",
        "submission window": "12 months (insurer-specific, verify)",
    },
)


@dataclass(frozen=True)
class CobOrder:
    primary: dict[str, str] | None
    secondary: dict[str, str] | None
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"primary": self.primary, "secondary": self.secondary, "reasons": list(self.reasons)}


def plan_for(household: Household, member_id: str) -> dict[str, str] | None:
    return next((p for p in household.plans if p.get("member") == member_id), None)


def _birthday_key(plan: dict[str, str]) -> tuple[int, int]:
    _, month, day = plan["birthday"].split("-")
    return int(month), int(day)


def cob_order(household: Household, subject_member_id: str, service_date: str) -> CobOrder:
    """Which plan pays first for this claimant. Adult claimant: own plan, then spouse's. Dependant child: the parent
    with the earlier birthday (month/day) in the year, then the other parent."""
    subject = household.member(subject_member_id)
    if subject is None:
        return CobOrder(None, None, [f"unknown member {subject_member_id!r}"])
    reasons = [f"service date {service_date}; submission window {RULES.params['submission window']}"]
    if subject.role == "adult":
        own = plan_for(household, subject.id)
        others = [plan_for(household, m.id) for m in household.members if m.role == "adult" and m.id != subject.id]
        spouse = next((p for p in others if p is not None), None)
        if own is None:
            reasons.append(f"{subject.name} has no plan on file; a spouse's plan would be the only payer")
            return CobOrder(spouse, None, reasons)
        reasons.append(f"{subject.name} is the claimant, so their own plan {own['plan_id']} ({own['insurer']}) pays first")
        if spouse is not None:
            reasons.append(f"the spouse's plan {spouse['plan_id']} ({spouse['insurer']}) pays second, up to 100% of the eligible expense")
        return CobOrder(own, spouse, reasons)
    parent_plans = [p for p in (plan_for(household, g) for g in subject.guardians) if p is not None]
    if not parent_plans:
        reasons.append(f"no guardian of {subject.name} has a plan on file")
        return CobOrder(None, None, reasons)
    ordered = sorted(parent_plans, key=_birthday_key)
    primary = ordered[0]
    secondary = ordered[1] if len(ordered) > 1 else None
    holder = household.member(primary["member"])
    reasons.append(
        f"{subject.name} is a dependant child; {holder.name if holder else primary['member']}'s birthday "
        f"({primary['birthday'][5:]}) falls earlier in the year, so plan {primary['plan_id']} ({primary['insurer']}) pays first"
    )
    if secondary is not None:
        reasons.append(f"plan {secondary['plan_id']} ({secondary['insurer']}) pays second, up to 100% of the eligible expense")
    return CobOrder(primary, secondary, reasons)


def residual(amount: str, primary_paid: str) -> str:
    """What is left for the secondary plan: eligible expense minus the primary payment, floored at zero."""
    left = money(amount) - money(primary_paid)
    if left < 0:
        left = Decimal("0")
    return f"{left:.2f}"
