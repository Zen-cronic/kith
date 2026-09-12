"""Helpers shared by the graph conditions, the pipeline and the fake provider."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from ..schemas import CriticVerdict, DocumentReading, Draft, Interpretation, NextStepCard

T = TypeVar("T", bound=BaseModel)


def reading_text(reading: DocumentReading) -> str:
    """The exact English text the interpreter renders and the fidelity score compares against."""
    parts = [reading.title.rstrip(".") + ".", reading.what_it_is.strip(), reading.what_it_asks.strip()]
    if reading.deadlines:
        parts.append("Dates: " + "; ".join(f"{d.label}: {d.date_text}" for d in reading.deadlines) + ".")
    if reading.amounts:
        parts.append("Amounts: " + ", ".join(reading.amounts) + ".")
    return " ".join(p for p in parts if p)


def structured_from_node(node_result: Any, schema: type[T]) -> T | None:
    """Pull the typed structured output out of a Strands NodeResult, or None."""
    if node_result is None:
        return None
    agent_result = getattr(node_result, "result", node_result)
    output = getattr(agent_result, "structured_output", None)
    if isinstance(output, schema):
        return output
    if isinstance(output, BaseModel):
        return schema.model_validate(output.model_dump())
    return None


def state_output(state: Any, node_id: str, schema: type[T]) -> T | None:
    return structured_from_node(state.results.get(node_id), schema)


def reading_of(state: Any) -> DocumentReading | None:
    return state_output(state, "reader", DocumentReading)


def interpretation_of(state: Any) -> Interpretation | None:
    return state_output(state, "interpreter", Interpretation)


def draft_of(state: Any) -> Draft | None:
    return state_output(state, "drafter", Draft)


def verdict_of(state: Any) -> CriticVerdict | None:
    return state_output(state, "critic", CriticVerdict)


def card_of(state: Any) -> NextStepCard | None:
    return state_output(state, "router", NextStepCard)
