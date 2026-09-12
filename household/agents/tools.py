"""Deterministic tools the agents call. The critic's checks live here so refusal is bound to code, not to a vibe."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from strands import Agent, tool
from strands.models import Model

from ..config import Settings
from ..fidelity import score_fidelity as _score
from ..rules import FIDELITY_RULE, RULES, lookup


@dataclass
class SessionRecord:
    """What the tools observed during one session; lets the pipeline verify the back-translation was independent."""

    back_translations: list[str] = field(default_factory=list)
    rule_lookups: list[str] = field(default_factory=list)
    fidelity_calls: list[dict[str, Any]] = field(default_factory=list)


def make_tools(model: Model, settings: Settings, record: SessionRecord) -> dict[str, Any]:
    @tool
    def lookup_rule(document_class: str) -> str:
        """Look up the front desk's rule for a document class. Returns the rule id, policy and citation, or 'assist'.

        Args:
            document_class: the catalogue id chosen by the document-reader, e.g. "ltb-n4".
        """
        record.rule_lookups.append(document_class)
        rule = lookup(document_class)
        if rule is None:
            return json.dumps({"document_class": document_class, "rule_id": None, "policy": "assist",
                               "detail": "no read-only rule for this class; the desk may draft"})
        return json.dumps({
            "document_class": document_class,
            "rule_id": rule.id,
            "title": rule.title,
            "kind": rule.kind,
            "policy": rule.policy,
            "citation": rule.citation_line(),
            "statement_en": rule.statement["en"],
        }, ensure_ascii=False)

    @tool
    def score_fidelity(source_en: str, back_translation_en: str) -> str:
        """Score how much of the English reading survived translation and independent back-translation.

        Args:
            source_en: the English reading text given to the interpreter.
            back_translation_en: the interpreter's back-translation of its own target text.
        """
        result = _score(source_en, back_translation_en, settings.fidelity_floor, settings.fidelity_caution)
        payload = {
            "score": result.score,
            "band": result.band,
            "numbers_expected": result.numbers_expected,
            "numbers_missing": result.numbers_missing,
            "rule_id_if_unreliable": FIDELITY_RULE.id,
        }
        record.fidelity_calls.append(payload)
        return json.dumps(payload)

    @tool
    async def back_translate(text: str, source_language: str) -> str:
        """Translate text back to English with a fresh agent that never sees the original English.

        Args:
            text: the target-language text to translate back.
            source_language: the language code of that text, e.g. "es".
        """
        translator = Agent(
            model=model,
            system_prompt=f"[[role:back-translator]] Translate the user's {source_language} text into plain English. "
            "Translate everything, add nothing, omit nothing, keep every number and date exactly. Reply with the "
            "translation only.",
            callback_handler=None,
            name="back-translator",
        )
        result = await translator.invoke_async(text)
        output = str(result).strip()
        record.back_translations.append(output)
        return output

    @tool
    def rule_text(rule_id: str, language: str) -> str:
        """Return a rule's refusal statement, handoff and safe-today text in English and, when available, the language.

        Args:
            rule_id: the rule id from the critic's verdict, e.g. "ON-LTB-N4" or "FIDELITY-FLOOR".
            language: the visitor's language code, e.g. "es".
        """
        rule = next((r for r in (*RULES, FIDELITY_RULE) if r.id == rule_id), None)
        if rule is None:
            return json.dumps({"rule_id": rule_id, "error": "unknown rule id"})
        return json.dumps({
            "rule_id": rule.id,
            "title": rule.title,
            "citation": rule.citation_line(),
            "statement_en": rule.statement["en"],
            "statement_target": rule.statement.get(language),
            "handoff_en": rule.handoff["en"],
            "handoff_target": rule.handoff.get(language),
            "safe_today_en": rule.safe_today.get("en"),
            "safe_today_target": rule.safe_today.get(language),
        }, ensure_ascii=False)

    return {
        "lookup_rule": lookup_rule,
        "score_fidelity": score_fidelity,
        "back_translate": back_translate,
        "rule_text": rule_text,
    }
