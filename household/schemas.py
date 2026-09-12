"""Typed contracts between the five agents. Each agent returns exactly one of these through Strands structured output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Stakes = Literal["high", "medium", "low"]


def _require_nonnullable_output_fields(schema: dict) -> None:
    """Keep Strands 1.54's tool schema consistent with our non-null validation.

    Its converter marks omitted/defaulted fields nullable. Require explicit values
    in model output, including [] for empty lists, while retaining Python defaults
    for local callers. Actually nullable fields such as rule_id remain optional.
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


class Deadline(AgentOutput):
    label: str = Field(description="What the date is, in plain words, e.g. 'termination date stated in the notice'")
    date_text: str = Field(description="The date exactly as written in the document. Never computed or reformatted.")
    quote: str = Field(description="Verbatim quote from the document containing this date")


class DocumentReading(AgentOutput):
    """What the document-reader says the document is. Every claim must be backed by a quote."""

    document_class: str = Field(
        description="Catalogue id such as 'ltb-n4', 'ltb-n1', 'cra-rc66', 'school-letter', 'ircc-letter', "
        "'court-notice', 'benefits-appointment', 'utility-bill', 'clinic-letter', 'community-notice', 'unknown'"
    )
    title: str = Field(description="Plain title of the document, naming the official form if it is one")
    issuer: str = Field(description="Who sent it")
    what_it_is: str = Field(description="One or two plain-English sentences a visitor with no legal background understands")
    what_it_asks: str = Field(description="What the visitor is being asked to do, or 'nothing'")
    deadlines: list[Deadline] = Field(default_factory=list)
    amounts: list[str] = Field(default_factory=list, description="Money amounts exactly as written")
    stakes: Stakes = Field(description="high = legal notice that can lead to eviction, removal, loss of status, court or child protection; "
                           "medium = money, benefits or an appointment at stake; low = informational or routine")
    stakes_reason: str
    evidence: list[str] = Field(description="Verbatim quotes from the document that support the classification")
    confidence: float = Field(ge=0, le=1)


class FlaggedTerm(AgentOutput):
    term: str
    note: str = Field(description="Why it does not map cleanly and how it was rendered")


class Interpretation(AgentOutput):
    """The reading, rendered in the visitor's language, plus an independent back-translation for verification."""

    language: str = Field(description="Target language code, e.g. 'es'")
    target_text: str = Field(description="The reading text in the visitor's language, plain and spoken-register")
    back_translation: str = Field(
        description="English back-translation of target_text produced WITHOUT looking at the English source "
        "(use the back_translate tool and copy its output verbatim)"
    )
    flagged_terms: list[FlaggedTerm] = Field(default_factory=list)


class PreparationStep(AgentOutput):
    instruction_en: str = Field(description="One short instruction for completing the ORIGINAL document. No blank form fields, salutation, signature or claim of completed action.")
    instruction_target: str = Field(description="The same instruction in the visitor language. Keep payee names unchanged.")
    source_quote: str = Field(description="Verbatim source excerpt supporting this instruction")


class Draft(AgentOutput):
    kind: Literal["reply-letter", "form-checklist", "note-for-staff", "none"]
    title: str
    body_en: str
    body_target: str = Field(description="The same draft in the visitor's language")
    preparation_steps: list[PreparationStep] = Field(default_factory=list, description="For form-checklist: concrete steps for the original form, covering its fields, visitor choices, return instructions and support options. The app renders these steps as the takeaway. For other kinds: [].")
    facts_used: list[str] = Field(description="Facts taken from the document, quoted")
    assumptions: list[str] = Field(
        default_factory=list,
        description="Anything stated that is NOT in the document and was not supplied by the visitor. Must be empty for an approvable draft.",
    )
    revision: int = 1


class CriticCheck(AgentOutput):
    name: str
    passed: bool
    detail: str


class CriticVerdict(AgentOutput):
    """The confidence/refusal critic's decision. 'revise' sends the draft back to the drafter; 'refuse' ends drafting and escalates."""

    decision: Literal["approve", "revise", "refuse"]
    rule_id: str | None = Field(default=None, description="Rule id from lookup_rule, when the decision rests on a rule")
    checks: list[CriticCheck] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    revision_notes: list[str] = Field(default_factory=list, description="Concrete fixes for the drafter when decision is 'revise'")


class NextStepCard(AgentOutput):
    """What the visitor leaves with. Both languages, always. Printable as a one-page summary."""

    outcome: Literal["proceed", "escalate"]
    headline_en: str
    headline_target: str
    statement_en: str = Field(description="For escalate: the refusal statement with the rule named. For proceed: what was done.")
    statement_target: str
    rule_citation: str | None = None
    who: str = Field(description="The named human role the visitor is handed to, or who acts next")
    who_target: str = Field(min_length=1, description="Who acts next, in the visitor's language; same role as who")
    next_step_en: str
    next_step_target: str
    when: str = Field(description="Document deadline verbatim, or state that staff availability is not confirmed. Never invent a service time.")
    when_target: str = Field(min_length=1, description="The same deadline or unconfirmed availability in the visitor's language; preserve source date characters")
    safe_today_en: str = Field(description="The one safe thing the visitor can do today, taken from the document itself")
    safe_today_target: str
    summary_en: str = Field(description="One-page printable summary: what the letter is, its deadline, the safe thing, who to see")
    summary_target: str
