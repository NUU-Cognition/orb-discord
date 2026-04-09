"""E2E test harness — orchestrates test channel creation and scenario execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

import discord

from .config import DISCORD_BOT_TOKEN, DISCORD_GUILD_ID
from .helpers import check_flint_server_health, rate_limit_delay
from .reporting import Reporter, ScenarioResult


@dataclass
class E2EContext:
    """Shared context passed to every test scenario."""
    client: discord.Client
    guild: discord.Guild
    channel: discord.TextChannel
    bot_member: discord.Member
    reporter: Reporter


async def find_bot_member(guild: discord.Guild, client: discord.Client) -> discord.Member | None:
    """Find the orb-discord bot member in the guild."""
    for member in guild.members:
        if member.bot and member.id != client.user.id:
            return member
    # If not found in cache, try fetching
    async for member in guild.fetch_members(limit=100):
        if member.bot and member.id != client.user.id:
            return member
    return None


async def run_harness(scenarios: list[tuple[str, object]]):
    """Main harness entry point. Creates channel, runs scenarios, reports results."""
    reporter = Reporter()

    print("=" * 60)
    print("  orb-discord E2E Test Harness")
    print("=" * 60)
    print()

    # 1. Verify Flint server is reachable
    print("[startup] Checking Flint server health...")
    if not await check_flint_server_health():
        print("\u274c Flint server is not reachable. Is it running?")
        print(f"   Expected at: {__import__('e2e.config', fromlist=['FLINT_SERVER_URL']).FLINT_SERVER_URL}")
        return reporter
    print("\u2705 Flint server is healthy")

    # 2. Connect test client to Discord
    print("[startup] Connecting test client to Discord...")
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    client = discord.Client(intents=intents)

    ready_event = asyncio.Event()

    @client.event
    async def on_ready():
        ready_event.set()

    # Start client in background
    login_task = asyncio.create_task(client.start(DISCORD_BOT_TOKEN))

    try:
        await asyncio.wait_for(ready_event.wait(), timeout=30)
    except asyncio.TimeoutError:
        print("\u274c Failed to connect to Discord within 30 seconds")
        login_task.cancel()
        return reporter

    print(f"\u2705 Connected as {client.user}")

    try:
        # 3. Find the test guild
        guild = client.get_guild(DISCORD_GUILD_ID)
        if not guild:
            print(f"\u274c Guild {DISCORD_GUILD_ID} not found. Is the bot in the server?")
            return reporter
        print(f"\u2705 Found guild: {guild.name}")

        # 4. Find the bot member (the orb-discord bot we're testing)
        # In E2E mode with a single token, the test client IS the bot
        bot_member = guild.me
        if not bot_member:
            print("\u274c Could not find bot member in guild")
            return reporter
        print(f"\u2705 Bot member: {bot_member.display_name}")

        # 5. Create fresh test channel
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        channel_name = f"e2e-test-{timestamp}"
        print(f"[startup] Creating test channel: #{channel_name}")

        channel = await guild.create_text_channel(
            name=channel_name,
            topic=f"E2E test run {timestamp} — do not delete until reviewed",
        )
        print(f"\u2705 Created channel: #{channel.name}")

        await rate_limit_delay()

        # 6. Build context
        ctx = E2EContext(
            client=client,
            guild=guild,
            channel=channel,
            bot_member=bot_member,
            reporter=reporter,
        )

        # 7. Post start marker
        await channel.send(
            f"**E2E Test Run Started**\n"
            f"Timestamp: `{timestamp}`\n"
            f"Scenarios: {len(scenarios)}\n"
            f"---"
        )

        # 8. Run each scenario
        for name, scenario_fn in scenarios:
            print()
            print(f"[scenario] Running: {name}")
            reporter.start_scenario(name)

            try:
                await scenario_fn(ctx)
                result = reporter.current_result
                if result and not result.failed:
                    reporter.pass_scenario()
                    print(f"\u2705 PASSED: {name}")
                else:
                    print(f"\u274c FAILED: {name}")
                    if result:
                        for failure in result.failures:
                            print(f"   - {failure}")
            except Exception as e:
                reporter.fail_scenario(f"Unhandled exception: {e}")
                print(f"\u274c ERROR: {name}: {e}")

            await rate_limit_delay()

        # 9. Post summary to channel
        summary_lines = ["\n---\n**E2E Test Run Complete**\n"]
        for result in reporter.results:
            status = "\u2705" if not result.failed else "\u274c"
            summary_lines.append(f"{status} **{result.name}**")
            if result.failed:
                for f in result.failures:
                    summary_lines.append(f"  - {f}")
        summary_lines.append(f"\n**{reporter.passed}/{reporter.total} passed**")
        await channel.send("\n".join(summary_lines))

    finally:
        await client.close()
        login_task.cancel()

    return reporter
