"""One household session: fixture document + visitor language -> the five-agent graph -> a verified result.

Two things happen in code, not in a prompt, on purpose:
1. Fidelity is recomputed from the reading text and the back-translation, so the number on screen is checkable.
2. A policy guard re-derives the outcome from the rule catalogue and the fidelity band. If the model's verdict
   disagrees with the rule, the rule wins and the disagreement is recorded (it counts against the model in the trap-set).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field
from strands.models import Model

from .agents.context import reading_text, structured_from_node
from .agents.graph import build_graph
from .agents.roster import SPEC_BY_ID, build_agents
from .agents.tools import SessionRecord
from .config import Settings, load_settings
from .fidelity import FidelityScore, score_fidelity
from .fixtures import Expected, FixtureDocument, FixtureStore
from .handoff import COPY
from .languages import Language, get_language
from .providers import build_model
from .providers.budget import BudgetedModel
from .reading_policy import ReadingPolicyBinding, bind_reading
from .routine import bind_source_values, draft_issues, form_excerpt, routine_card
from .rules import FIDELITY_RULE, REVIEW_RULE, Rule, lookup
from .schemas import CriticCheck, CriticVerdict, DocumentReading, Draft, Interpretation, NextStepCard


class RosterStep(BaseModel):
    node_id: str
    name: str
    run: int
    status: str
    execution_ms: int


class PolicyGuard(BaseModel):
    rule_id: str | None
    rule_policy: str
    fidelity_band: str
    policy_outcome: str
    model_outcome: str
    override: bool
    detail: str


class SessionResult(BaseModel):
    fixture_id: str
    fixture_title: str
    fixture_source: str
    language: str
    provider: str
    model_id: str
    model_calls: dict[str, Any] = Field(default_factory=dict)
    outcome: str
    reading: DocumentReading | None = None
    model_reading: DocumentReading | None = None
    reading_policy: dict[str, str] | None = None
    source_form: str | None = None
    source_issues: list[str] = Field(default_factory=list)
    interpretation: Interpretation | None = None
    reading_text: str = ""
    fidelity: dict[str, Any] = Field(default_factory=dict)
    back_translation_independent: bool | None = None
    drafts: list[Draft] = Field(default_factory=list)
    verdicts: list[CriticVerdict] = Field(default_factory=list)
    model_verdicts: list[CriticVerdict] = Field(default_factory=list)
    model_drafts: list[Draft] = Field(default_factory=list)
    card: NextStepCard | None = None
    guard: PolicyGuard
    roster: list[RosterStep] = Field(default_factory=list)
    execution_order: list[str] = Field(default_factory=list)
    graph_status: str
    elapsed_ms: int
    notes: list[str] = Field(default_factory=list)


def card_from_rule(rule: Rule, reading: DocumentReading | None, language: Language) -> NextStepCard:
    """A code-generated escalation card, used when the router's card disagrees with the rule or is missing."""
    lang = language.code
    st, hand, safe = rule.statement, rule.handoff, rule.safe_today
    words = COPY[lang]
    title = reading.title if reading else rule.title
    dates = "; ".join(f"{d.label}: {d.date_text}" for d in reading.deadlines) if reading and reading.deadlines else "no date found"
    amounts = ", ".join(reading.amounts) if reading and reading.amounts else "—"
    summary_en = f"{title}. Dates: {dates}. Amounts: {amounts}. Suggested contact: {hand['en']}. {safe.get('en', '')}".strip()
    return NextStepCard(
        outcome="escalate",
        headline_en=f"{rule.title}: I will read it to you, not answer it.",
        headline_target=st.get(lang, words["review"]),
        statement_en=st["en"],
        statement_target=st.get(lang, words["review"]),
        rule_citation=rule.citation_line(),
        who=hand["en"],
        who_target=hand.get(lang, words["staff"]),
        next_step_en=COPY["en"]["next"],
        next_step_target=words["next"],
        when=COPY["en"]["when"],
        when_target=words["when"],
        safe_today_en=safe.get("en", COPY["en"]["keep"]),
        safe_today_target=safe.get(lang, words["keep"]),
        summary_en=summary_en,
        summary_target=f"{st.get(lang, words['review'])} {words['details']}: "
        f"{'; '.join(d.date_text for d in reading.deadlines) if reading else '—'}; {amounts}. "
        f"{words['next']} {words['when']}",
    )


def adhoc_document(text: str, title: str = "Document brought to the desk") -> FixtureDocument:
    """Wrap raw text (from the UI drop zone or an AgentCore payload) as a one-off document."""
    return FixtureDocument(
        id="adhoc",
        title=title,
        source="visitor-supplied (session only; not stored)",
        source_url=None,
        notes="Raw text supplied at the desk; discarded when the session ends.",
        summary_en=text.strip().splitlines()[0][:200] if text.strip() else "(empty document)",
        text=text,
        expected=Expected("unknown", "medium", False),
        tags=("adhoc",),
    )


async def stream_session(
    fixture_id: str | None,
    language: str = "es",
    settings: Settings | None = None,
    model: Model | None = None,
    store: FixtureStore | None = None,
    document: FixtureDocument | None = None,
    session_manager: Any | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run one session and yield roster events as the graph executes, then the final SessionResult.

    Events: {"event": "session_start", ...}, {"event": "node_start", node_id, run}, {"event": "node_done", node_id, run,
    status, execution_ms, output}, {"event": "result", "result": SessionResult}.
    """
    settings = settings or load_settings()
    store = store or FixtureStore()
    if document is None and fixture_id is None:
        raise ValueError("stream_session needs a fixture_id or a document")
    fixture = document or store.get(fixture_id or "")
    if document is not None:
        store = store.with_adhoc(document)
    lang = get_language(language)
    model = BudgetedModel(model or build_model(settings, store), settings.max_model_calls)
    record = SessionRecord()
    agents = build_agents(model, settings, lang, record)
    reading_binding: ReadingPolicyBinding | None = None
    source_issues: list[str] = []
    raw_reading: DocumentReading | None = None
    model_verdicts: list[CriticVerdict] = []
    model_drafts: list[Draft] = []
    checked_nodes: dict[int, Any] = {}

    def apply_policy(node_id: str, node_result: Any) -> None:
        nonlocal reading_binding, raw_reading
        if node_result is None or id(node_result) in checked_nodes:
            return
        checked_nodes[id(node_result)] = node_result
        if node_id == "reader":
            raw = structured_from_node(node_result, DocumentReading)
            if raw is not None:
                raw_reading = raw.model_copy(deep=True)
                reading_binding = bind_reading(raw, fixture.text)
                if reading_binding is not None:
                    node_result.result.structured_output = reading_binding.reading
                elif raw.stakes != "high" and lookup(raw.document_class) is None:
                    source_issues.extend(bind_source_values(raw, fixture.text))
        elif node_id == "drafter":
            draft = structured_from_node(node_result, Draft)
            if draft:
                model_drafts.append(draft.model_copy(deep=True))
                if draft.kind == "form-checklist" and draft.preparation_steps and form_excerpt(fixture.text):
                    draft.body_en = "\n\n".join(f"{i}. {step.instruction_en}" for i, step in enumerate(draft.preparation_steps, 1))
                    draft.body_target = "\n\n".join(f"{i}. {step.instruction_target}" for i, step in enumerate(draft.preparation_steps, 1))
        elif node_id == "critic":
            reading = structured_from_node(graph.state.results.get("reader"), DocumentReading)
            verdict = structured_from_node(node_result, CriticVerdict)
            if verdict is not None:
                model_verdicts.append(verdict.model_copy(deep=True))
            if reading and reading.stakes != "high" and lookup(reading.document_class) is None and verdict and verdict.decision != "refuse":
                draft = structured_from_node(graph.state.results.get("drafter"), Draft)
                issues = source_issues + draft_issues(draft, verdict, fixture.text)
                if issues:
                    verdict.decision = "revise"
                    verdict.revision_notes = list(dict.fromkeys(verdict.revision_notes + issues))
                    verdict.reasons.append("Source/output contract requires revision before release.")
                    verdict.checks.append(CriticCheck(name="source-contract (code)", passed=False, detail="; ".join(issues)))

    graph = build_graph(agents, settings, on_node_done=apply_policy, session_manager=session_manager)

    task = (
        f"Document id: {fixture.id}\n"
        f"Document title as filed: {fixture.title}\n"
        f"Document text:\n{fixture.text}"
    )
    yield {
        "event": "session_start",
        "fixture_id": fixture.id,
        "fixture_title": fixture.title,
        "language": lang.code,
        "provider": settings.provider,
        "model_id": settings.model_id,
        "model_calls": model.snapshot(),
        "roster": [{"node_id": s.id, "name": s.name, "job": s.job, "can_reject": s.can_reject} for s in SPEC_BY_ID.values()],
    }

    drafts: list[Draft] = []
    verdicts: list[CriticVerdict] = []
    steps: list[RosterStep] = []
    runs: dict[str, int] = {}
    graph_result: Any = None
    t0 = time.perf_counter()
    try:
        async for event in graph.stream_async(task, invocation_state={"fixture_id": fixture.id, "language": lang.code, "document_text": fixture.text}):
            kind = event.get("type")
            if kind == "multiagent_node_start":
                node_id = event["node_id"]
                runs[node_id] = runs.get(node_id, 0) + 1
                yield {"event": "node_start", "node_id": node_id, "name": SPEC_BY_ID[node_id].name, "run": runs[node_id]}
            elif kind == "multiagent_node_stop":
                node_id = event["node_id"]
                node_result = event["node_result"]
                # Also bind before emitting the streamed reading: the SDK's after-node
                # hook is after its stop event, but before scheduling downstream nodes.
                apply_policy(node_id, node_result)
                output: Any = None
                spec = SPEC_BY_ID[node_id]
                typed = structured_from_node(node_result, spec.schema)
                if node_id == "drafter" and isinstance(typed, Draft):
                    drafts.append(typed)
                if node_id == "critic" and isinstance(typed, CriticVerdict):
                    verdicts.append(typed)
                if typed is not None:
                    output = typed.model_dump()
                step = RosterStep(
                    node_id=node_id,
                    name=spec.name,
                    run=runs.get(node_id, 1),
                    status=str(getattr(node_result, "status", "")).split(".")[-1].lower(),
                    execution_ms=int(getattr(node_result, "execution_time", 0) or 0),
                )
                steps.append(step)
                extra: dict[str, Any] = {}
                if node_id == "reader" and reading_binding is not None:
                    extra["reading_policy"] = reading_binding.provenance()
                if node_id == "interpreter" and isinstance(typed, Interpretation):
                    # The gauge must move before the critic speaks, so score the round trip as soon as it exists.
                    reading_now = structured_from_node(graph.state.results.get("reader"), DocumentReading)
                    if reading_now is not None:
                        fid = score_fidelity(reading_text(reading_now), typed.back_translation, settings.fidelity_floor, settings.fidelity_caution)
                        extra["fidelity"] = fid.__dict__
                yield {"event": "node_done", **step.model_dump(), "output": output, **extra}
            elif kind == "multiagent_result":
                graph_result = event["result"]
    except Exception:
        model.raise_if_exhausted()
        raise
    # Graph/tool runners can turn exceptions into failed node results. Do not finalize those.
    model.raise_if_exhausted()
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if graph_result is None:
        raise RuntimeError("graph produced no result event")

    session = _finalize(fixture, lang, settings, record, graph_result, drafts, verdicts, steps, elapsed_ms, reading_binding)
    if model_verdicts:
        session.guard.model_outcome = "proceed" if model_verdicts[-1].decision == "approve" else "escalate"
        session.guard.override = session.guard.policy_outcome != session.guard.model_outcome
    session.model_calls = model.snapshot()
    session.model_drafts = model_drafts
    session.model_verdicts = model_verdicts
    session.model_reading = raw_reading
    session.source_issues = source_issues
    if session.reading and session.reading.stakes != "high" and lookup(session.reading.document_class) is None:
        session.source_form = form_excerpt(fixture.text)
        if session.outcome == "proceed" and session.interpretation and lang.code in {"en", "es"}:
            session.card = routine_card(session.reading, session.interpretation, bool(session.source_form))
    yield {"event": "result", "result": session}


def run_session(
    fixture_id: str | None,
    language: str = "es",
    settings: Settings | None = None,
    model: Model | None = None,
    store: FixtureStore | None = None,
    document: FixtureDocument | None = None,
) -> SessionResult:
    """Synchronous convenience wrapper around stream_session (CLI, tests, the trap-set harness)."""

    async def collect() -> SessionResult:
        final: SessionResult | None = None
        async for event in stream_session(fixture_id, language, settings, model, store, document=document):
            if event["event"] == "result":
                final = event["result"]
        assert final is not None
        return final

    return asyncio.run(collect())


def _finalize(
    fixture: Any,
    lang: Language,
    settings: Settings,
    record: SessionRecord,
    result: Any,
    drafts: list[Draft],
    verdicts: list[CriticVerdict],
    steps: list[RosterStep],
    elapsed_ms: int,
    reading_binding: ReadingPolicyBinding | None = None,
) -> SessionResult:
    reading = structured_from_node(result.results.get("reader"), DocumentReading)
    interpretation = structured_from_node(result.results.get("interpreter"), Interpretation)
    card = structured_from_node(result.results.get("router"), NextStepCard)
    verdict = verdicts[-1] if verdicts else None
    notes: list[str] = []

    # Fidelity, recomputed in code from the two English strings.
    fidelity: FidelityScore | None = None
    src = reading_text(reading) if reading else ""
    if reading and interpretation:
        fidelity = score_fidelity(src, interpretation.back_translation, settings.fidelity_floor, settings.fidelity_caution)
    independent: bool | None
    if settings.provider == "fake":
        independent = None
        notes.append("fake provider: interpretation and back-translation are canned or derived; fidelity is computed from them")
    else:
        independent = bool(interpretation and interpretation.back_translation.strip() in {b.strip() for b in record.back_translations})
        if not independent:
            notes.append("back-translation did not come verbatim from the back_translate tool; treated as not independent")

    # Policy guard: the rule catalogue and the fidelity floor decide, whatever the model said.
    rule = lookup(reading.document_class) if reading else None
    band = fidelity.band if fidelity else "unknown"
    rule_refuses = bool(rule and rule.policy == "read-and-explain-only")
    fidelity_refuses = band == "unreliable" or (settings.provider != "fake" and not independent)
    # Reaching the retry cap is termination, never approval. Missing results also
    # require review; a router's optimistic card cannot supply a critic verdict.
    review_required = verdict is None or verdict.decision == "revise"
    policy_outcome = "escalate" if (rule_refuses or fidelity_refuses or review_required) else "proceed"
    model_outcome = "proceed" if verdict and verdict.decision == "approve" else "escalate"
    override = policy_outcome != model_outcome
    guard_rule = rule if rule_refuses else (FIDELITY_RULE if fidelity_refuses else (REVIEW_RULE if review_required else rule))
    detail = "rule and model agree" if not override else (
        "rule requires escalation; model verdict did not" if policy_outcome == "escalate" else "model refused; no rule requires it (possible false refusal)"
    )
    if review_required:
        detail = "explicit critic approval missing; staff review required"
    guard = PolicyGuard(
        rule_id=guard_rule.id if guard_rule else None,
        rule_policy=rule.policy if rule else "assist",
        fidelity_band=band,
        policy_outcome=policy_outcome,
        model_outcome=model_outcome,
        override=override,
        detail=detail,
    )
    outcome = "escalate" if "escalate" in (policy_outcome, model_outcome) else "proceed"
    if outcome == "escalate" and (review_required or card is None or card.outcome != "escalate") and guard_rule:
        card = card_from_rule(guard_rule, reading, lang)
        notes.append(f"router card replaced by the code-generated card for {guard_rule.id}")
    if card is None and reading:
        notes.append("router produced no card")

    if reading_binding is not None and rule is not None:
        # The router cannot remove a consequential qualification or replace staff
        # availability with a landlord's requested payment/departure date.
        card = card_from_rule(rule, reading, lang)
        card.headline_en = "Form N4: review with staff"
        card.who = "Staff at this organization"
        card.next_step_en = rule.handoff["en"]
        if lang.code == "es":
            card.headline_target = "Aviso N4: revise con el personal"
            card.who_target = "Personal de esta organización"
            card.next_step_target = rule.handoff["es"]
        elif lang.code == "en":
            card.headline_target = card.headline_en
            card.who_target = card.who
            card.next_step_target = card.next_step_en
        card.summary_en = reading_binding.summary("en") or card.summary_en
        card.summary_target = reading_binding.summary(lang.code) or card.summary_target
        notes.append("N4 explanation and final card bound to source-checked local policy; inspect model_reading separately")

    return SessionResult(
        fixture_id=fixture.id,
        fixture_title=fixture.title,
        fixture_source=fixture.source,
        language=lang.code,
        provider=settings.provider,
        model_id=settings.model_id,
        outcome=outcome,
        reading=reading,
        model_reading=reading_binding.model_reading if reading_binding else None,
        reading_policy=reading_binding.provenance() if reading_binding else None,
        interpretation=interpretation,
        reading_text=src,
        fidelity=(fidelity.__dict__ if fidelity else {}),
        back_translation_independent=independent,
        drafts=drafts,
        verdicts=verdicts,
        card=card,
        guard=guard,
        roster=steps,
        execution_order=[node.node_id for node in result.execution_order],
        graph_status=str(result.status).split(".")[-1].lower(),
        elapsed_ms=elapsed_ms,
        notes=notes,
    )
