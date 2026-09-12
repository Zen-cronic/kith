"""Live model hosts through Strands' first-class providers. Keys are read by the SDK clients from the environment only."""

from __future__ import annotations

import os

from strands.models import Model

from ..config import Settings


def build_live_model(settings: Settings) -> Model:
    if settings.provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("MODEL_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set (put it in .env)")
        from strands.models.anthropic import AnthropicModel

        return AnthropicModel(model_id=settings.anthropic_model_id, max_tokens=8000, client_args={"max_retries": 0})
    if settings.provider == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("MODEL_PROVIDER=openai but OPENAI_API_KEY is not set (put it in .env)")
        from strands.models.openai import OpenAIModel

        return OpenAIModel(model_id=settings.openai_model_id, client_args={"max_retries": 0})
    if settings.provider == "bedrock":
        from botocore.config import Config
        from strands.models import BedrockModel

        # Strands retries pass through the shared budget; botocore must not retry invisibly.
        client_config = Config(retries={"mode": "standard", "total_max_attempts": 1}, read_timeout=120)

        if settings.bedrock_model_id:
            return BedrockModel(model_id=settings.bedrock_model_id, region_name=settings.aws_region, boto_client_config=client_config)
        return BedrockModel(region_name=settings.aws_region, boto_client_config=client_config)
    raise ValueError(f"unknown provider {settings.provider!r}")
