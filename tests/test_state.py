"""Unit tests for orb_discord.state — BotState serialization, rehydration, dedup tracking."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from orb_discord.state import BotState


# ---------------------------------------------------------------------------
# BotState initialization
# ---------------------------------------------------------------------------


class TestBotStateInit:
    def test_fresh_state_has_empty_collections(self):
        state = BotState()
        assert state.tracked_sessions == {}
        assert state.question_messages == {}
        assert state.posted_requests == set()
        assert state.posted_results == set()
        assert state.dashboard_message is None
        assert state.dashboard_channel_id is None


# ---------------------------------------------------------------------------
# get_author / get_thread_link
# ---------------------------------------------------------------------------


class TestBotStateAccessors:
    def test_get_author_returns_user(self, bot_state, tracked_session):
        sid, state = tracked_session
        author = state.get_author(sid)
        assert author is not None

    def test_get_author_returns_none_for_unknown_session(self, bot_state):
        assert bot_state.get_author("nonexistent") is None

    def test_get_thread_link_returns_jump_url(self, tracked_session):
        sid, state = tracked_session
        state.tracked_sessions[sid]["thread"].jump_url = "https://discord.com/channels/..."
        assert state.get_thread_link(sid) == "https://discord.com/channels/..."

    def test_get_thread_link_returns_none_for_unknown(self, bot_state):
        assert bot_state.get_thread_link("nonexistent") is None


# ---------------------------------------------------------------------------
# save — serialization
# ---------------------------------------------------------------------------


class TestBotStateSave:
    @patch("orb_discord.config.STATE_FILE")
    def test_serializes_tracked_sessions(self, mock_state_file):
        state = BotState()
        sid = "test-sid"
        # Use real integer IDs to avoid JSON serialization issues with MagicMock
        mock_thread = MagicMock()
        mock_thread.id = 100
        mock_status_msg = MagicMock()
        mock_status_msg.id = 200
        mock_author = MagicMock()
        mock_author.id = 300
        state.tracked_sessions[sid] = {
            "thread": mock_thread,
            "status_msg": mock_status_msg,
            "author": mock_author,
            "thread_id": 100,
            "status_msg_id": 200,
            "author_id": 300,
        }
        state.save()
        mock_state_file.write_text.assert_called_once()
        written = json.loads(mock_state_file.write_text.call_args[0][0])
        assert sid in written["tracked_sessions"]
        sess_data = written["tracked_sessions"][sid]
        assert sess_data["thread_id"] == 100
        assert sess_data["status_msg_id"] == 200
        assert sess_data["author_id"] == 300

    @patch("orb_discord.config.STATE_FILE")
    def test_serializes_posted_requests_and_results(self, mock_state_file, bot_state):
        bot_state.save = BotState.save.__get__(bot_state)  # restore real save
        bot_state.posted_requests = {"r1", "r2"}
        bot_state.posted_results = {"sid1"}
        bot_state.save()
        written = json.loads(mock_state_file.write_text.call_args[0][0])
        assert set(written["posted_requests"]) == {"r1", "r2"}
        assert written["posted_results"] == ["sid1"]

    @patch("orb_discord.config.STATE_FILE")
    def test_serializes_question_messages(self, mock_state_file, bot_state):
        bot_state.save = BotState.save.__get__(bot_state)
        bot_state.question_messages[12345] = {"session_id": "s1", "request_id": "r1", "type": "blocking"}
        bot_state.save()
        written = json.loads(mock_state_file.write_text.call_args[0][0])
        assert "12345" in written["question_messages"]

    @patch("orb_discord.config.STATE_FILE")
    def test_serializes_dashboard_state(self, mock_state_file, bot_state):
        bot_state.save = BotState.save.__get__(bot_state)
        bot_state.dashboard_channel_id = 999
        bot_state.dashboard_message = None
        bot_state.save()
        written = json.loads(mock_state_file.write_text.call_args[0][0])
        assert written["dashboard"]["channel_id"] == 999
        assert written["dashboard"]["message_id"] is None


# ---------------------------------------------------------------------------
# _load_raw
# ---------------------------------------------------------------------------


class TestLoadRaw:
    @patch("orb_discord.config.STATE_FILE")
    def test_returns_empty_when_no_file(self, mock_state_file):
        mock_state_file.exists.return_value = False
        state = BotState()
        assert state._load_raw() == {}

    @patch("orb_discord.config.STATE_FILE")
    def test_returns_empty_on_invalid_json(self, mock_state_file):
        mock_state_file.exists.return_value = True
        mock_state_file.read_text.return_value = "not json"
        state = BotState()
        assert state._load_raw() == {}

    @patch("orb_discord.config.STATE_FILE")
    def test_returns_parsed_json(self, mock_state_file):
        data = {"tracked_sessions": {}, "posted_requests": ["r1"]}
        mock_state_file.exists.return_value = True
        mock_state_file.read_text.return_value = json.dumps(data)
        state = BotState()
        assert state._load_raw() == data


# ---------------------------------------------------------------------------
# posted_results / posted_requests dedup tracking
# ---------------------------------------------------------------------------


class TestDedupTracking:
    def test_posted_results_is_set(self, bot_state):
        bot_state.posted_results.add("sid-1")
        bot_state.posted_results.add("sid-1")
        assert len(bot_state.posted_results) == 1

    def test_posted_requests_is_set(self, bot_state):
        bot_state.posted_requests.add("r1")
        bot_state.posted_requests.add("r1")
        assert len(bot_state.posted_requests) == 1


# ---------------------------------------------------------------------------
# rehydrate
# ---------------------------------------------------------------------------


class TestRehydrate:
    @patch("orb_discord.config.STATE_FILE")
    @patch("orb_discord.state.api_get", new_callable=AsyncMock)
    async def test_restores_posted_sets(self, mock_api_get, mock_state_file):
        raw = {
            "tracked_sessions": {},
            "posted_requests": ["r1", "r2"],
            "posted_results": ["sid-a"],
            "question_messages": {"100": {"session_id": "s1", "request_id": "r1", "type": "blocking"}},
            "dashboard": {},
        }
        mock_state_file.exists.return_value = True
        mock_state_file.read_text.return_value = json.dumps(raw)

        state = BotState()
        state.save = MagicMock()
        bot = AsyncMock()
        await state.rehydrate(bot)

        assert state.posted_requests == {"r1", "r2"}
        assert state.posted_results == {"sid-a"}
        assert 100 in state.question_messages

    @patch("orb_discord.config.STATE_FILE")
    async def test_handles_empty_state_gracefully(self, mock_state_file):
        mock_state_file.exists.return_value = False
        state = BotState()
        state.save = MagicMock()
        bot = AsyncMock()
        await state.rehydrate(bot)
        assert state.tracked_sessions == {}

    @patch("orb_discord.config.STATE_FILE")
    @patch("orb_discord.state.api_get", new_callable=AsyncMock)
    async def test_cleans_finished_sessions(self, mock_api_get, mock_state_file):
        raw = {
            "tracked_sessions": {
                "sid-finished": {"thread_id": 1, "status_msg_id": 2, "author_id": 3},
            },
            "posted_requests": [],
            "posted_results": [],
            "question_messages": {},
            "dashboard": {},
        }
        mock_state_file.exists.return_value = True
        mock_state_file.read_text.return_value = json.dumps(raw)
        mock_api_get.return_value = {"session": {"status": "finished"}}

        state = BotState()
        state.save = MagicMock()
        bot = AsyncMock()
        await state.rehydrate(bot)
        assert "sid-finished" not in state.tracked_sessions

    @patch("orb_discord.config.STATE_FILE")
    @patch("orb_discord.state.api_get", new_callable=AsyncMock)
    async def test_cleans_sessions_with_no_server_data(self, mock_api_get, mock_state_file):
        raw = {
            "tracked_sessions": {
                "sid-gone": {"thread_id": 1, "status_msg_id": 2, "author_id": 3},
            },
            "posted_requests": [],
            "posted_results": [],
            "question_messages": {},
            "dashboard": {},
        }
        mock_state_file.exists.return_value = True
        mock_state_file.read_text.return_value = json.dumps(raw)
        mock_api_get.return_value = None  # server can't find session

        state = BotState()
        state.save = MagicMock()
        bot = AsyncMock()
        await state.rehydrate(bot)
        assert "sid-gone" not in state.tracked_sessions
