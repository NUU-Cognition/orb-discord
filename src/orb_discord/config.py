"""Configuration — loaded from config.json profiles with env var overrides."""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Project root is two levels up from this file (src/orb_discord/config.py -> project root)
_PROJECT_ROOT = Path(__file__).parent.parent.parent

_CONFIG_FILE = _PROJECT_ROOT / "config.json"

_profile: dict = {}
_profile_name: str = ""


def load_profile(name: str) -> None:
    """Load a named profile from config.json. Call once at startup."""
    global _profile, _profile_name
    _profile_name = name
    if not _CONFIG_FILE.exists():
        print(f"Warning: {_CONFIG_FILE} not found, using env vars only.", file=sys.stderr)
        _profile = {}
        return
    try:
        data = json.loads(_CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: failed to read {_CONFIG_FILE}: {e}", file=sys.stderr)
        _profile = {}
        return
    profiles = data.get("profiles", {})
    if name not in profiles:
        available = ", ".join(profiles.keys()) or "(none)"
        print(f"Error: profile '{name}' not found in config.json. Available: {available}", file=sys.stderr)
        sys.exit(1)
    _profile = profiles[name]


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
        sources = f"profile '{_profile_name}' key '{key}'"
        if env:
            sources = f"env var '{env}' or {sources}"
        raise RuntimeError(f"Missing required config: {sources}")
    return val


def get_discord_token() -> str:
    return _require("discord_token", env="DISCORD_TOKEN")


def get_server_url() -> str:
    return _get("server_url", env="FLINT_SERVER_URL", default=f"http://127.0.0.1:{_get('server_port', env='FLINT_SERVER_PORT', default='7433')}")


def get_max_turns() -> int:
    return int(_get("max_turns", env="MAX_TURNS", default="999"))


def get_poll_interval() -> int:
    return int(_get("poll_interval", env="POLL_INTERVAL", default="3"))


def get_dashboard_interval() -> int:
    return int(_get("dashboard_interval", env="DASHBOARD_INTERVAL", default="30"))


def get_requests_channel_id() -> str | None:
    return _get("requests_channel_id", env="REQUESTS_CHANNEL_ID")


def get_command_prefix() -> str:
    return _get("command_prefix", env="COMMAND_PREFIX", default="!")


def get_tasks_channel_id() -> str | None:
    return _get("tasks_channel_id", env="TASKS_CHANNEL_ID")


# --- Eagerly resolved constants (profile-independent) ---

EMBED_LIMIT = 3800

# --- Lazy aliases (resolved after load_profile is called) ---
# These are properties that other modules import at the top level.
# They are set by init_config() after the profile is loaded.

DISCORD_TOKEN: str = ""
FLINT_SERVER_URL: str = ""
MAX_TURNS: int = 999
POLL_INTERVAL: int = 3
DASHBOARD_INTERVAL: int = 30
REQUESTS_CHANNEL_ID: str | None = None
COMMAND_PREFIX: str = "!"
TASKS_CHANNEL_ID: str | None = None
STATE_FILE: Path = _PROJECT_ROOT / "state.json"


def init_config(profile_name: str) -> None:
    """Load profile and populate module-level config vars."""
    load_profile(profile_name)

    global DISCORD_TOKEN, FLINT_SERVER_URL, MAX_TURNS, POLL_INTERVAL
    global DASHBOARD_INTERVAL, REQUESTS_CHANNEL_ID, COMMAND_PREFIX, TASKS_CHANNEL_ID

    DISCORD_TOKEN = get_discord_token()
    FLINT_SERVER_URL = get_server_url()
    MAX_TURNS = get_max_turns()
    POLL_INTERVAL = get_poll_interval()
    DASHBOARD_INTERVAL = get_dashboard_interval()
    REQUESTS_CHANNEL_ID = get_requests_channel_id()
    COMMAND_PREFIX = get_command_prefix()
    TASKS_CHANNEL_ID = get_tasks_channel_id()
    STATE_FILE = _PROJECT_ROOT / f"state-{profile_name}.json"
