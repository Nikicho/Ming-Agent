"""Configuration management for Ming.

Loads config from:
1. config/default.yaml (base defaults)
2. config/local.yaml (user overrides, gitignored)
3. Environment variables (MING_LLM_API_KEY, etc.)
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    model: str = "deepseek/deepseek-chat"
    api_key: str = ""
    api_base: str = ""
    temperature: float = 0.3
    max_tokens: int = 4096


class ContextConfig(BaseModel):
    max_context_tokens: int = 128000
    compaction_threshold: float = 0.50
    compaction_safety_threshold: float = 0.85


class AgentConfig(BaseModel):
    max_iterations: int = 50
    max_seconds: int = 300
    max_cost_per_turn: float = 0


class LoggingConfig(BaseModel):
    level: str = "INFO"


class MingConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def _find_project_root() -> Path:
    """Walk up from CWD to find the Ming project root (contains pyproject.toml)."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return cwd


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> MingConfig:
    """Load configuration from YAML files + environment."""
    import os

    root = _find_project_root()
    config_dir = root / "config"

    # Layer 1: defaults
    data: dict[str, Any] = {}
    default_path = config_dir / "default.yaml"
    if default_path.exists():
        with open(default_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    # Layer 2: local overrides
    local_path = config_dir / "local.yaml"
    if local_path.exists():
        with open(local_path, encoding="utf-8") as f:
            local_data = yaml.safe_load(f) or {}
            data = _deep_merge(data, local_data)

    # Layer 3: environment variables
    env_api_key = os.environ.get("MING_LLM_API_KEY", "")
    if env_api_key:
        data.setdefault("llm", {})["api_key"] = env_api_key

    env_model = os.environ.get("MING_LLM_MODEL", "")
    if env_model:
        data.setdefault("llm", {})["model"] = env_model

    return MingConfig(**data)
