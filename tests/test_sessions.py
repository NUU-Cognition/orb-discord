"""Unit tests for flint_discord.sessions — poll loop, transcript rendering, result posting."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from flint_discord.sessions import (
    _format_tool,
    _poll_transcript_turns,
    _render_turns,
    extract_last_agent_text,
    extract_result,
    poll_session,
    post_session_result,
    surface_pending_requests,
    update_status_card,
)


# ---------------------------------------------------------------------------
# _format_tool
# ---------------------------------------------------------------------------


class TestFormatTool:
    def test_read_tool_shows_filename(self):
        tool = {"tool": "Read", "input": {"file_path": "/some/dir/file.py"}}
        assert _format_tool(tool) == "`Read` file.py"

    def test_bash_tool_truncates_long_command(self):
        cmd = "a" * 100
        result = _format_tool({"tool": "Bash", "input": {"command": cmd}})
        assert result.startswith("`Bash` ")
        assert result.endswith("\u2026")
        # 80 chars + ellipsis
        assert len(result) == len("`Bash` ") + 81

    def test_grep_tool_shows_pattern(self):
        tool = {"tool": "Grep", "input": {"pattern": "TODO"}}
        assert _format_tool(tool) == "`Grep` TODO"

    def test_agent_tool_shows_description(self):
        tool = {"tool": "Agent", "input": {"description": "search"}}
        assert _format_tool(tool) == "`Agent` search"

    def test_non_dict_input_returns_bare_name(self):
        tool = {"tool": "Custom", "input": "just a string"}
        assert _format_tool(tool) == "`Custom`"

    def test_unknown_tool_with_no_input(self):
        tool = {"tool": "Unknown", "input": {}}
        assert _format_tool(tool) == "`Unknown`"


# ---------------------------------------------------------------------------
# _render_turns
# ---------------------------------------------------------------------------


class TestRenderTurns:
    async def test_skips_non_agent_turns(self):
        target = AsyncMock()
        turns = [{"role": "human", "content": [{"type": "text", "text": "hello"}]}]
        await _render_turns(turns, target)
        target.send.assert_not_called()

    async def test_renders_tool_batch_as_summary(self):
        target = AsyncMock()
        turns = [
            {
                "role": "agent",
                "content": [
                    {
                        "type": "tool-batch",
                        "tools": [
                            {"tool": "Read", "input": {"file_path": "/a/file.py"}},
                            {"tool": "Grep", "input": {"pattern": "TODO"}},
                            {"tool": "Agent", "input": {"description": "search"}},
                        ],
                    }
                ],
            }
        ]
        await _render_turns(turns, target)
        call_args = target.send.call_args_list[0][0][0]
        assert "`Read` file.py" in call_args
        assert "`Grep` TODO" in call_args
        assert "`Agent` search" in call_args
        assert "\u2192" in call_args  # arrow separator

    async def test_truncates_long_text(self):
        target = AsyncMock()
        long_text = "x" * 2500
        turns = [{"role": "agent", "content": [{"type": "text", "text": long_text}]}]
        with patch("flint_discord.sessions.extract_discord_images", return_value=(long_text, [])):
            await _render_turns(turns, target)
        sent_text = target.send.call_args_list[0][1].get("cleaned", target.send.call_args_list[0][0][0] if target.send.call_args_list[0][0] else "")
        # The function truncates to 1997 + ellipsis = 2000 total
        # Check that send was called with truncated text
        first_call = target.send.call_args_list[0]
        msg = first_call[0][0] if first_call[0] else first_call[1].get("content", "")
        assert len(msg) <= 2000

    async def test_renders_text_turn(self):
        target = AsyncMock()
        turns = [{"role": "agent", "content": [{"type": "text", "text": "Hello world"}]}]
        with patch("flint_discord.sessions.extract_discord_images", return_value=("Hello world", [])):
            await _render_turns(turns, target)
        target.send.assert_called_once_with("Hello world")

    async def test_sends_discord_images(self):
        target = AsyncMock()
        fake_file = MagicMock(spec=discord.File)
        turns = [{"role": "agent", "content": [{"type": "text", "text": "text with image"}]}]
        with patch("flint_discord.sessions.extract_discord_images", return_value=("cleaned text", [fake_file])):
            await _render_turns(turns, target)
        # Text message
        assert target.send.call_args_list[0] == (("cleaned text",),)
        # Image message
        target.send.assert_any_call(files=[fake_file])

    async def test_truncates_long_tool_summary(self):
        target = AsyncMock()
        # Create a tool batch that produces a summary > 2000 chars
        tools = [{"tool": "Read", "input": {"file_path": f"/very/long/path/to/some/deeply/nested/directory/structure/file_number_{i:04d}.py"}} for i in range(200)]
        turns = [{"role": "agent", "content": [{"type": "tool-batch", "tools": tools}]}]
        await _render_turns(turns, target)
        sent = target.send.call_args_list[0][0][0]
        assert len(sent) <= 2000
        assert sent.endswith("\u2026")


# ---------------------------------------------------------------------------
# _poll_transcript_turns
# ---------------------------------------------------------------------------


class TestPollTranscriptTurns:
    @patch("flint_discord.sessions.api_get", new_callable=AsyncMock)
    async def test_returns_new_index_after_rendering(self, mock_api_get):
        target = AsyncMock()
        turns = [
            {"role": "agent", "content": [{"type": "text", "text": f"turn {i}"}]}
            for i in range(5)
        ]
        mock_api_get.return_value = {"turns": turns}
        with patch("flint_discord.sessions.extract_discord_images", return_value=("text", [])):
            result = await _poll_transcript_turns("sid", target, 2)
        assert result == 5
        # Only turns[2:] should be rendered (3 turns)

    @patch("flint_discord.sessions.api_get", new_callable=AsyncMock)
    async def test_skips_when_no_new_turns(self, mock_api_get):
        target = AsyncMock()
        mock_api_get.return_value = {"turns": [{"role": "agent", "content": []}] * 3}
        result = await _poll_transcript_turns("sid", target, 3)
        assert result == 3

    @patch("flint_discord.sessions.api_get", new_callable=AsyncMock)
    async def test_returns_index_when_api_returns_none(self, mock_api_get):
        target = AsyncMock()
        mock_api_get.return_value = None
        result = await _poll_transcript_turns("sid", target, 5)
        assert result == 5


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
# poll_session — multi-step via side_effect
# ---------------------------------------------------------------------------


class TestPollSession:
    @patch("flint_discord.sessions.asyncio.sleep", new_callable=AsyncMock)
    @patch("flint_discord.sessions._stream_transcript_sse", new_callable=AsyncMock)
    @patch("flint_discord.sessions.api_get", new_callable=AsyncMock)
    async def test_poll_through_to_finished(self, mock_api_get, mock_sse, mock_sleep, tracked_session):
        sid, state = tracked_session

        # First poll: in-progress; second poll: finished with result
        mock_api_get.side_effect = [
            None,  # transcript poll (SSE not active, _poll_transcript_turns call)
            {"session": {"status": "in-progress", "title": "Working"}},
            None,  # transcript poll
            {"session": {"status": "finished", "title": "Done", "runs": [{"result": "All done!"}]}},
            None,  # transcript poll after finished (the sleep(1) fallback)
            None,  # post_session_result transcript fallback (not needed since result exists)
        ]

        with patch("flint_discord.sessions.post_session_result", new_callable=AsyncMock) as mock_post_result:
            with patch("flint_discord.sessions.update_status_card", new_callable=AsyncMock):
                await poll_session(state, sid)

        mock_post_result.assert_called_once()
        # Session should be removed from tracked_sessions
        assert sid not in state.tracked_sessions
        state.save.assert_called()

    @patch("flint_discord.sessions.asyncio.sleep", new_callable=AsyncMock)
    @patch("flint_discord.sessions._stream_transcript_sse", new_callable=AsyncMock)
    @patch("flint_discord.sessions.api_get", new_callable=AsyncMock)
    async def test_poll_surfaces_requests_on_blocked(self, mock_api_get, mock_sse, mock_sleep, tracked_session):
        sid, state = tracked_session

        # First poll: blocked; second poll: finished
        mock_api_get.side_effect = [
            None,  # transcript poll
            {"session": {"status": "blocked", "title": "Blocked"}},
            None,  # transcript poll
            {"session": {"status": "finished", "title": "Done", "runs": [{"result": "ok"}]}},
            None,  # transcript poll after finished
            None,
        ]

        with patch("flint_discord.sessions.surface_pending_requests", new_callable=AsyncMock) as mock_surface:
            with patch("flint_discord.sessions.post_session_result", new_callable=AsyncMock):
                with patch("flint_discord.sessions.update_status_card", new_callable=AsyncMock):
                    await poll_session(state, sid)

        mock_surface.assert_called_once()

    @patch("flint_discord.sessions.asyncio.sleep", new_callable=AsyncMock)
    @patch("flint_discord.sessions._stream_transcript_sse", new_callable=AsyncMock)
    @patch("flint_discord.sessions.api_get", new_callable=AsyncMock)
    async def test_poll_handles_failed_status(self, mock_api_get, mock_sse, mock_sleep, tracked_session):
        sid, state = tracked_session

        mock_api_get.side_effect = [
            None,  # transcript poll
            {"session": {"status": "failed", "title": "Oops"}},
        ]

        with patch("flint_discord.sessions.update_status_card", new_callable=AsyncMock) as mock_update:
            await poll_session(state, sid)

        # Called twice: once for title change, once for failed status
        assert mock_update.call_count >= 1
        # The last call should be the failed status update with red color
        last_call = mock_update.call_args_list[-1]
        assert last_call[1].get("color") == 0xFF0000
        assert sid not in state.tracked_sessions

    @patch("flint_discord.sessions.asyncio.sleep", new_callable=AsyncMock)
    @patch("flint_discord.sessions._stream_transcript_sse", new_callable=AsyncMock)
    @patch("flint_discord.sessions.api_get", new_callable=AsyncMock)
    async def test_poll_exits_when_session_removed(self, mock_api_get, mock_sse, mock_sleep, tracked_session):
        sid, state = tracked_session

        async def remove_session(*args, **kwargs):
            state.tracked_sessions.pop(sid, None)

        mock_sleep.side_effect = remove_session

        mock_api_get.return_value = None  # won't be reached

        await poll_session(state, sid)
        assert sid not in state.tracked_sessions

    @patch("flint_discord.sessions.asyncio.sleep", new_callable=AsyncMock)
    @patch("flint_discord.sessions._stream_transcript_sse", new_callable=AsyncMock)
    @patch("flint_discord.sessions.api_get", new_callable=AsyncMock)
    async def test_poll_continues_on_api_none(self, mock_api_get, mock_sse, mock_sleep, tracked_session):
        """api_get returning None should not crash the loop."""
        sid, state = tracked_session

        call_count = 0

        async def api_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                return None  # transcript + session polls return None twice
            # On 5th+ call, return finished to break out
            if call_count == 5:
                return None  # transcript poll
            return {"session": {"status": "finished", "title": "Done", "runs": [{"result": "ok"}]}}

        mock_api_get.side_effect = api_side_effect

        with patch("flint_discord.sessions.post_session_result", new_callable=AsyncMock):
            with patch("flint_discord.sessions.update_status_card", new_callable=AsyncMock):
                await poll_session(state, sid)


# ---------------------------------------------------------------------------
# surface_pending_requests
# ---------------------------------------------------------------------------


class TestSurfacePendingRequests:
    @patch("flint_discord.sessions.api_get", new_callable=AsyncMock)
    @patch("flint_discord.sessions.post_question", new_callable=AsyncMock)
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

    @patch("flint_discord.sessions.api_get", new_callable=AsyncMock)
    @patch("flint_discord.sessions.post_question", new_callable=AsyncMock)
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
    @patch("flint_discord.sessions.send_long", new_callable=AsyncMock)
    @patch("flint_discord.sessions.extract_discord_images", return_value=("result text", []))
    async def test_posts_result_from_runs(self, mock_images, mock_send_long, bot_state, mock_thread):
        sid = "sid-1"
        bot_state.tracked_sessions[sid] = {"thread": mock_thread, "author": None}
        session = {"title": "Done", "runs": [{"result": "result text"}]}
        await post_session_result(bot_state, sid, session, mock_thread)
        mock_send_long.assert_called_once()
        assert sid in bot_state.posted_results

    @patch("flint_discord.sessions.send_long", new_callable=AsyncMock)
    @patch("flint_discord.sessions.extract_discord_images", return_value=("result text", []))
    async def test_skips_duplicate_result(self, mock_images, mock_send_long, bot_state, mock_thread):
        sid = "sid-1"
        bot_state.posted_results.add(sid)
        await post_session_result(bot_state, sid, {}, mock_thread)
        mock_send_long.assert_not_called()

    @patch("flint_discord.sessions.api_get", new_callable=AsyncMock)
    @patch("flint_discord.sessions.send_long", new_callable=AsyncMock)
    @patch("flint_discord.sessions.extract_discord_images", return_value=("fallback text", []))
    async def test_falls_back_to_transcript(self, mock_images, mock_send_long, mock_api_get, bot_state, mock_thread):
        sid = "sid-1"
        bot_state.tracked_sessions[sid] = {"thread": mock_thread, "author": None}
        session = {"title": "Done", "runs": []}  # no result
        mock_api_get.return_value = {
            "turns": [{"role": "agent", "content": [{"type": "text", "text": "fallback text"}]}]
        }
        await post_session_result(bot_state, sid, session, mock_thread)
        mock_send_long.assert_called_once()

    @patch("flint_discord.sessions.api_get", new_callable=AsyncMock)
    @patch("flint_discord.sessions.send_long", new_callable=AsyncMock)
    @patch("flint_discord.sessions.extract_discord_images", return_value=("Session completed but no result was returned.", []))
    async def test_handles_no_result_gracefully(self, mock_images, mock_send_long, mock_api_get, bot_state, mock_thread):
        sid = "sid-1"
        bot_state.tracked_sessions[sid] = {"thread": mock_thread, "author": None}
        session = {"runs": []}
        mock_api_get.return_value = {"turns": []}  # no agent turns
        await post_session_result(bot_state, sid, session, mock_thread)
        mock_send_long.assert_called_once()
