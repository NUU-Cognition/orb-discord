"""Configuration — loaded from config.json profiles with env var overrides."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Project root is two levels up from this file (src/orb_discord/config.py -> project root)
_PROJECT_ROOT = Path(__file__).parent.parent.parent

_CONFIG_FILE = _PROJECT_ROOT / "config.json"


def _load_profile() -> dict:
    """Load the active profile from config.json, falling back to empty dict."""
    if not _CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(_CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    profiles = data.get("profiles", {})
    active = os.environ.get("ORB_DISCORD_PROFILE") or data.get("active", "default")
    return profiles.get(active, {})


_profile = _load_profile()


def _get(key: str, *, env: str | None = None, default: str | None = None) -> str | None:
    """Resolve a config value: env var > profile > default."""
    if env:
        env_val = os.environ.get(env)
        if env_val is not None:
            return env_val
    profile_val = _profile.get(key)
    if profile_val is not None:
        return str(profile_val)
    return default


def _require(key: str, *, env: str | None = None) -> str:
    """Like _get but raises if the value is missing everywhere."""
    val = _get(key, env=env)
    if val is None:
        sources = f"config.json profile key '{key}'"
        if env:
            sources = f"env var '{env}' or {sources}"
        raise RuntimeError(f"Missing required config: {sources}")
    return val


DISCORD_TOKEN = _require("discord_token", env="DISCORD_TOKEN")
FLINT_SERVER_URL = _get("server_url", env="FLINT_SERVER_URL", default=f"http://127.0.0.1:{_get('server_port', env='FLINT_SERVER_PORT', default='7433')}")
MAX_TURNS = int(_get("max_turns", env="MAX_TURNS", default="999"))
POLL_INTERVAL = int(_get("poll_interval", env="POLL_INTERVAL", default="3"))
DASHBOARD_INTERVAL = int(_get("dashboard_interval", env="DASHBOARD_INTERVAL", default="30"))
REQUESTS_CHANNEL_ID = _get("requests_channel_id", env="REQUESTS_CHANNEL_ID")
COMMAND_PREFIX = _get("command_prefix", env="COMMAND_PREFIX", default="!")
TASKS_CHANNEL_ID = _get("tasks_channel_id", env="TASKS_CHANNEL_ID")

STATE_FILE = _PROJECT_ROOT / "state.json"
EMBED_LIMIT = 3800
