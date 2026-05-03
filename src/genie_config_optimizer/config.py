from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_ENV_PATH = ".env"
DEFAULT_CONFIG_PATH = ".config"


@dataclass
class AppConfig:
    databricks_host: str
    databricks_token: str
    workspace_id: str
    genie_space_id: str
    anthropic_api_key: str
    anthropic_model: str


class ConfigError(RuntimeError):
    pass


def load_config(
    env_path: str | os.PathLike = DEFAULT_ENV_PATH,
    config_path: str | os.PathLike = DEFAULT_CONFIG_PATH,
) -> AppConfig:
    env_path = Path(env_path)
    config_path = Path(config_path)

    if not env_path.exists():
        raise ConfigError(
            f".env not found at {env_path.resolve()} — copy .env.example to .env and fill it in."
        )
    if not config_path.exists():
        raise ConfigError(
            f".config not found at {config_path.resolve()} — copy .config.example to .config and fill it in."
        )

    load_dotenv(env_path)
    cp = configparser.ConfigParser()
    cp.read(config_path)

    try:
        host = cp["databricks"]["host"].strip()
        workspace_id = cp["databricks"]["workspace_id"].strip()
        genie_space_id = cp["databricks"]["genie_space_id"].strip()
    except KeyError as e:
        raise ConfigError(f"Missing key in [databricks] section of {config_path}: {e}")

    model = "claude-sonnet-4-6"
    if cp.has_section("anthropic") and cp.has_option("anthropic", "model"):
        model = cp["anthropic"]["model"].strip() or model

    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    missing = []
    if not token:
        missing.append("DATABRICKS_TOKEN")
    if not api_key:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        raise ConfigError(
            f"Missing in {env_path}: {', '.join(missing)}"
        )

    if not host.startswith("http"):
        raise ConfigError(
            f"databricks.host must include https:// (got {host!r})"
        )

    return AppConfig(
        databricks_host=host.rstrip("/"),
        databricks_token=token,
        workspace_id=workspace_id,
        genie_space_id=genie_space_id,
        anthropic_api_key=api_key,
        anthropic_model=model,
    )
