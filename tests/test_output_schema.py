"""Regress the model-facing schema, not just Pydantic's input defaults."""

import pytest
from pydantic import ValidationError
from strands.tools.structured_output.structured_output_tool import StructuredOutputTool

from household.agents.roster import ROSTER
from household.schemas import DocumentReading


def test_strands_does_not_advertise_null_for_nonnullable_defaults():
    for spec in ROSTER:
        schema = StructuredOutputTool(spec.schema).tool_spec["inputSchema"]["json"]
        for name, field in spec.schema.model_fields.items():
            if field.is_required():
                continue
            prop = schema["properties"][name]
            if field.default is None:
                assert "null" in prop["type"], (spec.id, name)
                continue
            assert name in schema["required"], (spec.id, name)
            assert "null" not in prop["type"], (spec.id, name)


def test_empty_lists_remain_valid_but_null_deadlines_do_not():
    values = dict(document_class="school-letter", title="Letter", issuer="School",
                  what_it_is="A letter", what_it_asks="Return the slip", stakes="low",
                  stakes_reason="Routine", evidence=[], confidence=1)
    assert DocumentReading(**values).deadlines == []
    assert DocumentReading(**values, deadlines=[]).deadlines == []
    with pytest.raises(ValidationError):
        DocumentReading(**values, deadlines=None)
