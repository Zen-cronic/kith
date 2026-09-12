from household.agents.graph import build_graph, describe_graph, edge_specs
from household.agents.roster import ROSTER, build_agents
from household.agents.tools import SessionRecord
from household.config import Settings
from household.fixtures import FixtureStore
from household.providers.fake import FakeModel

NODES = ("intake", "matcher", "planner", "authority", "executor", "briefer")


def test_mermaid_export_names_every_agent_and_the_loop() -> None:
    src = describe_graph(Settings())
    assert src.startswith("flowchart LR")
    for node in NODES:
        assert f'    {node}' in src
    assert 'authority{{"Authority"}}' in src
    assert 'authority -->|"verdict = revise (up to 2x)"| planner' in src
    assert 'authority -->|"at least one action allowed"| executor' in src
    assert 'authority -->|"nothing allowed: brief the member"| briefer' in src
    assert src.count("-->") >= 9
    assert "linkStyle" in src  # data edges are drawn dashed but still count as edges


def test_every_edge_has_a_kind_and_a_label_and_joins_roster_nodes() -> None:
    ids = {spec.id for spec in ROSTER}
    assert [spec.id for spec in ROSTER] == list(NODES)
    for e in edge_specs(Settings()):
        assert e.kind in {"control", "data"} and e.label
        assert e.src in ids and e.dst in ids


def test_builder_wires_the_same_edges_and_limits() -> None:
    settings = Settings(max_revisions=1)
    household = FixtureStore().household("demo")
    agents = build_agents(FakeModel(), settings, household, household.member("kofi"), SessionRecord())
    graph = build_graph(agents, settings, lambda target, g, e: "")
    wired = {(e.from_node.node_id, e.to_node.node_id) for e in graph.edges}
    assert wired == {(e.src, e.dst) for e in edge_specs(settings)}
    assert graph.entry_points and next(iter(graph.entry_points)).node_id == "intake"
    assert graph.max_node_executions == 6 + 2 * settings.max_revisions + 2
    assert graph.reset_on_revisit is True
