"""The skill contract. A skill is a frozen bundle of rules with citations, read-only or proposal-only tools, action
templates naming the rail and its label rule, an evidence schema, a prompt block for the planner, matcher hints, and
the form schema it owns for actions on the official-form rail (labelled fields plus verbatim source quotes).

Skills propose; they never decide (authority.py) and never execute (executor/). The `SKILLS` tuple in
`household.skills` is the order parameter: matcher tie-break, /api/meta, README and harness grouping all follow it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..model import ActionProposal, Household, Member, money


@dataclass(frozen=True)
class Citation:
    label: str
    url: str
    quote: str


@dataclass(frozen=True)
class RulesTable:
    title: str
    citations: tuple[Citation, ...] = ()
    params: Mapping[str, str] = field(default_factory=dict)

    def prompt_lines(self) -> list[str]:
        lines = [f"Rules: {self.title}"]
        for key, value in self.params.items():
            lines.append(f"- {key}: {value}")
        for citation in self.citations:
            lines.append(f'- {citation.label} ({citation.url}): "{citation.quote}"')
        return lines


@dataclass(frozen=True)
class ActionTemplate:
    action_type: str
    rail: str
    label: str = ""  # the receipt label rule a member reads, e.g. "PREPARE-ONLY, always"
    payload_fields: tuple[str, ...] = ()
    description: str = ""

    def prompt_line(self) -> str:
        fields = ", ".join(self.payload_fields) or "none"
        return f"- {self.action_type} on rail {self.rail} (label: {self.label}); payload fields: {fields}. {self.description}".rstrip()


# Official forms: the skill owns the schema; the official-form rail only renders it (prepared, never filed)


@dataclass(frozen=True)
class FormField:
    name: str
    value: str = ""
    quote: str = ""  # the verbatim source text the value was taken from, if any


@dataclass(frozen=True)
class SourceQuote:
    text: str
    source: str = ""


@dataclass(frozen=True)
class FormSpec:
    form_id: str
    title: str
    source: str = ""
    fields: tuple[FormField, ...] = ()
    quotes: tuple[SourceQuote, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


FormBuilder = Callable[[ActionProposal], FormSpec]


@dataclass(frozen=True)
class ToolContext:
    """What a skill tool may see. Tools are closures over one session; they never reach the store or a rail."""

    household: Household
    actor: Member
    now: datetime
    log: list[dict[str, Any]] = field(default_factory=list)

    def note(self, tool: str, **detail: Any) -> None:
        self.log.append({"tool": tool, **detail})


@dataclass(frozen=True)
class SkillTool:
    name: str
    build: Callable[[ToolContext], Any]


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    action_types: tuple[str, ...]
    tools: tuple[SkillTool, ...]
    rules: RulesTable
    evidence_schema: type[BaseModel]
    action_templates: Mapping[str, ActionTemplate]
    fixtures_dir: Path
    prompt_block: str
    matcher_hints: tuple[str, ...]
    form_builders: Mapping[str, FormBuilder] = field(default_factory=dict)  # action_type -> the form the skill owns

    def build_tools(self, ctx: ToolContext) -> dict[str, Any]:
        return {tool.name: tool.build(ctx) for tool in self.tools}

    def form_spec(self, proposal: ActionProposal) -> FormSpec | None:
        """The form this skill owns for a proposal on the official-form rail: labelled fields with values from the
        proposal payload and the verbatim quotes from its rules. None when the skill declares no form for the action."""
        build = self.form_builders.get(proposal.action_type)
        return build(proposal) if build is not None else None

    def template(self, action_type: str) -> ActionTemplate:
        return self.action_templates[action_type]

    def matches(self, text: str) -> bool:
        lowered = text.lower()
        return any(hint.lower() in lowered for hint in self.matcher_hints)

    def prompt(self) -> str:
        """The block the planner receives: what the skill does, its templates, its rules and the evidence it needs."""
        lines = [f"Skill {self.id} ({self.name}).", self.prompt_block.strip(), "Action templates (use only these):"]
        lines += [t.prompt_line() for t in self.action_templates.values()]
        lines += self.rules.prompt_lines()
        fields = ", ".join(self.evidence_schema.model_fields)
        lines.append(f"Evidence this skill needs, quoted from the intake: {fields}.")
        return "\n".join(lines)


def proposal_json(
    template: ActionTemplate,
    *,
    subject_member_id: str,
    recipient: str = "",
    amount_text: str = "",
    currency: str = "CAD",
    payload: Mapping[str, str] | None = None,
    evidence_refs: list[str] | None = None,
    rationale: str = "",
    claimed_grant_id: str = "",
) -> str:
    """A ProposedAction as JSON, shaped exactly like the planner's structured output. Proposal-only tools return this
    so the model copies a well-formed action instead of inventing one."""
    if amount_text:
        money(amount_text)
    proposal = {
        "action_type": template.action_type,
        "rail": template.rail,
        "subject_member_id": subject_member_id,
        "recipient": recipient,
        "amount_text": amount_text,
        "currency": currency,
        "payload": [{"key": k, "value": v} for k, v in (payload or {}).items()],
        "evidence_refs": list(evidence_refs or []),
        "rationale": rationale,
        "claimed_grant_id": claimed_grant_id,
    }
    return json.dumps(proposal, ensure_ascii=False)


def error_json(message: str, **detail: Any) -> str:
    return json.dumps({"error": message, **detail}, ensure_ascii=False)
