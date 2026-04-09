"""Unit tests for orb_discord.dashboard — embed building, update loop, channel discovery."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

import orb_discord.dashboard as dashboard_module
from orb_discord.dashboard import build_dashboard_embed, run_dashboard


class _AsyncIter:
    """Async iterator helper for mocking channel.history()."""

    def __init__(self, items):
        self._items = list(items)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the module-level singleton guard between tests."""
    dashboard_module._dashboard_loop_running = False
    yield
    dashboard_module._dashboard_loop_running = False


@pytest.fixture
def dashboard_channel():
    """A mocked #flint-dashboard TextChannel."""
    ch = MagicMock(spec=discord.TextChannel)
    ch.id = 12345
    ch.name = "flint-dashboard"
    ch.send = AsyncMock()
    ch.history = MagicMock(return_value=_AsyncIter([]))
    return ch


@pytest.fixture
def dashboard_msg():
    """A mocked dashboard Message."""
    msg = AsyncMock(spec=discord.Message)
    msg.edit = AsyncMock()
    msg.id = 67890
    return msg


# ---------------------------------------------------------------------------
# build_dashboard_embed
# ---------------------------------------------------------------------------


class TestBuildDashboardEmbed:
    @patch("orb_discord.dashboard.api_get", new_callable=AsyncMock)
    async def test_embed_builds_correctly_from_active_sessions(self, mock_api_get, bot_state):
        """Given 3 sessions (in-progress, blocked, finished) and 1 pending request,
        embed shows 2 entries, pending request, warning header, red color."""
        mock_api_get.side_effect = [
            {
                "sessions": [
                    {"id": "aaaa1111-0000-0000-0000-000000000000", "status": "in-progress", "title": "Working", "updated": ""},
                    {"id": "bbbb2222-0000-0000-0000-000000000000", "status": "blocked", "title": "Stuck", "updated": ""},
                    {"id": "cccc3333-0000-0000-0000-000000000000", "status": "finished", "title": "Done", "updated": ""},
                ]
            },
            {
                "requests": [
                    {"sessionId": "bbbb2222-0000-0000-0000-000000000000", "type": "blocking", "question": "What should I do?"},
                ]
            },
        ]

        embed = await build_dashboard_embed(bot_state)

        # 2 active entries (finished excluded)
        assert "`aaaa1111`" in embed.description
        assert "`bbbb2222`" in embed.description
        assert "cccc3333" not in embed.description

        # Pending request shown
        assert "Needs response" in embed.description
        assert "What should I do?" in embed.description

        # Warning header
        assert "1 request(s) awaiting response" in embed.author.name

        # Color is red
        assert embed.color.value == 0xFF6B6B

    @patch("orb_discord.dashboard.api_get", new_callable=AsyncMock)
    async def test_no_active_sessions_shows_empty_state(self, mock_api_get, bot_state):
        """All sessions terminal -> 'No active sessions' with grey color."""
        mock_api_get.side_effect = [
            {"sessions": [
                {"id": "s1", "status": "finished"},
                {"id": "s2", "status": "failed"},
                {"id": "s3", "status": "cancelled"},
            ]},
            {"requests": []},
        ]

        embed = await build_dashboard_embed(bot_state)

        assert "No active sessions" in embed.description
        assert embed.color.value == 0x95A5A6

    @patch("orb_discord.dashboard.api_get", new_callable=AsyncMock)
    async def test_session_title_fallback_chain(self, mock_api_get, bot_state):
        """Session with no title uses first 40 chars of prompt."""
        prompt = "implement the authentication flow for the new API endpoint"
        mock_api_get.side_effect = [
            {"sessions": [
                {"id": "fallback0-0000-0000-0000-000000000000", "status": "in-progress", "prompt": prompt, "updated": ""},
            ]},
            {"requests": []},
        ]

        embed = await build_dashboard_embed(bot_state)

        expected = prompt[:40]  # "implement the authentication flow for th"
        assert expected in embed.description


# ---------------------------------------------------------------------------
# run_dashboard — update loop
# ---------------------------------------------------------------------------


class TestRunDashboard:
    @patch("orb_discord.dashboard.asyncio.sleep", new_callable=AsyncMock)
    @patch("orb_discord.dashboard.build_dashboard_embed", new_callable=AsyncMock)
    async def test_embed_skips_update_when_content_unchanged(
        self, mock_build, mock_sleep, bot_state, mock_bot, dashboard_channel, dashboard_msg
    ):
        """Hash-based dedup prevents edit when content unchanged."""
        bot_state.dashboard_channel_id = dashboard_channel.id
        bot_state.dashboard_message = dashboard_msg
        mock_bot.get_channel = MagicMock(return_value=dashboard_channel)

        embed = discord.Embed(title="Flint Sessions", description="same content")
        mock_build.return_value = embed

        # Run loop twice then exit
        mock_bot.is_closed = MagicMock(side_effect=[False, False, True])

        await run_dashboard(bot_state, mock_bot)

        # edit called only once — second iteration skipped due to same hash
        assert dashboard_msg.edit.call_count == 1

    @patch("orb_discord.dashboard.asyncio.sleep", new_callable=AsyncMock)
    @patch("orb_discord.dashboard.build_dashboard_embed", new_callable=AsyncMock)
    async def test_channel_auto_discovery_by_name(
        self, mock_build, mock_sleep, bot_state, mock_bot, dashboard_channel, dashboard_msg
    ):
        """Discovers #flint-dashboard by scanning guild text channels."""
        bot_state.dashboard_channel_id = None

        other_ch = MagicMock(spec=discord.TextChannel)
        other_ch.name = "general"
        other_ch.id = 11111

        guild = MagicMock(spec=discord.Guild)
        guild.text_channels = [other_ch, dashboard_channel]
        mock_bot.guilds = [guild]
        mock_bot.get_channel = MagicMock(return_value=dashboard_channel)

        dashboard_channel.send = AsyncMock(return_value=dashboard_msg)
        dashboard_channel.history = MagicMock(return_value=_AsyncIter([]))

        mock_build.return_value = discord.Embed(title="Flint Sessions")
        mock_bot.is_closed = MagicMock(return_value=True)

        await run_dashboard(bot_state, mock_bot)

        assert bot_state.dashboard_channel_id == dashboard_channel.id

    @patch("orb_discord.dashboard.asyncio.sleep", new_callable=AsyncMock)
    @patch("orb_discord.dashboard.build_dashboard_embed", new_callable=AsyncMock)
    async def test_deleted_message_triggers_repost(
        self, mock_build, mock_sleep, bot_state, mock_bot, dashboard_channel, dashboard_msg
    ):
        """NotFound on edit triggers new message post and state update."""
        bot_state.dashboard_channel_id = dashboard_channel.id
        bot_state.dashboard_message = dashboard_msg
        mock_bot.get_channel = MagicMock(return_value=dashboard_channel)

        embed = discord.Embed(title="Flint Sessions", description="content")
        mock_build.return_value = embed

        # edit raises NotFound
        dashboard_msg.edit = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "Not Found")
        )

        new_msg = AsyncMock(spec=discord.Message)
        new_msg.id = 99999
        dashboard_channel.send = AsyncMock(return_value=new_msg)

        # Run one iteration then exit
        mock_bot.is_closed = MagicMock(side_effect=[False, True])

        await run_dashboard(bot_state, mock_bot)

        dashboard_channel.send.assert_called_once()
        assert bot_state.dashboard_message == new_msg
        bot_state.save.assert_called()


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @patch("orb_discord.dashboard.asyncio.sleep", new_callable=AsyncMock)
    @patch("orb_discord.dashboard.build_dashboard_embed", new_callable=AsyncMock)
    async def test_api_failure_skips_update_retries_next(
        self, mock_build, mock_sleep, bot_state, mock_bot, dashboard_channel, dashboard_msg
    ):
        """API exception caught, update skipped, retries next interval."""
        bot_state.dashboard_channel_id = dashboard_channel.id
        bot_state.dashboard_message = dashboard_msg
        mock_bot.get_channel = MagicMock(return_value=dashboard_channel)

        embed = discord.Embed(title="Flint Sessions", description="recovered")
        mock_build.side_effect = [Exception("API down"), embed]

        mock_bot.is_closed = MagicMock(side_effect=[False, False, True])

        await run_dashboard(bot_state, mock_bot)

        # First iteration: exception caught, no edit.
        # Second iteration: edit succeeds.
        dashboard_msg.edit.assert_called_once()

    async def test_singleton_guard_prevents_duplicate_loops(self, bot_state, mock_bot):
        """Second run_dashboard() call is no-op when guard is set."""
        dashboard_module._dashboard_loop_running = True

        await run_dashboard(bot_state, mock_bot)

        mock_bot.wait_until_ready.assert_not_called()

    async def test_dashboard_command_in_dm(self):
        """!dashboard in DM replies with error message."""
        from orb_discord.cogs.admin import AdminCog

        bot = AsyncMock()
        state = MagicMock()
        cog = AdminCog(bot, state)

        ctx = AsyncMock()
        ctx.guild = None
        ctx.reply = AsyncMock()

        await cog.cmd_dashboard.callback(cog, ctx)

        ctx.reply.assert_called_once_with("Dashboard must be created in a server.")

    async def test_bot_lacks_channel_create_permission(self):
        """Forbidden on create_text_channel propagates (discord.py handles it)."""
        from orb_discord.cogs.admin import AdminCog

        bot = AsyncMock()
        state = MagicMock()
        state.dashboard_message = None
        state.dashboard_channel_id = None
        cog = AdminCog(bot, state)

        ctx = AsyncMock()
        ctx.guild = MagicMock(spec=discord.Guild)
        ctx.guild.default_role = MagicMock()
        ctx.guild.me = MagicMock()
        ctx.guild.create_text_channel = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), "Missing Permissions")
        )

        with pytest.raises(discord.Forbidden):
            await cog.cmd_dashboard.callback(cog, ctx)

    @patch("orb_discord.dashboard.asyncio.sleep", new_callable=AsyncMock)
    @patch("orb_discord.dashboard.build_dashboard_embed", new_callable=AsyncMock)
    async def test_state_lost_after_crash_scans_all_guilds(
        self, mock_build, mock_sleep, bot_state, mock_bot, dashboard_channel, dashboard_msg
    ):
        """run_dashboard() scans all guilds when state is lost."""
        bot_state.dashboard_channel_id = None

        # Channel in second guild
        other_ch = MagicMock(spec=discord.TextChannel)
        other_ch.name = "general"
        other_ch.id = 1

        guild1 = MagicMock(spec=discord.Guild)
        guild1.text_channels = [other_ch]

        guild2 = MagicMock(spec=discord.Guild)
        guild2.text_channels = [dashboard_channel]

        mock_bot.guilds = [guild1, guild2]
        mock_bot.get_channel = MagicMock(return_value=dashboard_channel)

        dashboard_channel.send = AsyncMock(return_value=dashboard_msg)
        dashboard_channel.history = MagicMock(return_value=_AsyncIter([]))

        mock_build.return_value = discord.Embed(title="Flint Sessions")
        mock_bot.is_closed = MagicMock(return_value=True)

        await run_dashboard(bot_state, mock_bot)

        assert bot_state.dashboard_channel_id == dashboard_channel.id
