"""Education-money rules: the Canada Education Savings Grant (CESG) and Canada Learning Bond (CLB) parameters as
canada.ca states them, quoted verbatim (fetched 2026-09-12; the same sentences are recorded in
fixtures/skills/education/cesg-estimating-amounts.json and a test cross-checks them), plus the pure arithmetic that
turns a contribution into the grant it earns and the due-date check for tuition instalments. Every number here is
computed in code from the cited parameters; the model only copies them."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from ...model import Household, Member, as_utc, external_account_id, money
from ..base import Citation, RulesTable

CESG_URL = "https://www.canada.ca/en/services/benefits/education/education-savings/estimating-amounts.html"
FETCHED = "2026-09-12"
HOUSEHOLD_TZ = ZoneInfo("America/Toronto")

# CESG parameters (each one is quoted in RULES.citations)
BASIC_RATE = Decimal("0.20")
YEARLY_BASIC_MAX = Decimal("500.00")  # 20% of the first $2,500 contributed in a calendar year
YEARLY_MAX_WITH_CARRY_FORWARD = Decimal("1000.00")  # up to $1,000 in a calendar year when unused room exists
LIFETIME_MAX = Decimal("7200.00")
LAST_ELIGIBLE_AGE = 17  # until the end of the calendar year the beneficiary turns 17
CLB_FIRST_YEAR = Decimal("500.00")
CLB_LATER_YEARS = Decimal("100.00")
CLB_LIFETIME_MAX = Decimal("2000.00")
CENT = Decimal("0.01")
ZERO = Decimal("0.00")

RULES = RulesTable(
    title="RESP grants: CESG and CLB (canada.ca, Estimating amounts, fetched 2026-09-12) and tuition due dates",
    citations=(
        Citation("CESG, basic rate", CESG_URL,
                 "If eligible, beneficiaries can receive up to 20% of the first $2,500 contributed to the RESP."),
        Citation("CESG, yearly maximum", CESG_URL,
                 "The CESG adds a maximum of $500 to an RESP each year, and up to another $100 for eligible families "
                 "living with low or middle-income"),
        Citation("CESG, carry-forward", CESG_URL,
                 "CESG amounts accumulate and can carry-forward to the current year. If you don’t receive the maximum "
                 "CESG amount in a given year, you can still receive it in future years. You can catch up on this "
                 "amount by making more contributions to the RESP."),
        Citation("CESG, carry-forward yearly cap", CESG_URL,
                 "If there is an unused CESG amount from previous years, the subscriber can contribute more than $2,500 "
                 "to the RESP per year and receive up to 20% of their contributions (up to $5,000) each year. This way, "
                 "a child could get up to $1,000 of the CESG in their RESP per calendar year if there are unused "
                 "amounts from previous years."),
        Citation("CESG, lifetime maximum", CESG_URL,
                 "The maximum that can be received from the CESG is $7,200 until the age of 17 per eligible beneficiary."),
        Citation("CESG, age limit", CESG_URL,
                 "The CESG is available until the end of the calendar year that the beneficiary turns 17"),
        Citation("CLB, amounts", CESG_URL,
                 "A beneficiary will receive $500 their first year of eligibility, then another $100 for each year of "
                 "eligibility up to and including age 15"),
        Citation("CLB, no contribution needed", CESG_URL,
                 "No contributions to the RESP are needed to get the CLB"),
    ),
    params={
        "basic CESG": "20% of the contribution, up to 500.00 CAD of grant per calendar year (the first 2,500.00 contributed)",
        "carry-forward": "unused yearly room accumulates; with room from earlier years the grant in one calendar year can "
        "reach 1,000.00 CAD (20% of up to 5,000.00 contributed)",
        "lifetime": "7,200.00 CAD of CESG per beneficiary, until the end of the calendar year they turn 17",
        "additional CESG": "10% or 20% on the first 500.00 for low- and middle-income families; income-tested, so it is "
        "stated, not computed",
        "CLB": "500.00 the first eligible year and 100.00 each later eligible year to age 15 (lifetime 2,000.00); "
        "income-tested and needs no contribution, so it is stated, not computed",
        "tuition due dates": "an instalment whose due date is already past is never proposed; it goes to needs",
    },
)


# RESP payee naming


def resp_payee(member: Member) -> str:
    """The payee name for a child's RESP: 'RESP for <first name>', which the ledger keeps as ext-resp-for-<first name>."""
    return f"RESP for {member.name.split()[0]}"


def resp_account_id(member: Member) -> str:
    return external_account_id(resp_payee(member))


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _text(value: Decimal) -> str:
    return f"{_cents(value):.2f}"


def year_contributed(household: Household, member: Member, now: datetime) -> Decimal:
    """What the household has already put into this child's RESP this calendar year, from the receipts joined to
    their payment:transfer proposals (the same history the authority reads)."""
    since = datetime(as_utc(now).year, 1, 1, tzinfo=as_utc(now).tzinfo).isoformat()
    target = resp_account_id(member)
    total = ZERO
    for receipt in household.receipts_for(member.id, "payment:transfer", since):
        record = household.action(receipt.action_id)
        if record is None:
            continue
        destination = record.proposal.payload.get("to_account") or record.proposal.recipient or ""
        if destination == target or external_account_id(destination) == target:
            total += household.amount_of(receipt)
    return total


# CESG arithmetic (pure)


@dataclass(frozen=True)
class CesgRoom:
    beneficiary_member_id: str
    beneficiary_name: str
    age_this_year: int
    eligible_by_age: bool
    contribution: str
    basic_20pct: str
    year_contributed_so_far: str
    year_room_before: str
    grant: str
    carry_forward_used: str
    year_room_after: str
    lifetime_room_after: str
    assumptions: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def note(self) -> str:
        """The one-line grant_room_note the planner copies onto the proposal."""
        return (
            f"CESG on {self.contribution}: {self.grant} (20% basic CESG; {self.year_room_after} of this year's grant "
            f"room left after this; lifetime room {self.lifetime_room_after} of {_text(LIFETIME_MAX)}). "
            f"Source: canada.ca, Estimating amounts (fetched {FETCHED}); " + " ".join(self.assumptions)
        ).strip()


def cesg_room(
    household: Household,
    beneficiary_member_id: str,
    contribution: str,
    now: datetime,
    *,
    year_contributed_so_far: str | None = None,
    carry_forward_room: str = "0.00",
    lifetime_grant_received: str = "0.00",
) -> CesgRoom | None:
    """The basic CESG a contribution earns: 20% of it, capped by the calendar-year room (500.00, or up to 1,000.00
    when unused room from earlier years is given) less what this year's earlier contributions already earned, and by
    the 7,200.00 lifetime maximum less what was received to date. Zero after the year the beneficiary turns 17.
    None for an unknown member. Raises ValueError for an amount that is not money."""
    member = household.member(beneficiary_member_id)
    if member is None:
        return None
    amount = money(contribution)
    so_far = money(year_contributed_so_far) if year_contributed_so_far is not None else year_contributed(household, member, now)
    carry = money(carry_forward_room)
    received = money(lifetime_grant_received)
    age = as_utc(now).year - member.birth_year
    eligible = age <= LAST_ELIGIBLE_AGE
    year_cap = min(YEARLY_BASIC_MAX + carry, YEARLY_MAX_WITH_CARRY_FORWARD)
    earned_so_far = min(_cents(so_far * BASIC_RATE), year_cap)
    year_room_before = max(year_cap - earned_so_far, ZERO)
    lifetime_room = max(LIFETIME_MAX - received, ZERO)
    basic = _cents(amount * BASIC_RATE)
    grant = min(basic, year_room_before, lifetime_room) if eligible else ZERO
    basic_room_left = max(YEARLY_BASIC_MAX - earned_so_far, ZERO)
    carry_used = max(grant - basic_room_left, ZERO)
    assumptions = []
    if year_contributed_so_far is None:
        assumptions.append(f"this year's earlier RESP contributions ({_text(so_far)}) are read from the household ledger;")
    if carry == ZERO and received == ZERO:
        assumptions.append("carry-forward room and CESG received to date are not on file, assumed 0.00.")
    else:
        assumptions.append(f"carry-forward room {_text(carry)} and CESG received to date {_text(received)} as given by the member.")
    if not eligible:
        assumptions.append(f"{member.name} turns {age} this year, past the last eligible year (17): no CESG.")
    return CesgRoom(
        beneficiary_member_id=member.id,
        beneficiary_name=member.name,
        age_this_year=age,
        eligible_by_age=eligible,
        contribution=_text(amount),
        basic_20pct=_text(basic),
        year_contributed_so_far=_text(so_far),
        year_room_before=_text(year_room_before),
        grant=_text(grant),
        carry_forward_used=_text(carry_used),
        year_room_after=_text(year_room_before - grant),
        lifetime_room_after=_text(lifetime_room - grant),
        assumptions=assumptions,
        citations=[c.label for c in RULES.citations if c.label.startswith("CESG")],
    )


# Tuition due dates


DUE_FORMATS = ("%Y-%m-%d", "%B %d, %Y", "%B %d %Y", "%d %B %Y", "%b %d, %Y", "%b %d %Y")
WORD = re.compile(r"[a-z0-9]+")


def parse_due(text: str) -> date | None:
    cleaned = " ".join(text.strip().rstrip(".").split())
    for fmt in DUE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def local_today(now: datetime) -> date:
    """The household's calendar day (Toronto): a due date is a day, not an instant."""
    return as_utc(now).astimezone(HOUSEHOLD_TZ).date()


def due_status(due_text: str, now: datetime) -> dict[str, Any]:
    """Whether a due date, as written, is already past on the session clock. Unparseable text is reported, not guessed."""
    parsed = parse_due(due_text)
    today = local_today(now)
    if parsed is None:
        return {"due_text": due_text, "due": None, "today": today.isoformat(), "past_due": None,
                "days_until": None, "note": "due date not understood; quote it exactly as written or ask the member"}
    days = (parsed - today).days
    return {"due_text": due_text, "due": parsed.isoformat(), "today": today.isoformat(), "past_due": days < 0, "days_until": days}


def tuition_schedule(household: Household, payee: str, now: datetime) -> dict[str, Any]:
    """The tuition entries on file whose institution matches every word of the payee, each with its due status."""
    words = [w for w in WORD.findall(payee.lower()) if len(w) > 2]
    entries = [t for t in household.tuition if words and all(w in str(t.get("institution", "")).lower() for w in words)]
    schedule = [{**t, **due_status(str(t.get("due", "")), now)} for t in entries]
    return {"payee": payee, "schedule": schedule, "note": "" if schedule else "no schedule on file"}
