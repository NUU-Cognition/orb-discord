"""Unit tests for orb_discord.sessions — poll loop, transcript rendering, result posting."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from orb_discord.sessions import (
    delete_status_card,
    extract_last_agent_text,
    extract_result,
    poll_session,
    post_session_result,
    surface_pending_requests,
    update_status_card,
)


# ---------------------------------------------------------------------------
# update_status_card
# ---------------------------------------------------------------------------


class TestUpdateStatusCard:
    async def test_updates_embed_on_status_msg(self, tracked_session):
        sid, state = tracked_session
        session = {"status": "in-progress", "title": "Test Session"}
        await update_status_card(state, sid, session)
        info = state.tracked_sessions[sid]
        info["status_msg"].edit.assert_called_once()
        call_kwargs = info["status_msg"].edit.call_args[1]
        embed = call_kwargs["embed"]
        assert isinstance(embed, discord.Embed)

    async def test_noop_when_no_status_msg(self, bot_state):
        bot_state.tracked_sessions["x"] = {"status_msg": None}
        await update_status_card(bot_state, "x", {"status": "finished"})
        # Should not raise

    async def test_handles_not_found(self, tracked_session):
        sid, state = tracked_session
        state.tracked_sessions[sid]["status_msg"].edit.side_effect = discord.NotFound(
            MagicMock(status=404), "Not Found"
        )
        await update_status_card(state, sid, {"status": "finished"})
        assert state.tracked_sessions[sid]["status_msg"] is None


# ---------------------------------------------------------------------------
# delete_status_card
# ---------------------------------------------------------------------------


class TestDeleteStatusCard:
    async def test_deletes_status_msg(self, tracked_session):
        sid, state = tracked_session
        status_msg = state.tracked_sessions[sid]["status_msg"]
        await delete_status_card(state, sid)
        status_msg.delete.assert_called_once()
        assert state.tracked_sessions[sid]["status_msg"] is None

    async def test_noop_when_no_status_msg(self, bot_state):
        bot_state.tracked_sessions["x"] = {"status_msg": None}
        await delete_status_card(bot_state, "x")
        # Should not raise

    async def test_handles_not_found(self, tracked_session):
        sid, state = tracked_session
        state.tracked_sessions[sid]["status_msg"].delete.side_effect = discord.NotFound(
            MagicMock(status=404), "Not Found"
        )
        await delete_status_card(state, sid)
        assert state.tracked_sessions[sid]["status_msg"] is None


# ---------------------------------------------------------------------------
# poll_session — multi-step via side_effect
# ---------------------------------------------------------------------------


class TestPollSession:
    @patch("orb_discord.sessions.asyncio.sleep", new_callable=AsyncMock)
    @patch("orb_discord.sessions.api_get", new_callable=AsyncMock)
    async def test_poll_through_to_finished(self, mock_api_get, mock_sleep, tracked_session):
        sid, state = tracked_session

        # First poll: in-progress; second poll: finished with result
        mock_api_get.side_effect = [
            {"session": {"status": "in-progress", "title": "Working"}},
            {"session": {"status": "finished", "title": "Done", "runs": [{"result": "All done!"}]}},
        ]

        with patch("orb_discord.sessions.post_session_result", new_callable=AsyncMock) as mock_post_result:
            with patch("orb_discord.sessions.update_status_card", new_callable=AsyncMock):
                with patch("orb_discord.sessions.delete_status_card", new_callable=AsyncMock) as mock_delete:
                    await poll_session(state, sid)

        mock_post_result.assert_called_once()
        mock_delete.assert_called_once()
        # Session should be removed from tracked_sessions
        assert sid not in state.tracked_sessions
        state.save.assert_called()

    @patch("orb_discord.sessions.asyncio.sleep", new_callable=AsyncMock)
    @patch("orb_discord.sessions.api_get", new_callable=AsyncMock)
    async def test_poll_surfaces_requests_on_blocked(self, mock_api_get, mock_sleep, tracked_session):
        sid, state = tracked_session

        # First poll: blocked; second poll: finished
        mock_api_get.side_effect = [
            {"session": {"status": "blocked", "title": "Blocked"}},
            {"session": {"status": "finished", "title": "Done", "runs": [{"result": "ok"}]}},
        ]

        with patch("orb_discord.sessions.surface_pending_requests", new_callable=AsyncMock) as mock_surface:
            with patch("orb_discord.sessions.post_session_result", new_callable=AsyncMock):
                with patch("orb_discord.sessions.update_status_card", new_callable=AsyncMock):
                    with patch("orb_discord.sessions.delete_status_card", new_callable=AsyncMock):
                        await poll_session(state, sid)

        mock_surface.assert_called_once()

    @patch("orb_discord.sessions.asyncio.sleep", new_callable=AsyncMock)
    @patch("orb_discord.sessions.api_get", new_callable=AsyncMock)
    async def test_poll_handles_failed_status(self, mock_api_get, mock_sleep, tracked_session):
        sid, state = tracked_session

        mock_api_get.side_effect = [
            {"session": {"status": "failed", "title": "Oops"}},
        ]

        with patch("orb_discord.sessions.delete_status_card", new_callable=AsyncMock) as mock_delete:
            await poll_session(state, sid)

        mock_delete.assert_called_once()
        assert sid not in state.tracked_sessions

    @patch("orb_discord.sessions.asyncio.sleep", new_callable=AsyncMock)
    @patch("orb_discord.sessions.api_get", new_callable=AsyncMock)
    async def test_poll_exits_when_session_removed(self, mock_api_get, mock_sleep, tracked_session):
        sid, state = tracked_session

        async def remove_session(*args, **kwargs):
            state.tracked_sessions.pop(sid, None)

        mock_sleep.side_effect = remove_session

        mock_api_get.return_value = None  # won't be reached

        await poll_session(state, sid)
        assert sid not in state.tracked_sessions

    @patch("orb_discord.sessions.asyncio.sleep", new_callable=AsyncMock)
    @patch("orb_discord.sessions.api_get", new_callable=AsyncMock)
    async def test_poll_continues_on_api_none(self, mock_api_get, mock_sleep, tracked_session):
        """api_get returning None should not crash the loop."""
        sid, state = tracked_session

        call_count = 0

        async def api_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return None  # session polls return None twice
            return {"session": {"status": "finished", "title": "Done", "runs": [{"result": "ok"}]}}

        mock_api_get.side_effect = api_side_effect

        with patch("orb_discord.sessions.post_session_result", new_callable=AsyncMock):
            with patch("orb_discord.sessions.update_status_card", new_callable=AsyncMock):
                await poll_session(state, sid)


# ---------------------------------------------------------------------------
# surface_pending_requests
# ---------------------------------------------------------------------------


class TestSurfacePendingRequests:
    @patch("orb_discord.sessions.api_get", new_callable=AsyncMock)
    @patch("orb_discord.sessions.post_question", new_callable=AsyncMock)
    async def test_posts_unanswered_requests(self, mock_post_q, mock_api_get, bot_state, mock_thread):
        sid = "sid-1"
        bot_state.tracked_sessions[sid] = {"thread": mock_thread}
        mock_api_get.return_value = {
            "requests": [
                {"id": "r1", "question": "What now?"},
                {"id": "r2", "question": "And this?", "answered": True},
            ]
        }
        await surface_pending_requests(bot_state, sid, mock_thread)
        mock_post_q.assert_called_once()
        call_args = mock_post_q.call_args[0]
        assert call_args[2]["id"] == "r1"

    @patch("orb_discord.sessions.api_get", new_callable=AsyncMock)
    @patch("orb_discord.sessions.post_question", new_callable=AsyncMock)
    async def test_skips_already_posted_requests(self, mock_post_q, mock_api_get, bot_state, mock_thread):
        sid = "sid-1"
        bot_state.posted_requests.add("r1")
        mock_api_get.return_value = {"requests": [{"id": "r1", "question": "Again?"}]}
        await surface_pending_requests(bot_state, sid, mock_thread)
        mock_post_q.assert_not_called()


# ---------------------------------------------------------------------------
# extract_result / extract_last_agent_text
# ---------------------------------------------------------------------------


class TestExtractResult:
    def test_extracts_from_last_run(self):
        session = {"runs": [{"result": "first"}, {"result": "second"}]}
        assert extract_result(session) == "second"

    def test_returns_none_when_no_runs(self):
        assert extract_result({}) is None

    def test_returns_none_when_result_empty(self):
        assert extract_result({"runs": [{"result": ""}]}) is None


class TestExtractLastAgentText:
    def test_extracts_last_agent_text(self):
        turns = [
            {"role": "human", "content": [{"type": "text", "text": "hi"}]},
            {"role": "agent", "content": [{"type": "text", "text": "first"}]},
            {"role": "agent", "content": [{"type": "text", "text": "last"}]},
        ]
        assert extract_last_agent_text(turns) == "last"

    def test_returns_none_when_no_agent_turns(self):
        turns = [{"role": "human", "content": [{"type": "text", "text": "hi"}]}]
        assert extract_last_agent_text(turns) is None

    def test_joins_multiple_text_blocks(self):
        turns = [
            {
                "role": "agent",
                "content": [
                    {"type": "text", "text": "part1"},
                    {"type": "text", "text": "part2"},
                ],
            }
        ]
        assert extract_last_agent_text(turns) == "part1\n\npart2"


# ---------------------------------------------------------------------------
# post_session_result
# ---------------------------------------------------------------------------


class TestPostSessionResult:
    @patch("orb_discord.sessions.send_long", new_callable=AsyncMock)
    @patch("orb_discord.sessions.extract_discord_images", return_value=("result text", []))
    async def test_posts_result_from_runs(self, mock_images, mock_send_long, bot_state, mock_thread):
        sid = "sid-1"
        bot_state.tracked_sessions[sid] = {"thread": mock_thread, "author": None}
        session = {"title": "Done", "runs": [{"result": "result text"}]}
        await post_session_result(bot_state, sid, session, mock_thread)
        mock_send_long.assert_called_once()
        assert sid in bot_state.posted_results

    @patch("orb_discord.sessions.send_long", new_callable=AsyncMock)
    @patch("orb_discord.sessions.extract_discord_images", return_value=("result text", []))
    async def test_skips_duplicate_result(self, mock_images, mock_send_long, bot_state, mock_thread):
        sid = "sid-1"
        bot_state.posted_results.add(sid)
        await post_session_result(bot_state, sid, {}, mock_thread)
        mock_send_long.assert_not_called()

    @patch("orb_discord.sessions.api_get", new_callable=AsyncMock)
    @patch("orb_discord.sessions.send_long", new_callable=AsyncMock)
    @patch("orb_discord.sessions.extract_discord_images", return_value=("fallback text", []))
    async def test_falls_back_to_transcript(self, mock_images, mock_send_long, mock_api_get, bot_state, mock_thread):
        sid = "sid-1"
        bot_state.tracked_sessions[sid] = {"thread": mock_thread, "author": None}
        session = {"title": "Done", "runs": []}  # no result
        mock_api_get.return_value = {
            "turns": [{"role": "agent", "content": [{"type": "text", "text": "fallback text"}]}]
        }
        await post_session_result(bot_state, sid, session, mock_thread)
        mock_send_long.assert_called_once()

    @patch("orb_discord.sessions.api_get", new_callable=AsyncMock)
    @patch("orb_discord.sessions.send_long", new_callable=AsyncMock)
    @patch("orb_discord.sessions.extract_discord_images", return_value=("Session completed but no result was returned.", []))
    async def test_handles_no_result_gracefully(self, mock_images, mock_send_long, mock_api_get, bot_state, mock_thread):
        sid = "sid-1"
        bot_state.tracked_sessions[sid] = {"thread": mock_thread, "author": None}
        session = {"runs": []}
        mock_api_get.return_value = {"turns": []}  # no agent turns
        await post_session_result(bot_state, sid, session, mock_thread)
        mock_send_long.assert_called_once()
