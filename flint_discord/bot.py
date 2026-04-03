"""Bot setup, on_ready, and on_message routing."""

from __future__ import annotations

import discord
import httpx
from discord.ext import commands

from .api import api_get, api_post
from .config import COMMAND_PREFIX, DISCORD_TOKEN
from .dashboard import run_dashboard
from .events import watch_events
from .formatting import extract_session_id
from .sessions import launch_session, poll_session, resume_session
from .state import BotState


def create_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)
    bot.state = BotState()  # type: ignore[attr-defined]
    return bot


async def setup_bot(bot: commands.Bot):
    """Load cogs and register event handlers."""
    state: BotState = bot.state  # type: ignore[attr-defined]

    await bot.load_extension("flint_discord.cogs.sessions")
    await bot.load_extension("flint_discord.cogs.admin")
    await bot.load_extension("flint_discord.cogs.tasks")

    @bot.event
    async def on_ready():
        print(f"Bot connected as {bot.user}")
        from .config import FLINT_SERVER_URL
        print(f"Flint server: {FLINT_SERVER_URL}")
        await bot.tree.sync()
        print("Slash commands synced")
        await state.rehydrate(bot)
        # Restart poll loops for rehydrated sessions
        for sid in list(state.tracked_sessions):
            bot.loop.create_task(poll_session(state, sid))
        bot.loop.create_task(watch_events(state, bot))
        bot.loop.create_task(run_dashboard(state, bot))

    @bot.event
    async def on_message(message: discord.Message):
        if message.author == bot.user:
            return
        if message.content.startswith(COMMAND_PREFIX):
            await bot.process_commands(message)
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mentioned = bot.user in message.mentions if bot.user else False

        is_reply = False
        ref_msg: discord.Message | None = None
        if message.reference and message.reference.message_id:
            try:
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
                if ref_msg.author == bot.user:
                    is_reply = True
            except discord.NotFound:
                pass

        is_in_thread = False
        thread_sid: str | None = None
        if isinstance(message.channel, discord.Thread):
            for sid, info in state.tracked_sessions.items():
                if info.get("thread") and info["thread"].id == message.channel.id:
                    is_in_thread = True
                    thread_sid = sid
                    break

        if not is_dm and not is_mentioned and not is_reply and not is_in_thread:
            return

        prompt = message.content
        if bot.user:
            prompt = prompt.replace(f"<@{bot.user.id}>", "").strip()
        if message.attachments:
            attachment_lines = "\n".join(
                f"- {a.filename}: {a.url}" for a in message.attachments
            )
            prompt = (prompt + "\n\nAttachments:\n" + attachment_lines).strip()
        if not prompt:
            return await message.reply("Send me a message and I'll pass it to Claude!")

        # Reply to a question -> answer the request
        if is_reply and ref_msg and ref_msg.id in state.question_messages:
            qinfo = state.question_messages[ref_msg.id]
            async with message.channel.typing():
                await _respond_to_request(state, qinfo["session_id"], prompt, message)
            return

        # Determine the session ID from reply or thread context
        target_sid: str | None = None
        if is_reply and ref_msg:
            target_sid = extract_session_id(ref_msg)
        if not target_sid and is_in_thread and thread_sid:
            target_sid = thread_sid

        if target_sid:
            # Check if this session is blocked/deferred — route to /respond instead of /resume
            async with message.channel.typing():
                session_data = await api_get(f"/orbh/sessions/{target_sid}")
                if session_data and "session" in session_data:
                    status = session_data["session"].get("status")
                    if status in ("blocked", "deferred"):
                        await _respond_to_request(state, target_sid, prompt, message)
                        return
                await resume_session(state, target_sid, prompt, message.channel, message, bot)
            return

        # New session
        async with message.channel.typing():
            try:
                await launch_session(state, prompt, message.channel, message, bot)
            except httpx.ConnectError:
                await message.reply("Cannot connect to Flint server. Is it running?\n`flint server start`")
            except Exception as e:
                await message.reply(f"Error: {e}")


async def _respond_to_request(state: BotState, sid: str, text: str, message: discord.Message):
    """Answer a pending request on a blocked/deferred session."""
    resp = await api_post(f"/orbh/sessions/{sid}/respond", {"text": text})
    if resp and resp.get("status") == "responded":
        await message.add_reaction("\u2705")
    else:
        error = resp.get("error", "Failed to respond") if resp else "Server error"
        await message.reply(f"Error: {error}")


def run():
    bot = create_bot()

    @bot.event
    async def setup_hook():
        await setup_bot(bot)

    bot.run(DISCORD_TOKEN)
