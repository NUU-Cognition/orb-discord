"""E2E configuration — loaded from environment variables with validation."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"\u274c Missing required environment variable: {name}")
        sys.exit(1)
    return value


DISCORD_BOT_TOKEN = _require("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = int(_require("DISCORD_GUILD_ID"))
FLINT_SERVER_URL = _require("FLINT_SERVER_URL")

# How long to wait for bot responses (seconds)
RESPONSE_TIMEOUT = int(os.environ.get("E2E_RESPONSE_TIMEOUT", "60"))

# Delay between Discord API calls to respect rate limits (seconds)
API_DELAY = float(os.environ.get("E2E_API_DELAY", "2.0"))
