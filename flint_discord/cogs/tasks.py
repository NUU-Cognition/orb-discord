"""Task management — /task list, /task view, /task create, /task launch."""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..api import api_get, api_patch, api_post
from ..formatting import PaginatorView, split_pages
from ..sessions import DISCORD_SHARD_INSTRUCTION, poll_session

if TYPE_CHECKING:
    from ..state import BotState

# ---------- constants ----------

TASK_STATUS_COLORS = {
    "todo": 0x95A5A6, "in-progress": 0xFFA500, "blocked": 0xFF6B6B,
    "review": 0x3498DB, "reviewing": 0x9B59B6, "done": 0x2ECC71,
    "deprecated": 0x7F8C8D,
}
TASK_STATUS_EMOJI = {
    "todo": "\U0001f4cb", "in-progress": "\u2699\ufe0f", "blocked": "\u26d4",
    "review": "\U0001f50d", "reviewing": "\U0001f52c", "done": "\u2705",
    "deprecated": "\U0001f5d1\ufe0f",
}
STATUS_ORDER = ["todo", "in-progress", "blocked", "review", "reviewing", "done", "deprecated"]


# ---------- helpers ----------

def _task_number(filename: str) -> int | None:
    m = re.search(r"\(Task\)\s+(\d+)", filename)
    return int(m.group(1)) if m else None


def _task_title(filename: str) -> str:
    name = filename.removesuffix(".md")
    m = re.match(r"\(Task\)\s+\d+\s+(.+)", name)
    return m.group(1) if m else name


def _checkbox_progress(body: str) -> tuple[int, int]:
    checked = len(re.findall(r"- \[x\]", body, re.IGNORECASE))
    unchecked = len(re.findall(r"- \[ \]", body))
    return checked, checked + unchecked


def _recent_log(body: str, n: int = 5) -> list[str]:
    entries: list[str] = []
    in_log = False
    for line in body.split("\n"):
        if "# Task Log" in line:
            in_log = True
            continue
        if in_log:
            if line.startswith("#"):
                break
            if line.strip().startswith("-"):
                entries.append(line.strip())
    return entries[-n:]


# ---------- embeds ----------

def _task_detail_embed(artifact: dict) -> discord.Embed:
    fm = artifact.get("frontmatter", {})
    body = artifact.get("body", "")
    filename = artifact.get("filename", "")
    num = _task_number(filename)
    title = _task_title(filename)
    status = fm.get("status", "unknown")
    priority = fm.get("priority", "")
    increment = fm.get("increment", "").replace("[[", "").replace("]]", "")

    emoji = TASK_STATUS_EMOJI.get(status, "\u2753")
    color = TASK_STATUS_COLORS.get(status, 0x95A5A6)

    lines = [f"**Status:** {emoji} {status}"]
    if priority:
        lines.append(f"**Priority:** {priority}")
    if increment:
        lines.append(f"**Increment:** {increment}")
    checked, total = _checkbox_progress(body)
    if total:
        bar = "\u2588" * checked + "\u2591" * (total - checked)
        lines.append(f"**Progress:** {bar}  {checked}/{total}")

    log = _recent_log(body)
    if log:
        lines.append("\n**Recent Log:**")
        for entry in log:
            lines.append(f"> {entry}")

    embed = discord.Embed(
        title=f"Task #{num}: {title}",
        description="\n".join(lines),
        color=color,
    )
    embed.set_footer(text=f"artifact: {artifact.get('id', '')}")
    return embed


# ---------- views ----------

class _TransitionButton(discord.ui.Button["TaskStatusView"]):
    def __init__(self, label: str, target: str, style: discord.ButtonStyle, artifact_id: str, task_number: int):
        super().__init__(label=label, style=style)
        self.target = target
        self.artifact_id = artifact_id
        self.task_number = task_number

    async def callback(self, interaction: discord.Interaction):
        result = await api_patch(f"/api/artifacts/{self.artifact_id}", {"frontmatter": {"status": self.target}})
        if not result or "error" in result:
            error = result.get("error", "Update failed") if result else "Server unreachable"
            return await interaction.response.send_message(f"Error: {error}", ephemeral=True)
        embed = _task_detail_embed(result)
        view = TaskStatusView(self.artifact_id, self.target, self.task_number)
        await interaction.response.edit_message(embed=embed, view=view)


class TaskStatusView(discord.ui.View):
    """Status transition buttons for task embeds."""

    TRANSITIONS: dict[str, tuple[str, str, discord.ButtonStyle]] = {
        "todo": ("Start", "in-progress", discord.ButtonStyle.primary),
        "in-progress": ("Submit for Review", "review", discord.ButtonStyle.primary),
        "review": ("Mark Done", "done", discord.ButtonStyle.success),
        "blocked": ("Unblock", "in-progress", discord.ButtonStyle.primary),
    }

    def __init__(self, artifact_id: str, current_status: str, task_number: int):
        super().__init__(timeout=300)
        if current_status in self.TRANSITIONS:
            label, target, style = self.TRANSITIONS[current_status]
            self.add_item(_TransitionButton(label, target, style, artifact_id, task_number))


class _TaskCreateModal(discord.ui.Modal, title="Create Task"):
    task_title = discord.ui.TextInput(label="Title", placeholder="Short task title", max_length=100)
    task_description = discord.ui.TextInput(
        label="Description", style=discord.TextStyle.paragraph,
        placeholder="What needs to be done?", max_length=2000, required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        result = await api_post("/api/artifacts", {
            "template": "tmp-proj-task-v0.1",
            "data": {
                "title": self.task_title.value,
                "description": self.task_description.value or self.task_title.value,
                "status": "todo",
            },
        })
        if not result or "error" in result:
            error = result.get("error", "Unknown") if result else "Server unreachable"
            return await interaction.followup.send(f"Failed: {error}", ephemeral=True)
        filename = result.get("filename", "")
        num = _task_number(filename)
        t = _task_title(filename)
        embed = discord.Embed(
            title=f"Task #{num} Created",
            description=f"**{t}**\n\n\U0001f4cb todo",
            color=TASK_STATUS_COLORS["todo"],
        )
        await interaction.followup.send(embed=embed)


# ---------- cog ----------

class TasksCog(commands.GroupCog, name="task", description="Task management"):
    def __init__(self, bot: commands.Bot, state: BotState):
        self.bot = bot
        self.state = state
        self._task_statuses: dict[str, str] = {}
        self._watching = False
        super().__init__()

    async def cog_load(self):
        self.bot.loop.create_task(self._watch_artifact_events())

    # --- /task list ---

    @app_commands.command(name="list", description="List tasks grouped by status")
    @app_commands.describe(status="Filter by status")
    @app_commands.choices(status=[
        app_commands.Choice(name="Todo", value="todo"),
        app_commands.Choice(name="In Progress", value="in-progress"),
        app_commands.Choice(name="Blocked", value="blocked"),
        app_commands.Choice(name="Review", value="review"),
        app_commands.Choice(name="Reviewing", value="reviewing"),
        app_commands.Choice(name="Done", value="done"),
        app_commands.Choice(name="Deprecated", value="deprecated"),
    ])
    async def task_list(self, interaction: discord.Interaction, status: str | None = None):
        await interaction.response.defer()
        params = "?type=task&limit=200"
        if status:
            params += f"&status={status}"
        data = await api_get(f"/api/artifacts{params}")
        if not data:
            return await interaction.followup.send("Cannot reach Flint server.")
        items = data.get("items", [])
        if not items:
            return await interaction.followup.send("No tasks found.")

        groups: dict[str, list[dict]] = {}
        for item in items:
            s = item.get("frontmatter", {}).get("status", "unknown")
            groups.setdefault(s, []).append(item)

        lines: list[str] = []
        for s in STATUS_ORDER:
            tasks = groups.pop(s, None)
            if not tasks:
                continue
            e = TASK_STATUS_EMOJI.get(s, "\u2753")
            lines.append(f"\n{e} **{s.upper()}** ({len(tasks)})")
            for item in tasks[:15]:
                num = _task_number(item.get("filename", ""))
                t = _task_title(item.get("filename", ""))
                p = item.get("frontmatter", {}).get("priority", "")
                lines.append(f"  `#{num}` {t}" + (f" [{p}]" if p else ""))
            if len(tasks) > 15:
                lines.append(f"  *\u2026and {len(tasks) - 15} more*")

        for s, tasks in groups.items():
            lines.append(f"\n\u2753 **{s.upper()}** ({len(tasks)})")
            for item in tasks[:5]:
                num = _task_number(item.get("filename", ""))
                t = _task_title(item.get("filename", ""))
                lines.append(f"  `#{num}` {t}")

        text = "\n".join(lines)
        total = data.get("total", len(items))
        pages = split_pages(text)
        if len(pages) == 1:
            embed = discord.Embed(title=f"Tasks ({total})", description=pages[0], color=0x6B5CE7)
            await interaction.followup.send(embed=embed)
        else:
            view = PaginatorView(pages, title=f"Tasks ({total})", color=0x6B5CE7)
            await interaction.followup.send(embed=view.make_embed(), view=view)

    # --- /task view ---

    @app_commands.command(name="view", description="View a task's full details")
    @app_commands.describe(number="Task number (e.g. 367)")
    async def task_view(self, interaction: discord.Interaction, number: int):
        await interaction.response.defer()
        artifact = await self._find_task(number)
        if not artifact:
            return await interaction.followup.send(f"Task #{number} not found.")
        embed = _task_detail_embed(artifact)
        view = TaskStatusView(artifact["id"], artifact.get("frontmatter", {}).get("status", ""), number)
        await interaction.followup.send(embed=embed, view=view)

    # --- /task create ---

    @app_commands.command(name="create", description="Create a new task")
    async def task_create(self, interaction: discord.Interaction):
        await interaction.response.send_modal(_TaskCreateModal())

    # --- /task launch ---

    @app_commands.command(name="launch", description="Launch an OrbH session to work a task")
    @app_commands.describe(number="Task number to work on")
    async def task_launch(self, interaction: discord.Interaction, number: int):
        await interaction.response.defer()
        artifact = await self._find_task(number)
        if not artifact:
            return await interaction.followup.send(f"Task #{number} not found.")
        title = _task_title(artifact.get("filename", ""))
        path = artifact.get("path", "")
        aid = artifact.get("id", "")

        prompt = (
            f"Load the projects shard before continuing.\n\n"
            f"Load [[hinit-proj]] before starting.\n\n"
            f"Follow the [[hwkfl-proj-do_task]] workflow on task \"{path}\".\n"
            f"Read the task fully, set it to in-progress if needed, implement the work end to end, "
            f"tick checkboxes immediately when complete, and keep the Task Log updated.\n\n"
            f"GOAL: Complete all requirements and move the task to `review` when done.\n"
            f"If you are blocked on a decision or need human input, use a deferred question "
            f"(`flint orbh request`) and set the task to `blocked`.\n"
            f"When the work is complete, move the task to review and provide a concise completion summary."
        ) + DISCORD_SHARD_INSTRUCTION

        session_data = await api_post("/orbh/sessions", {
            "runtime": "claude",
            "prompt": prompt,
            "artifactId": aid,
            "title": f"Doing Task #{number}",
            "description": f"Working on: {title}",
        })
        if not session_data or "session" not in session_data:
            error = session_data.get("error", "Unknown error") if session_data else "Server unreachable"
            return await interaction.followup.send(f"Failed to launch: {error}")

        session = session_data["session"]
        sid = session["id"]

        embed = discord.Embed(
            title=f"Launched: Task #{number}",
            description=f"**{title}**\n\n\u2699\ufe0f Session started\u2026\n\n`{sid}`",
            color=0xFFA500,
        )
        embed.set_footer(text=f"session: {sid}")
        msg = await interaction.followup.send(embed=embed, wait=True)

        thread = None
        channel = interaction.channel
        if isinstance(channel, discord.TextChannel) and msg:
            thread = await msg.create_thread(name=f"Task #{number}: {title[:50]}")

        target = thread or channel
        self.state.tracked_sessions[sid] = {
            "thread": target, "status_msg": msg, "author": interaction.user,
            "thread_id": target.id, "status_msg_id": msg.id, "author_id": interaction.user.id,
        }
        self.state.save()
        self.bot.loop.create_task(poll_session(self.state, sid))

    # --- internals ---

    async def _find_task(self, number: int) -> dict | None:
        data = await api_get(f"/api/artifacts?type=task&search={number}")
        if not data:
            return None
        for item in data.get("items", []):
            if _task_number(item.get("filename", "")) == number:
                return item
        return None

    async def _watch_artifact_events(self):
        """Watch artifact SSE events and post task status change notifications."""
        await self.bot.wait_until_ready()
        if self._watching:
            return
        self._watching = True

        from ..config import FLINT_SERVER_URL, TASKS_CHANNEL_ID

        if not TASKS_CHANNEL_ID:
            return
        channel = self.bot.get_channel(int(TASKS_CHANNEL_ID))
        if not channel:
            return

        import httpx

        while not self.bot.is_closed():
            try:
                async with httpx.AsyncClient(timeout=None) as http:
                    async with http.stream("GET", f"{FLINT_SERVER_URL}/events/stream?channels=artifacts") as resp:
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
                                    payload = json.loads(data_str)
                                except json.JSONDecodeError:
                                    continue
                                if event_type:
                                    payload["event"] = event_type
                                await self._handle_artifact_event(payload, channel)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError):
                pass
            except Exception as e:
                print(f"Artifact SSE error: {e}")
            await asyncio.sleep(5)

    async def _handle_artifact_event(self, data: dict, channel: discord.abc.Messageable):
        event = data.get("event")
        if event == "snapshot":
            for item in data.get("artifacts", []):
                fm = item.get("frontmatter", {})
                if "#proj/task" in fm.get("tags", []):
                    self._task_statuses[item["id"]] = fm.get("status", "")
        elif event == "artifact.updated":
            artifact = data.get("artifact", {})
            fm = artifact.get("frontmatter", {})
            if "#proj/task" not in fm.get("tags", []):
                return
            aid = artifact.get("id", "")
            new_status = fm.get("status", "")
            old_status = self._task_statuses.get(aid)
            self._task_statuses[aid] = new_status
            if old_status and old_status != new_status:
                num = _task_number(artifact.get("filename", ""))
                title = _task_title(artifact.get("filename", ""))
                old_e = TASK_STATUS_EMOJI.get(old_status, "\u2753")
                new_e = TASK_STATUS_EMOJI.get(new_status, "\u2753")
                color = TASK_STATUS_COLORS.get(new_status, 0x95A5A6)
                embed = discord.Embed(
                    description=f"**Task #{num}: {title}**\n\n{old_e} {old_status} \u2192 {new_e} {new_status}",
                    color=color,
                )
                await channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(TasksCog(bot, bot.state))  # type: ignore[attr-defined]
