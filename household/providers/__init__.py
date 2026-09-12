"""One provider interface. MODEL_PROVIDER selects fake | anthropic | openai | bedrock; nothing else in the code changes."""

from __future__ import annotations

from strands.models import Model

from ..config import Settings
from ..fixtures import FixtureStore


def build_model(settings: Settings, store: FixtureStore | None = None) -> Model:
    if settings.provider == "fake":
        from .fake import FakeModel

        return FakeModel(store or FixtureStore())
    from .live import build_live_model

    return build_live_model(settings)
