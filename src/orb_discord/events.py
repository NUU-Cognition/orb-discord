"""Unified SSE event watcher — connects to /events/stream and dispatches."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import discord
import httpx

from .api import api_get
from . import config
from .formatting import STATUS_EMOJI, STATUS_COLORS
from .sessions import complete_session, post_question, update_status_card

if TYPE_CHECKING:
    from discord.ext.commands import Bot

    from .state import BotState


async def watch_events(state: BotState, bot: Bot):
    """Connect to the unified /events/stream SSE stream for typed events."""
    await bot.wait_until_ready()
    requests_channel = bot.get_channel(int(config.REQUESTS_CHANNEL_ID)) if config.REQUESTS_CHANNEL_ID else None

    while not bot.is_closed():
        try:
            async with httpx.AsyncClient(timeout=None) as http:
                async with http.stream("GET", f"{config.FLINT_SERVER_URL}/events/stream?channels=orbh") as resp:
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

    if event == "request.created":
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
        session = data.get("session") or {}
        title = session.get("title") or "Session"
        new_status = data.get("to", "unknown")
        emoji = STATUS_EMOJI.get(new_status, "\u2753")
        color = STATUS_COLORS.get(new_status, 0x95A5A6)
        session_data = {"status": new_status, "title": title}
        await update_status_card(state, sid, session_data, description=f"**{title}**\n\n{emoji} {new_status}", color=color)

    elif event == "session.finished":
        full = await api_get(f"/orbh/sessions/{sid}")
        session = full["session"] if full and "session" in full else {"status": "finished"}
        await complete_session(state, sid, session)

    elif event == "session.failed":
        await complete_session(state, sid, {"status": "failed"})

    elif event == "session.cancelled":
        await complete_session(state, sid, {"status": "cancelled"})
