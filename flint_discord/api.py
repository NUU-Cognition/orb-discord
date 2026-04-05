"""Flint server API client — reusable httpx wrappers."""

from __future__ import annotations

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
