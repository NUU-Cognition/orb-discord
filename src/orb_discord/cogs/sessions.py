"""Session management commands — !sessions, !session, !kill, !requests."""

from __future__ import annotations

from discord.ext import commands
import discord

from ..api import api_get, api_delete
from .. import config
from ..formatting import STATUS_COLORS, STATUS_EMOJI, relative_time, send_long
from ..state import BotState


class SessionsCog(commands.Cog, name="Sessions"):
    def __init__(self, bot: commands.Bot, state: BotState):
        self.bot = bot
        self.state = state

    @commands.command(name="sessions")
    async def cmd_sessions(self, ctx: commands.Context):
        """List all OrbH sessions."""
        data = await api_get("/orbh/sessions")
        if not data:
            return await ctx.reply("Cannot reach Flint server.")
        sessions = data.get("sessions", [])
        if not sessions:
            return await ctx.reply("No sessions.")
        lines = []
        for s in sessions:
            emoji = STATUS_EMOJI.get(s.get("status", ""), "\u2753")
            sid = s["id"][:8]
            title = s.get("title") or s.get("prompt", "")[:50] or "untitled"
            status = s.get("status", "unknown")
            updated = relative_time(s.get("updated", ""))
            lines.append(f"{emoji} `{sid}` **{title}** [{status}] ({s.get('runtime', '?')}, {updated})")
        await send_long(ctx.channel, "\n".join(lines), title="OrbH Sessions", color=0x6B5CE7)

    @commands.command(name="session")
    async def cmd_session(self, ctx: commands.Context, session_id: str = ""):
        """Show session details."""
        if not session_id:
            return await ctx.reply(f"Usage: `{config.COMMAND_PREFIX}session <id>`")
        data = await api_get(f"/orbh/sessions/{session_id}")
        if not data or "session" not in data:
            return await ctx.reply(f"Session `{session_id}` not found.")
        s = data["session"]
        color = STATUS_COLORS.get(s.get("status", ""), 0x95A5A6)
        emoji = STATUS_EMOJI.get(s.get("status", ""), "\u2753")
        lines = [
            f"**Status:** {emoji} {s.get('status', 'unknown')}",
            f"**Runtime:** {s.get('runtime', '?')}",
            f"**Started:** {relative_time(s.get('started', ''))}",
            f"**Updated:** {relative_time(s.get('updated', ''))}",
        ]
        if s.get("title"):
            lines.insert(0, f"**Title:** {s['title']}")
        for i, run in enumerate(s.get("runs", [])):
            r_emoji = STATUS_EMOJI.get(run.get("status", "?"), "\u2753")
            lines.append(f"  {r_emoji} Run {i + 1}: {run.get('status', '?')} (pid {run.get('pid') or '?'})")
            for req in run.get("requests", []):
                a = "\u2705" if req.get("answered") else "\u23F3"
                lines.append(f"    {a} {req.get('type', '?')}: {req.get('question', '')[:80]}")
        if s.get("prompt"):
            lines.append(f"\n**Prompt:**\n> {s['prompt'][:500]}")
        embed = discord.Embed(description="\n".join(lines), color=color)
        embed.set_footer(text=f"session: {s['id']}")
        await ctx.send(embed=embed)

    @commands.command(name="kill")
    async def cmd_kill(self, ctx: commands.Context, session_id: str = ""):
        """Kill a running session."""
        if not session_id:
            return await ctx.reply(f"Usage: `{config.COMMAND_PREFIX}kill <id>`")
        data = await api_delete(f"/orbh/sessions/{session_id}")
        if not data:
            return await ctx.reply(f"Session `{session_id}` not found or server unreachable.")
        if data.get("status") == "killed":
            await ctx.message.add_reaction("\u2705")
            await ctx.reply(f"Session `{session_id[:8]}` killed.")
        else:
            await ctx.reply(f"Error: {data.get('error', 'Unknown error')}")

    @commands.command(name="requests")
    async def cmd_requests(self, ctx: commands.Context):
        """List pending agent requests."""
        data = await api_get("/orbh/requests")
        if not data:
            return await ctx.reply("Cannot reach Flint server.")
        reqs = data.get("requests", [])
        if not reqs:
            return await ctx.reply("No pending requests.")
        lines = []
        for r in reqs:
            emoji = "\u26D4" if r.get("type") == "blocking" else "\u23F8\uFE0F"
            sid = r.get("sessionId", "?")[:8]
            title = r.get("sessionTitle") or "untitled"
            q = r.get("question", "")[:100]
            link = self.state.get_thread_link(r.get("sessionId", ""))
            loc = f" [thread]({link})" if link else ""
            lines.append(f"{emoji} `{sid}` **{title}**{loc}\n  {q} ({relative_time(r.get('asked', ''))})")
        await send_long(ctx.channel, "\n\n".join(lines), title="Pending Requests", color=0xFFD93D)


async def setup(bot: commands.Bot):
    # state is set on bot before loading cogs
    await bot.add_cog(SessionsCog(bot, bot.state))  # type: ignore[attr-defined]
