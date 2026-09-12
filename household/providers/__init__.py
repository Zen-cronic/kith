"""One provider interface. MODEL_PROVIDER selects fake | anthropic | openai | bedrock; nothing else in the code changes.
NODE_MODELS overrides the model for one node (e.g. a vision-capable model for intake); in fake mode it is ignored so
an offline run never reaches a network."""

from __future__ import annotations

from dataclasses import replace

from strands.models import Model

from ..config import Settings
from ..fixtures import FixtureStore


def build_model(settings: Settings, store: FixtureStore | None = None) -> Model:
    if settings.provider == "fake":
        from .fake import FakeModel

        return FakeModel(store or FixtureStore())
    from .live import build_live_model

    return build_live_model(settings)


def build_node_model(settings: Settings, node_id: str, default: Model, store: FixtureStore | None = None) -> Model:
    """The model for one roster node: the NODE_MODELS override when set (live providers only), else the default."""
    spec = settings.node_models.get(node_id)
    if spec is None or settings.provider == "fake":
        return default
    provider, model_id = spec.split(":", 1)
    if provider == "fake":
        return default
    field = {"anthropic": "anthropic_model_id", "openai": "openai_model_id", "bedrock": "bedrock_model_id"}[provider]
    from .live import build_live_model

    return build_live_model(replace(settings, provider=provider, **{field: model_id}))
