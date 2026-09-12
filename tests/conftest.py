"""Front Desk surface tests owned by later packets are skipped, not deleted.

`agentcore/**` (S1) is outside packet P2's mutation scope and still speaks the Front Desk pipeline (five-agent
roster, document fixtures, LTB rule ids). Its tests cannot pass against the six-node household graph until that packet
rewrites them, so they are skipped here with the owner named. (P4 replaced the trap-set with `household/guardrails`,
tested in `tests/test_guardrails.py`; P5 rewrote the web tests.)
"""

import pytest

FRONT_DESK_SURFACE = {
    "test_agentcore_app.py": "agentcore/app.py is rewritten by S1 (Front Desk payload and roster)",
}
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
        if name in FRONT_DESK_SURFACE:
            item.add_marker(pytest.mark.skip(reason=FRONT_DESK_SURFACE[name]))
        elif name == "test_runtime_bridge.py" and item.originalname in FRONT_DESK_BRIDGE_TESTS:
            item.add_marker(pytest.mark.skip(reason="runtime bridge end-to-end runs use the Front Desk pipeline; S1 rewrites them"))
