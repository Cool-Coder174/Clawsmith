from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

_REPO_ROOT = Path(__file__).parent.parent


class ConfigurationError(Exception):
    """Raised when the configuration file is missing, unreadable, or invalid."""


class ModelConfig(BaseModel):
    provider: str
    model_name: str
    max_tokens: int = 4096
    temperature: float = 0.2
    input_cost_per_token: float | None = None
    output_cost_per_token: float | None = None


class ModelsConfig(BaseModel):
    local_router: ModelConfig
    local_code: ModelConfig
    premium: ModelConfig
    prompt_polisher: ModelConfig


class RoutingConfig(BaseModel):
    low_complexity_threshold: float = 0.35
    high_complexity_threshold: float = 0.70
    ambiguity_bump_threshold: float = 0.60


class ExecutionConfig(BaseModel):
    default_timeout: int = 300
    max_retries: int = 2
    artifacts_dir: str = "artifacts"
    logs_dir: str = "logs"
    allowed_commands: list[str] = Field(default_factory=list)


class McpServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    transport: str = "sse"


class OpenClawConfig(BaseModel):
    skill_name: str = "ClawSmith"
    mcp_endpoint: str = "http://127.0.0.1:8765/sse"
    webhook_secret: str = ""


class ClawsmithConfig(BaseModel):
    models: ModelsConfig
    routing: RoutingConfig = RoutingConfig()
    execution: ExecutionConfig = ExecutionConfig()
    mcp_server: McpServerConfig = McpServerConfig()
    openclaw: OpenClawConfig = OpenClawConfig()


_ENV_PREFIX = "CLAWSMITH_"
_ENV_SEP = "__"


def _coerce_value(value: str) -> Any:
    """Attempt to coerce a string env value to an appropriate Python type."""
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return value


def _apply_env_overrides(data: dict[str, Any]) -> None:
    """Merge ``CLAWSMITH_<SECTION>__<KEY>`` environment variables into *data*.

    Nested keys use double-underscore separators.  For example::

        CLAWSMITH_ROUTING__LOW_COMPLEXITY_THRESHOLD=0.5
        CLAWSMITH_MODELS__PREMIUM__MAX_TOKENS=16384
        CLAWSMITH_EXECUTION__ALLOWED_COMMANDS=["cursor","python"]
        CLAWSMITH_MCP_SERVER__PORT=9000
    """
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(_ENV_PREFIX):
            continue
        suffix = env_key[len(_ENV_PREFIX):]
        if not suffix:
            continue
        parts = [p.lower() for p in suffix.split(_ENV_SEP)]
        target = data
        for part in parts[:-1]:
            if part not in target or not isinstance(target[part], dict):
                target[part] = {}
            target = target[part]
        target[parts[-1]] = _coerce_value(env_val)


_REQUIRED_SECTIONS = ("models", "routing", "execution", "mcp_server", "openclaw")


def _validate_path_field(value: str, field_name: str, errors: list[str]) -> None:
    """Check that a path value is syntactically valid and repo-relative safe."""
    if not value:
        errors.append(f"{field_name} is empty")
        return
    p = Path(value)
    if p.is_absolute():
        errors.append(
            f"{field_name} must be a relative path, got absolute: {value}"
        )
        return
    if ".." in p.parts:
        errors.append(
            f"{field_name} must not traverse outside the repository root: {value}"
        )
        return
    try:
        (_REPO_ROOT / p).resolve()
    except (OSError, ValueError):
        errors.append(f"{field_name} is not a syntactically valid path: {value}")


def validate_config(
    cfg: ClawsmithConfig,
    raw_data: dict[str, Any] | None = None,
) -> None:
    """Raise ``ConfigurationError`` if *cfg* has critical missing values.

    When *raw_data* is provided (the parsed YAML dict before Pydantic
    defaults are applied), required section presence is enforced against
    it so that missing sections are caught even when Pydantic fills in
    default values.
    """
    errors: list[str] = []

    if raw_data is not None:
        for section in _REQUIRED_SECTIONS:
            if section not in raw_data:
                errors.append(f"Required section '{section}' is missing from YAML")

    for tier_name in ("local_router", "local_code", "premium", "prompt_polisher"):
        tier: ModelConfig = getattr(cfg.models, tier_name)
        if not tier.model_name:
            errors.append(f"models.{tier_name}.model_name is empty")
        if not tier.provider:
            errors.append(f"models.{tier_name}.provider is empty")

    if not cfg.execution.allowed_commands:
        errors.append("execution.allowed_commands is empty")

    _validate_path_field(
        cfg.execution.artifacts_dir, "execution.artifacts_dir", errors
    )
    _validate_path_field(cfg.execution.logs_dir, "execution.logs_dir", errors)

    if errors:
        raise ConfigurationError(
            "Configuration validation failed:\n  - " + "\n  - ".join(errors)
        )


def load_config(path: Path | None = None) -> ClawsmithConfig:
    """Load and validate the ClawSmith configuration from a YAML file.

    Resolution order for the config path:
      1. Explicit ``path`` argument
      2. ``CLAWSMITH_CONFIG_PATH`` environment variable
      3. ``config/settings.yaml`` relative to the repository root
    """
    load_dotenv(_REPO_ROOT / ".env")

    if path is None:
        env_path = os.environ.get("CLAWSMITH_CONFIG_PATH")
        if env_path:
            path = Path(env_path)
        else:
            path = _REPO_ROOT / "config" / "settings.yaml"

    if not path.exists():
        raise ConfigurationError(
            f"Configuration file not found: {path}\n"
            "Create one by copying config/settings.yaml or set CLAWSMITH_CONFIG_PATH."
        )

    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Failed to parse YAML config at {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigurationError(
            f"Expected a YAML mapping at the top level of {path}, got {type(data).__name__}."
        )

    raw_sections = dict(data)
    _apply_env_overrides(data)

    try:
        cfg = ClawsmithConfig(**data)
    except Exception as exc:
        raise ConfigurationError(
            f"Configuration validation failed for {path}: {exc}"
        ) from exc

    validate_config(cfg, raw_data=raw_sections)
    return cfg


# ---------------------------------------------------------------------------
# Singleton access
# ---------------------------------------------------------------------------

_config: ClawsmithConfig | None = None


def get_config() -> ClawsmithConfig:
    """Return the cached configuration, loading it on first call."""
    global _config  # noqa: PLW0603
    if _config is None:
        _config = load_config()
    return _config


def reset_config() -> None:
    """Clear the cached configuration (useful in tests)."""
    global _config  # noqa: PLW0603
    _config = None
