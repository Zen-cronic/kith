"""Settings come from the environment (and a gitignored .env). Secrets are never read by anything but the SDK clients."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROVIDERS: tuple[str, ...] = ("fake", "anthropic", "openai", "bedrock")
SELECTABLE: tuple[str, ...] = ("auto", *PROVIDERS)
ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    provider: str = "fake"
    requested_provider: str = "fake"
    anthropic_model_id: str = "claude-opus-5"
    openai_model_id: str = "gpt-5"
    bedrock_model_id: str | None = None
    aws_region: str = "us-east-1"
    speech_provider: str = "browser"
    fidelity_floor: float = 0.50
    fidelity_caution: float = 0.70
    max_revisions: int = 2
    max_model_calls: int = 20

    def __post_init__(self) -> None:
        if isinstance(self.max_model_calls, bool) or not isinstance(self.max_model_calls, int) or self.max_model_calls <= 0:
            raise ValueError("MAX_MODEL_CALLS must be a positive integer")

    @property
    def model_id(self) -> str:
        return {
            "fake": "fake-fixture-model",
            "anthropic": self.anthropic_model_id,
            "openai": self.openai_model_id,
            "bedrock": self.bedrock_model_id or "(strands regional default)",
        }[self.provider]


def load_settings(env_file: str | os.PathLike[str] | None = None, **overrides: object) -> Settings:
    """Read settings from .env + environment. Explicit keyword overrides win (used by tests and the CLI)."""
    load_dotenv(env_file or ROOT / ".env", override=False)
    requested = str(overrides.pop("provider", None) or os.environ.get("MODEL_PROVIDER", "auto")).strip().lower()
    if requested not in SELECTABLE:
        raise ValueError(f"MODEL_PROVIDER must be one of {SELECTABLE}, got {requested!r}")
    provider = resolve_auto() if requested == "auto" else requested
    values = {
        "provider": provider,
        "requested_provider": requested,
        "anthropic_model_id": os.environ.get("ANTHROPIC_MODEL_ID", "claude-opus-5"),
        "openai_model_id": os.environ.get("OPENAI_MODEL_ID", "gpt-5"),
        "bedrock_model_id": os.environ.get("BEDROCK_MODEL_ID") or None,
        "aws_region": os.environ.get("AWS_REGION", "us-east-1"),
        "speech_provider": os.environ.get("SPEECH_PROVIDER", "browser"),
        "max_model_calls": overrides.get("max_model_calls") if overrides.get("max_model_calls") is not None else int(os.environ.get("MAX_MODEL_CALLS", "20")),
    }
    values.update({k: v for k, v in overrides.items() if v is not None})
    return Settings(**values)  # type: ignore[arg-type]


def resolve_auto() -> str:
    """auto = the first live provider with a key in the environment, else the fake provider. Bedrock is never
    auto-selected: it needs model access granted in the account, so it is chosen explicitly."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "fake"


def provider_readiness() -> dict[str, dict[str, str | bool]]:
    """Which providers could run right now. Reports presence of keys only, never their values."""
    return {
        "fake": {"ready": True, "why": "deterministic fixture-backed model; no network"},
        "anthropic": {
            "ready": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "why": "ANTHROPIC_API_KEY present" if os.environ.get("ANTHROPIC_API_KEY") else "ANTHROPIC_API_KEY missing",
        },
        "openai": {
            "ready": bool(os.environ.get("OPENAI_API_KEY")),
            "why": "OPENAI_API_KEY present" if os.environ.get("OPENAI_API_KEY") else "OPENAI_API_KEY missing",
        },
        "bedrock": {
            "ready": bool(os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE")),
            "why": "AWS credentials in environment (validity not checked here)"
            if (os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"))
            else "no AWS credentials in environment",
        },
    }
