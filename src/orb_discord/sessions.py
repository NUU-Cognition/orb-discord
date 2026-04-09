"""Session lifecycle — launch, resume, poll, post results, question handling."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord

from .api import api_get, api_post
from . import config
from .formatting import STATUS_COLORS, STATUS_EMOJI, extract_discord_images, send_long

if TYPE_CHECKING:
    from .state import BotState

DISCORD_SHARD_INSTRUCTION = "\n\nAfter initialization, also read Shards/Discord/init-disc.md and follow its conventions."


async def update_status_card(state: BotState, sid: str, session: dict, *, description: str | None = None, color: int | None = None):
    info = state.tracked_sessions.get(sid)
    if not info or not info.get("status_msg"):
        return
    status = session.get("status", "unknown")
    title = session.get("title") or None
    emoji = STATUS_EMOJI.get(status, "\u2753")
    card_color = color or STATUS_COLORS.get(status, 0x95A5A6)
    if description is None:
        description = f"**{title}**\n\n{emoji} {status}" if title else f"{emoji} {status}"
    embed = discord.Embed(description=description, color=card_color)
    embed.set_footer(text=f"session: {sid}")
    try:
        await info["status_msg"].edit(embed=embed)
    except discord.NotFound:
        info["status_msg"] = None


async def delete_status_card(state: BotState, sid: str):
    """Delete the status card message for a session."""
    info = state.tracked_sessions.get(sid)
    if not info or not info.get("status_msg"):
        return
    try:
        await info["status_msg"].delete()
    except discord.NotFound:
        pass
    info["status_msg"] = None


async def launch_session(state: BotState, prompt: str, channel: discord.abc.Messageable, trigger: discord.Message, bot):
    prompt = prompt + DISCORD_SHARD_INSTRUCTION
    data = await api_post("/orbh/sessions", {"runtime": "claude", "prompt": prompt, "maxTurns": config.MAX_TURNS})
    if not data or "session" not in data:
        error = data.get("error", "Unknown error") if data else "Cannot reach server"
        return await trigger.reply(f"Failed to launch: {error}")

    session = data["session"]
    sid = session["id"]

    thread = None
    if isinstance(channel, discord.TextChannel):
        thread = await trigger.create_thread(name=f"Session {sid[:8]}")
    target = thread or channel

    embed = discord.Embed(description=f"Session launched. Working...\n\n> {prompt[:300]}", color=0xFFA500)
    embed.set_footer(text=f"session: {sid}")
    status_msg = await target.send(embed=embed)

    state.tracked_sessions[sid] = {
        "thread": target, "status_msg": status_msg, "author": trigger.author,
        "thread_id": target.id, "status_msg_id": status_msg.id, "author_id": trigger.author.id,
    }
    state.save()
    bot.loop.create_task(poll_session(state, sid))


async def resume_session(state: BotState, sid: str, prompt: str, channel: discord.abc.Messageable, trigger: discord.Message, bot):
    prompt = prompt + DISCORD_SHARD_INSTRUCTION
    data = await api_post(f"/orbh/sessions/{sid}/resume", {"prompt": prompt})
    if not data or "session" not in data:
        error = data.get("error", "Unknown error") if data else "Cannot reach server"
        return await trigger.reply(f"Failed to resume: {error}")

    target = state.tracked_sessions.get(sid, {}).get("thread", channel)

    embed = discord.Embed(description=f"Resuming session...\n\n> {prompt[:300]}", color=0xFFA500)
    embed.set_footer(text=f"session: {sid}")
    status_msg = await target.send(embed=embed)

    state.tracked_sessions.setdefault(sid, {})
    state.tracked_sessions[sid]["thread"] = target
    state.tracked_sessions[sid]["status_msg"] = status_msg
    state.tracked_sessions[sid]["thread_id"] = target.id
    state.tracked_sessions[sid]["status_msg_id"] = status_msg.id
    if "author" not in state.tracked_sessions[sid]:
        state.tracked_sessions[sid]["author"] = trigger.author
        state.tracked_sessions[sid]["author_id"] = trigger.author.id
    state.save()
    bot.loop.create_task(poll_session(state, sid))


async def poll_session(state: BotState, sid: str):
    info = state.tracked_sessions.get(sid)
    if not info:
        return
    target = info.get("thread")
    if not target:
        state.tracked_sessions.pop(sid, None)
        state.save()
        return

    last_title = None

    while sid in state.tracked_sessions:
        await asyncio.sleep(config.POLL_INTERVAL)

        data = await api_get(f"/orbh/sessions/{sid}")
        if not data or "session" not in data:
            continue

        session = data["session"]
        status = session.get("status")
        title = session.get("title") or None

        if title and title != last_title:
            last_title = title
            await update_status_card(state, sid, session, description=f"**{title}**\n\n\u2699\uFE0F Working...")

        if status in ("blocked", "deferred"):
            await surface_pending_requests(state, sid, target)
            continue

        if status == "finished":
            await delete_status_card(state, sid)
            await post_session_result(state, sid, session, target)
            break

        if status == "failed":
            await delete_status_card(state, sid)
            author = state.get_author(sid)
            embed = discord.Embed(description="Session failed.", color=0xFF0000)
            embed.set_footer(text=f"session: {sid}")
            await target.send(content=author.mention if author else None, embed=embed)
            break

        if status == "cancelled":
            await delete_status_card(state, sid)
            break

    state.tracked_sessions.pop(sid, None)
    state.save()


async def surface_pending_requests(state: BotState, sid: str, channel: discord.abc.Messageable):
    data = await api_get(f"/orbh/sessions/{sid}/requests")
    if not data:
        return
    for req in data.get("requests", []):
        rid = req.get("id")
        if not rid or rid in state.posted_requests or req.get("answered"):
            continue
        await post_question(state, sid, req, channel)


async def post_question(state: BotState, sid: str, req: dict, channel: discord.abc.Messageable):
    rid = req.get("id", "unknown")
    if rid in state.posted_requests:
        return
    state.posted_requests.add(rid)

    question = req.get("question", "No question text")
    question, image_files = extract_discord_images(question)
    req_type = req.get("type", "blocking")
    label = "Blocking Question" if req_type == "blocking" else "Deferred Question"
    color = 0xFF6B6B if req_type == "blocking" else 0xFFD93D
    author = state.get_author(sid)

    embed = discord.Embed(title=label, description=question, color=color)
    embed.set_footer(text=f"session: {sid} | request: {rid}")
    embed.add_field(name="Reply to answer", value="Reply to this message with your response.", inline=False)

    msg = await channel.send(content=author.mention if author else None, embed=embed)
    if image_files:
        await channel.send(files=image_files)
    state.question_messages[msg.id] = {"session_id": sid, "request_id": rid, "type": req_type}
    state.save()


def extract_result(session: dict) -> str | None:
    runs = session.get("runs", [])
    if runs:
        return runs[-1].get("result") or None
    return None


def extract_last_agent_text(turns: list[dict]) -> str | None:
    for turn in reversed(turns):
        if turn.get("role") != "agent":
            continue
        parts = [c["text"] for c in turn.get("content", []) if c.get("type") == "text"]
        if parts:
            return "\n\n".join(parts)
    return None


async def post_session_result(state: BotState, sid: str, session: dict, channel: discord.abc.Messageable):
    if sid in state.posted_results:
        return
    state.posted_results.add(sid)
    result = extract_result(session)
    if not result:
        transcript = await api_get(f"/orbh/sessions/{sid}/transcript")
        if transcript:
            result = extract_last_agent_text(transcript.get("turns", []))
    if not result:
        result = "Session completed but no result was returned."
    title = session.get("title") or "Session Complete"
    author = state.get_author(sid)
    result, image_files = extract_discord_images(result)
    await send_long(channel, result, session_id=sid, title=title, color=0x2ECC71, mention=author)
    if image_files:
        await channel.send(files=image_files)
