"""The six voice tools as closures over one session's ledger, member and socket. The model can only ask: every
proposal goes through `authority.decide`, an allow executes through `executor.execute` at once (same receipts as the
typed path), and everything else is read-only. Each result carries a `say` sentence so the model never reads JSON
aloud, and each call emits a `tool` frame on the socket so the screen shows what code decided."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from strands import tool
from strands.types.tools import ToolContext

from .. import authority, executor
from ..agents.hooks import Guard
from ..agents.tools import RULE_TEXT
from ..config import Settings
from ..executor import rails
from ..model import ActionProposal, ActionRecord, AuthorityDecision, Household, Member, Receipt, money
from ..skills import CORE, skill_for
from ..skills.allowance.rules import allowance_rule
from ..store import DATA_DIR, LedgerStore
from .prompts import first_name

VOICE_TOOL_NAMES: tuple[str, ...] = (
    "propose_action", "household_status", "lookup_recall", "explain_grant", "read_last_upload", "stop_conversation",
)
VOICE_ACTION_TYPES: tuple[str, ...] = ("allowance:transfer", "payment:transfer", "email:send")
DEFAULT_UPLOADS_DIR = DATA_DIR / "uploads"

Emit = Callable[[dict[str, Any]], None]


@dataclass
class VoiceRecord:
    """What code observed during one voice session: proposals, decisions and receipts by action id, every tool call."""

    session_id: str
    proposals: dict[str, ActionProposal] = field(default_factory=dict)
    decisions: dict[str, AuthorityDecision] = field(default_factory=dict)
    receipts: dict[str, Receipt] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_requested: bool = False


def voice_guard(actor: Member) -> Guard:
    """The hook guard for a voice session: propose_action may only carry an action type voice can take."""

    def guard(name: str, tool_input: dict[str, Any]) -> str | None:
        if name != "propose_action":
            return None
        action_type = str(tool_input.get("action_type", ""))
        if action_type not in VOICE_ACTION_TYPES:
            return f"{action_type or 'that'} is not something {first_name(actor)} can ask for by voice; only {', '.join(VOICE_ACTION_TYPES)}"
        return None

    return guard


def upload_summary_path(uploads_dir: Path, household_id: str) -> Path:
    """Where the intake writes what the last shown document said: data/uploads/<household>.last.json."""
    return uploads_dir / f"{household_id}.last.json"


def make_voice_tools(
    household: Household,
    actor: Member,
    settings: Settings,
    ledger: LedgerStore | None,
    record: VoiceRecord,
    emit: Emit,
    *,
    uploads_dir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    uploads_dir = uploads_dir or DEFAULT_UPLOADS_DIR
    name = first_name(actor)

    def clock() -> datetime:
        return now or datetime.now(UTC)

    def names_of(member_ids: list[str]) -> str:
        found = [first_name(m) for mid in member_ids if (m := household.member(mid)) is not None]
        return " or ".join(found) or "nobody on file"

    def done(tool_name: str, say: str, **detail: Any) -> str:
        """Log the call, emit the tool frame, return the JSON the model reads (never aloud)."""
        result = {**detail, "tool": tool_name, "say": say}
        record.tool_calls.append(result)
        emit({**detail, "type": "tool", "name": tool_name, "status": "done", "say": say})
        return json.dumps(result, ensure_ascii=False, default=str)

    def failed(tool_name: str, error: str) -> str:
        record.tool_calls.append({"tool": tool_name, "error": error})
        emit({"type": "tool", "name": tool_name, "status": "error", "error": error, "say": error})
        return json.dumps({"tool": tool_name, "error": error, "say": error}, ensure_ascii=False)

    @tool
    def propose_action(action_type: str, amount: str = "", purpose: str = "", recipient: str = "", subject_member_id: str = "") -> str:
        """Propose one action the member asked for. Code decides at once (allow, block or needs-approval) and an
        allowed action runs through its rail and returns a receipt; a needs-approval action waits for the approver
        named in the result. Speak the "say" sentence; never read the rest aloud.

        Args:
            action_type: "allowance:transfer" (a child's own allowance), "payment:transfer" (from the household
                account) or "email:send".
            amount: the amount exactly as spoken, as a plain decimal such as "8.00"; "" for an email.
            purpose: what it is for, in the member's words (the memo, or the email body).
            recipient: the payee or email address exactly as given, or "" when none was given.
            subject_member_id: whose affairs it concerns; "" means the member speaking.
        """
        if action_type not in VOICE_ACTION_TYPES:
            return failed("propose_action", f"{action_type!r} is not something I can do by voice")
        subject_id = subject_member_id.strip() or actor.id
        subject = household.member(subject_id)
        if subject is None:
            return failed("propose_action", f"I don't know anyone called {subject_id!r} in this household")
        amount_text = amount.strip() or None
        if action_type != "email:send":
            if amount_text is None:
                return failed("propose_action", "I need the amount to propose that")
            try:
                money(amount_text)
            except ValueError:
                return failed("propose_action", f"{amount!r} is not an amount I can use")
        if action_type == "email:send" and not recipient.strip():
            return failed("propose_action", "I need the email address to send to")
        skill = skill_for("allowance") if action_type == "allowance:transfer" else CORE
        template = skill.template(action_type)
        pot = next((a.id for a in household.accounts if a.kind == "household"), "")
        if action_type == "allowance:transfer":
            payload = {"memo": purpose.strip() or "allowance"}
            to = pot
        elif action_type == "payment:transfer":
            payload = {"payee": recipient.strip(), "purpose": purpose.strip()}
            to = recipient.strip() or pot
        else:
            payload = {"subject": purpose.strip()[:80] or "From the household agent", "body": purpose.strip()}
            to = recipient.strip()
        at = clock()
        index = len(record.proposals) + 1
        proposal = ActionProposal(
            id=f"act-voice-{record.session_id[:8]}-{index}",
            skill_id=skill.id,
            action_type=action_type,  # type: ignore[arg-type]
            rail=template.rail,  # type: ignore[arg-type]
            actor_member_id=actor.id,
            subject_member_id=subject.id,
            recipient=to or None,
            amount=amount_text if action_type != "email:send" else None,
            currency=household.currency,
            payload=payload,
            evidence_refs=[f"spoken by {name}: {purpose.strip() or action_type}"],
            rationale=f"{name} asked by voice for {action_type} ({amount_text or 'no amount'} {household.currency}): {purpose.strip()}",
        )
        decision = authority.decide(proposal, household, at)
        record.proposals[proposal.id] = proposal
        record.decisions[proposal.id] = decision
        if household.action(proposal.id) is None:
            household.actions.append(ActionRecord(proposal=proposal, decisions=[decision], created_at=at.isoformat()))
        receipt: Receipt | None = None
        if decision.outcome == "allow":
            receipt = executor.execute(proposal, household, settings, now=at, grant_id=decision.grant_id)
            executor.record(household, receipt)
            record.receipts[proposal.id] = receipt
        if ledger is not None:
            ledger.save(household)
        shown = f"{amount_text} {household.currency}" if amount_text else "the email"
        what = purpose.strip() or action_type
        if decision.outcome == "allow" and receipt is not None:
            label = {"COMPLETE": "it went through", "PREPARE-ONLY": "it was prepared for a person to file",
                     "SIMULATED": "this is a simulated run, nothing real moved",
                     "SIMULATED-replay": "this replays a recorded result, nothing real moved"}[receipt.mode]
            say = f"Done. {shown} for {what}; {label}. Receipt {receipt.mode}."
        elif decision.outcome == "needs-approval":
            say = f"I've asked {names_of(decision.approver_ids)} to approve {shown} for {what}. Nothing moves until they do."
        else:
            say = f"I can't do that: {decision.reasons[-1] if decision.reasons else 'the household rules block it'}."
        return done(
            "propose_action", say,
            action_id=proposal.id, outcome=decision.outcome, rule_id=decision.rule_id, grant_id=decision.grant_id,
            approver_ids=list(decision.approver_ids), explanation=authority.explain(decision),
            proposal=proposal.model_dump(mode="json"), decision=decision.model_dump(mode="json"),
            receipt=receipt.model_dump(mode="json") if receipt else None,
        )

    @tool
    def household_status() -> str:
        """What is waiting and what was done, for the member speaking: approvals they must give, their own requests
        waiting on someone, their allowance when they have one, and the last receipts. Speak the "say" sentence."""
        waiting_on_me: list[dict[str, Any]] = []
        mine_waiting: list[dict[str, Any]] = []
        for action in household.actions:
            latest = action.decisions[-1] if action.decisions else None
            if latest is None or latest.outcome != "needs-approval" or action.receipt is not None:
                continue
            item = {"action_id": action.proposal.id, "action_type": action.proposal.action_type, "amount": action.proposal.amount,
                    "currency": action.proposal.currency, "for": first_name(household.member(action.proposal.subject_member_id) or actor),
                    "approvers": list(latest.approver_ids)}
            if actor.id in latest.approver_ids:
                waiting_on_me.append(item)
            if action.proposal.actor_member_id == actor.id:
                mine_waiting.append(item)
        recent = [r.model_dump(mode="json") for r in household.receipts[-3:]]
        rule = allowance_rule(household, actor.id, clock())
        parts: list[str] = []
        if rule is not None:
            parts.append(f"Your allowance has {rule['balance']} {rule['currency']}; you may take up to {rule['auto_limit']} without asking and {rule['left_this_week']} is left this week.")
        if waiting_on_me:
            parts.append(f"{len(waiting_on_me)} request{'s' if len(waiting_on_me) > 1 else ''} waiting for your approval.")
        if mine_waiting:
            parts.append(f"{len(mine_waiting)} of your request{'s are' if len(mine_waiting) > 1 else ' is'} waiting on {names_of(mine_waiting[0]['approvers'])}.")
        if recent:
            parts.append(f"The last receipt was {recent[-1]['mode']} on {recent[-1]['rail']}.")
        say = " ".join(parts) or f"Nothing is waiting for you right now, {name}."
        return done("household_status", say, waiting_for_me=waiting_on_me, my_requests_waiting=mine_waiting, recent_receipts=recent)

    @tool
    def lookup_recall(recall_number: str) -> str:
        """Look up a product recall by its recall number (digits, e.g. "26639") in the recorded recall records.
        Read-only; nothing is filed or claimed. Speak the "say" sentence."""
        number = "".join(ch for ch in recall_number if ch.isdigit())
        path = rails.cpsc_fixture_path(number) if number else None
        recorded = rails.load_recorded(path) if path is not None else None
        response = recorded.get("response") if recorded else None
        first = response[0] if isinstance(response, list) and response and isinstance(response[0], dict) else None
        if first is None:
            return done("lookup_recall", f"I have no record of recall {number or recall_number!r}. Nothing was filed.",
                        recall_number=number, found=False)
        summary = rails.summarize_recall(first)
        remedies = ", ".join(r for r in summary["remedy_options"] if r) or "not listed"
        say = f"Recall {summary['recall_number']}: {summary['title']}. Remedies: {remedies}. This was a recorded lookup; nothing was filed."
        return done("lookup_recall", say, recall_number=number, found=True, fetched=recorded.get("fetched"), summary=summary)

    @tool
    def explain_grant(grant_id: str) -> str:
        """Plain words for a grant id or rule id the member was given, e.g. "g-daniel-ama-benefits", "rule:self" or
        "approval:ama". Speak the "say" sentence."""
        key = grant_id.strip()
        if key in RULE_TEXT:
            return done("explain_grant", f"That rule means {RULE_TEXT[key]}.", grant_id=key, text=RULE_TEXT[key])
        if key.startswith("approval:"):
            approver = household.member(key.split(":", 1)[1])
            text = f"approved by {first_name(approver) if approver else key[9:]}"
            return done("explain_grant", f"That means it was {text}.", grant_id=key, text=text)
        grant = household.grant(key)
        if grant is None:
            return failed("explain_grant", f"I don't know a grant called {key!r}")
        grantor = household.member(grant.grantor_id)
        grantee = household.member(grant.grantee_id)
        limit = f" up to {grant.limit_amount} {grant.limit_currency} {grant.limit_period}" if grant.limit_amount else ""
        text = (f"{first_name(grantor) if grantor else grant.grantor_id} lets {first_name(grantee) if grantee else grant.grantee_id} "
                f"handle {', '.join(grant.scope)} for them{limit}, until {grant.expires_at[:10]}")
        status = "" if grant.status == "active" else f" It is {grant.status}."
        return done("explain_grant", f"{text}.{status}", grant_id=key, text=text, grant_status=grant.status, basis=grant.basis)

    @tool
    def read_last_upload() -> str:
        """What the document most recently shown to the household agent (a photo or PDF) said, as the intake read
        it: title, summary, amounts and dates. Says so when nothing has been shown. Speak the "say" sentence."""
        path = upload_summary_path(uploads_dir, household.id)
        if not path.exists():
            return done("read_last_upload", "Nobody has shown me a document yet.", found=False)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return failed("read_last_upload", "the last document could not be read")
        title = str(data.get("title") or "a document")
        summary = str(data.get("summary_en") or "").strip()
        amounts = [str(a.get("amount_text") if isinstance(a, dict) else a) for a in data.get("amounts", [])]
        say = f"The last document was {title}. {summary}".strip()
        if amounts:
            say += f" It mentions {', '.join(amounts)}."
        return done("read_last_upload", say, found=True, title=title, summary_en=summary, amounts=amounts, dates=data.get("dates", []), at=data.get("at"))

    @tool(context=True)
    def stop_conversation(tool_context: ToolContext) -> str:
        """End the conversation politely. Use only when the member says "stop conversation" or clearly says goodbye."""
        tool_context.invocation_state.setdefault("request_state", {})["stop_event_loop"] = True
        record.stop_requested = True
        return done("stop_conversation", f"Bye {name}. Anything waiting stays on the list until someone approves it.")

    return {
        "propose_action": propose_action,
        "household_status": household_status,
        "lookup_recall": lookup_recall,
        "explain_grant": explain_grant,
        "read_last_upload": read_last_upload,
        "stop_conversation": stop_conversation,
    }
