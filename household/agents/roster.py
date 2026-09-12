"""The roster: five named Strands agents with one job each. Shown on one screen in the UI and in `household run`."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel
from strands import Agent
from strands.models import Model

from ..config import Settings
from ..languages import Language
from ..schemas import CriticVerdict, DocumentReading, Draft, Interpretation, NextStepCard
from . import prompts
from .tools import SessionRecord, make_tools


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    job: str
    schema: type[BaseModel]
    can_reject: bool
    tools: tuple[str, ...]


ROSTER: tuple[AgentSpec, ...] = (
    AgentSpec("reader", "Document reader", "Reads the letter, quotes every date and amount, names the form", DocumentReading, False, ()),
    AgentSpec("interpreter", "Interpreter", "Says it in the visitor's language, then back-translates it with a fresh agent", Interpretation, False, ("back_translate",)),
    AgentSpec("drafter", "Form drafter", "Drafts the reply or checklist from quoted facts only; blanks, never guesses", Draft, False, ()),
    AgentSpec("critic", "Confidence / refusal critic", "Looks up the rule and the fidelity score; approves, sends the draft back, or refuses", CriticVerdict, True, ("lookup_rule", "score_fidelity")),
    AgentSpec("router", "Escalation router", "Writes the bilingual card: who, what next, when, the one safe thing today", NextStepCard, False, ("rule_text",)),
)

SPEC_BY_ID = {spec.id: spec for spec in ROSTER}


def system_prompt_for(spec: AgentSpec, language: Language) -> str:
    return {
        "reader": prompts.READER,
        "interpreter": prompts.interpreter(language),
        "drafter": prompts.drafter(language),
        "critic": prompts.CRITIC,
        "router": prompts.router(language),
    }[spec.id]


def build_agents(model: Model, settings: Settings, language: Language, record: SessionRecord) -> dict[str, Agent]:
    tools = make_tools(model, settings, record)
    agents: dict[str, Agent] = {}
    for spec in ROSTER:
        agents[spec.id] = Agent(
            model=model,
            system_prompt=system_prompt_for(spec, language),
            structured_output_model=spec.schema,
            tools=[tools[name] for name in spec.tools],
            callback_handler=None,
            name=spec.name,
            agent_id=spec.id,
        )
    return agents
