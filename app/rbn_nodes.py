from __future__ import annotations

import asyncio
import json
import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

from .config import settings
from .db import connect, set_health
from .geo import haversine_km, locator_to_latlon

log = logging.getLogger(__name__)
GRID = re.compile(r"\b([A-R]{2}[0-9]{2}(?:[A-X]{2})?)\b", re.I)
CALL = re.compile(r"^[A-Z0-9]{1,4}[0-9][A-Z0-9/]{1,8}$", re.I)


def _normalize_call(call: str) -> str:
    c = call.strip().upper().rstrip(":")
    c = re.sub(r"-(?:#|\d+)$", "", c)
    return c


def parse_node_html(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        text = tr.get_text(" ", strip=True).upper()
        grid_m = GRID.search(text)
        if not grid_m:
            continue
        grid = grid_m.group(1).upper()
        cells = [td.get_text(" ", strip=True).upper() for td in tr.find_all(["td", "th"])]
        tokens: list[str] = []
        for cell in cells:
            tokens.extend(re.split(r"\s+", cell))
        call = next((_normalize_call(t) for t in tokens if CALL.match(_normalize_call(t))), None)
        if call:
            found[call] = grid
    return sorted(found.items())


def parse_node_json(payload: object) -> list[tuple[str, str]]:
    """Parse current RBN JSON node directory rows.

    The RBN endpoint currently returns a top-level list with objects containing
    at least ``call`` and ``grid``. A few wrapper keys and common aliases are
    accepted as a compatibility cushion for future API changes.
    """
    records: object = payload
    if isinstance(payload, dict):
        for key in ("nodes", "data", "results"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                records = candidate
                break
        else:
            records = [payload]

    if not isinstance(records, list):
        return []

    found: dict[str, str] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        raw_call = item.get("call") or item.get("callsign") or item.get("spotter")
        raw_grid = item.get("grid") or item.get("locator") or item.get("maidenhead")
        if not isinstance(raw_call, str) or not isinstance(raw_grid, str):
            continue

        call = _normalize_call(raw_call)
        grid = raw_grid.strip().upper()
        if not CALL.match(call) or not GRID.fullmatch(grid):
            continue
        found[call] = grid

    return sorted(found.items())


def parse_node_payload(text: str, content_type: str = "") -> list[tuple[str, str]]:
    """Parse an RBN node response, preferring JSON with an HTML fallback."""
    stripped = text.lstrip()
    if "json" in content_type.lower() or stripped.startswith(("[", "{")):
        try:
            parsed = parse_node_json(json.loads(text))
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = []
        if parsed:
            return parsed
    return parse_node_html(text)


def sync_rbn_nodes(nodes: list[tuple[str, str, float]]) -> int:
    """Atomically make the local RBN node table match one successful refresh.

    A non-empty directory replaces the previous snapshot in one SQLite
    transaction. Empty input is rejected so a broken/empty upstream response
    can never wipe the last known-good node directory.
    """
    deduped: dict[str, tuple[str, str, float]] = {}
    for call, grid, distance_km in nodes:
        call = _normalize_call(str(call))
        grid = str(grid).strip().upper()
        if CALL.match(call) and GRID.fullmatch(grid):
            deduped[call] = (call, grid, float(distance_km))

    if not deduped:
        raise ValueError("Refusing to replace RBN node directory with an empty set")

    now = int(time.time())
    rows = [(call, grid, distance, now) for call, grid, distance in deduped.values()]
    with connect() as con:
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute("DELETE FROM rbn_nodes")
            con.executemany(
                "INSERT INTO rbn_nodes(callsign,grid,distance_km,updated_at) VALUES(?,?,?,?)",
                rows,
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
    return len(rows)


async def refresh_once() -> int:
    qlat, qlon = locator_to_latlon(settings.qth_locator)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(
            settings.rbn_node_url,
            headers={"User-Agent": f"HAM-Spotter/{settings.callsign}"},
        )
        response.raise_for_status()

    parsed = parse_node_payload(response.text, response.headers.get("content-type", ""))
    nodes: list[tuple[str, str, float]] = []
    for call, grid in parsed:
        try:
            lat, lon = locator_to_latlon(grid)
            nodes.append((call, grid, haversine_km(qlat, qlon, lat, lon)))
        except ValueError:
            continue

    if not nodes:
        raise RuntimeError("RBN node directory parsed, but no callsign/grid pairs were found")

    node_count = sync_rbn_nodes(nodes)
    set_health("rbn_nodes", "LIVE", seen=True)
    log.info("RBN node directory synchronized: %d nodes", node_count)
    return node_count


async def refresh_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await refresh_once()
        except Exception as exc:
            log.exception("RBN node refresh failed")
            set_health("rbn_nodes", "ERROR", error=str(exc))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(300, settings.rbn_node_refresh_minutes * 60))
        except asyncio.TimeoutError:
            pass
