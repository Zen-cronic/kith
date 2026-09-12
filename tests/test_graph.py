from household.agents.graph import describe_graph, edge_specs
from household.config import Settings


def test_mermaid_export_names_every_agent_and_the_loop() -> None:
    src = describe_graph(Settings())
    assert src.startswith("flowchart LR")
    for node in ("reader", "interpreter", "drafter", "critic", "router"):
        assert node in src
    assert 'critic -->|"verdict = revise (up to 2x)"| drafter' in src
    assert 'interpreter -->|"stakes high: bypass the drafter"| critic' in src


def test_every_edge_has_a_kind_and_a_label() -> None:
    for e in edge_specs(Settings()):
        assert e.kind in {"control", "data"} and e.label
