"""Scenario: Image Extraction — trigger discord-image fences, verify file uploads."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ..helpers import (
    flint_api_get,
    flint_api_post,
    rate_limit_delay,
    wait_for_session_status,
)

if TYPE_CHECKING:
    from ..harness import E2EContext


async def test_image_extraction(ctx: E2EContext):
    """Test image extraction: session output with discord-image fences -> bot uploads files."""
    result = ctx.reporter.current_result

    # 1. Launch a session that produces discord-image fences in output
    print("    Launching session that produces discord-image output...")
    bot_mention = f"<@{ctx.bot_member.id}>"
    trigger_msg = await ctx.channel.send(
        f"{bot_mention} Create a small PNG image file at /tmp/e2e-test-image.png "
        f"(use Python to generate a 10x10 red PNG), then output it using the "
        f"discord-image fence format: ```discord-image-1\\n/tmp/e2e-test-image.png\\n```"
    )

    await rate_limit_delay()

    # 2. Wait for thread creation
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
            if t.name.startswith("Session "):
                thread = t
                break
        if thread:
            break

    result.assert_not_none(thread, "Thread created for image extraction session")
    if not thread:
        result.fail("No thread — cannot verify image extraction")
        return

    # 3. Extract session ID
    print("    Extracting session ID...")
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

    # 4. Wait for session to finish
    print("    Waiting for session to complete...")
    final = await wait_for_session_status(
        thread_sid,
        {"finished", "failed", "cancelled"},
        timeout=180,
    )
    result.assert_not_none(final, "Session completed")

    await asyncio.sleep(5)  # Let the bot post results

    # 5. Check for file attachments in the thread
    print("    Checking for file attachments in thread...")
    attachment_found = False
    messages = [m async for m in thread.history(limit=30)]
    for msg in messages:
        if msg.author.id != ctx.client.user.id:
            continue
        if msg.attachments:
            for att in msg.attachments:
                print(f"    Found attachment: {att.filename} ({att.size} bytes)")
                attachment_found = True
                break
        if attachment_found:
            break

    result.assert_true(
        attachment_found,
        "Bot uploaded image file attachment from discord-image fence",
    )

    await ctx.channel.send(
        f"**Image Extraction Scenario** — "
        f"{'File attachment found' if attachment_found else 'No file attachment detected'}"
    )
