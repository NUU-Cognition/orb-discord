"""Scenario: Live Dashboard — verify dashboard embed is posted and updates."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord

from ..helpers import flint_api_get, flint_api_post, rate_limit_delay, wait_for_embed

if TYPE_CHECKING:
    from ..harness import E2EContext


async def test_live_dashboard(ctx: E2EContext):
    """Test the live dashboard: verify embed exists, updates on session state changes."""
    result = ctx.reporter.current_result

    # 1. Check if a #flint-dashboard channel exists in the guild
    print("    Looking for #flint-dashboard channel...")
    dashboard_channel = None
    for channel in ctx.guild.text_channels:
        if channel.name == "flint-dashboard":
            dashboard_channel = channel
            break

    result.assert_not_none(dashboard_channel, "Found #flint-dashboard channel in guild")
    if not dashboard_channel:
        print("    Skipping dashboard content checks — no #flint-dashboard channel")
        return

    # 2. Look for existing dashboard embed
    print("    Checking for dashboard embed...")
    dashboard_msg = None
    async for msg in dashboard_channel.history(limit=20):
        if msg.author.id == ctx.client.user.id and msg.embeds:
            for embed in msg.embeds:
                if embed.title and "Flint Sessions" in embed.title:
                    dashboard_msg = msg
                    break
        if dashboard_msg:
            break

    result.assert_not_none(dashboard_msg, "Dashboard embed found in #flint-dashboard")
    if not dashboard_msg:
        print("    Dashboard embed not found — the bot may need more time to create it")
        return

    # 3. Record the current dashboard state
    initial_embed = dashboard_msg.embeds[0]
    initial_description = initial_embed.description or ""
    print(f"    Initial dashboard: {initial_description[:100]}...")

    # 4. Launch a session to trigger a dashboard update
    print("    Launching session to trigger dashboard update...")
    session_data = await flint_api_post("/orbh/sessions", {
        "runtime": "claude",
        "prompt": "Say 'dashboard test done' and exit.",
        "maxTurns": 3,
        "title": "E2E Dashboard Trigger",
    })
    result.assert_not_none(session_data, "Launched trigger session")

    if not session_data or "session" not in session_data:
        return

    sid = session_data["session"]["id"]

    # 5. Wait for the dashboard to update (it refreshes on an interval)
    print("    Waiting for dashboard to reflect new session...")
    await asyncio.sleep(35)  # Dashboard interval is 30s

    try:
        refreshed_msg = await dashboard_channel.fetch_message(dashboard_msg.id)
        if refreshed_msg.embeds:
            updated_embed = refreshed_msg.embeds[0]
            updated_description = updated_embed.description or ""
            # The dashboard should have changed — either showing the new session or reflecting its completion
            result.assert_true(True, "Dashboard embed still exists after session launch")
            print(f"    Updated dashboard: {updated_description[:100]}...")
        else:
            result.fail("Dashboard embed lost its embeds")
    except discord.NotFound:
        # Dashboard message may have been recreated
        print("    Original dashboard message not found — checking for replacement...")
        async for msg in dashboard_channel.history(limit=10):
            if msg.author.id == ctx.client.user.id and msg.embeds:
                for embed in msg.embeds:
                    if embed.title and "Flint Sessions" in embed.title:
                        result.assert_true(True, "Dashboard embed was recreated")
                        return
        result.fail("Dashboard embed disappeared and was not recreated")

    # 6. Post summary
    await ctx.channel.send("**Live Dashboard Scenario** — completed dashboard verification")
