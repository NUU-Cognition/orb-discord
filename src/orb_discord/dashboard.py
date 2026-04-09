"""Live-updating dashboard embed in a locked channel."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord

from .api import api_get
from . import config
from .formatting import STATUS_EMOJI, relative_time

if TYPE_CHECKING:
    from discord.ext.commands import Bot

    from .state import BotState


async def build_dashboard_embed(state: BotState) -> discord.Embed:
    data = await api_get("/orbh/sessions")
    req_data = await api_get("/orbh/requests")

    pending_by_session: dict[str, list[dict]] = {}
    if req_data:
        for r in req_data.get("requests", []):
            pending_by_session.setdefault(r.get("sessionId", ""), []).append(r)

    sessions = data.get("sessions", []) if data else []
    active = [s for s in sessions if s.get("status") not in ("finished", "failed", "cancelled")]

    if not active:
        embed = discord.Embed(title="Flint Sessions", description="No active sessions.", color=0x95A5A6)
        embed.set_footer(text=f"Updated {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
        return embed

    lines = []
    needs_response = 0

    for s in active:
        sid = s["id"]
        emoji = STATUS_EMOJI.get(s.get("status", ""), "\u2753")
        title = s.get("title") or s.get("prompt", "")[:40] or "untitled"
        status = s.get("status", "unknown")
        updated = relative_time(s.get("updated", ""))

        link = state.get_thread_link(sid)
        title_text = f"[{title}]({link})" if link else title
        line = f"{emoji} `{sid[:8]}` **{title_text}** \u2014 {status} ({updated})"

        pending = pending_by_session.get(sid, [])
        if pending:
            needs_response += len(pending)
            for p in pending:
                p_emoji = "\u26D4" if p.get("type") == "blocking" else "\u23F8\uFE0F"
                line += f"\n  {p_emoji} **Needs response:** {p.get('question', '')[:80]}"
        lines.append(line)

    color = 0xFF6B6B if needs_response > 0 else 0xFFA500
    embed = discord.Embed(title="Flint Sessions", description="\n\n".join(lines), color=color)
    if needs_response > 0:
        embed.set_author(name=f"\u26A0\uFE0F {needs_response} request(s) awaiting response")
    embed.set_footer(text=f"Updated {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
    return embed


_dashboard_loop_running = False


async def run_dashboard(state: BotState, bot: Bot):
    global _dashboard_loop_running
    if _dashboard_loop_running:
        return
    _dashboard_loop_running = True

    await bot.wait_until_ready()

    # Auto-discover #flint-dashboard channel if state was lost
    if not state.dashboard_channel_id:
        for guild in bot.guilds:
            for ch in guild.text_channels:
                if ch.name == "flint-dashboard":
                    state.dashboard_channel_id = ch.id
                    print(f"Auto-discovered dashboard channel #{ch.name}")
                    break
            if state.dashboard_channel_id:
                break

    if not state.dashboard_channel_id:
        _dashboard_loop_running = False
        return

    channel = bot.get_channel(state.dashboard_channel_id)
    if not channel or not isinstance(channel, discord.TextChannel):
        print(f"Dashboard channel {state.dashboard_channel_id} not found.")
        _dashboard_loop_running = False
        return

    # Find existing dashboard message or create one
    if not state.dashboard_message:
        async for msg in channel.history(limit=20):
            if msg.author == bot.user and msg.embeds and msg.embeds[0].title == "Flint Sessions":
                state.dashboard_message = msg
                print(f"Found existing dashboard message in #{channel.name}")
                break
        if not state.dashboard_message:
            embed = await build_dashboard_embed(state)
            state.dashboard_message = await channel.send(embed=embed)
            print(f"Created new dashboard message in #{channel.name}")
        state.save()

    _last_hash = None
    while not bot.is_closed():
        try:
            embed = await build_dashboard_embed(state)
            embed_hash = hash(str(embed.to_dict()))
            if embed_hash != _last_hash:
                await state.dashboard_message.edit(embed=embed)
                _last_hash = embed_hash
        except discord.NotFound:
            embed = await build_dashboard_embed(state)
            state.dashboard_message = await channel.send(embed=embed)
            _last_hash = hash(str(embed.to_dict()))
            state.save()
        except Exception as e:
            print(f"Dashboard error: {e}")
        await asyncio.sleep(config.DASHBOARD_INTERVAL)
