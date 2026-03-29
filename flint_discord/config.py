"""Configuration — loaded from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
FLINT_SERVER_PORT = os.environ.get("FLINT_SERVER_PORT", "7433")
FLINT_SERVER_URL = os.environ.get("FLINT_SERVER_URL", f"http://127.0.0.1:{FLINT_SERVER_PORT}")
MAX_TURNS = int(os.environ.get("MAX_TURNS", "999"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "3"))
DASHBOARD_INTERVAL = int(os.environ.get("DASHBOARD_INTERVAL", "30"))
REQUESTS_CHANNEL_ID = os.environ.get("REQUESTS_CHANNEL_ID")
COMMAND_PREFIX = os.environ.get("COMMAND_PREFIX", "!")
TASKS_CHANNEL_ID = os.environ.get("TASKS_CHANNEL_ID")

STATE_FILE = Path(__file__).parent.parent / "state.json"
EMBED_LIMIT = 3800
