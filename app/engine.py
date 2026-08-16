from __future__ import annotations

import asyncio
import collections
import logging
import statistics
import time
from typing import Any

from .config import settings
from .db import cleanup_old_spots, get_band_rows, get_band_state, set_band_state, sync_opening_event
from .geo import sector_label
from .dxcc import summarize_psk_rows
from .telegram import Telegram
from .formatting import band_detail_text
from .rarity import record_rows
from .mode_scores import cw_mode_score, ssb_mode_score

log = logging.getLogger(__name__)


def _unique_calls(rows, source_prefix: str | None = None) -> set[str]:
    out = set()
    for r in rows:
        if source_prefix and not str(r["source"]).startswith(source_prefix):
            continue
        if r["tx_call"]:
            out.add(str(r["tx_call"]).upper())
    return out


def _unique_rx(rows, source_prefix: str | None = None) -> set[str]:
    out = set()
    for r in rows:
        if source_prefix and not str(r["source"]).startswith(source_prefix):
            continue
        if r["rx_call"]:
            out.add(str(r["rx_call"]).upper())
    return out


def analyse_band(band: str, now: int | None = None) -> tuple[str, int, int | None, str, dict[str, Any]]:
    now = now or int(time.time())
    window = settings.windows_seconds.get(band, 300)
    # Pull 30 min so current activity can be compared with prior local baseline.
    rows = get_band_rows(band, now - 1800)
    current = [r for r in rows if r["ts"] >= now - window]
    previous = [r for r in rows if now - 1800 <= r["ts"] < now - window]

    min_dx = settings.min_dx_km.get(band, 1000)
    psk_current = [
        r for r in current
        if r["source"] == "pskreporter" and r["tx_distance_km"] is not None and float(r["tx_distance_km"]) >= min_dx
    ]
    # DIGITAL uses PSK Reporter plus the FT8 RBN stream only. CW is scored separately.
    rbn_current = [r for r in current if str(r["source"]) == "rbn_ft8"]

    psk_prev = [
        r for r in previous
        if r["source"] == "pskreporter" and r["tx_distance_km"] is not None and float(r["tx_distance_km"]) >= min_dx
    ]
    rbn_prev = [r for r in previous if str(r["source"]) == "rbn_ft8"]

    psk_tx = _unique_calls(psk_current)
    rbn_tx = _unique_calls(rbn_current)
    all_tx = psk_tx | rbn_tx
    psk_rx = _unique_rx(psk_current)
    rbn_rx = _unique_rx(rbn_current)
    all_rx = psk_rx | rbn_rx

    # Unique station counts, not raw spot counts, drive the score to reduce contest inflation.
    tx_target = {
        "4m": 5, "2m": 5, "70cm": 4, "23cm": 3,
        "6m": 8, "10m": 12, "12m": 10, "15m": 12, "17m": 12,
        "20m": 16, "40m": 14, "60m": 8, "80m": 10,
    }.get(band, 12)
    rx_target = {
        "4m": 2, "2m": 2, "70cm": 2, "23cm": 2,
        "6m": 3, "10m": 4, "12m": 4, "15m": 4, "17m": 4,
        "20m": 5, "40m": 4, "60m": 3, "80m": 4,
    }.get(band, 4)
    tx_score = min(25.0, 25.0 * len(all_tx) / tx_target)
    rx_score = min(20.0, 20.0 * len(all_rx) / rx_target)

    cur_rate = len(all_tx) / max(window / 60.0, 1.0)
    prev_calls = _unique_calls(psk_prev) | _unique_calls(rbn_prev)
    prev_minutes = max((1800 - window) / 60.0, 1.0)
    prev_rate = len(prev_calls) / prev_minutes
    trend_ratio = (cur_rate + 0.25) / (prev_rate + 0.25)
    if trend_ratio >= 4:
        trend_score = 15.0
    elif trend_ratio >= 2:
        trend_score = 12.0
    elif trend_ratio >= 1.25:
        trend_score = 8.0
    elif trend_ratio >= 0.8:
        trend_score = 4.0
    else:
        trend_score = 0.0

    distances = [float(r["tx_distance_km"]) for r in psk_current if r["tx_distance_km"] is not None]
    median_dx = statistics.median(distances) if distances else 0.0
    distance_score = min(10.0, 10.0 * median_dx / max(min_dx * 2.0, 1.0)) if distances else 0.0

    sector_calls: dict[int, set[str]] = collections.defaultdict(set)
    for r in psk_current:
        if r["sector"] is not None and r["tx_call"]:
            sector_calls[int(r["sector"])].add(str(r["tx_call"]).upper())
    top_sector = None
    top_count = 0
    if sector_calls:
        top_sector, calls = max(sector_calls.items(), key=lambda item: len(item[1]))
        top_count = len(calls)
    coherence = top_count / max(len(psk_tx), 1) if psk_tx else 0.0
    direction_score = 10.0 * coherence if psk_tx else 0.0

    # Direction confidence combines concentration (coherence) with the amount
    # of independent evidence in the leading 30-degree sector. This prevents
    # a 1-of-3 split from becoming a rotor/Yagi recommendation.
    evidence = min(1.0, top_count / 8.0) if top_count else 0.0
    direction_confidence_pct = int(round(100.0 * (0.70 * coherence + 0.30 * evidence))) if psk_tx else 0
    if direction_confidence_pct >= 60:
        direction_confidence = "HIGH"
    elif direction_confidence_pct >= 40:
        direction_confidence = "MEDIUM"
    elif top_sector is not None:
        direction_confidence = "LOW"
    else:
        direction_confidence = "NONE"
    direction_reliable = bool(top_sector is not None and top_count >= 3 and direction_confidence_pct >= 40)

    snrs = [float(r["snr"]) for r in psk_current if r["snr"] is not None]
    best_snr = max(snrs) if snrs else None
    signal_score = 0.0 if best_snr is None else max(0.0, min(5.0, (best_snr + 20.0) / 4.0))

    # Independent RBN confirmation. It can strengthen an opening but does not invent a direction.
    if psk_tx and rbn_tx and len(rbn_rx) >= 2:
        source_score = 15.0
    elif psk_tx and rbn_tx:
        source_score = 10.0
    elif rbn_tx:
        source_score = min(8.0, len(rbn_tx) / 3.0)
    else:
        source_score = 0.0

    digital_score = int(round(min(100.0, tx_score + rx_score + trend_score + distance_score + direction_score + signal_score + source_score)))
    geo = summarize_psk_rows(psk_current, top_sector)
    # Persist one compact DXCC/day memory independent of the 72h raw-spot retention.
    record_rows(band, psk_current)

    digital_details = {
        "mode": "digital",
        "score": digital_score,
        "state": "CLOSED",
        "can_open": bool(psk_tx),
        "window_seconds": window,
        "psk_unique_tx": len(psk_tx),
        "psk_unique_rx": len(psk_rx),
        "rbn_unique_tx": len(rbn_tx),
        "rbn_unique_rx": len(rbn_rx),
        "unique_tx": len(all_tx),
        "unique_rx": len(all_rx),
        "trend_ratio": round(trend_ratio, 2),
        "median_dx_km": round(median_dx),
        "best_snr": best_snr,
        "top_sector": top_sector,
        "direction_label": sector_label(top_sector),
        "direction_coherence": round(coherence, 2),
        "direction_confidence": direction_confidence,
        "direction_confidence_pct": direction_confidence_pct,
        "direction_reliable": direction_reliable,
        "top_sector_unique_tx": top_count,
        "min_dx_km": min_dx,
        "dominant_region": geo.get("dominant_region"),
        "dominant_region_calls": geo.get("dominant_region_calls", 0),
        "countries": geo.get("countries", []),
        "country_basis": geo.get("country_basis"),
        "target_median_dx_km": geo.get("target_median_dx_km", 0),
        "target_sector_share": geo.get("target_sector_share", 0.0),
        "country_scope": geo.get("country_scope"),
    }

    # Separate real operating-mode scores. SSB is based on human DX-cluster
    # reports from CTY-derived regional spotters around the configured station; CW uses local RBN skimmers.
    ssb = ssb_mode_score(band, rows, psk_current, now, digital_score)
    cw = cw_mode_score(band, rows, psk_current, now)
    modes = {"digital": digital_details, "cw": cw, "ssb": ssb}

    # Derive a state for the digital score before mode selection.
    def raw_state(value: int, can_open: bool) -> str:
        if value >= settings.strong_score and can_open:
            return "STRONG"
        if value >= settings.open_score and can_open:
            return "OPEN"
        if value >= settings.watch_score:
            return "WATCH"
        return "CLOSED"
    digital_details["state"] = raw_state(digital_score, bool(psk_tx))

    primary = settings.primary_prop_mode if settings.primary_prop_mode in modes else "ssb"
    selected = modes[primary]
    score = int(selected.get("score") or 0)
    can_open = bool(selected.get("can_open"))

    old = get_band_state(band)
    old_state = old["state"] if old else "CLOSED"
    # Hysteresis applies to the selected primary operating mode.
    if old_state == "STRONG" and score >= settings.strong_score - 8 and can_open:
        state = "STRONG"
    elif old_state in {"OPEN", "STRONG"} and score >= settings.open_score - 8 and can_open:
        state = "OPEN"
    else:
        state = raw_state(score, can_open)

    selected["state"] = state
    selected_sector = selected.get("top_sector")
    selected_label = selected.get("direction_label") or sector_label(selected_sector)

    # Preserve legacy detail keys for history/Telegram while exposing all three
    # mode engines to the dashboard/API.
    details = dict(selected)
    details["primary_mode"] = primary
    details["mode_scores"] = modes
    details.setdefault("psk_unique_tx", len(psk_tx))
    details.setdefault("psk_unique_rx", len(psk_rx))
    details.setdefault("rbn_unique_tx", len(rbn_tx))
    details.setdefault("rbn_unique_rx", len(rbn_rx))
    details.setdefault("unique_tx_total", int(selected.get("unique_tx") or 0))
    details.setdefault("unique_rx_total", int(selected.get("unique_rx") or 0))
    details.setdefault("top_sector", selected_sector)
    details.setdefault("direction_label", selected_label)
    details.setdefault("direction_confidence", selected.get("direction_confidence") or "NONE")
    details.setdefault("direction_confidence_pct", int(selected.get("direction_confidence_pct") or 0))
    details.setdefault("direction_reliable", bool(selected.get("direction_reliable")))
    details.setdefault("top_sector_unique_tx", int(selected.get("top_sector_unique_tx") or 0))
    details.setdefault("dominant_region", selected.get("dominant_region"))
    details.setdefault("countries", selected.get("countries") or [])
    details.setdefault("target_median_dx_km", selected.get("target_median_dx_km") or 0)
    details["digital_context_score"] = digital_score

    return state, score, selected_sector, selected_label, details


def alert_text(band: str, state: str, score: int, direction: str, details: dict[str, Any]) -> str:
    return band_detail_text(
        band,
        state,
        score,
        direction,
        details,
        qth=settings.qth_locator,
        callsign=settings.callsign,
    )


async def engine_loop(stop_event: asyncio.Event, telegram: Telegram) -> None:
    cleanup_counter = 0
    while not stop_event.is_set():
        now = int(time.time())
        for band in settings.bands:
            try:
                state, score, sector, label, details = analyse_band(band, now)
                old = get_band_state(band)
                old_state = old["state"] if old else "CLOSED"
                set_band_state(band, state, score, sector, label, details)
                sync_opening_event(band, state, score, sector, label, details, now=now)
                if state in {"OPEN", "STRONG"}:
                    msg = alert_text(band, state, score, label, details)
                    if state != old_state or old_state in {"OPEN", "STRONG"}:
                        # Only use bearing changes as an alert trigger when the
                        # current direction is sufficiently supported.
                        alert_sector = sector if details.get("direction_reliable") else None
                        await telegram.maybe_alert(band, state, score, alert_sector, msg)
            except Exception:
                log.exception("Band analysis failed for %s", band)
        cleanup_counter += 1
        if cleanup_counter >= max(1, 3600 // max(settings.analyse_interval_seconds, 1)):
            cleanup_old_spots()
            cleanup_counter = 0
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.analyse_interval_seconds)
        except asyncio.TimeoutError:
            pass
