from __future__ import annotations

import collections
import json
import time
from typing import Any

from .config import settings
from .cty_prefixes import lookup_call
from .db import connect
from .dxcc import dxcc_name, entity_display_name, geo_region
from .geo import locator_to_latlon, sector_label
from .space_weather import snapshot as space_weather_snapshot

VHF_BANDS = ("4m", "2m", "70cm", "23cm")
_METEOR_MODES = {"MSK144", "FSK441", "JT6M"}


def _label(score: int, levels: tuple[tuple[int, str], ...]) -> str:
    for threshold, name in sorted(levels, reverse=True):
        if score >= threshold:
            return name
    return levels[-1][1]


def _rows(since: int, bands: tuple[str, ...] = VHF_BANDS):
    placeholders = ",".join("?" for _ in bands)
    with connect() as con:
        return con.execute(
            f"SELECT * FROM spots WHERE ts>=? AND band IN ({placeholders}) ORDER BY ts DESC",
            (since, *bands),
        ).fetchall()


def _mode_name(raw: Any) -> str:
    return str(raw or "").upper().replace(" ", "")


def tropo_evidence(now: int | None = None, minutes: int = 90) -> dict[str, Any]:
    """Return conservative, observation-based tropo evidence.

    V1.12.1 calibration rules:
    - Explicit meteor-scatter modes never contribute to tropo.
    - Extremely long single-band paths are reported separately and do not
      automatically count as tropospheric propagation.
    - A large number of transient reports on only one band is not enough for
      "wahrscheinlich".  Temporal persistence or multi-band corroboration is
      required; "stark" requires both.
    """
    now = int(now or time.time())
    window_minutes = max(30, minutes)
    rows = _rows(now - window_minutes * 60, ("2m", "70cm", "23cm"))

    min_km = {"2m": 300.0, "70cm": 200.0, "23cm": 150.0}
    # Conservative plausibility guard for this inference layer.  Paths beyond
    # these limits can be real, but the spot stream alone is not enough to call
    # them tropo; they remain visible as "extreme" evidence for inspection.
    plausible_max_km = {"2m": 2200.0, "70cm": 1800.0, "23cm": 1500.0}

    distance_rows = [
        r for r in rows
        if r["source"] == "pskreporter"
        and r["tx_distance_km"] is not None
        and float(r["tx_distance_km"]) >= min_km.get(str(r["band"]), 999999)
    ]
    meteor_rows = [r for r in distance_rows if _mode_name(r["mode"]) in _METEOR_MODES]
    non_meteor = [r for r in distance_rows if _mode_name(r["mode"]) not in _METEOR_MODES]
    extreme_rows = [
        r for r in non_meteor
        if float(r["tx_distance_km"]) > plausible_max_km.get(str(r["band"]), 0)
    ]
    qualifying = [
        r for r in non_meteor
        if float(r["tx_distance_km"]) <= plausible_max_km.get(str(r["band"]), 0)
    ]

    by_band: dict[str, dict[str, Any]] = {}
    all_tx, all_rx = set(), set()
    active_bands = 0
    max_distance = 0
    persistence_bands = 0

    # Persistence means that the band contains qualifying reports in the most
    # recent 30 minutes AND in the preceding part of the analysis window.
    recent_cut = now - 30 * 60
    window_start = now - window_minutes * 60

    for band in ("2m", "70cm", "23cm"):
        br = [r for r in qualifying if str(r["band"]) == band]
        tx = {str(r["tx_call"] or "").upper() for r in br if r["tx_call"]}
        rx = {str(r["rx_call"] or "").upper() for r in br if r["rx_call"]}
        cur = {
            str(r["tx_call"] or "").upper() for r in br
            if int(r["ts"] or 0) >= recent_cut and r["tx_call"]
        }
        old = {
            str(r["tx_call"] or "").upper() for r in br
            if window_start <= int(r["ts"] or 0) < recent_cut and r["tx_call"]
        }
        dmax = max(
            [int(round(float(r["tx_distance_km"]))) for r in br if r["tx_distance_km"] is not None]
            or [0]
        )
        persistent = bool(cur and old)
        if tx:
            active_bands += 1
        if persistent:
            persistence_bands += 1
        max_distance = max(max_distance, dmax)
        all_tx |= tx
        all_rx |= rx
        by_band[band] = {
            "unique_tx": len(tx),
            "unique_rx": len(rx),
            "max_distance_km": dmax,
            "persistent": persistent,
        }

    # Build a raw score from corroboration, station diversity and persistence.
    score = 0
    score += min(30, active_bands * 12)
    score += min(18, len(all_tx) * 2)
    score += min(12, len(all_rx) * 2)
    score += min(32, persistence_bands * 16)
    if by_band["70cm"]["unique_tx"]:
        score += 6
    if by_band["23cm"]["unique_tx"]:
        score += 10
    if max_distance >= 800:
        score += 5
    elif max_distance >= 500:
        score += 3
    elif max_distance >= 300:
        score += 1

    # Gating is the important V1.12.1 calibration: transient single-band
    # activity can remain visible but cannot become a probable tropo opening.
    if active_bands == 0:
        score = 0
    elif persistence_bands == 0 and active_bands == 1:
        score = min(score, 20)
    elif persistence_bands == 0:
        score = min(score, 59)
    elif active_bands == 1:
        score = min(score, 69)
    score = min(100, score)

    if score >= 70 and persistence_bands >= 1 and active_bands >= 2:
        label = "stark"
    elif score >= 45 and (persistence_bands >= 1 or active_bands >= 2):
        label = "wahrscheinlich"
    elif score >= 25:
        label = "möglich"
    elif active_bands or extreme_rows:
        label = "keine stabilen Hinweise"
    else:
        label = "keine Hinweise"

    observed_max = max(
        [int(round(float(r["tx_distance_km"]))) for r in non_meteor if r["tx_distance_km"] is not None]
        or [0]
    )
    extreme_max = max(
        [int(round(float(r["tx_distance_km"]))) for r in extreme_rows if r["tx_distance_km"] is not None]
        or [0]
    )

    return {
        "score": score,
        "label": label,
        "unique_tx": len(all_tx),
        "unique_rx": len(all_rx),
        "active_bands": active_bands,
        "persistent_bands": persistence_bands,
        "max_distance_km": max_distance,
        "observed_max_distance_km": observed_max,
        "bands": by_band,
        "excluded_meteor_reports": len(meteor_rows),
        "excluded_extreme_paths": len(extreme_rows),
        "extreme_max_distance_km": extreme_max,
        "plausible_max_km": {k: int(v) for k, v in plausible_max_km.items()},
        "basis": (
            "Konservative Tropo-Wertung aus 2 m / 70 cm / 23 cm: Meteor-Modi sind ausgeschlossen; "
            "extreme Einzelpfade werden separat markiert. Wahrscheinlich/stark erfordert Persistenz "
            "und/oder Mehrband-Bestätigung."
        ),
    }


def sporadic_e_evidence(now: int | None = None, minutes: int = 45) -> dict[str, Any]:
    now = int(now or time.time())
    rows = _rows(now - max(15, minutes) * 60, ("4m", "2m"))
    qualifying = [
        r for r in rows
        if r["source"] == "pskreporter" and r["tx_distance_km"] is not None
        and 700 <= float(r["tx_distance_km"]) <= 2300
        and _mode_name(r["mode"]) not in _METEOR_MODES
    ]
    tx = {str(r["tx_call"] or "").upper() for r in qualifying if r["tx_call"]}
    rx = {str(r["rx_call"] or "").upper() for r in qualifying if r["rx_call"]}
    bands = sorted({str(r["band"] or "") for r in qualifying})
    sectors: dict[int, set[str]] = collections.defaultdict(set)
    for r in qualifying:
        if r["sector"] is not None and r["tx_call"]:
            sectors[int(r["sector"])].add(str(r["tx_call"]).upper())
    top_sector = None
    top_count = 0
    if sectors:
        top_sector, calls = max(sectors.items(), key=lambda kv: len(kv[1]))
        top_count = len(calls)
    score = min(100, len(tx) * 8 + len(rx) * 6 + len(bands) * 12 + min(20, top_count * 4))
    # One isolated report is a hint, not an Es diagnosis.
    if len(tx) < 2:
        score = min(score, 24)
    return {
        "score": score,
        "label": _label(score, ((70, "starke Hinweise"), (45, "deutliche Hinweise"), (25, "möglich"), (0, "keine Hinweise"))),
        "unique_tx": len(tx), "unique_rx": len(rx), "bands": bands,
        "top_sector": top_sector, "direction_label": sector_label(top_sector),
        "basis": "Distanz-/Aktivitätsmuster auf 4 m und 2 m; als Hinweis, nicht als sichere Es-Bestimmung.",
    }


def meteor_scatter_activity(now: int | None = None, minutes: int = 60) -> dict[str, Any]:
    now = int(now or time.time())
    rows = _rows(now - max(15, minutes) * 60, ("4m", "2m", "70cm"))
    ms = [r for r in rows if r["source"] == "pskreporter" and _mode_name(r["mode"]) in _METEOR_MODES]
    tx = {str(r["tx_call"] or "").upper() for r in ms if r["tx_call"]}
    rx = {str(r["rx_call"] or "").upper() for r in ms if r["rx_call"]}
    by_mode = collections.Counter(_mode_name(r["mode"]) for r in ms)
    by_band = collections.Counter(str(r["band"] or "") for r in ms)
    score = min(100, len(tx) * 10 + len(rx) * 7 + min(20, len(ms)))
    return {
        "score": score,
        "label": "aktiv" if len(tx) >= 2 else ("einzelne Hinweise" if tx else "keine MS-Spots"),
        "unique_tx": len(tx), "unique_rx": len(rx), "reports": len(ms),
        "modes": dict(by_mode), "bands": dict(by_band),
        "basis": "Nur explizite PSK-Reporter-Modi MSK144/FSK441/JT6M werden als Meteor-Scatter gezählt.",
    }


def aurora_potential(now: int | None = None) -> dict[str, Any]:
    now = int(now or time.time())
    weather = space_weather_snapshot()
    kp = float(weather.get("kp") or 0)
    g = int(weather.get("g_scale") or 0)
    bz = weather.get("bz_nt")
    score = 0
    if kp >= 8:
        score = 90
    elif kp >= 7:
        score = 78
    elif kp >= 6:
        score = 65
    elif kp >= 5:
        score = 50
    elif kp >= 4:
        score = 28
    if g >= 1:
        score = max(score, min(95, 45 + g * 10))
    if bz is not None and float(bz) <= -8:
        score = min(100, score + 8)

    rows = _rows(now - 45 * 60, ("4m", "2m", "70cm"))
    north_rows = [
        r for r in rows if r["source"] == "pskreporter" and r["sector"] in (330, 0, 30)
        and _mode_name(r["mode"]) in {"CW", "SSB", "USB", "LSB"}
    ]
    north_tx = {str(r["tx_call"] or "").upper() for r in north_rows if r["tx_call"]}
    if len(north_tx) >= 2:
        score = min(100, score + 10)
    return {
        "score": score,
        "label": _label(score, ((75, "hoch"), (55, "erhöht"), (30, "möglich"), (0, "gering"))),
        "kp": kp, "g_scale": g, "bz_nt": bz,
        "north_vhf_tx": len(north_tx),
        "basis": "NOAA-Geomagnetik plus beobachtete nördliche VHF-Aktivität; zeigt Potenzial, keine bestätigte Aurora-Verbindung.",
    }


def _is_beacon_row(row: Any) -> bool:
    call = str(row["tx_call"] or "").upper().strip()
    raw = str(row["raw"] or "").lower()
    mode = _mode_name(row["mode"])
    return bool(call.endswith("/B") or call.endswith("/BEACON") or "/B/" in call or "beacon" in raw or mode == "BEACON")


def beacon_snapshot(now: int | None = None, hours: int = 24, limit: int = 20) -> dict[str, Any]:
    now = int(now or time.time())
    since = now - max(1, hours) * 3600
    marks = ",".join("?" for _ in VHF_BANDS)
    # V1.12.2: do not materialize every VHF raw spot from the last 24 h just
    # to discover the handful that are beacons.  Filter candidates in SQLite.
    with connect() as con:
        rows = con.execute(
            f"""SELECT * FROM spots
                WHERE ts>=? AND band IN ({marks})
                  AND (
                    UPPER(COALESCE(tx_call,'')) LIKE '%/B'
                    OR UPPER(COALESCE(tx_call,'')) LIKE '%/BEACON'
                    OR UPPER(COALESCE(mode,''))='BEACON'
                    OR LOWER(COALESCE(raw,'')) LIKE '%beacon%'
                  )
                ORDER BY ts DESC""",
            (since, *VHF_BANDS),
        ).fetchall()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        if not _is_beacon_row(r) or not r["tx_call"]:
            continue
        call = str(r["tx_call"] or "").upper()
        band = str(r["band"] or "")
        key = (band, call)
        item = grouped.setdefault(key, {
            "band": band, "call": call, "last_seen": 0, "rx": set(), "sources": set(),
            "distance_km": None, "azimuth_deg": None, "sector": None, "tx_grid": None, "tx_dxcc": None,
            "frequency_hz": None,
        })
        item["last_seen"] = max(int(item["last_seen"]), int(r["ts"] or 0))
        if r["rx_call"]: item["rx"].add(str(r["rx_call"]).upper())
        if r["source"]: item["sources"].add(str(r["source"]))
        if r["tx_distance_km"] is not None:
            item["distance_km"] = int(round(float(r["tx_distance_km"])))
        if r["azimuth_deg"] is not None:
            item["azimuth_deg"] = int(round(float(r["azimuth_deg"])))
        if r["sector"] is not None:
            item["sector"] = int(r["sector"])
        if r["tx_grid"]:
            item["tx_grid"] = str(r["tx_grid"])
        if r["tx_dxcc"] is not None:
            item["tx_dxcc"] = int(r["tx_dxcc"])
        if r["frequency_hz"]:
            item["frequency_hz"] = int(r["frequency_hz"])

    out = []
    for item in grouped.values():
        call = item["call"]
        cty = lookup_call(call)
        dxcc = item["tx_dxcc"] if item["tx_dxcc"] is not None else (cty.dxcc if cty else None)
        name = dxcc_name(dxcc) or (entity_display_name(cty.entity) if cty else None) or "Beacon"
        region = None
        if item["tx_grid"]:
            try:
                lat, lon = locator_to_latlon(str(item["tx_grid"])[:8])
                region = geo_region(lat, lon)
            except ValueError:
                pass
        if region is None and cty:
            region = {"EU":"Europa","NA":"Nordamerika","SA":"Südamerika","AF":"Afrika","AS":"Asien","OC":"Ozeanien/Pazifik","AN":"Antarktis"}.get(str(cty.continent or "").upper())
        out.append({
            "band": item["band"], "call": call, "name": name, "region": region,
            "last_seen": item["last_seen"], "age_seconds": max(0, now - int(item["last_seen"])),
            "unique_rx": len(item["rx"]), "sources": sorted(item["sources"]),
            "distance_km": item["distance_km"], "azimuth_deg": item["azimuth_deg"],
            "direction_label": sector_label(item["sector"]),
            "frequency_khz": round(item["frequency_hz"] / 1000, 1) if item["frequency_hz"] else None,
        })
    out.sort(key=lambda x: (-int(x.get("distance_km") or 0), -int(x.get("unique_rx") or 0), -int(x.get("last_seen") or 0)))
    return {"hours": hours, "beacons": out[:max(1, limit)], "count": len(out)}


def vhf_intel_snapshot(now: int | None = None) -> dict[str, Any]:
    now = int(now or time.time())
    tropo = tropo_evidence(now)
    es = sporadic_e_evidence(now)
    meteor = meteor_scatter_activity(now)
    aurora = aurora_potential(now)
    beacons = beacon_snapshot(now)
    mechanisms = [
        ("Tropo", tropo["score"], tropo["label"]),
        ("Sporadic-E", es["score"], es["label"]),
        ("Meteor Scatter", meteor["score"], meteor["label"]),
        ("Aurora-Potenzial", aurora["score"], aurora["label"]),
    ]
    best = max(mechanisms, key=lambda x: x[1])
    return {
        "generated_at": now,
        "tropo": tropo, "sporadic_e": es, "meteor_scatter": meteor, "aurora": aurora,
        "beacons": beacons,
        "strongest_hint": {"mechanism": best[0], "score": best[1], "label": best[2]},
        "disclaimer": "Mechanismen werden aus vorhandenen Empfangsdaten bzw. NOAA-Geomagnetik abgeleitet. Sie sind Hinweise, keine sichere physikalische Diagnose.",
    }
