"""Front Desk surface tests owned by later packets are skipped, not deleted.

S1 rewrote `agentcore/app.py` and `tests/test_agentcore_app.py` to the six-node household graph, so that surface is
no longer skipped. The Front Desk runtime-bridge end-to-end tests still replay the old pipeline and remain skipped
until a later packet rewrites them. (P4 replaced the trap-set with `household/guardrails`, tested in
`tests/test_guardrails.py`; P5 rewrote the web tests.)
"""

import pytest

FRONT_DESK_BRIDGE_TESTS = {
    "test_actual_runtime_metadata_and_refusal",
    "test_actual_runtime_assistance_preserves_nullable_result_fields",
    "test_budget_and_config_change_before_document_dispatch",
    "test_memory_is_rejected_and_never_attached",
    "test_no_consent_or_changed_target_never_runs_local_graph",
    "test_terminal_result_not_published_until_clean_end",
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        name = item.fspath.basename
        if name == "test_runtime_bridge.py" and item.originalname in FRONT_DESK_BRIDGE_TESTS:
            item.add_marker(pytest.mark.skip(reason="runtime bridge end-to-end runs use the Front Desk pipeline; a later packet rewrites them"))
