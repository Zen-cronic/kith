"""The typed household ledger: who is in the household, who may decide what for whom, and what was done.

Money is a decimal string plus a currency code; timestamps are ISO-8601 strings. Every model forbids unknown
keys so a forged or mistyped field fails validation instead of being silently ignored.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

ActionType = Literal[
    "email:send",
    "payment:transfer",
    "allowance:transfer",
    "benefits:claim",
    "form:prepare",
    "recall:remedy",
    "flight:claim",
]
Rail = Literal["ses-email", "internal-ledger", "stripe-test", "official-form", "external-api-readonly"]
Role = Literal["adult", "minor"]
Language = Literal["en", "es", "fr"]
LimitPeriod = Literal["per-action", "per-week", "per-month"]
GrantStatus = Literal["active", "revoked"]
GrantBasis = Literal["self", "parent-for-minor", "spouse-grant"]
ConsentKind = Literal["grant-accept", "action-approve", "action-decline", "data-share"]
ConsentChannel = Literal["ui", "voice-pin"]
Outcome = Literal["allow", "block", "needs-approval"]
ReceiptMode = Literal["COMPLETE", "PREPARE-ONLY", "SIMULATED", "SIMULATED-replay"]
AccountKind = Literal["allowance", "household", "external"]  # external = a payee outside the household

AGENT_ACTOR = "agent"  # grantee id (and actor id) for actions the agent takes on its own initiative
PIN_ITERATIONS = 200_000
PIN_SALT_BYTES = 16


# Money and time helpers


def signed_money(value: str) -> Decimal:
    """Parse a money string that may be negative (an external counterparty's balance is the outside world)."""
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"not a money amount: {value!r}") from exc
    if not amount.is_finite():
        raise ValueError(f"money must be a finite, non-negative amount, got {value!r}")
    return amount


def money(value: str) -> Decimal:
    """Parse a money string. Raises ValueError for anything that is not a finite, non-negative decimal."""
    amount = signed_money(value)
    if amount < 0:
        raise ValueError(f"money must be a finite, non-negative amount, got {value!r}")
    return amount


def _check_money(value: str) -> str:
    money(value)
    return value


def _check_signed_money(value: str) -> str:
    signed_money(value)
    return value


Money = Annotated[str, AfterValidator(_check_money)]
SignedMoney = Annotated[str, AfterValidator(_check_signed_money)]


def external_account_id(name: str) -> str:
    """`ext-<slug>` for a payee outside the household: lowercase, every run of other characters becomes one dash."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return f"ext-{slug or 'unnamed'}"


def parse_iso(value: str) -> datetime:
    """ISO-8601 → aware datetime. Naive strings are taken as UTC so comparisons never mix aware and naive."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def hash_pin(pin: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt_hex), PIN_ITERATIONS).hex()


def consent_proof(member_id: str, target_id: str, at: str, pin_hash: str | None) -> str:
    return hashlib.sha256("|".join([member_id, target_id, at, pin_hash or ""]).encode("utf-8")).hexdigest()


# Channel handles: sms numbers and emails match case-insensitively (and trimmed); anything else matches exactly.
CASE_INSENSITIVE_CHANNELS = {"sms", "email"}


def normalize_native_id(channel: str, native_id: str) -> str:
    value = native_id.strip()
    return value.lower() if channel in CASE_INSENSITIVE_CHANNELS else value


# Ledger models


class LedgerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Member(LedgerModel):
    id: str
    name: str
    role: Role
    birth_year: int
    guardians: list[str] = Field(default_factory=list, description="Member ids who may approve for a minor")
    email: str | None = None
    language: Language = "en"
    pin_hash: str | None = None
    pin_salt: str | None = None
    memory_actor_id: str = Field(description="AgentCore Memory actor id, f'{household_id}.{member_id}'")
    handles: dict[str, str] = Field(
        default_factory=dict, description="Channel name -> native id, e.g. {'telegram': '8675309', 'sms': '+16475551234'}"
    )

    def set_pin(self, pin: str) -> None:
        """Identification, not biometrics: PBKDF2-HMAC-SHA256 with a fresh 16-byte salt."""
        self.pin_salt = os.urandom(PIN_SALT_BYTES).hex()
        self.pin_hash = hash_pin(pin, self.pin_salt)

    def verify_pin(self, pin: str) -> bool:
        if not self.pin_hash or not self.pin_salt:
            return False
        return hmac.compare_digest(hash_pin(pin, self.pin_salt), self.pin_hash)


class AuthorityGrant(LedgerModel):
    id: str
    grantor_id: str
    grantee_id: str = Field(description="Member id, or 'agent' when the agent may act on its own initiative")
    subject_id: str
    scope: list[ActionType]
    limit_amount: Money | None = None
    limit_currency: str = "CAD"
    limit_period: LimitPeriod = "per-action"
    expires_at: str
    status: GrantStatus = "active"
    basis: GrantBasis
    consent_id: str | None = None
    created_at: str


class ConsentRecord(LedgerModel):
    id: str
    member_id: str
    kind: ConsentKind
    target_id: str
    at: str
    channel: ConsentChannel
    proof: str = Field(description="sha256(member_id|target_id|at|pin_hash or '')")

    def verify(self, member: Member) -> bool:
        return hmac.compare_digest(consent_proof(self.member_id, self.target_id, self.at, member.pin_hash), self.proof)


class ActionProposal(LedgerModel):
    id: str
    skill_id: str
    action_type: ActionType
    rail: Rail
    actor_member_id: str
    subject_member_id: str
    recipient: str | None = None
    amount: Money | None = None
    currency: str = "CAD"
    payload: dict[str, str] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    rationale: str = ""
    idempotency_key: str = ""
    claimed_grant_id: str | None = Field(default=None, description="Grant the requester cites; must exist in the ledger")


class AuthorityDecision(LedgerModel):
    action_id: str
    outcome: Outcome
    rule_id: str = Field(default="", description="Which authority rule decided; one of authority.ALL_RULE_IDS")
    grant_id: str | None = None
    approver_ids: list[str] = Field(default_factory=list)
    reasons: list[str]


class Receipt(LedgerModel):
    id: str
    action_id: str
    rail: Rail
    mode: ReceiptMode
    provider_ref: str | None = None
    request_digest: str
    response_digest: str
    at: str
    executed_under_grant: str | None = None
    label_reason: str


class ActionRecord(LedgerModel):
    proposal: ActionProposal
    decisions: list[AuthorityDecision] = Field(default_factory=list)
    receipt: Receipt | None = None
    created_at: str


class Account(LedgerModel):
    id: str
    owner_member_id: str = Field(description="The member who owns it; '' for an external counterparty (the outside world)")
    kind: AccountKind
    balance: SignedMoney
    currency: str = "CAD"
    rules: dict[str, str] = Field(default_factory=dict, description="e.g. allowance auto_limit and weekly caps")

    @model_validator(mode="after")
    def _household_balances_never_go_negative(self) -> Account:
        """Only an external counterparty may carry a negative balance: it is the outside world, not household money."""
        if self.kind != "external":
            money(self.balance)
        return self


class LedgerEntry(LedgerModel):
    id: str
    at: str
    debit_account: str
    credit_account: str
    amount: Money
    currency: str
    memo: str
    action_id: str


class Household(LedgerModel):
    id: str
    name: str
    jurisdiction: str = "CA-ON"
    currency: str = "CAD"
    self_confirm_limit: Money = "200.00"
    members: list[Member]
    grants: list[AuthorityGrant] = Field(default_factory=list)
    consents: list[ConsentRecord] = Field(default_factory=list)
    actions: list[ActionRecord] = Field(default_factory=list)
    receipts: list[Receipt] = Field(default_factory=list)
    accounts: list[Account] = Field(default_factory=list)
    ledger: list[LedgerEntry] = Field(default_factory=list)
    products: list[dict[str, str]] = Field(default_factory=list)
    plans: list[dict[str, str]] = Field(default_factory=list)
    tuition: list[dict[str, str]] = Field(default_factory=list)
    redeemed_enrollment_nonces: set[str] = Field(
        default_factory=set, description="Enrollment-code nonces already redeemed; a code cannot be replayed"
    )

    # Lookups

    def member(self, member_id: str) -> Member | None:
        return next((m for m in self.members if m.id == member_id), None)

    def member_by_handle(self, channel: str, native_id: str) -> Member | None:
        """The member whose handle for `channel` matches `native_id` (case-insensitive for sms/email), else None."""
        want = normalize_native_id(channel, native_id)
        for member in self.members:
            stored = member.handles.get(channel)
            if stored is not None and normalize_native_id(channel, stored) == want:
                return member
        return None

    def grant(self, grant_id: str) -> AuthorityGrant | None:
        return next((g for g in self.grants if g.id == grant_id), None)

    def account(self, account_id: str) -> Account | None:
        return next((a for a in self.accounts if a.id == account_id), None)

    def allowance_account(self, member_id: str) -> Account | None:
        return next((a for a in self.accounts if a.owner_member_id == member_id and a.kind == "allowance"), None)

    def counterparty_account(self, name: str, *, attach: bool = True) -> Account:
        """The outside-world account for a payee named in a proposal: found by `ext-<slug>`, or created with a zero
        balance in the household currency, no owner, and the payee's name kept under `rules.name`. With attach=False a
        new account is returned without being added, so a refused or simulated posting leaves no trace."""
        account_id = external_account_id(name)
        found = self.account(account_id)
        if found is None:
            found = Account(id=account_id, owner_member_id="", kind="external", balance="0.00", currency=self.currency,
                            rules={"name": name.strip()})
            if attach:
                self.accounts.append(found)
        return found

    def action(self, action_id: str) -> ActionRecord | None:
        return next((a for a in self.actions if a.proposal.id == action_id), None)

    def grants_for(self, subject_id: str) -> list[AuthorityGrant]:
        return [g for g in self.grants if g.subject_id == subject_id]

    # Receipt history (joined back to the proposal through action_id)

    def receipts_for(self, subject_id: str, action_type: ActionType, since_iso: str) -> list[Receipt]:
        since = parse_iso(since_iso)
        found: list[Receipt] = []
        for receipt in self.receipts:
            record = self.action(receipt.action_id)
            if record is None or parse_iso(receipt.at) < since:
                continue
            proposal = record.proposal
            if proposal.subject_member_id == subject_id and proposal.action_type == action_type:
                found.append(receipt)
        return found

    def receipts_under(self, grant_id: str, since_iso: str) -> list[Receipt]:
        since = parse_iso(since_iso)
        return [r for r in self.receipts if r.executed_under_grant == grant_id and parse_iso(r.at) >= since]

    def amount_of(self, receipt: Receipt) -> Decimal:
        record = self.action(receipt.action_id)
        if record is None or record.proposal.amount is None:
            return Decimal("0")
        return money(record.proposal.amount)
