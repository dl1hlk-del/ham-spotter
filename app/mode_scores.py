from __future__ import annotations

import collections
import math
import statistics
from typing import Any

from .config import settings
from .dxcc import summarize_psk_rows
from .cty_prefixes import lookup_call
from .geo import sector_label


def _call_prefix(call: str) -> str:
    c = str(call or "").upper().split("/", 1)[0].strip()
    return c


def spotter_region_weight(call: str) -> float:
    """Approximate regional relevance for a human DX-cluster spotter.

    Cluster spots usually do not contain a receiver locator. V1.13 therefore
    derives a coarse, station-independent regional weight from CTY.DAT: same
    DXCC/prefix is strongest, then same ITU/CQ zone, then same continent.
    Exact 325-km filtering remains reserved for PSK Reporter/RBN data that
    actually carries a receiver locator.
    """
    c = _call_prefix(call)
    if not c:
        return 0.0
    home = lookup_call(settings.callsign)
    spotter = lookup_call(c)
    if home and spotter:
        if home.dxcc is not None and spotter.dxcc is not None and home.dxcc == spotter.dxcc:
            return 1.0
        if home.primary_prefix and spotter.primary_prefix and home.primary_prefix == spotter.primary_prefix:
            return 1.0
        if home.continent and spotter.continent and home.continent == spotter.continent:
            if home.itu_zone is not None and spotter.itu_zone is not None and home.itu_zone == spotter.itu_zone:
                return 0.9
            if home.cq_zone is not None and spotter.cq_zone is not None and home.cq_zone == spotter.cq_zone:
                return 0.8
            if home.itu_zone is not None and spotter.itu_zone is not None and abs(home.itu_zone - spotter.itu_zone) <= 2:
                return 0.7
            if home.cq_zone is not None and spotter.cq_zone is not None and abs(home.cq_zone - spotter.cq_zone) <= 1:
                return 0.6
            return 0.35
        return 0.0

    # First-start fallback before CTY.DAT is loaded: prefer obviously similar
    # callsign families without hard-coding any country or QTH.
    home_call = _call_prefix(settings.callsign)
    if not home_call:
        return 0.0
    if c[:2] == home_call[:2]:
        return 1.0
    if c[:1] == home_call[:1]:
        return 0.5
    return 0.2


def re_match_prefix(call: str, prefixes: tuple[str, ...]) -> bool:
    return any(call.startswith(p) for p in prefixes)


def _state_score(score: int, *, can_open: bool) -> str:
    if score >= settings.strong_score and can_open:
        return "STRONG"
    if score >= settings.open_score and can_open:
        return "OPEN"
    if score >= settings.watch_score:
        return "WATCH"
    return "CLOSED"


def _matched_geo(psk_rows: list[Any], target_calls: set[str]) -> dict[str, Any]:
    matched = [r for r in psk_rows if str(r["tx_call"] or "").upper() in target_calls]
    sector_calls: dict[int, set[str]] = collections.defaultdict(set)
    for r in matched:
        if r["sector"] is not None and r["tx_call"]:
            sector_calls[int(r["sector"])].add(str(r["tx_call"]).upper())
    top_sector = None
    top_count = 0
    if sector_calls:
        top_sector, calls = max(sector_calls.items(), key=lambda kv: len(kv[1]))
        top_count = len(calls)
    unique = {str(r["tx_call"] or "").upper() for r in matched if r["tx_call"]}
    coherence = top_count / max(len(unique), 1) if unique else 0.0
    confidence_pct = int(round(100 * (0.7 * coherence + 0.3 * min(1.0, top_count / 5)))) if unique else 0
    reliable = bool(top_sector is not None and top_count >= 2 and confidence_pct >= 40)
    geo = summarize_psk_rows(matched, top_sector) if matched else {}
    distances = [float(r["tx_distance_km"]) for r in matched if r["tx_distance_km"] is not None]
    return {
        "top_sector": top_sector,
        "direction_label": sector_label(top_sector),
        "direction_confidence": "HIGH" if confidence_pct >= 60 else ("MEDIUM" if confidence_pct >= 40 else ("LOW" if top_sector is not None else "NONE")),
        "direction_confidence_pct": confidence_pct,
        "direction_reliable": reliable,
        "top_sector_unique_tx": top_count,
        "dominant_region": geo.get("dominant_region"),
        "countries": geo.get("countries", []),
        "target_median_dx_km": geo.get("target_median_dx_km") or (round(statistics.median(distances)) if distances else 0),
    }


def ssb_mode_score(band: str, rows: list[Any], psk_rows: list[Any], now: int, digital_score: int) -> dict[str, Any]:
    window = max(180, int(settings.ssb_window_seconds))
    current = [r for r in rows if r["source"] == "dxcluster_ssb" and int(r["ts"] or 0) >= now - window]
    previous = [r for r in rows if r["source"] == "dxcluster_ssb" and now - 1800 <= int(r["ts"] or 0) < now - window]

    target_spotters: dict[str, set[str]] = collections.defaultdict(set)
    weighted_spotters: dict[str, float] = {}
    frequencies: dict[str, list[int]] = collections.defaultdict(list)
    for r in current:
        tx = str(r["tx_call"] or "").upper().strip()
        sp = str(r["rx_call"] or "").upper().strip()
        if not tx or not sp:
            continue
        weight = spotter_region_weight(sp)
        if weight <= 0:
            continue
        target_spotters[tx].add(sp)
        weighted_spotters[sp] = max(weighted_spotters.get(sp, 0.0), weight)
        if r["frequency_hz"]:
            frequencies[tx].append(int(r["frequency_hz"]))

    targets = set(target_spotters)
    weighted_rx = sum(weighted_spotters.values())
    confirmations = sum(1 for s in target_spotters.values() if len(s) >= 2)

    target_goal = {
        "23cm": 1, "70cm": 2, "2m": 3, "4m": 2,
        "6m":3,"10m":6,"12m":5,"15m":7,"17m":6,"20m":10,"40m":8,"60m":3,"80m":6
    }.get(band,6)
    tx_points = min(38.0, 38.0 * len(targets) / max(target_goal, 1))
    rx_points = min(27.0, 27.0 * weighted_rx / 4.0)
    confirm_points = min(15.0, confirmations * 5.0)

    prev_targets = {
        str(r["tx_call"] or "").upper().strip()
        for r in previous
        if r["tx_call"] and spotter_region_weight(str(r["rx_call"] or "")) > 0
    }
    cur_rate = len(targets) / max(window / 60.0, 1.0)
    prev_rate = len(prev_targets) / max((1800 - window) / 60.0, 1.0)
    trend_ratio = (cur_rate + 0.2) / (prev_rate + 0.2)
    trend_points = 10.0 if trend_ratio >= 2.0 else (6.0 if trend_ratio >= 1.25 else (3.0 if trend_ratio >= 0.8 else 0.0))

    # Digital activity is only supporting evidence. Real SSB cluster reports
    # still account for at least 90% of the attainable score.
    support_points = min(10.0, max(0.0, float(digital_score) - 35.0) / 6.5) if targets else 0.0
    score = int(round(min(100.0, tx_points + rx_points + confirm_points + trend_points + support_points)))
    if band in {"4m", "2m", "70cm", "23cm"}:
        # VHF/UHF/SHF cluster traffic is naturally much sparser.  A single
        # station is not enough by itself; require either two independent DX
        # calls or the same call confirmed by multiple regional spotters.
        can_open = bool((len(targets) >= 2 and weighted_rx >= 1.0) or confirmations >= 1)
    else:
        can_open = bool(len(targets) >= 2 and (weighted_rx >= 1.5 or confirmations >= 1))
    geo = _matched_geo(psk_rows, targets)

    top_calls = []
    for tx in sorted(targets, key=lambda c: (-len(target_spotters[c]), c))[:8]:
        freq = round(statistics.median(frequencies.get(tx, [0])) / 1000.0, 1) if frequencies.get(tx) else None
        top_calls.append({"call": tx, "spotters": len(target_spotters[tx]), "frequency_khz": freq})

    return {
        "mode": "ssb",
        "score": score,
        "state": _state_score(score, can_open=can_open),
        "can_open": can_open,
        "unique_tx": len(targets),
        "unique_rx": len(weighted_spotters),
        "weighted_rx": round(weighted_rx, 2),
        "confirmed_tx": confirmations,
        "trend_ratio": round(trend_ratio, 2),
        "top_calls": top_calls,
        **geo,
    }


def cw_mode_score(band: str, rows: list[Any], psk_rows: list[Any], now: int) -> dict[str, Any]:
    window = settings.windows_seconds.get(band, 300)
    current = [r for r in rows if r["source"] == "rbn_cw" and int(r["ts"] or 0) >= now - window]
    previous = [r for r in rows if r["source"] == "rbn_cw" and now - 1800 <= int(r["ts"] or 0) < now - window]
    targets = {str(r["tx_call"] or "").upper() for r in current if r["tx_call"]}
    skimmers = {str(r["rx_call"] or "").upper() for r in current if r["rx_call"]}
    target_skimmers: dict[str, set[str]] = collections.defaultdict(set)
    for r in current:
        if r["tx_call"] and r["rx_call"]:
            target_skimmers[str(r["tx_call"]).upper()].add(str(r["rx_call"]).upper())
    confirmations = sum(1 for x in target_skimmers.values() if len(x) >= 2)
    goal = {
        "23cm": 2, "70cm": 2, "2m": 3, "4m": 2,
        "6m":3,"10m":8,"12m":6,"15m":8,"17m":8,"20m":12,"40m":10,"60m":5,"80m":8
    }.get(band,8)
    tx_points = min(45.0, 45.0 * len(targets) / goal)
    rx_points = min(30.0, 30.0 * len(skimmers) / 4.0)
    confirm_points = min(15.0, confirmations * 3.0)
    prev_targets = {str(r["tx_call"] or "").upper() for r in previous if r["tx_call"]}
    cur_rate = len(targets) / max(window / 60.0, 1.0)
    prev_rate = len(prev_targets) / max((1800-window)/60.0,1.0)
    trend_ratio = (cur_rate + 0.2) / (prev_rate + 0.2)
    trend_points = 10.0 if trend_ratio >= 2 else (6.0 if trend_ratio >= 1.25 else 2.0)
    score = int(round(min(100.0, tx_points + rx_points + confirm_points + trend_points)))
    if band in {"4m", "2m", "70cm", "23cm"}:
        can_open = bool((len(targets) >= 2 and len(skimmers) >= 1) or confirmations >= 1)
    else:
        can_open = bool(len(targets) >= 3 and len(skimmers) >= 2)
    geo = _matched_geo(psk_rows, targets)
    return {
        "mode":"cw", "score":score, "state":_state_score(score, can_open=can_open), "can_open":can_open,
        "unique_tx":len(targets), "unique_rx":len(skimmers), "confirmed_tx":confirmations,
        "trend_ratio":round(trend_ratio,2), **geo,
    }
