"""Official forms are prepared, never filed: the skill's field schema and verbatim source quotes are rendered to
`runs/forms/<action_id>.md` for a human to review and file. Writes are atomic (tmp + replace).

The schema comes from the proposing skill (`Skill.form_spec`) when it owns one for the action type; otherwise from
the proposal payload (`fields` / `quotes` JSON lists, or one `field:<name>` / `quote:<n>` key per item).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import ROOT
from ..model import ActionProposal, Household
from ..skills import skill_for
from ..skills.base import FormField, FormSpec, SourceQuote

__all__ = ["BANNER", "FORMS_DIR", "FormField", "FormSpec", "SourceQuote", "render", "safe_name", "spec_for",
           "spec_from_payload", "spec_from_skill", "write"]

FORMS_DIR = ROOT / "runs" / "forms"
BANNER = "PREPARED, NOT FILED. A human reviews this and files it; the agent never submits an official form."
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
NO_FIELDS = "no form fields: a prepared form needs the skill's field schema"


def safe_name(action_id: str) -> str:
    return _UNSAFE.sub("-", action_id).strip("-.") or "form"


def _load_list(raw: str, what: str) -> list[Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{what} is not valid JSON: {exc.msg}") from exc
    if not isinstance(value, list):
        raise ValueError(f"{what} must be a JSON list")
    return value


def spec_from_payload(proposal: ActionProposal) -> FormSpec:
    """Read the skill's field schema from the proposal payload. Two encodings are accepted, since the payload is a
    flat str→str map: JSON lists under `fields` / `quotes`, or one key per item (`field:<name>`, `quote:<n>`)."""
    payload = proposal.payload
    form_id = payload.get("form_id") or proposal.action_type
    fields: list[FormField] = []
    if "fields" in payload:
        for item in _load_list(payload["fields"], "fields"):
            if isinstance(item, dict):
                fields.append(FormField(str(item.get("name", "")), str(item.get("value", "")), str(item.get("quote", ""))))
            elif isinstance(item, list) and item:
                fields.append(FormField(str(item[0]), str(item[1]) if len(item) > 1 else "", str(item[2]) if len(item) > 2 else ""))
            else:
                raise ValueError("fields entries must be objects or [name, value] lists")
    else:
        fields = [FormField(key[len("field:"):], value) for key, value in sorted(payload.items()) if key.startswith("field:")]
    quotes: list[SourceQuote] = []
    if "quotes" in payload:
        for item in _load_list(payload["quotes"], "quotes"):
            if isinstance(item, dict):
                quotes.append(SourceQuote(str(item.get("text", "")), str(item.get("source", ""))))
            else:
                quotes.append(SourceQuote(str(item)))
    else:
        quotes = [SourceQuote(value) for key, value in sorted(payload.items()) if key.startswith("quote:")]
    if not any(field.name for field in fields):
        raise ValueError(NO_FIELDS)
    return FormSpec(form_id, payload.get("form_title") or form_id, payload.get("form_source", ""), tuple(fields), tuple(quotes))


def spec_from_skill(proposal: ActionProposal) -> FormSpec | None:
    """The form the proposing skill owns for this action type, if any (unknown skill ids fall back to the core skill,
    which owns no forms)."""
    return skill_for(proposal.skill_id).form_spec(proposal)


def spec_for(proposal: ActionProposal) -> FormSpec:
    """The form to render: the skill's own schema first, else the one carried in the payload. Raises ValueError when
    neither yields a named field."""
    spec = spec_from_skill(proposal)
    if spec is None:
        return spec_from_payload(proposal)
    if not any(field.name for field in spec.fields):
        raise ValueError(NO_FIELDS)
    return spec


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def render(proposal: ActionProposal, household: Household, spec: FormSpec, at: datetime) -> str:
    subject = household.member(proposal.subject_member_id)
    actor = household.member(proposal.actor_member_id)
    lines = [
        f"# {spec.title} (prepared, not filed)",
        "",
        f"> {BANNER}",
        "",
        f"- Form: `{spec.form_id}`" + (f" — source: {spec.source}" if spec.source else ""),
        f"- Household: {household.name} (`{household.id}`)",
        f"- Subject: {subject.name if subject else proposal.subject_member_id} (`{proposal.subject_member_id}`)",
        f"- Prepared by: {actor.name if actor else proposal.actor_member_id} (`{proposal.actor_member_id}`) at {at.isoformat()}",
        f"- Action: `{proposal.id}` ({proposal.action_type}, skill `{proposal.skill_id}`)",
    ]
    if proposal.evidence_refs:
        lines.append("- Evidence: " + ", ".join(f"`{ref}`" for ref in proposal.evidence_refs))
    lines += ["", "## Fields", "", "| Field | Value | Verbatim source |", "|---|---|---|"]
    for field in spec.fields:
        lines.append(f"| {_cell(field.name)} | {_cell(field.value)} | {_cell(field.quote)} |")
    if spec.quotes:
        lines += ["", "## Source quotes (verbatim)", ""]
        for quote in spec.quotes:
            lines.append("> " + quote.text.replace("\n", "\n> "))
            if quote.source:
                lines.append(f"> — {quote.source}")
            lines.append("")
    if proposal.rationale:
        lines += ["## Why this was prepared", "", proposal.rationale]
    return "\n".join(lines).rstrip("\n") + "\n"


def write(text: str, action_id: str, directory: Path | None = None) -> Path:
    target_dir = directory or FORMS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{safe_name(action_id)}.md"
    temp = path.with_suffix(".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)
    return path
