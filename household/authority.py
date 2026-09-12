"""Authority is decided in code. `decide` is pure: same proposal, same ledger, same clock → same decision.

Rules run in order and the first decisive match wins. Every branch appends a human-readable reason so the
decision can be shown to the member, echoed by the model, and audited later. The model never decides;
it may only repeat what this module returned.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from .model import (
    AGENT_ACTOR,
    ActionProposal,
    AuthorityDecision,
    AuthorityGrant,
    Household,
    Member,
    as_utc,
    money,
    parse_iso,
)

# Rule ids, in evaluation order. Every id is hit by at least one unit case (tests assert the coverage).
RULE_UNKNOWN_MEMBER = "unknown-member"
RULE_FORGED_GRANT = "forged-grant"
RULE_RAIL_POLICY = "rail-policy"
RULE_MINOR_ALLOWANCE = "minor-allowance"
RULE_MINOR_GUARDIAN = "minor-guardian"
RULE_MINOR_NO_GUARDIAN = "minor-no-guardian"
RULE_SELF = "self"
RULE_SELF_CONFIRM = "self-confirm"
RULE_PARENT_FOR_MINOR = "parent-for-minor"
RULE_GRANT = "grant"
RULE_GRANT_REFUSED = "grant-refused"
RULE_NO_GRANT = "no-grant"
ALL_RULE_IDS: tuple[str, ...] = (
    RULE_UNKNOWN_MEMBER,
    RULE_FORGED_GRANT,
    RULE_RAIL_POLICY,
    RULE_MINOR_ALLOWANCE,
    RULE_MINOR_GUARDIAN,
    RULE_MINOR_NO_GUARDIAN,
    RULE_SELF,
    RULE_SELF_CONFIRM,
    RULE_PARENT_FOR_MINOR,
    RULE_GRANT,
    RULE_GRANT_REFUSED,
    RULE_NO_GRANT,
)

BANK_RAILS = ("internal-ledger", "stripe-test")
WINDOWS = {"per-week": timedelta(days=7), "per-month": timedelta(days=30)}  # rolling windows ending at `now`


def decide(proposal: ActionProposal, household: Household, now: datetime) -> AuthorityDecision:
    """Allow, block, or route for approval. No I/O, no clock, no model: `now` is the only time source."""
    now = as_utc(now)
    reasons: list[str] = []
    amount = money(proposal.amount) if proposal.amount is not None else None
    currency = proposal.currency

    def decision(outcome: str, rule_id: str, *, grant_id: str | None = None, approvers: list[str] | None = None):
        return AuthorityDecision(
            action_id=proposal.id,
            outcome=outcome,  # type: ignore[arg-type]
            rule_id=rule_id,
            grant_id=grant_id,
            approver_ids=list(approvers or []),
            reasons=list(reasons),
        )

    # 1. Both parties must be in the ledger. The agent itself may act as the actor under an "agent" grant.
    actor = household.member(proposal.actor_member_id)
    subject = household.member(proposal.subject_member_id)
    if actor is None and proposal.actor_member_id != AGENT_ACTOR:
        reasons.append(f"unknown member: actor {proposal.actor_member_id!r} is not in household {household.id!r}")
        return decision("block", RULE_UNKNOWN_MEMBER)
    if subject is None:
        reasons.append(f"unknown member: subject {proposal.subject_member_id!r} is not in household {household.id!r}")
        return decision("block", RULE_UNKNOWN_MEMBER)

    # 2. A cited grant that the ledger has never seen is a forgery, whoever cites it.
    claimed: AuthorityGrant | None = None
    if proposal.claimed_grant_id is not None:
        claimed = household.grant(proposal.claimed_grant_id)
        if claimed is None:
            reasons.append(f"forged grant: {proposal.claimed_grant_id!r} is not in the household ledger")
            return decision("block", RULE_FORGED_GRANT)

    # 3. Rail policy. official-form is allowed but never submits; that label is decided at execution.
    if proposal.action_type == "payment:transfer" and proposal.rail not in BANK_RAILS:
        reasons.append(
            f"no consumer bank rail: payment:transfer may only run on {' or '.join(BANK_RAILS)}, not {proposal.rail}"
        )
        return decision("block", RULE_RAIL_POLICY)
    if proposal.action_type == "email:send" and not (proposal.recipient or "").strip():
        reasons.append("no recipient: email:send needs a recipient address")
        return decision("block", RULE_RAIL_POLICY)
    if proposal.rail == "official-form":
        reasons.append("PREPARE-ONLY rail: official-form renders a prepared form for a human to file; it never submits")

    # 4. A minor may spend their own allowance within their rule; everything else goes to a guardian.
    if actor is not None and actor.role == "minor":
        if proposal.action_type == "allowance:transfer" and subject.id == actor.id:
            if _within_allowance_rule(actor, amount, currency, household, now, reasons):
                return decision("allow", RULE_MINOR_ALLOWANCE, grant_id=f"rule:{RULE_MINOR_ALLOWANCE}")
        else:
            reasons.append(f"{actor.name} is a minor and may not decide {proposal.action_type} for {subject.name}")
        if not actor.guardians:
            reasons.append(f"{actor.name} has no guardian on file, so nobody can approve this")
            return decision("block", RULE_MINOR_NO_GUARDIAN)
        reasons.append("minor requires guardian approval")
        return decision("needs-approval", RULE_MINOR_GUARDIAN, approvers=list(actor.guardians))

    # 5. An adult acting for themself confirms only above the household self-confirm limit.
    if actor is not None and actor.id == subject.id:
        limit = money(household.self_confirm_limit)
        if amount is None or amount <= limit:
            shown = f"{amount} {currency}" if amount is not None else "no amount"
            reasons.append(
                f"{actor.name} decides for themself; {shown} is within the self-confirm limit "
                f"{household.self_confirm_limit} {household.currency}"
            )
            return decision("allow", RULE_SELF, grant_id=f"rule:{RULE_SELF}")
        reasons.append(
            f"self-confirm above limit: {amount} {currency} exceeds the self-confirm limit "
            f"{household.self_confirm_limit} {household.currency}, so {actor.name} must confirm"
        )
        return decision("needs-approval", RULE_SELF_CONFIRM, approvers=[actor.id])

    # 6. A guardian decides for their minor.
    if actor is not None and subject.role == "minor" and actor.id in subject.guardians:
        reasons.append(f"{actor.name} is a guardian of {subject.name}")
        return decision("allow", RULE_PARENT_FOR_MINOR, grant_id=f"rule:{RULE_PARENT_FOR_MINOR}")

    # 7. Otherwise only an active, in-scope, unexpired, under-limit grant from the subject allows it.
    actor_label = actor.name if actor is not None else "the agent"
    candidates = [
        g
        for g in household.grants
        if g.grantor_id == subject.id
        and g.grantee_id in {proposal.actor_member_id, AGENT_ACTOR}
        and proposal.action_type in g.scope
    ]
    failures: list[str] = []
    if claimed is not None:
        # A cited grant is judged on its own terms: it must itself cover this actor, subject and action.
        if claimed in candidates:
            candidates = [claimed]
        else:
            candidates = []
            failures.append(
                f"grant {claimed.id} does not cover {actor_label} deciding {proposal.action_type} for {subject.name}"
            )
    for grant in candidates:
        failure = _grant_failure(grant, amount, currency, household, now)
        if failure is None:
            reasons.append(
                f"grant {grant.id} ({grant.basis}) from {subject.name} covers {actor_label} deciding "
                f"{proposal.action_type}" + _limit_text(grant)
            )
            return decision("allow", RULE_GRANT, grant_id=grant.id)
        failures.append(failure)
    if failures:
        reasons.extend(failures)
        return decision("needs-approval", RULE_GRANT_REFUSED, approvers=[subject.id])
    reasons.append(f"no grant covers {proposal.action_type} for {actor_label} deciding for {subject.name}")
    return decision("needs-approval", RULE_NO_GRANT, approvers=[subject.id])


def explain(decision: AuthorityDecision) -> str:
    """One English sentence a member (or a judge) can read."""
    why = "; ".join(r.rstrip(".") for r in decision.reasons) or "no reason recorded"
    if decision.outcome == "allow":
        return f"Allowed under {decision.grant_id}: {why}."
    if decision.outcome == "block":
        return f"Blocked: {why}."
    return f"Needs approval from {', '.join(decision.approver_ids) or 'nobody on file'}: {why}."


# Helpers


def _within_allowance_rule(
    minor: Member, amount: Decimal | None, currency: str, household: Household, now: datetime, reasons: list[str]
) -> bool:
    account = household.allowance_account(minor.id)
    auto_limit = account.rules.get("auto_limit") if account is not None else None
    if account is None or auto_limit is None:
        reasons.append(f"{minor.name} has no allowance rule on file")
        return False
    if amount is None:
        reasons.append("allowance:transfer needs an amount")
        return False
    if currency != account.currency:
        reasons.append(f"{minor.name}'s allowance is in {account.currency}, not {currency}")
        return False
    if amount > money(auto_limit):
        reasons.append(f"{amount} {currency} is above {minor.name}'s auto-approve allowance limit {auto_limit} {currency}")
        return False
    weekly = account.rules.get("weekly")
    since = (now - WINDOWS["per-week"]).isoformat()
    spent = sum((household.amount_of(r) for r in household.receipts_for(minor.id, "allowance:transfer", since)), Decimal("0"))
    if weekly is not None and spent + amount > money(weekly):
        reasons.append(
            f"weekly allowance cap {weekly} {currency} would be exceeded: {spent} used this week + {amount} requested"
        )
        return False
    cap = f"{spent} of {weekly} used this week" if weekly is not None else "no weekly cap"
    reasons.append(
        f"{minor.name} may take up to {auto_limit} {currency} from their own allowance without asking ({cap})"
    )
    return True


def _grant_failure(
    grant: AuthorityGrant, amount: Decimal | None, currency: str, household: Household, now: datetime
) -> str | None:
    """None when the grant covers the request right now; otherwise the exact reason it does not."""
    if grant.status == "revoked":
        return f"grant {grant.id} is revoked"
    if now >= parse_iso(grant.expires_at):
        return f"grant {grant.id} expired at {grant.expires_at}"
    if grant.limit_amount is None or amount is None:
        return None
    if currency != grant.limit_currency:
        return f"grant {grant.id} limit is in {grant.limit_currency} but the request is in {currency}"
    limit = money(grant.limit_amount)
    if grant.limit_period == "per-action":
        if amount > limit:
            return f"grant {grant.id} per-action limit {grant.limit_amount} {grant.limit_currency} exceeded by {amount} {currency}"
        return None
    since = (now - WINDOWS[grant.limit_period]).isoformat()
    spent = sum((household.amount_of(r) for r in household.receipts_under(grant.id, since)), Decimal("0"))
    if spent + amount > limit:
        return (
            f"grant {grant.id} {grant.limit_period} limit {grant.limit_amount} {grant.limit_currency} exceeded: "
            f"{spent} used in the last {WINDOWS[grant.limit_period].days} days + {amount} requested"
        )
    return None


def _limit_text(grant: AuthorityGrant) -> str:
    if grant.limit_amount is None:
        return " with no amount limit"
    return f" up to {grant.limit_amount} {grant.limit_currency} {grant.limit_period}"
