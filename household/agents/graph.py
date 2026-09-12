"""The Strands Graph. Six nodes with a revise loop and a branch around the executor:

    intake -> matcher -> planner -> authority -> [any allow] executor -> briefer
                                    authority -> [no allow] ---------> briefer
                                    authority -> planner   (revise; loops up to max_revisions)

Control edges decide who runs next. Data edges only decide what a node sees when it runs; they are drawn dashed.
The human approval gate is not a node: needs-approval decisions are skipped by the executor and surfaced.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from strands import Agent
from strands.hooks import AfterNodeCallEvent, BeforeInvocationEvent
from strands.multiagent import GraphBuilder
from strands.multiagent.graph import Graph, GraphState

from ..config import Settings
from .context import verdict_of
from .roster import ROSTER

ComposeInput = Callable[[str, Graph, BeforeInvocationEvent], "str | list[dict[str, Any]]"]


def _done(state: GraphState, node_id: str) -> bool:
    return node_id in state.results


def _runs(state: GraphState, node_id: str) -> int:
    return sum(1 for node in state.execution_order if node.node_id == node_id)


def _verdict(state: GraphState) -> str | None:
    verdict = verdict_of(state)
    return verdict.verdict if verdict else None


def _any_allow(state: GraphState) -> bool:
    verdict = verdict_of(state)
    return bool(verdict and any(d.outcome == "allow" for d in verdict.decisions))


@dataclass(frozen=True)
class EdgeSpec:
    """One edge of the real graph, with the label a person reads. Control edges decide who runs next; data edges
    only decide what a node sees as input when it does run."""

    src: str
    dst: str
    label: str
    kind: str  # "control" | "data"


def edge_specs(settings: Settings) -> tuple[EdgeSpec, ...]:
    return (
        EdgeSpec("intake", "matcher", "always", "control"),
        EdgeSpec("matcher", "planner", "case assigned", "control"),
        EdgeSpec("intake", "planner", "reading (data)", "data"),
        EdgeSpec("planner", "authority", "plan ready", "control"),
        EdgeSpec("authority", "planner", f"verdict = revise (up to {settings.max_revisions}x)", "control"),
        EdgeSpec("authority", "executor", "at least one action allowed", "control"),
        EdgeSpec("authority", "briefer", "nothing allowed: brief the member", "control"),
        EdgeSpec("executor", "briefer", "receipts", "control"),
        EdgeSpec("intake", "briefer", "reading (data)", "data"),
        EdgeSpec("matcher", "briefer", "assignment (data)", "data"),
        EdgeSpec("planner", "briefer", "plan (data)", "data"),
    )


def describe_graph(settings: Settings | None = None) -> str:
    """Mermaid source for the real graph, generated from the same edge list the builder uses."""
    settings = settings or Settings()
    lines = ["flowchart LR"]
    for spec in ROSTER:
        shape = ("{{", "}}") if spec.can_reject else ("[", "]")
        lines.append(f'    {spec.id}{shape[0]}"{spec.name}"{shape[1]}')
    data_edges: list[int] = []
    for index, e in enumerate(edge_specs(settings)):
        if e.kind == "data":
            data_edges.append(index)
        lines.append(f'    {e.src} -->|"{e.label}"| {e.dst}')
    if data_edges:
        lines.append(f"    linkStyle {','.join(str(i) for i in data_edges)} stroke-dasharray: 4 3;")
    lines.append("    classDef authority fill:#fde8e8,stroke:#b42318,color:#111;")
    lines.append("    class authority authority;")
    return "\n".join(lines)


def build_graph(
    agents: dict[str, Agent],
    settings: Settings,
    compose_input: ComposeInput,
    on_node_done: Callable[[str, Any], None] | None = None,
    session_manager: Any | None = None,
) -> Graph:
    max_planner_runs = settings.max_revisions + 1

    def wants_revision(state: GraphState) -> bool:
        return _verdict(state) == "revise" and _runs(state, "planner") < max_planner_runs

    def settled(state: GraphState) -> bool:
        # The authority must have judged the *current* plan: after the last permitted planner run the previous
        # verdict is stale until the authority runs again, so count runs rather than trusting the old result.
        return (
            _done(state, "authority")
            and _runs(state, "authority") >= _runs(state, "planner")
            and not wants_revision(state)
        )

    def briefer_ready(state: GraphState) -> bool:
        return settled(state) and (not _any_allow(state) or _done(state, "executor"))

    conditions: dict[tuple[str, str], Callable[[GraphState], bool] | None] = {
        ("intake", "matcher"): None,
        ("matcher", "planner"): None,
        ("intake", "planner"): lambda s: _done(s, "matcher"),
        ("planner", "authority"): None,
        # The authority can reject the plan and send it back, a bounded number of times.
        ("authority", "planner"): wants_revision,
        # Once settled, the executor runs only when something was allowed; otherwise the briefer runs directly.
        ("authority", "executor"): lambda s: settled(s) and _any_allow(s),
        ("authority", "briefer"): lambda s: settled(s) and not _any_allow(s),
        ("executor", "briefer"): None,
        ("intake", "briefer"): briefer_ready,
        ("matcher", "briefer"): briefer_ready,
        ("planner", "briefer"): briefer_ready,
    }

    builder = GraphBuilder()
    for spec in ROSTER:
        builder.add_node(agents[spec.id], spec.id)
    for e in edge_specs(settings):
        builder.add_edge(e.src, e.dst, condition=conditions[(e.src, e.dst)])
    assert {(e.src, e.dst) for e in edge_specs(settings)} == set(conditions), "edge list and conditions must match"

    builder.set_entry_point("intake")
    builder.reset_on_revisit(True)
    builder.set_max_node_executions(6 + 2 * settings.max_revisions + 2)
    builder.set_execution_timeout(900)
    if session_manager is not None:
        builder.set_session_manager(session_manager)
    graph = builder.build()

    # The SDK's default Graph input uses str(AgentResult), not structured_output. Bind public invocation hooks to
    # the pipeline's compose_input, which renders the typed predecessor results. Each request builds a fresh
    # graph/agent roster, so closures are session-local.
    for node_id, agent in agents.items():
        if node_id == "intake":
            continue

        def typed_input(event: BeforeInvocationEvent, target_id: str = node_id) -> None:
            composed = compose_input(target_id, graph, event)
            content = [{"text": composed}] if isinstance(composed, str) else list(composed)
            event.messages = [{"role": "user", "content": content}]

        agent.hooks.add_callback(BeforeInvocationEvent, typed_input)

    if on_node_done is not None:

        def _after(event: AfterNodeCallEvent) -> None:
            on_node_done(event.node_id, event.source.state.results.get(event.node_id))

        graph.add_hook(_after, AfterNodeCallEvent)
    return graph


def traversable_predecessors(graph: Graph, target_id: str, event: BeforeInvocationEvent) -> list[str]:
    """Completed predecessors whose edge into `target_id` is currently satisfied, in graph order."""
    found: list[str] = []
    for edge in graph.edges:
        if edge.to_node.node_id != target_id or edge.from_node not in graph.state.completed_nodes:
            continue
        if not edge.should_traverse(graph.state, invocation_state=event.invocation_state):
            continue
        found.append(edge.from_node.node_id)
    return found
