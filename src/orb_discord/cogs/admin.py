"""Admin commands — !health, !dashboard, !help."""

from __future__ import annotations

import discord
from discord.ext import commands

from ..api import api_get
from .. import config
from ..dashboard import build_dashboard_embed, run_dashboard
from ..state import BotState


class AdminCog(commands.Cog, name="Admin"):
    def __init__(self, bot: commands.Bot, state: BotState):
        self.bot = bot
        self.state = state

    @commands.command(name="health")
    async def cmd_health(self, ctx: commands.Context):
        """Server health check."""
        data = await api_get("/health")
        if not data:
            return await ctx.send(embed=discord.Embed(description="Cannot reach Flint server.", color=0xFF0000))
        lines = [
            f"**Status:** {data.get('status', '?')}",
            f"**Runtimes:** {data.get('runtimes', 0)}",
            f"**Workspace:** `{data.get('flintPath') or 'none'}`",
            f"**Chrome:** {'yes' if data.get('chrome') else 'no'}",
            f"**Tracked sessions:** {len(self.state.tracked_sessions)}",
        ]
        embed = discord.Embed(description="\n".join(lines), color=0x2ECC71 if data.get("status") == "ok" else 0xFF0000)
        embed.title = "Flint Server"
        await ctx.send(embed=embed)

    @commands.command(name="dashboard")
    async def cmd_dashboard(self, ctx: commands.Context):
        """Create a locked #flint-dashboard channel with a live-updating status embed."""
        if not ctx.guild:
            return await ctx.reply("Dashboard must be created in a server.")

        # Delete old dashboard message
        if self.state.dashboard_message:
            try:
                await self.state.dashboard_message.delete()
            except discord.NotFound:
                pass

        # Delete old dashboard channel
        if self.state.dashboard_channel_id:
            old_channel = ctx.guild.get_channel(self.state.dashboard_channel_id)
            if old_channel:
                try:
                    await old_channel.delete(reason="Flint dashboard recreated")
                except discord.Forbidden:
                    pass

        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(
                send_messages=False, add_reactions=False,
                create_public_threads=False, create_private_threads=False,
            ),
            ctx.guild.me: discord.PermissionOverwrite(
                send_messages=True, manage_messages=True, embed_links=True,
            ),
        }

        channel = await ctx.guild.create_text_channel(
            "flint-dashboard", overwrites=overwrites,
            topic="Live Flint session dashboard. Auto-updated by the bot.",
            reason="Flint dashboard created",
        )

        self.state.dashboard_channel_id = channel.id
        embed = await build_dashboard_embed(self.state)
        self.state.dashboard_message = await channel.send(embed=embed)
        await self.state.dashboard_message.pin()
        self.state.save()

        try:
            await ctx.reply(f"Dashboard created: {channel.mention}")
        except discord.NotFound:
            await channel.send("Dashboard created (moved from deleted channel).")

        self.bot.loop.create_task(run_dashboard(self.state, self.bot))

    @commands.command(name="help")
    async def cmd_help(self, ctx: commands.Context):
        """Show available commands."""
        lines = [
            f"`{config.COMMAND_PREFIX}sessions` \u2014 List all OrbH sessions",
            f"`{config.COMMAND_PREFIX}session <id>` \u2014 Show session details",
            f"`{config.COMMAND_PREFIX}watch <id>` \u2014 View session transcript",
            f"`{config.COMMAND_PREFIX}stats <id>` \u2014 Session statistics (tokens, tools, turns)",
            f"`{config.COMMAND_PREFIX}resume <id> [prompt]` \u2014 Resume a session with a new run",
            f"`{config.COMMAND_PREFIX}respond <id> <answer>` \u2014 Answer a session's pending question",
            f"`{config.COMMAND_PREFIX}kill <id>` \u2014 Kill a running session",
            f"`{config.COMMAND_PREFIX}requests` \u2014 List pending agent requests",
            f"`{config.COMMAND_PREFIX}health` \u2014 Server health check",
            f"`{config.COMMAND_PREFIX}dashboard` \u2014 Create live dashboard channel",
            "",
            "**Tasks (slash commands):**",
            "`/task list [status]` \u2014 List tasks grouped by status",
            "`/task view <number>` \u2014 View task details with status buttons",
            "`/task create` \u2014 Create a new task via modal",
            "`/task launch <number>` \u2014 Launch OrbH session for a task",
            "",
            "**Sessions:**",
            "Mention or DM to start a new session.",
            "Reply to a session embed to resume it.",
            "Reply to a question embed to answer it.",
            "Type in a session thread to continue.",
        ]
        embed = discord.Embed(description="\n".join(lines), color=0x6B5CE7)
        embed.title = "Flint Bot"
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot, bot.state))  # type: ignore[attr-defined]
