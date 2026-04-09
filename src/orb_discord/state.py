"""Bot state persistence — save/load/rehydrate tracked sessions and dashboard."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import discord

from .api import api_get
from .config import STATE_FILE

if TYPE_CHECKING:
    from discord.ext.commands import Bot


class BotState:
    """Mutable state container for the bot. Persisted to JSON."""

    def __init__(self):
        # session_id -> { thread, status_msg, author, thread_id, status_msg_id, author_id }
        self.tracked_sessions: dict[str, dict] = {}
        # discord message_id -> { session_id, request_id, type }
        self.question_messages: dict[int, dict] = {}
        # request IDs already posted
        self.posted_requests: set[str] = set()
        # session IDs that already had results posted (dedup poller vs SSE)
        self.posted_results: set[str] = set()
        # dashboard
        self.dashboard_message: discord.Message | None = None
        self.dashboard_channel_id: int | None = None

    def get_author(self, sid: str) -> discord.User | discord.Member | None:
        return self.tracked_sessions.get(sid, {}).get("author")

    def get_thread_link(self, sid: str) -> str | None:
        thread = self.tracked_sessions.get(sid, {}).get("thread")
        if thread and hasattr(thread, "jump_url"):
            return thread.jump_url
        return None

    def save(self):
        sessions_data = {}
        for sid, info in self.tracked_sessions.items():
            sessions_data[sid] = {
                "thread_id": info.get("thread").id if info.get("thread") else info.get("thread_id"),
                "status_msg_id": info.get("status_msg").id if info.get("status_msg") else info.get("status_msg_id"),
                "author_id": info.get("author").id if info.get("author") else info.get("author_id"),
            }
        questions_data = {str(mid): qinfo for mid, qinfo in self.question_messages.items()}
        state = {
            "tracked_sessions": sessions_data,
            "question_messages": questions_data,
            "posted_requests": list(self.posted_requests),
            "posted_results": list(self.posted_results),
            "dashboard": {
                "channel_id": self.dashboard_channel_id,
                "message_id": self.dashboard_message.id if self.dashboard_message else None,
            },
        }
        STATE_FILE.write_text(json.dumps(state, indent=2))

    def _load_raw(self) -> dict:
        if not STATE_FILE.exists():
            return {}
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    async def rehydrate(self, bot: Bot):
        """Restore tracked sessions, questions, and dashboard from saved state + server."""
        raw = self._load_raw()
        if not raw:
            print("No saved state found, starting fresh.")
            return

        for rid in raw.get("posted_requests", []):
            self.posted_requests.add(rid)

        for sid in raw.get("posted_results", []):
            self.posted_results.add(sid)

        for mid_str, qinfo in raw.get("question_messages", {}).items():
            self.question_messages[int(mid_str)] = qinfo

        # Dashboard
        dash = raw.get("dashboard", {})
        if dash.get("channel_id"):
            self.dashboard_channel_id = dash["channel_id"]
            channel = bot.get_channel(self.dashboard_channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                if dash.get("message_id"):
                    try:
                        self.dashboard_message = await channel.fetch_message(dash["message_id"])
                        print(f"Restored dashboard in #{channel.name}")
                    except discord.NotFound:
                        self.dashboard_message = None
                        print("Dashboard message was deleted, will recreate.")
                else:
                    print(f"Dashboard channel #{channel.name} restored, message will be found or created.")
            else:
                # Channel gone — try to find it by name
                self.dashboard_channel_id = None
                print("Dashboard channel not found, will search by name.")

        # Sessions
        restored = 0
        cleaned = 0
        for sid, sdata in raw.get("tracked_sessions", {}).items():
            server_data = await api_get(f"/orbh/sessions/{sid}")
            if not server_data or "session" not in server_data:
                cleaned += 1
                continue
            session = server_data["session"]
            if session.get("status") in ("finished", "failed", "cancelled"):
                cleaned += 1
                continue

            thread = None
            if sdata.get("thread_id"):
                thread = bot.get_channel(sdata["thread_id"])
                if not thread:
                    try:
                        thread = await bot.fetch_channel(sdata["thread_id"])
                    except (discord.NotFound, discord.Forbidden):
                        pass

            status_msg = None
            if thread and sdata.get("status_msg_id"):
                try:
                    status_msg = await thread.fetch_message(sdata["status_msg_id"])
                except (discord.NotFound, discord.Forbidden):
                    pass

            author = None
            if sdata.get("author_id"):
                try:
                    author = await bot.fetch_user(sdata["author_id"])
                except discord.NotFound:
                    pass

            if not thread:
                cleaned += 1
                continue

            self.tracked_sessions[sid] = {
                "thread": thread, "status_msg": status_msg, "author": author,
                "thread_id": sdata.get("thread_id"),
                "status_msg_id": sdata.get("status_msg_id"),
                "author_id": sdata.get("author_id"),
            }
            restored += 1

        print(f"State restored: {restored} active sessions resumed, {cleaned} stale sessions cleaned up.")
        self.save()
