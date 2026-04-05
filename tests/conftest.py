"""Shared fixtures for flint-discord tests."""

from __future__ import annotations

import os

# Set required env vars before any flint_discord imports
os.environ.setdefault("DISCORD_TOKEN", "test-token")

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from flint_discord.state import BotState


@pytest.fixture
def bot_state():
    """BotState with empty tracked state."""
    state = BotState()
    state.save = MagicMock()  # prevent disk I/O
    return state


@pytest.fixture
def mock_bot(bot_state):
    """Bot mock with state and user identity."""
    bot = AsyncMock()
    bot.user = MagicMock(id=100)
    bot.state = bot_state
    return bot


@pytest.fixture
def mock_thread():
    """Async-capable Discord thread mock."""
    thread = AsyncMock(spec=discord.Thread)
    thread.id = 123456
    thread.send = AsyncMock(return_value=AsyncMock(id=999))
    return thread


@pytest.fixture
def tracked_session(bot_state, mock_thread):
    """BotState pre-loaded with one tracked session."""
    sid = "test-session-id"
    status_msg = AsyncMock(spec=discord.Message)
    status_msg.edit = AsyncMock()
    bot_state.tracked_sessions[sid] = {
        "thread": mock_thread,
        "status_msg": status_msg,
        "author": AsyncMock(spec=discord.User, id=1, mention="<@1>"),
        "thread_id": mock_thread.id,
        "status_msg_id": status_msg.id,
        "author_id": 1,
    }
    return sid, bot_state
