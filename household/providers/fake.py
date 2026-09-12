"""A deterministic Strands Model for tests and the demo script.

It never calls a network. Outputs come from `fixtures/canned/<fixture>.<lang>.json` when a canned file exists for the
fixture and language; otherwise they are derived from the fixture's own metadata and labelled as such. It emits real
Strands stream events (tool use for the critic's checks, then the structured-output tool), so the same Graph, tools and
schemas run in fake and live mode. Anything measured in fake mode is a pipeline check, not a model measurement.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncGenerator, AsyncIterable
from typing import Any

from strands.models import Model
from strands.types.content import Messages
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolSpec

from ..fixtures import FixtureDocument, FixtureStore
from ..handoff import COPY
from ..rules import FIDELITY_RULE, lookup
from ..schemas import CriticVerdict, DocumentReading, Draft, Interpretation, NextStepCard

_ROLE = re.compile(r"\[\[role:([a-z_-]+)\]\]")
FAKE_LABEL = "[fake provider: derived from fixture metadata, not a model output]"

SCHEMA_BY_ROLE = {
    "reader": DocumentReading,
    "interpreter": Interpretation,
    "drafter": Draft,
    "critic": CriticVerdict,
    "router": NextStepCard,
}


def _tool_use_id() -> str:
    return f"tooluse_{uuid.uuid4().hex[:24]}"


def _last_user_text(messages: Messages) -> str:
    """Text of the most recent user turn that carries text (tool-result-only turns are skipped)."""
    for message in reversed(messages):
        if message["role"] == "user":
            text = " ".join(block.get("text", "") for block in message["content"] if "text" in block).strip()
            if text:
                return text
    return ""


def _has_tool_result(messages: Messages) -> bool:
    return bool(messages) and messages[-1]["role"] == "user" and any("toolResult" in b for b in messages[-1]["content"])


class FakeModel(Model):
    def __init__(self, store: FixtureStore | None = None) -> None:
        self.store = store or FixtureStore()
        self._config: dict[str, Any] = {"model_id": "fake-fixture-model"}
        self.calls: list[dict[str, Any]] = []

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return self._config

    # Structured output requested through the legacy Agent.structured_output path.
    async def structured_output(
        self, output_model: type, prompt: Messages, system_prompt: str | None = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        role = self._role(system_prompt)
        state = kwargs.get("invocation_state") or {}
        payload = self._payload(role, state.get("fixture_id"), state.get("language", "es"), _last_user_text(prompt))
        yield {"output": output_model.model_validate(payload)}

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        *,
        tool_choice: Any = None,
        system_prompt_content: Any = None,
        invocation_state: dict[str, Any] | None = None,
        cancel_signal: Any = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        state = invocation_state or {}
        role = self._role(system_prompt)
        fixture_id = state.get("fixture_id")
        language = state.get("language", "es")
        names = {spec["name"] for spec in (tool_specs or [])}
        last_text = _last_user_text(messages)
        self.calls.append({"role": role, "fixture_id": fixture_id, "tools": sorted(names)})

        yield {"messageStart": {"role": "assistant"}}
        if role == "critic" and {"lookup_rule", "score_fidelity"} <= names and not _has_tool_result(messages):
            # First critic pass: run the deterministic checks through real Strands tool use.
            reading = self._payload("reader", fixture_id, language, last_text)
            interp = self._payload("interpreter", fixture_id, language, last_text)
            from ..agents.context import reading_text

            for name, args in (
                ("lookup_rule", {"document_class": reading["document_class"]}),
                (
                    "score_fidelity",
                    {
                        "source_en": reading_text(DocumentReading.model_validate(reading)),
                        "back_translation_en": interp["back_translation"],
                    },
                ),
            ):
                tid = _tool_use_id()
                yield {"contentBlockStart": {"start": {"toolUse": {"name": name, "toolUseId": tid}}}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(args)}}}}
                yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            schema = SCHEMA_BY_ROLE.get(role)
            schema_name = schema.__name__ if schema else None
            if schema_name and schema_name in names:
                payload = self._payload(role, fixture_id, language, last_text)
                tid = _tool_use_id()
                yield {"contentBlockStart": {"start": {"toolUse": {"name": schema_name, "toolUseId": tid}}}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(payload, ensure_ascii=False)}}}}
                yield {"contentBlockStop": {}}
                yield {"messageStop": {"stopReason": "tool_use"}}
            else:
                yield {"contentBlockStart": {"start": {}}}
                yield {"contentBlockDelta": {"delta": {"text": f"{FAKE_LABEL} role={role}"}}}
                yield {"contentBlockStop": {}}
                yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}, "metrics": {"latencyMs": 0}}}

    # Payload selection

    @staticmethod
    def _role(system_prompt: str | None) -> str:
        match = _ROLE.search(system_prompt or "")
        return match.group(1) if match else "unknown"

    def _payload(self, role: str, fixture_id: str | None, language: str, last_text: str) -> dict[str, Any]:
        fixture = self.store.get(fixture_id) if fixture_id else None
        canned = self.store.canned(fixture_id, language) if fixture_id else None
        if canned:
            if role == "drafter" and "From critic" in last_text and "drafter_revised" in canned:
                return canned["drafter_revised"]
            if role == "critic" and '"revision":2' in last_text.replace(" ", "") and "critic_final" in canned:
                return canned["critic_final"]
            if role in canned:
                return canned[role]
        if fixture is None:
            raise ValueError(f"FakeModel has no fixture for role={role} fixture_id={fixture_id!r}")
        return self._derived(role, fixture, language, last_text)

    def _derived(self, role: str, fx: FixtureDocument, language: str, last_text: str) -> dict[str, Any]:
        cls = fx.expected.document_class
        rule = lookup(cls)
        label = FAKE_LABEL
        first_line = next((ln.strip() for ln in fx.text.splitlines() if ln.strip()), fx.title)
        if role == "reader":
            return {
                "document_class": cls,
                "title": fx.title,
                "issuer": "see document",
                "what_it_is": f"{fx.summary_en} {label}",
                "what_it_asks": "See the document.",
                "deadlines": [],
                "amounts": [],
                "stakes": fx.expected.stakes,
                "stakes_reason": f"fixture metadata says stakes={fx.expected.stakes} {label}",
                "evidence": [first_line],
                "confidence": 0.5,
            }
        if role == "interpreter":
            # Derived = faithful by construction: the "back-translation" is the derived reading text itself.
            from ..agents.context import reading_text

            derived_reading = DocumentReading.model_validate(self._derived("reader", fx, language, last_text))
            english = reading_text(derived_reading)
            return {
                "language": language,
                "target_text": f"[{language}] {english}",
                "back_translation": english,
                "flagged_terms": [],
            }
        if role == "drafter":
            from ..routine import form_excerpt
            has_form = bool(form_excerpt(fx.text))
            revised = "From critic" in last_text
            return {
                "kind": "form-checklist" if has_form else "note-for-staff",
                "title": f"Note about: {fx.title}",
                "body_en": f"Review the original form and fill it with your own information. {label}" if has_form else f"Visitor brought: {fx.summary_en} {label}",
                "body_target": f"[{language}] Review the original form and fill it with your own information. {label}" if has_form else f"[{language}] {fx.summary_en} {label}",
                "preparation_steps": [{"instruction_en": f"Review the original form and fill it with your own information. {label}",
                                       "instruction_target": f"[{language}] Review the original form and fill it with your own information. {label}",
                                       "source_quote": form_excerpt(fx.text)}] if has_form else [],
                "facts_used": [first_line],
                "assumptions": [],
                "revision": 2 if revised else 1,
            }
        if role == "critic":
            if rule and rule.policy == "read-and-explain-only":
                return {
                    "decision": "refuse",
                    "rule_id": rule.id,
                    "checks": [{"name": "rule-catalogue", "passed": True, "detail": f"{cls} -> {rule.id} ({rule.policy}) {label}"}],
                    "reasons": [f"{rule.title}: {rule.policy}"],
                    "revision_notes": [],
                }
            return {
                "decision": "approve",
                "rule_id": None,
                "checks": [{"name": "rule-catalogue", "passed": True, "detail": f"{cls}: no read-only rule; assist {label}"},
                           {"name": "draft-facts", "passed": True, "detail": f"Fixture replay only {label}"},
                           {"name": "draft-assumptions", "passed": True, "detail": f"Fixture replay only {label}"}],
                "reasons": ["no rule requires refusal; draft has no assumptions"],
                "revision_notes": [],
            }
        if role == "router":
            r = rule if (rule and rule.policy == "read-and-explain-only") else None
            if r:
                st = r.statement
                return {
                    "outcome": "escalate",
                    "headline_en": f"This is: {r.title}. I will read it, not answer it.",
                    "headline_target": st.get(language, st["en"]),
                    "statement_en": st["en"],
                    "statement_target": st.get(language, st["en"]),
                    "rule_citation": r.citation_line(),
                    "who": r.handoff["en"],
                    "who_target": r.handoff.get(language, COPY[language]["staff"]),
                    "next_step_en": COPY["en"]["next"],
                    "next_step_target": COPY[language]["next"],
                    "when": COPY["en"]["when"],
                    "when_target": COPY[language]["when"],
                    "safe_today_en": r.safe_today.get("en", COPY["en"]["keep"]),
                    "safe_today_target": r.safe_today.get(language, COPY[language]["keep"]),
                    "summary_en": f"{fx.title}. {fx.summary_en} Suggested contact: {r.handoff['en']}. {label}",
                    "summary_target": f"[{language}] {fx.summary_en} {label}",
                }
            return {
                "outcome": "proceed",
                "headline_en": f"{fx.title}: here is what to do.",
                "headline_target": f"[{language}] {fx.title} {label}",
                "statement_en": f"I read the document and prepared the reply. {label}",
                "statement_target": f"[{language}] {label}",
                "rule_citation": None,
                "who": "you, with a staff member if you want help",
                "who_target": "usted; pida ayuda al personal si la necesita" if language == "es" else f"[{language}] {label}",
                "next_step_en": "Review and sign the prepared reply.",
                "next_step_target": f"[{language}] {label}",
                "when": "See the document for its deadline; no service time is confirmed.",
                "when_target": COPY[language]["when"],
                "safe_today_en": "Keep the original letter.",
                "safe_today_target": f"[{language}] {label}",
                "summary_en": f"{fx.title}. {fx.summary_en} {label}",
                "summary_target": f"[{language}] {fx.summary_en} {label}",
            }
        if role == "fidelity":
            return FIDELITY_RULE.statement
        raise ValueError(f"FakeModel cannot derive a payload for role {role!r}")
