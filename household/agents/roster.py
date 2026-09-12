"""The roster: six named Strands agents with one job each. Shown on one screen in the UI and in `household run`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel
from strands import Agent
from strands.models import Model

from ..config import Settings
from ..model import Household, Member
from ..schemas import ActionPlan, AuthorityVerdict, Briefing, CaseAssignment, ExecutionReport, IntakeReading
from ..skills import SKILL_TOOL_NAMES
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
    AgentSpec("intake", "Intake reader", "Reads the photo, PDF or typed request; quotes every amount and date; names the document", IntakeReading, False, ()),
    AgentSpec("matcher", "Case matcher", "Says who this is about and which skill applies, looking the household up", CaseAssignment, False, ("household_lookup",)),
    AgentSpec("planner", "Planner", "Proposes actions from the matched skill's rules; never sends, pays or files", ActionPlan, False, SKILL_TOOL_NAMES),
    AgentSpec("authority", "Authority", "Checks every proposal against the ledger in code; can send the plan back", AuthorityVerdict, True, ("check_authority",)),
    AgentSpec("executor", "Executor", "Runs only allowed actions through the rails and returns receipts", ExecutionReport, False, ("execute_action",)),
    AgentSpec("briefer", "Briefer", "Tells the member what happened and what is waiting on whom, in their language", Briefing, False, ("grant_text",)),
)

SPEC_BY_ID = {spec.id: spec for spec in ROSTER}


def system_prompt_for(spec: AgentSpec, actor: Member) -> str:
    return {
        "intake": prompts.INTAKE,
        "matcher": prompts.matcher(),
        "planner": prompts.PLANNER,
        "authority": prompts.AUTHORITY,
        "executor": prompts.EXECUTOR,
        "briefer": prompts.briefer(actor.language),
    }[spec.id]


def build_agents(
    model_for: Callable[[str], Model] | Model,
    settings: Settings,
    household: Household,
    actor: Member,
    record: SessionRecord,
    now: datetime | None = None,
) -> dict[str, Agent]:
    """One Agent per roster spec. `model_for(node_id)` returns that node's (budgeted) model; a bare Model is used
    for every node."""
    tools = make_tools(household, actor, settings, record, now)
    agents: dict[str, Agent] = {}
    for spec in ROSTER:
        model = model_for(spec.id) if callable(model_for) else model_for
        agents[spec.id] = Agent(
            model=model,
            system_prompt=system_prompt_for(spec, actor),
            structured_output_model=spec.schema,
            tools=[tools[name] for name in spec.tools],
            callback_handler=None,
            name=spec.name,
            agent_id=spec.id,
        )
    return agents
