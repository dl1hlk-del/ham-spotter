from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

from .config import settings
from .db import save_rbn_nodes, set_health
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


async def refresh_once() -> int:
    qlat, qlon = locator_to_latlon(settings.qth_locator)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(settings.rbn_node_url, headers={"User-Agent": f"HAM-Spotter/{settings.callsign}"})
        response.raise_for_status()
    parsed = parse_node_html(response.text)
    nodes: list[tuple[str, str, float]] = []
    for call, grid in parsed:
        try:
            lat, lon = locator_to_latlon(grid)
            nodes.append((call, grid, haversine_km(qlat, qlon, lat, lon)))
        except ValueError:
            continue
    if not nodes:
        raise RuntimeError("RBN node page parsed, but no callsign/grid pairs were found")
    save_rbn_nodes(nodes)
    set_health("rbn_nodes", "LIVE", seen=True)
    log.info("RBN node directory refreshed: %d nodes", len(nodes))
    return len(nodes)


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
