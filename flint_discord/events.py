"""Unified SSE event watcher — connects to /orbh/events and dispatches."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import discord
import httpx

from .api import api_get
from .config import REQUESTS_CHANNEL_ID
from .formatting import STATUS_EMOJI, STATUS_COLORS
from .sessions import post_question, post_session_result, update_status_card

if TYPE_CHECKING:
    from discord.ext.commands import Bot

    from .state import BotState


async def watch_events(state: BotState, bot: Bot):
    """Connect to the unified /orbh/events SSE stream for typed events."""
    from .config import FLINT_SERVER_URL

    await bot.wait_until_ready()
    requests_channel = bot.get_channel(int(REQUESTS_CHANNEL_ID)) if REQUESTS_CHANNEL_ID else None

    while not bot.is_closed():
        try:
            async with httpx.AsyncClient(timeout=None) as http:
                async with http.stream("GET", f"{FLINT_SERVER_URL}/orbh/events") as resp:
                    buffer = ""
                    async for chunk in resp.aiter_text():
                        buffer += chunk
                        while "\n\n" in buffer:
                            raw_block, buffer = buffer.split("\n\n", 1)
                            event_type = None
                            data_str = None
                            for line in raw_block.split("\n"):
                                if line.startswith("event: "):
                                    event_type = line[7:]
                                elif line.startswith("data: "):
                                    data_str = line[6:]
                                elif line.startswith(":"):
                                    continue
                            if not data_str:
                                continue
                            try:
                                data = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            evt = event_type or data.get("event")
                            if evt:
                                data["event"] = evt
                            await _handle_event(state, data, requests_channel)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError):
            pass
        except Exception as e:
            print(f"SSE error: {e}")
        await asyncio.sleep(5)


async def _handle_event(state: BotState, data: dict, fallback_channel: discord.abc.Messageable | None):
    event = data.get("event")
    sid = data.get("sessionId")
    if not sid:
        return

    if event in ("request.created", "request.pending"):
        rid = data.get("requestId")
        if not rid or rid in state.posted_requests:
            return
        target = state.tracked_sessions.get(sid, {}).get("thread") or fallback_channel
        if not target:
            return
        await post_question(state, sid, {"id": rid, "type": data.get("type"), "question": data.get("question")}, target)

    elif event == "request.answered":
        rid = data.get("requestId")
        if not rid:
            return
        # Find the question message and react to confirm it was answered
        for msg_id, qinfo in state.question_messages.items():
            if qinfo.get("request_id") == rid and qinfo.get("session_id") == sid:
                info = state.tracked_sessions.get(sid)
                target = info.get("thread") if info else None
                if target:
                    try:
                        msg = await target.fetch_message(msg_id)
                        await msg.add_reaction("\u2705")
                    except discord.NotFound:
                        pass
                break

    elif event == "session.status_changed":
        info = state.tracked_sessions.get(sid)
        if not info:
            return
        title = data.get("title") or "Session"
        new_status = data.get("status", "unknown")
        emoji = STATUS_EMOJI.get(new_status, "\u2753")
        color = STATUS_COLORS.get(new_status, 0x95A5A6)
        session_data = {"status": new_status, "title": title}
        await update_status_card(state, sid, session_data, description=f"**{title}**\n\n{emoji} {new_status}", color=color)

    elif event == "session.finished":
        info = state.tracked_sessions.get(sid)
        if not info:
            return
        title = data.get("title") or "Session"
        session_data = {"status": "finished", "title": title}
        await update_status_card(state, sid, session_data, description=f"**{title}**\n\n\u2705 Finished", color=0x2ECC71)
        target = info.get("thread")
        if target:
            full = await api_get(f"/orbh/sessions/{sid}")
            if full and "session" in full:
                await post_session_result(state, sid, full["session"], target)
        # Clean up so poller exits immediately
        state.tracked_sessions.pop(sid, None)
        state.save()

    elif event == "session.failed":
        info = state.tracked_sessions.get(sid)
        if not info:
            return
        title = data.get("title") or "Session"
        session_data = {"status": "failed", "title": title}
        await update_status_card(state, sid, session_data, description=f"**{title}**\n\n\u274c Failed", color=0xFF0000)
        target = info.get("thread")
        if target:
            author = state.get_author(sid)
            embed = discord.Embed(description="Session failed.", color=0xFF0000)
            embed.set_footer(text=f"session: {sid}")
            await target.send(content=author.mention if author else None, embed=embed)
        # Clean up so poller exits immediately
        state.tracked_sessions.pop(sid, None)
        state.save()

    elif event == "session.cancelled":
        info = state.tracked_sessions.get(sid)
        if not info:
            return
        title = data.get("title") or "Session"
        session_data = {"status": "cancelled", "title": title}
        await update_status_card(state, sid, session_data, description=f"**{title}**\n\n\u23f9\ufe0f Cancelled")
        # Clean up so poller exits immediately
        state.tracked_sessions.pop(sid, None)
        state.save()
