"""Orbh server API client — reusable httpx wrappers."""

from __future__ import annotations

import httpx

from . import config


async def api_get(path: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.get(f"{config.FLINT_SERVER_URL}{path}")
            return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


async def api_post(path: str, body: dict) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            r = await http.post(f"{config.FLINT_SERVER_URL}{path}", json=body)
            return r.json()
    except httpx.HTTPError:
        return None


async def api_patch(path: str, body: dict) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.patch(f"{config.FLINT_SERVER_URL}{path}", json=body)
            return r.json() if r.status_code == 200 else r.json()
    except httpx.HTTPError:
        return None


async def api_delete(path: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.delete(f"{config.FLINT_SERVER_URL}{path}")
            return r.json()
    except httpx.HTTPError:
        return None
