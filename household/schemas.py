"""Typed contracts between the six agents. Each agent returns exactly one of these through Strands structured output.

Every output is flat: lists are non-null, nested items are small flat records, and there are no Literal unions inside
lists (Nova rejects them). Anything that must be *true* (an action type, a rail, an outcome) is validated in code
after the model returns it; the schema only guarantees shape.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .model import Receipt


def _require_nonnullable_output_fields(schema: dict) -> None:
    """Keep Strands 1.54's tool schema consistent with our non-null validation.

    Its converter marks omitted/defaulted fields nullable. Require explicit values
    in model output, including [] for empty lists, while retaining Python defaults
    for local callers. Actually nullable fields remain optional.
    """
    required = list(schema.get("required", []))
    for name, prop in schema.get("properties", {}).items():
        types = prop.get("type", [])
        nullable = types == "null" or (isinstance(types, list) and "null" in types)
        nullable = nullable or any(option.get("type") == "null" for option in prop.get("anyOf", []))
        if not nullable and name not in required:
            required.append(name)
    schema["required"] = required


class AgentOutput(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require_nonnullable_output_fields)


# Intake


class AmountItem(AgentOutput):
    label: str = Field(description="What the amount is, in plain words, e.g. 'billed by the dentist'")
    amount_text: str = Field(description="The amount exactly as written, e.g. '$180.00'. Never computed or reformatted.")
    quote: str = Field(description="Verbatim line from the request or document containing this amount")


class DateItem(AgentOutput):
    label: str = Field(description="What the date is, e.g. 'service date' or 'due date'")
    date_text: str = Field(description="The date exactly as written. Never computed or reformatted.")
    quote: str = Field(description="Verbatim line from the request or document containing this date")


class IntakeReading(AgentOutput):
    """What came in: a typed request, a photo, a PDF. Every amount and date is quoted, never inferred."""

    document_class: str = Field(description="'text-request', 'dental-eob', 'tuition-invoice', 'allowance-note', 'recall-notice', 'unknown'")
    issuer: str = Field(description="Who wrote it: the member speaking, or the institution on the document")
    subject_hint: str = Field(description="Who it seems to be about, in plain words, or '' when unclear")
    amounts: list[AmountItem] = Field(default_factory=list)
    dates: list[DateItem] = Field(default_factory=list)
    transcribed_lines: list[str] = Field(default_factory=list, description="Lines copied verbatim from the input")
    summary_en: str = Field(description="One or two plain-English sentences saying what is being asked")
    evidence: list[str] = Field(default_factory=list, description="Verbatim quotes supporting the classification")
    confidence: str = Field(description="'high', 'medium' or 'low'")


# Matcher


class CaseAssignment(AgentOutput):
    """Who this is about, who is asking, which skill handles it, and which account is involved."""

    subject_member_id: str = Field(description="Member id whose affairs this concerns")
    actor_member_id: str = Field(description="Member id who is asking (the session actor)")
    skill_id: str = Field(description="Skill id from the list given, or 'household' for a plain payment or email")
    account_id: str = Field(description="Household account id involved, or '' when none")
    confidence: str = Field(description="'high', 'medium' or 'low'")
    reasons: list[str] = Field(default_factory=list)


# Planner


class PayloadField(AgentOutput):
    key: str
    value: str


class ProposedAction(AgentOutput):
    """One action the planner proposes. It is a proposal only: code assigns the id, the actor and the idempotency key,
    and code decides whether it may run."""

    action_type: str = Field(description="One of the action types listed in the skill block, e.g. 'allowance:transfer'")
    rail: str = Field(description="The rail named by the template, e.g. 'internal-ledger'")
    subject_member_id: str = Field(description="Member id whose affairs the action concerns")
    recipient: str = Field(description="Email address, account id or payee exactly as given, or '' when none")
    amount_text: str = Field(description="Decimal amount as a plain number string such as '8.00', or '' when no money moves")
    currency: str = Field(description="Currency code, normally 'CAD'")
    payload: list[PayloadField] = Field(default_factory=list, description="Template fields, e.g. memo, subject, body")
    evidence_refs: list[str] = Field(default_factory=list, description="Verbatim quotes from the intake that justify the action")
    rationale: str = Field(description="One sentence saying why this action follows from the request and the rules")
    claimed_grant_id: str = Field(description="Grant id the requester cites, or '' when none is cited")


class ActionPlan(AgentOutput):
    actions: list[ProposedAction] = Field(default_factory=list)
    needs: list[str] = Field(default_factory=list, description="Facts still missing or unverified; nothing is proposed on them")
    notes: list[str] = Field(default_factory=list)


# Authority


class DecisionEcho(AgentOutput):
    """The check_authority result, echoed exactly. The code recomputes it afterwards; a mismatch is recorded."""

    action_id: str
    outcome: str = Field(description="'allow', 'block' or 'needs-approval', exactly as the tool returned it")
    rule_id: str = Field(description="The rule id the tool returned")
    grant_id: str = Field(description="The grant or rule the tool cited, or '' when the tool returned null (never the word null)")
    approver_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class AuthorityVerdict(AgentOutput):
    decisions: list[DecisionEcho] = Field(default_factory=list)
    verdict: str = Field(description="'proceed' when at least one action is allowed and no revision is needed; "
                         "'revise' to send the plan back once with the reasons; 'stop' when nothing may proceed")


# Executor


class ExecutionReport(AgentOutput):
    receipts: list[Receipt] = Field(default_factory=list, description="Receipts exactly as execute_action returned them")
    skipped: list[str] = Field(default_factory=list, description="Action ids that were not executed, with no receipt")


# Briefer


class Briefing(AgentOutput):
    """What the member hears at the end, in English and in their own language."""

    headline_en: str
    headline_target: str = Field(min_length=1, description="The headline in the member's language")
    done: list[str] = Field(default_factory=list, description="One line per completed action, naming its receipt label")
    waiting_on: list[str] = Field(default_factory=list, description="One line per action waiting on a named approver")
    labels: list[str] = Field(default_factory=list, description="Receipt modes shown, e.g. 'SIMULATED', 'PREPARE-ONLY'")
    next_step_en: str
    next_step_target: str = Field(min_length=1)
