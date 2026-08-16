from __future__ import annotations

import collections
import math
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .config import settings
from .cty_prefixes import lookup_call
from .db import band_activity_history, connect
from .dxcc import dxcc_name, entity_display_name, geo_region
from .geo import locator_to_latlon, sector_label

MODES = ("ssb", "cw", "digital")
MODE_LABELS = {"ssb": "🎙️ SSB", "cw": "📻 CW", "digital": "💻 DIGITAL"}
STATE_RANK = {"CLOSED": 0, "WATCH": 1, "OPEN": 2, "STRONG": 3}


def _band_row(status: dict[str, Any], band: str) -> dict[str, Any] | None:
    for row in status.get("bands", []) or []:
        if str(row.get("band") or "").lower() == str(band).lower():
            return row
    return None


def _mode_data(row: dict[str, Any], mode: str) -> dict[str, Any]:
    base = row.get("details") or {}
    md = ((base.get("mode_scores") or {}).get(mode) or {})
    if md:
        return md
    if mode == base.get("primary_mode"):
        return {
            "score": int(row.get("score") or 0),
            "state": str(row.get("state") or "CLOSED"),
            "top_sector": row.get("direction_sector"),
            "direction_label": row.get("direction_label") or "unbekannt",
            "dominant_region": base.get("dominant_region"),
            "unique_tx": base.get("unique_tx_total") or 0,
            "unique_rx": base.get("unique_rx_total") or 0,
        }
    return {}


def _history_delta(mode: str, band: str, seconds: int = 1800) -> int | None:
    hist = band_activity_history(hours=max(1, math.ceil(seconds / 3600) + 1), bucket_seconds=300, mode=mode)
    entry = next((x for x in hist.get("bands", []) if str(x.get("band") or "").lower() == band.lower()), None)
    if not entry:
        return None
    points = entry.get("points") or []
    if len(points) < 2:
        return None
    latest_ts = int(points[-1].get("ts") or 0)
    latest_score = int(points[-1].get("score") or 0)
    target = latest_ts - seconds
    candidates = [p for p in points if int(p.get("ts") or 0) <= target]
    base = candidates[-1] if candidates else points[0]
    return latest_score - int(base.get("score") or 0)


def matrix_snapshot(status: dict[str, Any], bands: tuple[str, ...] | list[str]) -> dict[str, Any]:
    rows = []
    for band in bands:
        state_row = _band_row(status, band)
        cells = {}
        for mode in MODES:
            md = _mode_data(state_row or {}, mode)
            cells[mode] = {
                "score": int(md.get("score") or 0),
                "state": str(md.get("state") or "CLOSED"),
                "unique_tx": int(md.get("unique_tx") or 0),
                "unique_rx": int(md.get("unique_rx") or 0),
                "direction_label": str(md.get("direction_label") or "unbekannt"),
            }
        rows.append({"band": band, "modes": cells})
    return {"bands": rows}


def radar_snapshot(status: dict[str, Any], bands: tuple[str, ...] | list[str], limit: int = 8) -> dict[str, Any]:
    items = []
    # Fetch each mode history once; this keeps dashboard/API cost bounded.
    histories = {m: band_activity_history(hours=1, bucket_seconds=300, mode=m) for m in MODES}
    hist_map = {
        m: {str(x.get("band") or "").lower(): x for x in histories[m].get("bands", [])}
        for m in MODES
    }
    for band in bands:
        state_row = _band_row(status, band) or {}
        for mode in MODES:
            md = _mode_data(state_row, mode)
            score = int(md.get("score") or 0)
            state = str(md.get("state") or "CLOSED")
            h = hist_map[mode].get(band.lower()) or {}
            points = h.get("points") or []
            delta = None
            if len(points) >= 2:
                latest_ts = int(points[-1].get("ts") or 0)
                target = latest_ts - 1800
                candidates = [p for p in points if int(p.get("ts") or 0) <= target]
                base = candidates[-1] if candidates else points[0]
                delta = int(points[-1].get("score") or 0) - int(base.get("score") or 0)
            rank = score + STATE_RANK.get(state, 0) * 8 + max(-8, min(12, int(delta or 0)))
            if state == "CLOSED" and score < max(20, settings.watch_score - 10):
                continue
            items.append({
                "band": band,
                "mode": mode,
                "mode_label": MODE_LABELS[mode],
                "score": score,
                "state": state,
                "delta_30m": delta,
                "rank": rank,
                "region": md.get("dominant_region"),
                "direction_label": md.get("direction_label") or "unbekannt",
                "direction_sector": md.get("top_sector"),
                "direction_confidence": md.get("direction_confidence") or "NONE",
                "direction_confidence_pct": int(md.get("direction_confidence_pct") or 0),
                "unique_tx": int(md.get("unique_tx") or 0),
                "unique_rx": int(md.get("unique_rx") or 0),
            })
    items.sort(key=lambda x: (-int(x["rank"]), -int(x["score"]), x["band"], x["mode"]))
    return {"generated_at": int(time.time()), "items": items[:max(1, int(limit))]}


def compass_snapshot(status: dict[str, Any], bands: tuple[str, ...] | list[str], limit: int = 4) -> dict[str, Any]:
    items = []
    for band in bands:
        row = _band_row(status, band) or {}
        for mode in MODES:
            md = _mode_data(row, mode)
            sector = md.get("top_sector")
            conf = int(md.get("direction_confidence_pct") or 0)
            score = int(md.get("score") or 0)
            if sector is None or conf < 25 or score < settings.watch_score:
                continue
            items.append({
                "band": band, "mode": mode, "mode_label": MODE_LABELS[mode],
                "score": score, "state": str(md.get("state") or "CLOSED"),
                "sector": int(sector), "direction_label": md.get("direction_label") or sector_label(int(sector)),
                "confidence_pct": conf, "region": md.get("dominant_region"),
            })
    items.sort(key=lambda x: (-(x["confidence_pct"] + x["score"]), -x["score"]))
    return {"items": items[:max(1, int(limit))]}


def _local_day_start_epoch(now: int) -> int:
    tz = ZoneInfo(settings.dashboard_timezone)
    dt = datetime.fromtimestamp(now, tz)
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


def _mode_source_clause(mode: str) -> tuple[str, tuple[Any, ...]]:
    if mode == "ssb":
        return "source='dxcluster_ssb'", ()
    if mode == "cw":
        return "source='rbn_cw'", ()
    return "source IN ('pskreporter','rbn_ft8')", ()


def _enrichment_for_calls(con, calls: set[str], since: int) -> dict[str, Any]:
    """Return one best geolocated PSK row per callsign.

    V1.12.2 batches calls so large busy-band days cannot exceed SQLite's
    parameter limit.  The supporting source/tx_call/ts index makes this a
    targeted lookup rather than a retained-spot table scan.
    """
    if not calls:
        return {}
    out: dict[str, Any] = {}
    ordered = sorted(calls)
    for pos in range(0, len(ordered), 300):
        batch = ordered[pos:pos + 300]
        placeholders = ",".join("?" for _ in batch)
        rows = con.execute(
            f"""SELECT tx_call,tx_distance_km,azimuth_deg,tx_grid,tx_dxcc,ts
                FROM spots
                WHERE source='pskreporter' AND ts>=? AND tx_call IN ({placeholders})
                  AND tx_distance_km IS NOT NULL
                ORDER BY tx_call, tx_distance_km DESC, ts DESC""",
            (since, *batch),
        ).fetchall()
        for r in rows:
            call = str(r["tx_call"] or "").upper()
            if call and call not in out:
                out[call] = r
    return out


def best_dx_today(mode: str, bands: tuple[str, ...] | list[str], limit: int = 12, now: int | None = None) -> dict[str, Any]:
    """Best DX per mode with a dedicated fast DIGITAL path.

    DIGITAL spots already contain transmitter distance, locator and DXCC, so
    there is no reason to group the full day and then run a second enrichment
    pass for every callsign.  The database now returns only the top-distance
    candidates needed by the dashboard.  SSB/CW retain the enrichment path
    because their source feeds do not normally contain transmitter geometry.
    """
    mode = mode if mode in MODES else "ssb"
    now = int(now or time.time())
    start = _local_day_start_epoch(now)
    band_list = [str(x).lower() for x in bands]
    if not band_list:
        return {"mode": mode, "day_start": start, "generated_at": now, "stations": [], "count": 0, "known_distance_count": 0}
    band_marks = ",".join("?" for _ in band_list)

    if mode == "digital":
        # The dashboard displays at most `limit` rows.  Keep a modest candidate
        # pool so duplicate/invalid rows can be filtered without scanning and
        # materialising every unique digital callsign heard since midnight.
        candidate_limit = max(48, max(1, int(limit)) * 8)
        with connect() as con:
            rows = con.execute(
                f"""SELECT band,UPPER(tx_call) AS tx_call,MAX(ts) AS last_seen,
                           MAX(tx_distance_km) AS direct_distance_km,
                           MAX(tx_dxcc) AS direct_dxcc,MAX(tx_grid) AS tx_grid,
                           MAX(azimuth_deg) AS azimuth_deg
                    FROM spots
                    WHERE ts>=? AND source IN ('pskreporter','rbn_ft8')
                      AND band IN ({band_marks})
                      AND tx_call IS NOT NULL AND tx_call<>''
                      AND tx_distance_km IS NOT NULL
                    GROUP BY band,UPPER(tx_call)
                    ORDER BY direct_distance_km DESC,last_seen DESC
                    LIMIT ?""",
                (start, *band_list, candidate_limit),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for r in rows:
            band = str(r["band"] or "").lower()
            call = str(r["tx_call"] or "").upper()
            try:
                dist = int(round(float(r["direct_distance_km"]))) if r["direct_distance_km"] is not None else None
            except (TypeError, ValueError):
                dist = None
            try:
                az = int(round(float(r["azimuth_deg"]))) if r["azimuth_deg"] is not None else None
            except (TypeError, ValueError):
                az = None
            try:
                dxcc = int(r["direct_dxcc"]) if r["direct_dxcc"] is not None else None
            except (TypeError, ValueError):
                dxcc = None
            grid = str(r["tx_grid"] or "")
            cty = lookup_call(call)
            if dxcc is None and cty and cty.dxcc is not None:
                dxcc = int(cty.dxcc)
            name = dxcc_name(dxcc) or (entity_display_name(cty.entity) if cty else None) or "DX-Station"
            region = None
            if grid:
                try:
                    lat, lon = locator_to_latlon(grid[:8])
                    region = geo_region(lat, lon)
                except ValueError:
                    pass
            if region is None and cty:
                region = {"EU":"Europa","NA":"Nordamerika","SA":"Südamerika","AF":"Afrika","AS":"Asien","OC":"Ozeanien/Pazifik","AN":"Antarktis"}.get(str(cty.continent or "").upper())
            items.append({
                "band": band, "call": call, "name": name, "dxcc": dxcc, "region": region,
                "distance_km": dist, "azimuth_deg": az,
                "direction_label": sector_label(int(round(az / 30) * 30) % 360) if az is not None else "unbekannt",
                "last_seen": int(r["last_seen"] or 0), "mode": mode,
                "location_accuracy": "station-grid" if grid else ("direct-distance" if dist is not None else ("entity-only" if cty else None)),
            })
        items.sort(key=lambda x: (-int(x.get("distance_km") or 0), -int(x.get("last_seen") or 0)))
        return {
            "mode": mode, "day_start": start, "generated_at": now,
            "stations": items[:max(1, int(limit))], "count": len(items),
            "known_distance_count": sum(1 for x in items if x.get("distance_km")),
            "optimized": True,
        }

    clause, params = _mode_source_clause(mode)
    with connect() as con:
        rows = con.execute(
            f"""SELECT band, UPPER(tx_call) AS tx_call, MAX(ts) AS last_seen,
                       MAX(tx_distance_km) AS direct_distance_km,
                       MAX(tx_dxcc) AS direct_dxcc
                FROM spots
                WHERE ts>=? AND {clause}
                  AND band IN ({band_marks})
                  AND tx_call IS NOT NULL AND tx_call<>''
                GROUP BY band, UPPER(tx_call)""",
            (start, *params, *band_list),
        ).fetchall()
        calls = {str(r["tx_call"] or "").upper() for r in rows if r["tx_call"]}
        enrich = _enrichment_for_calls(con, calls, max(0, now - settings.retention_hours * 3600))

    items: list[dict[str, Any]] = []
    for r in rows:
        band = str(r["band"] or "").lower()
        call = str(r["tx_call"] or "").upper()
        e = enrich.get(call)
        if e is not None and e["tx_distance_km"] is not None:
            dist = int(round(float(e["tx_distance_km"])))
            az = int(round(float(e["azimuth_deg"]))) if e["azimuth_deg"] is not None else None
            grid = str(e["tx_grid"] or "")
            dxcc = int(e["tx_dxcc"]) if e["tx_dxcc"] is not None else None
            accuracy = "station-grid" if grid else "psk-distance"
        else:
            dist = int(round(float(r["direct_distance_km"]))) if r["direct_distance_km"] is not None else None
            az = None
            grid = ""
            dxcc = int(r["direct_dxcc"]) if r["direct_dxcc"] is not None else None
            accuracy = "direct-distance" if dist is not None else None
        cty = lookup_call(call)
        if dxcc is None and cty and cty.dxcc is not None:
            dxcc = int(cty.dxcc)
        name = dxcc_name(dxcc) or (entity_display_name(cty.entity) if cty else None) or "DX-Station"
        region = None
        if grid:
            try:
                lat, lon = locator_to_latlon(grid[:8])
                region = geo_region(lat, lon)
            except ValueError:
                pass
        if region is None and cty:
            region = {"EU":"Europa","NA":"Nordamerika","SA":"Südamerika","AF":"Afrika","AS":"Asien","OC":"Ozeanien/Pazifik","AN":"Antarktis"}.get(str(cty.continent or "").upper())
        items.append({
            "band": band, "call": call, "name": name, "dxcc": dxcc, "region": region,
            "distance_km": dist, "azimuth_deg": az,
            "direction_label": sector_label(int(round(az / 30) * 30) % 360) if az is not None else "unbekannt",
            "last_seen": int(r["last_seen"] or 0), "mode": mode,
            "location_accuracy": accuracy or ("entity-only" if cty else None),
        })
    items.sort(key=lambda x: (-int(x.get("distance_km") or 0), -int(x.get("last_seen") or 0)))
    return {
        "mode": mode, "day_start": start, "generated_at": now,
        "stations": items[:max(1, int(limit))], "count": len(items),
        "known_distance_count": sum(1 for x in items if x.get("distance_km")),
    }


def decision_snapshot(status: dict[str, Any], bands: tuple[str, ...] | list[str], mode: str = "ssb") -> dict[str, Any]:
    mode = mode if mode in MODES else "ssb"
    return {
        "generated_at": int(time.time()),
        "matrix": matrix_snapshot(status, bands),
        "radar": radar_snapshot(status, bands),
        "compass": compass_snapshot(status, bands),
        "best_dx": best_dx_today(mode, bands),
    }
