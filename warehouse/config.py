"""Where the Snowflake connection settings come from.

Reads the [connections.<name>] block that the Week 4 setup script already wrote to
~/.snowflake/config.toml, then lets environment variables override any field.
The private key never leaves your machine and is never printed.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

# Environment variable -> config field. Any of these overrides the TOML value.
ENV_OVERRIDES = {
    "SNOWFLAKE_ACCOUNT": "account",
    "SNOWFLAKE_USER": "user",
    "SNOWFLAKE_PRIVATE_KEY_FILE": "private_key_file",
    "SNOWFLAKE_ROLE": "role",
    "SNOWFLAKE_WAREHOUSE": "warehouse",
    "SNOWFLAKE_DATABASE": "database",
    "SNOWFLAKE_SCHEMA": "schema",
}

REQUIRED = ("account", "user", "private_key_file", "role", "warehouse", "database", "schema")


def _config_candidates() -> list[Path]:
    explicit = os.environ.get("SNOWFLAKE_CONFIG_FILE")
    if explicit:
        return [Path(explicit)]
    home = Path.home() / ".snowflake"
    paths = [home / "config.toml", home / "connections.toml"]
    local_app = os.environ.get("LOCALAPPDATA")  # Snowflake CLI's default on Windows
    if local_app:
        paths += [Path(local_app) / "snowflake" / "config.toml",
                  Path(local_app) / "snowflake" / "connections.toml"]
    return paths


def _read_toml_block(name: str) -> tuple[dict, Path | None]:
    for path in _config_candidates():
        if not path.is_file():
            continue
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        # config.toml nests under [connections.<name>]; connections.toml uses [<name>]
        block = data.get("connections", {}).get(name) or data.get(name)
        if isinstance(block, dict):
            return dict(block), path
    return {}, None


def load_settings() -> dict:
    """Return the connection settings, or raise with a message saying what's missing."""
    name = os.environ.get("SNOWFLAKE_CONNECTION", "claude")
    settings, source = _read_toml_block(name)
    for env_key, field in ENV_OVERRIDES.items():
        if os.environ.get(env_key):
            settings[field] = os.environ[env_key]
    missing = [f for f in REQUIRED if not settings.get(f)]
    if missing:
        where = str(source) if source else "no config file found (looked in: " + ", ".join(
            str(p) for p in _config_candidates()) + ")"
        raise RuntimeError(
            f"Snowflake settings incomplete for connection '{name}'. Missing: {', '.join(missing)}. "
            f"Source: {where}. Set the missing values as environment variables (e.g. SNOWFLAKE_ROLE)."
        )
    settings["_source"] = str(source) if source else "environment variables"
    return settings
