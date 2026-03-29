"""Discord formatting helpers — embeds, pagination, status maps, time."""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from pathlib import Path

import discord

from .config import EMBED_LIMIT

STATUS_COLORS = {
    "queued": 0x95A5A6, "in-progress": 0xFFA500, "blocked": 0xFF6B6B,
    "deferred": 0xFFD93D, "finished": 0x2ECC71, "failed": 0xFF0000,
    "cancelled": 0x95A5A6,
}
STATUS_EMOJI = {
    "queued": "\u23F3", "in-progress": "\u2699\uFE0F", "blocked": "\u26D4",
    "deferred": "\u23F8\uFE0F", "finished": "\u2705", "failed": "\u274C",
    "cancelled": "\u23F9\uFE0F",
}

SESSION_ID_RE = re.compile(r"session: ([0-9a-f\-]{36})")
DISCORD_IMAGE_RE = re.compile(r"```discord-image-(\d+)\s*\n(.+?)\n```", re.DOTALL)


def relative_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        s = int(delta.total_seconds())
        if s < 60:
            return f"{s}s ago"
        if s < 3600:
            return f"{s // 60}m ago"
        if s < 86400:
            return f"{s // 3600}h ago"
        return f"{s // 86400}d ago"
    except (ValueError, TypeError):
        return iso or "unknown"


def split_pages(text: str, limit: int = EMBED_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    pages: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            pages.append(remaining)
            break
        cut = remaining[:limit].rfind("\n\n")
        if cut < limit // 3:
            cut = remaining[:limit].rfind("\n")
        if cut < limit // 3:
            cut = limit
        pages.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    return pages


class PaginatorView(discord.ui.View):
    def __init__(self, pages: list[str], *, session_id: str = "", title: str | None = None, color: int = 0x6B5CE7):
        super().__init__(timeout=None)
        self.pages = pages
        self.current = 0
        self.session_id = session_id
        self.title = title
        self.color = color
        self._update_buttons()

    def _update_buttons(self):
        self.first_btn.disabled = self.current == 0
        self.prev_btn.disabled = self.current == 0
        self.next_btn.disabled = self.current >= len(self.pages) - 1
        self.last_btn.disabled = self.current >= len(self.pages) - 1

    def make_embed(self) -> discord.Embed:
        embed = discord.Embed(description=self.pages[self.current], color=self.color)
        if self.title:
            embed.title = self.title
        parts = []
        if self.session_id:
            parts.append(f"session: {self.session_id}")
        parts.append(f"Page {self.current + 1}/{len(self.pages)}")
        embed.set_footer(text=" | ".join(parts))
        return embed

    @discord.ui.button(label="\u00AB", style=discord.ButtonStyle.secondary)
    async def first_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current = 0
        self._update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="\u2039", style=discord.ButtonStyle.primary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current = max(0, self.current - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="\u203A", style=discord.ButtonStyle.primary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current = min(len(self.pages) - 1, self.current + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="\u00BB", style=discord.ButtonStyle.secondary)
    async def last_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current = len(self.pages) - 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="\u2B73", style=discord.ButtonStyle.secondary)
    async def download_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        full_text = "\n\n".join(self.pages)
        await interaction.response.send_message(
            file=discord.File(fp=io.StringIO(full_text), filename="response.md"),
            ephemeral=True,
        )


def extract_discord_images(text: str) -> tuple[str, list[discord.File]]:
    """Extract discord-image-N fences from text, returning cleaned text and ordered File list."""
    matches = DISCORD_IMAGE_RE.findall(text)
    if not matches:
        return text, []
    # Sort by the numeric index
    ordered = sorted(matches, key=lambda m: int(m[0]))
    files: list[discord.File] = []
    for _, raw_path in ordered:
        path = Path(raw_path.strip())
        if path.is_file():
            files.append(discord.File(str(path)))
    # Strip all discord-image fences from the text
    cleaned = DISCORD_IMAGE_RE.sub("", text).strip()
    return cleaned, files


def extract_session_id(msg: discord.Message) -> str | None:
    for embed in msg.embeds:
        if embed.footer and embed.footer.text:
            m = SESSION_ID_RE.search(embed.footer.text)
            if m:
                return m.group(1)
    return None


async def send_long(
    channel: discord.abc.Messageable,
    text: str,
    *,
    session_id: str = "",
    title: str | None = None,
    color: int = 0x6B5CE7,
    mention: discord.User | discord.Member | None = None,
) -> discord.Message:
    content = mention.mention if mention else None
    pages = split_pages(text)
    if len(pages) == 1:
        embed = discord.Embed(description=pages[0], color=color)
        if title:
            embed.title = title
        if session_id:
            embed.set_footer(text=f"session: {session_id}")
        return await channel.send(content=content, embed=embed)
    view = PaginatorView(pages, session_id=session_id, title=title, color=color)
    return await channel.send(content=content, embed=view.make_embed(), view=view)
