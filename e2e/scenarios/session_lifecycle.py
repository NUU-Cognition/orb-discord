"""Scenario: Session Lifecycle — send message, verify thread, embeds, status transitions, result."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ..helpers import (
    flint_api_get,
    flint_api_post,
    rate_limit_delay,
    wait_for_embed,
    wait_for_session_status,
)

if TYPE_CHECKING:
    from ..harness import E2EContext


async def test_session_lifecycle(ctx: E2EContext):
    """Test the full session lifecycle: launch -> thread -> status embeds -> result."""
    result = ctx.reporter.current_result

    # 1. Launch a session via the Flint server API
    print("    Launching a test session via Flint server API...")
    session_data = await flint_api_post("/orbh/sessions", {
        "runtime": "claude",
        "prompt": "Say exactly: 'E2E lifecycle test complete'. Do nothing else.",
        "maxTurns": 5,
        "title": "E2E Lifecycle Test",
    })
    result.assert_not_none(session_data, "Session launch API returned data")
    if not session_data or "session" not in session_data:
        result.fail("Could not launch session — aborting scenario")
        return

    session = session_data["session"]
    sid = session["id"]
    print(f"    Session ID: {sid}")

    await rate_limit_delay()

    # 2. Notify the bot by mentioning it with a prompt in the test channel
    # The bot watches for mentions and creates a thread
    bot_mention = f"<@{ctx.bot_member.id}>"
    trigger_msg = await ctx.channel.send(f"{bot_mention} say exactly: 'E2E lifecycle verification'")
    result.assert_not_none(trigger_msg, "Trigger message sent to channel")

    await rate_limit_delay()

    # 3. Wait for the bot to create a thread from the trigger message
    print("    Waiting for bot thread creation...")
    thread = None
    for _ in range(20):
        await asyncio.sleep(3)
        # Check if a thread was created on our trigger message
        try:
            refreshed = await ctx.channel.fetch_message(trigger_msg.id)
            if refreshed.thread:
                thread = refreshed.thread
                break
        except Exception:
            pass
        # Also check channel threads
        for t in ctx.channel.threads:
            if t.name.startswith("Session "):
                thread = t
                break
        if thread:
            break

    result.assert_not_none(thread, "Bot created a thread for the session")
    if not thread:
        result.fail("No thread created — cannot verify further steps")
        return

    # 4. Wait for the initial status embed in the thread
    print("    Waiting for initial session embed...")
    status_embed_msg = await wait_for_embed(
        thread,
        bot_user=ctx.client.user,
        footer_contains="session:",
        timeout=30,
    )
    result.assert_not_none(status_embed_msg, "Initial session embed posted in thread")

    # 5. Extract the session ID from the embed footer
    thread_sid = None
    if status_embed_msg and status_embed_msg.embeds:
        footer = status_embed_msg.embeds[0].footer
        if footer and footer.text:
            import re
            m = re.search(r"session: ([0-9a-f\-]{36})", footer.text)
            if m:
                thread_sid = m.group(1)
    result.assert_not_none(thread_sid, "Session ID found in embed footer")

    if not thread_sid:
        return

    # 6. Wait for the session to finish (via Flint server polling)
    print("    Waiting for session to complete...")
    final_session = await wait_for_session_status(
        thread_sid,
        {"finished", "failed", "cancelled"},
        timeout=120,
    )
    result.assert_not_none(final_session, "Session reached terminal state")
    if final_session:
        result.assert_true(
            final_session.get("status") == "finished",
            f"Session finished (got: {final_session.get('status')})",
        )

    # 7. Verify the result was posted to the thread
    print("    Checking for result post in thread...")
    await asyncio.sleep(5)  # Give the bot time to post the result
    result_msg = await wait_for_embed(
        thread,
        bot_user=ctx.client.user,
        contains="Finished",
        timeout=30,
    )
    # The bot posts results either as embeds or plain messages
    if not result_msg:
        # Check for any message from the bot in the thread
        messages = [m async for m in thread.history(limit=20)]
        bot_messages = [m for m in messages if m.author.id == ctx.client.user.id]
        result.assert_true(len(bot_messages) > 1, "Bot posted result or status update in thread")
    else:
        result.assert_true(True, "Result embed posted in thread")
