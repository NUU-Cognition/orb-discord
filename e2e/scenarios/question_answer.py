"""Scenario: Question / Answer Flow — trigger blocking question, reply, verify resume."""

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


async def test_question_answer(ctx: E2EContext):
    """Test blocking question flow: session asks question -> bot posts it -> user replies -> session resumes."""
    result = ctx.reporter.current_result

    # 1. Launch a session that will ask a blocking question
    print("    Launching session with blocking question via API...")
    session_data = await flint_api_post("/orbh/sessions", {
        "runtime": "claude",
        "prompt": (
            "Use `flint orbh ask` to ask the user: 'What color is the sky?' "
            "Wait for the answer, then say exactly: 'The answer was: <answer>'. "
            "Do nothing else."
        ),
        "maxTurns": 10,
        "title": "E2E Q&A Test",
    })
    result.assert_not_none(session_data, "Session launch returned data")
    if not session_data or "session" not in session_data:
        result.fail("Could not launch session — aborting scenario")
        return

    sid = session_data["session"]["id"]
    print(f"    Session ID: {sid}")

    # 2. Notify the bot about this session by mentioning it
    bot_mention = f"<@{ctx.bot_member.id}>"
    trigger_msg = await ctx.channel.send(
        f"{bot_mention} Ask me a question (E2E test)"
    )

    await rate_limit_delay()

    # 3. Wait for the session to become blocked
    print("    Waiting for session to reach blocked/deferred state...")
    blocked_session = await wait_for_session_status(
        sid,
        {"blocked", "deferred"},
        timeout=90,
    )
    result.assert_not_none(blocked_session, "Session reached blocked/deferred state")

    if not blocked_session:
        # Session may have finished without blocking — check its state
        final = await flint_api_get(f"/orbh/sessions/{sid}")
        if final and "session" in final:
            result.fail(f"Session did not block — status: {final['session'].get('status')}")
        else:
            result.fail("Session did not block and could not retrieve status")
        return

    await asyncio.sleep(5)  # Give the bot time to surface the question

    # 4. Look for the question message in the channel or thread
    print("    Looking for question embed from bot...")
    # Check threads first
    question_channel = ctx.channel
    for t in ctx.channel.threads:
        question_channel = t
        break

    question_msg = await wait_for_embed(
        question_channel,
        bot_user=ctx.client.user,
        contains="Question",
        timeout=30,
    )
    if not question_msg:
        # Try looking for any embed with "reply" or "response" in it
        question_msg = await wait_for_embed(
            question_channel,
            bot_user=ctx.client.user,
            contains="reply",
            timeout=15,
        )

    result.assert_not_none(question_msg, "Bot surfaced the blocking question as a Discord message")

    if not question_msg:
        # Try to answer via API directly
        print("    Question not found in Discord — answering via API...")
        resp = await flint_api_post(f"/orbh/sessions/{sid}/respond", {"text": "blue"})
        result.assert_not_none(resp, "Answered question via API fallback")
    else:
        # 5. Reply to the question message
        print("    Replying to question...")
        await question_msg.reply("blue")
        await rate_limit_delay()

    # 6. Wait for the session to finish after receiving the answer
    print("    Waiting for session to complete after answer...")
    final_session = await wait_for_session_status(
        sid,
        {"finished", "failed", "cancelled"},
        timeout=120,
    )
    result.assert_not_none(final_session, "Session completed after answer")
    if final_session:
        result.assert_true(
            final_session.get("status") == "finished",
            f"Session finished successfully (got: {final_session.get('status')})",
        )
