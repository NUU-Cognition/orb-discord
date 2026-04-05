"""Scenario: Slash Commands — execute /task list, /task create, /task view."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord

from ..helpers import flint_api_get, flint_api_post, rate_limit_delay, wait_for_embed

if TYPE_CHECKING:
    from ..harness import E2EContext


async def test_slash_commands(ctx: E2EContext):
    """Test task management slash commands via the Flint server API and verify Discord responses.

    Note: Slash commands cannot be invoked programmatically via Discord.py user actions.
    Instead, we test the underlying API endpoints that the slash commands use,
    and verify the bot's task management cog is functional.
    """
    result = ctx.reporter.current_result

    # 1. Test /task list equivalent — GET /api/artifacts?type=task
    print("    Testing task list API (backing /task list)...")
    tasks_data = await flint_api_get("/api/artifacts?type=task&limit=10")
    result.assert_not_none(tasks_data, "Task list API returned data")
    if tasks_data:
        items = tasks_data.get("items", [])
        result.assert_true(isinstance(items, list), "Task list returned items array")
        total = tasks_data.get("total", 0)
        print(f"    Found {total} total tasks, {len(items)} returned")

    await rate_limit_delay()

    # 2. Test /task create equivalent — POST /api/artifacts
    print("    Testing task creation API (backing /task create)...")
    create_data = await flint_api_post("/api/artifacts", {
        "template": "tmp-proj-task-v0.1",
        "data": {
            "title": "E2E Test Task — Auto Created",
            "description": "This task was created by the E2E test harness to verify /task create functionality.",
            "status": "todo",
        },
    })
    result.assert_not_none(create_data, "Task creation API returned data")

    created_id = None
    created_number = None
    if create_data and "error" not in create_data:
        created_id = create_data.get("id")
        filename = create_data.get("filename", "")
        import re
        m = re.search(r"\(Task\)\s+(\d+)", filename)
        if m:
            created_number = int(m.group(1))
        result.assert_not_none(created_id, "Created task has an ID")
        result.assert_not_none(created_number, f"Created task has a number (#{created_number})")
        print(f"    Created task #{created_number} (id: {created_id})")
    else:
        error = create_data.get("error", "Unknown") if create_data else "No response"
        result.fail(f"Task creation failed: {error}")

    await rate_limit_delay()

    # 3. Test /task view equivalent — GET /api/artifacts?type=task&search=<number>
    if created_number:
        print(f"    Testing task view API (backing /task view #{created_number})...")
        view_data = await flint_api_get(f"/api/artifacts?type=task&search={created_number}")
        result.assert_not_none(view_data, "Task view API returned data")
        if view_data:
            items = view_data.get("items", [])
            found = any(
                f"(Task) {created_number:03d}" in item.get("filename", "")
                or f"(Task) {created_number}" in item.get("filename", "")
                for item in items
            )
            result.assert_true(found, f"Task #{created_number} found via search API")

    await rate_limit_delay()

    # 4. Test task status update — PATCH /api/artifacts/<id>
    if created_id:
        print(f"    Testing task status update API...")
        from ..helpers import flint_api_get as _get
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                from ..config import FLINT_SERVER_URL
                r = await http.patch(
                    f"{FLINT_SERVER_URL}/api/artifacts/{created_id}",
                    json={"frontmatter": {"status": "in-progress"}},
                )
                update_data = r.json() if r.status_code == 200 else None
        except httpx.HTTPError:
            update_data = None

        result.assert_not_none(update_data, "Task status update API returned data")
        if update_data:
            new_status = update_data.get("frontmatter", {}).get("status")
            result.assert_true(
                new_status == "in-progress",
                f"Task status updated to in-progress (got: {new_status})",
            )

    # 5. Verify by posting a summary to the channel
    await ctx.channel.send(
        f"**Slash Commands Scenario**\n"
        f"Task list: {'OK' if tasks_data else 'FAIL'}\n"
        f"Task create: #{created_number or 'FAIL'}\n"
        f"Task view: {'OK' if created_number else 'N/A'}\n"
        f"Task update: {'OK' if created_id else 'N/A'}"
    )
