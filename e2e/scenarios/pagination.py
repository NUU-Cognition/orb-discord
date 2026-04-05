"""Scenario: Pagination — trigger long output, verify PaginatorView with navigation buttons."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord

from ..helpers import (
    flint_api_get,
    flint_api_post,
    rate_limit_delay,
    wait_for_session_status,
)

if TYPE_CHECKING:
    from ..harness import E2EContext


async def test_pagination(ctx: E2EContext):
    """Test pagination: trigger long output session, verify PaginatorView appears with buttons."""
    result = ctx.reporter.current_result

    # 1. Send a message to the bot that should produce long output
    print("    Triggering session with long output...")
    bot_mention = f"<@{ctx.bot_member.id}>"
    trigger_msg = await ctx.channel.send(
        f"{bot_mention} Write a very long response. "
        f"Generate at least 5000 characters of text by listing numbers 1 through 200 "
        f"with a short sentence for each number. Do not use any tools."
    )

    await rate_limit_delay()

    # 2. Wait for the bot to create a thread
    print("    Waiting for thread creation...")
    thread = None
    for _ in range(20):
        await asyncio.sleep(3)
        try:
            refreshed = await ctx.channel.fetch_message(trigger_msg.id)
            if refreshed.thread:
                thread = refreshed.thread
                break
        except Exception:
            pass
        for t in ctx.channel.threads:
            if t.created_at and t.name.startswith("Session "):
                thread = t
                break
        if thread:
            break

    result.assert_not_none(thread, "Thread created for long output session")
    if not thread:
        result.fail("No thread — cannot verify pagination")
        return

    # 3. Extract session ID from the thread
    print("    Extracting session ID from thread...")
    thread_sid = None
    messages = [m async for m in thread.history(limit=10)]
    for msg in messages:
        if msg.author.id == ctx.client.user.id and msg.embeds:
            import re
            for embed in msg.embeds:
                if embed.footer and embed.footer.text:
                    m = re.search(r"session: ([0-9a-f\-]{36})", embed.footer.text)
                    if m:
                        thread_sid = m.group(1)
                        break
        if thread_sid:
            break

    result.assert_not_none(thread_sid, "Session ID found in thread")
    if not thread_sid:
        return

    # 4. Wait for the session to finish
    print("    Waiting for session to complete...")
    final = await wait_for_session_status(
        thread_sid,
        {"finished", "failed", "cancelled"},
        timeout=180,
    )
    result.assert_not_none(final, "Session completed")

    await asyncio.sleep(5)  # Let the bot post results

    # 5. Check for a paginated message (message with a View containing buttons)
    print("    Checking for PaginatorView (message with navigation buttons)...")
    paginator_found = False
    messages = [m async for m in thread.history(limit=30)]
    for msg in messages:
        if msg.author.id != ctx.client.user.id:
            continue
        # Check for components (buttons) — this indicates a View is attached
        if msg.components:
            for action_row in msg.components:
                buttons = [c for c in action_row.children if isinstance(c, discord.Button)]
                if len(buttons) >= 2:
                    paginator_found = True
                    button_labels = [b.label for b in buttons]
                    print(f"    Found paginator with buttons: {button_labels}")
                    break
        # Also check embeds with "Page X/Y" footer as indicator
        if not paginator_found and msg.embeds:
            for embed in msg.embeds:
                if embed.footer and embed.footer.text and "Page " in embed.footer.text:
                    paginator_found = True
                    print(f"    Found paginated embed: {embed.footer.text}")
                    break
        if paginator_found:
            break

    result.assert_true(
        paginator_found,
        "PaginatorView with navigation buttons found in thread",
    )

    await ctx.channel.send(
        f"**Pagination Scenario** — {'PaginatorView found' if paginator_found else 'No paginator detected'}"
    )
