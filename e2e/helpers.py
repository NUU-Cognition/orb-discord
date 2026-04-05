"""Shared helpers for E2E test scenarios."""

from __future__ import annotations

import asyncio
from typing import Callable

import discord
import httpx

from .config import API_DELAY, FLINT_SERVER_URL, RESPONSE_TIMEOUT


async def rate_limit_delay():
    """Sleep to respect Discord API rate limits."""
    await asyncio.sleep(API_DELAY)


async def wait_for_message(
    channel: discord.TextChannel | discord.Thread,
    *,
    check: Callable[[discord.Message], bool] | None = None,
    timeout: float = RESPONSE_TIMEOUT,
    bot_user: discord.User | discord.ClientUser,
) -> discord.Message | None:
    """Wait for a message from the bot in the given channel."""
    end = asyncio.get_event_loop().time() + timeout
    seen_ids: set[int] = set()

    while asyncio.get_event_loop().time() < end:
        await asyncio.sleep(2)
        messages = [m async for m in channel.history(limit=20)]
        for msg in messages:
            if msg.id in seen_ids:
                continue
            seen_ids.add(msg.id)
            if msg.author.id != bot_user.id:
                continue
            if check is None or check(msg):
                return msg
    return None


async def wait_for_thread(
    channel: discord.TextChannel,
    *,
    timeout: float = RESPONSE_TIMEOUT,
    after_message: discord.Message | None = None,
) -> discord.Thread | None:
    """Wait for a thread to be created in the channel."""
    end = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < end:
        await asyncio.sleep(3)
        threads = channel.threads
        for thread in threads:
            if after_message and thread.id == after_message.id:
                continue
            if thread.name.startswith("Session "):
                return thread
        # Also check archived threads
        async for thread in channel.archived_threads(limit=10):
            if thread.name.startswith("Session "):
                return thread
    return None


async def wait_for_embed(
    channel: discord.TextChannel | discord.Thread,
    *,
    bot_user: discord.User | discord.ClientUser,
    contains: str | None = None,
    footer_contains: str | None = None,
    timeout: float = RESPONSE_TIMEOUT,
) -> discord.Message | None:
    """Wait for a message with an embed matching criteria."""
    end = asyncio.get_event_loop().time() + timeout
    seen_ids: set[int] = set()

    while asyncio.get_event_loop().time() < end:
        await asyncio.sleep(2)
        messages = [m async for m in channel.history(limit=30)]
        for msg in messages:
            if msg.id in seen_ids:
                continue
            seen_ids.add(msg.id)
            if msg.author.id != bot_user.id:
                continue
            for embed in msg.embeds:
                desc = embed.description or ""
                footer = embed.footer.text if embed.footer else ""
                if contains and contains not in desc:
                    continue
                if footer_contains and footer_contains not in footer:
                    continue
                return msg
    return None


async def flint_api_get(path: str) -> dict | None:
    """GET request to Flint server."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.get(f"{FLINT_SERVER_URL}{path}")
            return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


async def flint_api_post(path: str, body: dict) -> dict | None:
    """POST request to Flint server."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            r = await http.post(f"{FLINT_SERVER_URL}{path}", json=body)
            return r.json()
    except httpx.HTTPError:
        return None


async def check_flint_server_health() -> bool:
    """Verify the Flint server is reachable."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            r = await http.get(f"{FLINT_SERVER_URL}/orbh/sessions")
            return r.status_code == 200
    except httpx.HTTPError:
        return False


async def wait_for_session_status(
    session_id: str,
    target_statuses: set[str],
    *,
    timeout: float = 120,
) -> dict | None:
    """Poll Flint server until a session reaches one of the target statuses."""
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        data = await flint_api_get(f"/orbh/sessions/{session_id}")
        if data and "session" in data:
            status = data["session"].get("status")
            if status in target_statuses:
                return data["session"]
        await asyncio.sleep(3)
    return None
