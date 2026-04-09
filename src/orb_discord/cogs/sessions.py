"""Session management commands — !sessions, !session, !kill, !requests, !watch, !resume, !stats."""

from __future__ import annotations

from discord.ext import commands
import discord

from ..api import api_get, api_delete, api_post
from .. import config
from ..formatting import (
    STATUS_COLORS, STATUS_EMOJI, relative_time, send_long,
    format_transcript_turn, format_session_stats,
)
from ..sessions import resume_session, poll_session
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
        """Kill a session. If already inactive, force status to cancelled."""
        if not session_id:
            return await ctx.reply(f"Usage: `{config.COMMAND_PREFIX}kill <id>`")

        data = await api_get(f"/orbh/sessions/{session_id}")
        if not data or "session" not in data:
            return await ctx.reply(f"Session `{session_id}` not found.")

        session = data["session"]
        sid = session["id"]
        old_status = session.get("status", "unknown")

        result = await api_delete(f"/orbh/sessions/{sid}")
        # Clean up local tracking regardless
        if sid in self.state.tracked_sessions:
            self.state.tracked_sessions.pop(sid, None)
            self.state.save()

        if result and result.get("status") == "killed":
            await ctx.message.add_reaction("\u2705")
            if old_status in ("finished", "failed", "cancelled"):
                await ctx.reply(f"Session `{sid[:8]}` was **{old_status}** \u2014 forced to cancelled.")
            else:
                await ctx.reply(f"Session `{sid[:8]}` killed.")
        else:
            error = result.get("error", "Unknown error") if result else "Server unreachable"
            await ctx.reply(f"Kill failed: {error}")

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


    @commands.command(name="watch")
    async def cmd_watch(self, ctx: commands.Context, session_id: str = ""):
        """View session transcript."""
        if not session_id:
            return await ctx.reply(f"Usage: `{config.COMMAND_PREFIX}watch <id>`")

        async with ctx.channel.typing():
            session_data, transcript_data = await _fetch_session_and_transcript(session_id)

        if not transcript_data:
            return await ctx.reply(f"Transcript for `{session_id}` not found.")

        session = session_data.get("session", {}) if session_data else {}
        sid = session.get("id", session_id)
        title = session.get("title") or f"Session {sid[:8]}"
        turns = transcript_data.get("turns", [])

        if not turns:
            return await ctx.reply("No transcript data.")

        # Format each turn
        formatted = [format_transcript_turn(t) for t in turns]
        full_text = "\n\n\u2500\u2500\u2500\n\n".join(formatted)

        # Append usage summary
        usage = transcript_data.get("usage") or {}
        in_tok = usage.get("input_tokens") or usage.get("inputTokens", 0)
        out_tok = usage.get("output_tokens") or usage.get("outputTokens", 0)
        if in_tok or out_tok:
            full_text += (
                f"\n\n\u2501\u2501\u2501\n"
                f"**Total:** `{in_tok + out_tok:,}` tokens "
                f"({in_tok:,} in / {out_tok:,} out) \u2022 {len(turns)} turns"
            )

        await send_long(ctx.channel, full_text, session_id=sid, title=f"\U0001f4dc {title}", color=0x6B5CE7)

    @commands.command(name="resume")
    async def cmd_resume(self, ctx: commands.Context, session_id: str = "", *, prompt: str = ""):
        """Resume a session with a new run."""
        if not session_id:
            return await ctx.reply(f"Usage: `{config.COMMAND_PREFIX}resume <id> [prompt]`")

        data = await api_get(f"/orbh/sessions/{session_id}")
        if not data or "session" not in data:
            return await ctx.reply(f"Session `{session_id}` not found.")

        session = data["session"]
        sid = session["id"]
        status = session.get("status")

        if status in ("in-progress", "queued"):
            return await ctx.reply(f"Session `{sid[:8]}` is already **{status}**.")

        if status in ("blocked", "deferred"):
            return await ctx.reply(
                f"Session `{sid[:8]}` is **{status}** \u2014 it has a pending question.\n"
                f"Use `{config.COMMAND_PREFIX}respond {sid[:8]} <answer>` to answer it."
            )

        if not prompt:
            prompt = "Continue working."

        async with ctx.channel.typing():
            await resume_session(self.state, sid, prompt, ctx.channel, ctx.message, self.bot)

    @commands.command(name="respond")
    async def cmd_respond(self, ctx: commands.Context, session_id: str = "", *, text: str = ""):
        """Answer a session's pending question."""
        if not session_id:
            return await ctx.reply(f"Usage: `{config.COMMAND_PREFIX}respond <id> <answer>`")

        data = await api_get(f"/orbh/sessions/{session_id}")
        if not data or "session" not in data:
            return await ctx.reply(f"Session `{session_id}` not found.")

        session = data["session"]
        sid = session["id"]
        status = session.get("status")

        if status not in ("blocked", "deferred"):
            return await ctx.reply(
                f"Session `{sid[:8]}` is **{status}** \u2014 no pending question.\n"
                f"Use `{config.COMMAND_PREFIX}resume {sid[:8]} [prompt]` to start a new run."
            )

        if not text:
            req_data = await api_get(f"/orbh/sessions/{sid}/requests")
            pending = [r for r in (req_data or {}).get("requests", []) if not r.get("answered")]
            if pending:
                q = pending[-1].get("question", "")[:400]
                return await ctx.reply(
                    f"**Pending question:**\n> {q}\n\n"
                    f"Reply with: `{config.COMMAND_PREFIX}respond {sid[:8]} <your answer>`"
                )
            return await ctx.reply(f"No pending question found for `{sid[:8]}`.")

        resp = await api_post(f"/orbh/sessions/{sid}/respond", {"text": text})
        if resp and resp.get("status") == "responded":
            await ctx.message.add_reaction("\u2705")
            if sid not in self.state.tracked_sessions:
                existing_thread = self.state.tracked_sessions.get(sid, {}).get("thread")
                target = existing_thread or ctx.channel
                self.state.tracked_sessions[sid] = {
                    "thread": target, "status_msg": None, "author": ctx.author,
                    "thread_id": target.id, "status_msg_id": None, "author_id": ctx.author.id,
                }
                self.state.save()
                self.bot.loop.create_task(poll_session(self.state, sid))
        else:
            error = resp.get("error", "Failed") if resp else "Server error"
            await ctx.reply(f"Error: {error}")

    @commands.command(name="stats")
    async def cmd_stats(self, ctx: commands.Context, session_id: str = ""):
        """Show session statistics."""
        if not session_id:
            return await ctx.reply(f"Usage: `{config.COMMAND_PREFIX}stats <id>`")

        async with ctx.channel.typing():
            session_data, transcript_data = await _fetch_session_and_transcript(session_id)

        if not session_data or "session" not in session_data:
            return await ctx.reply(f"Session `{session_id}` not found.")

        session = session_data["session"]
        sid = session["id"]
        title = session.get("title") or f"Session {sid[:8]}"
        status = session.get("status", "unknown")

        description = format_session_stats(session, transcript_data)
        color = STATUS_COLORS.get(status, 0x95A5A6)
        embed = discord.Embed(title=f"\U0001f4ca {title}", description=description, color=color)
        embed.set_footer(text=f"session: {sid}")
        await ctx.send(embed=embed)


async def _fetch_session_and_transcript(session_id: str) -> tuple[dict | None, dict | None]:
    """Fetch session detail and transcript in parallel-safe order."""
    session_data = await api_get(f"/orbh/sessions/{session_id}")
    transcript_data = await api_get(f"/orbh/sessions/{session_id}/transcript")
    return session_data, transcript_data


async def setup(bot: commands.Bot):
    # state is set on bot before loading cogs
    await bot.add_cog(SessionsCog(bot, bot.state))  # type: ignore[attr-defined]
