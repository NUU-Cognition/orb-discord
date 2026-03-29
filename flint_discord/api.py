"""Flint server API client — reusable httpx wrappers."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from .config import FLINT_SERVER_URL


async def api_get(path: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.get(f"{FLINT_SERVER_URL}{path}")
            return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


async def api_post(path: str, body: dict) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            r = await http.post(f"{FLINT_SERVER_URL}{path}", json=body)
            return r.json()
    except httpx.HTTPError:
        return None


async def api_patch(path: str, body: dict) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.patch(f"{FLINT_SERVER_URL}{path}", json=body)
            return r.json() if r.status_code == 200 else r.json()
    except httpx.HTTPError:
        return None


async def api_delete(path: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.delete(f"{FLINT_SERVER_URL}{path}")
            return r.json()
    except httpx.HTTPError:
        return None


async def sse_stream(path: str) -> AsyncIterator[tuple[str | None, str]]:
    """Yield (event_type, data_str) tuples from an SSE endpoint."""
    async with httpx.AsyncClient(timeout=None) as http:
        async with http.stream("GET", f"{FLINT_SERVER_URL}{path}") as resp:
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
                            continue  # heartbeat / comment
                    if data_str:
                        yield event_type, data_str
