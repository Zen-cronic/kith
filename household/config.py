"""Settings come from the environment (and a gitignored .env). Secrets are never read by anything but the SDK clients."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROVIDERS: tuple[str, ...] = ("fake", "anthropic", "openai", "bedrock")
SELECTABLE: tuple[str, ...] = ("auto", *PROVIDERS)
EXECUTION_MODES: tuple[str, ...] = ("simulated", "live")
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
    # Per-node model overrides, e.g. {"intake": "bedrock:us.amazon.nova-pro-v1:0"}; empty = the default model everywhere.
    node_models: dict[str, str] = field(default_factory=dict)
    # simulated = every rail returns a SIMULATED receipt with the request digest; live = real rails (P3).
    execution_mode: str = "simulated"

    def __post_init__(self) -> None:
        if isinstance(self.max_model_calls, bool) or not isinstance(self.max_model_calls, int) or self.max_model_calls <= 0:
            raise ValueError("MAX_MODEL_CALLS must be a positive integer")
        if self.execution_mode not in EXECUTION_MODES:
            raise ValueError(f"EXECUTION_MODE must be one of {EXECUTION_MODES}, got {self.execution_mode!r}")
        for node_id, spec in self.node_models.items():
            if ":" not in spec or spec.split(":", 1)[0] not in PROVIDERS:
                raise ValueError(f"NODE_MODELS entry for {node_id!r} must look like provider:model_id, got {spec!r}")

    @property
    def model_id(self) -> str:
        return {
            "fake": "fake-fixture-model",
            "anthropic": self.anthropic_model_id,
            "openai": self.openai_model_id,
            "bedrock": self.bedrock_model_id or "(strands regional default)",
        }[self.provider]


def parse_node_models(raw: str | None) -> dict[str, str]:
    """NODE_MODELS="intake=bedrock:us.amazon.nova-pro-v1:0,planner=anthropic:claude-opus-5" -> {node: provider:model}."""
    parsed: dict[str, str] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"NODE_MODELS entry must look like node=provider:model_id, got {item!r}")
        node_id, spec = item.split("=", 1)
        parsed[node_id.strip()] = spec.strip()
    return parsed


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
        "node_models": parse_node_models(os.environ.get("NODE_MODELS")),
        "execution_mode": os.environ.get("EXECUTION_MODE", "simulated").strip().lower(),
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
