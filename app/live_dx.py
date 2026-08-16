from __future__ import annotations

import math
import time
from collections import defaultdict
from typing import Any

from .config import settings
from .db import connect
from .dxcc import dxcc_name, geo_region
from .geo import locator_to_latlon, sector_label
from .rarity import personal_rarity_for_band


def _region_for(dxcc: int | None, grid: str | None) -> str | None:
    if dxcc in {1, 6, 50, 110, 291}:
        return "Nordamerika"
    grid = str(grid or "").strip().upper()
    if len(grid) < 4:
        return None
    try:
        lat, lon = locator_to_latlon(grid[:8])
        return geo_region(lat, lon)
    except ValueError:
        return None


def _highlight_score(
    *,
    band: str,
    distance_km: float,
    local_rx: int,
    best_snr: float | None,
    rbn_rx: int,
    region: str | None,
    rarity_stars: int,
) -> int:
    min_dx = max(float(settings.min_dx_km.get(band, 1000)), 1.0)
    ratio = max(1.0, float(distance_km) / min_dx)
    distance_points = min(38.0, 8.0 + 12.0 * math.log2(ratio))
    rx_points = min(22.0, 4.0 + 4.0 * max(1, int(local_rx)))
    rbn_points = 0.0 if not rbn_rx else min(14.0, 6.0 + 2.0 * int(rbn_rx))
    signal_points = 0.0 if best_snr is None else max(0.0, min(10.0, (float(best_snr) + 20.0) / 3.0))
    region_points = 8.0 if region and region != "Europa" else 0.0
    rarity_points = min(10.0, max(0, int(rarity_stars)) * 2.0)

    special = 0.0
    if band == "23cm" and distance_km >= 250:
        special += 12.0
    elif band == "70cm" and distance_km >= 400:
        special += 12.0
    elif band == "2m" and distance_km >= 600:
        special += 12.0
    elif band == "4m" and distance_km >= 1000:
        special += 12.0
    elif band == "6m" and distance_km >= 2500:
        special += 12.0
    elif band in {"10m", "12m"} and distance_km >= 6000:
        special += 6.0
    elif band in {"40m", "60m"} and distance_km >= 5000:
        special += 6.0
    elif band == "80m" and distance_km >= 5000:
        special += 10.0

    return int(round(min(100.0, distance_points + rx_points + rbn_points + signal_points + region_points + rarity_points + special)))


def _highlight_label(score: int) -> str:
    if score >= 75:
        return "🔥 Top DX"
    if score >= 60:
        return "⭐ sehr interessant"
    return "✨ interessant"


def _split_modes(raw: str | None) -> list[str]:
    if not raw:
        return []
    return sorted({x.strip().upper() for x in str(raw).split(",") if x.strip()})


def live_dx_snapshot(*, now: int | None = None, minutes: int | None = None, limit: int | None = None) -> dict[str, Any]:
    """Fast DIGITAL Live-DX snapshot.

    V1.12.3 deliberately aggregates the high-volume PSK Reporter stream in
    SQLite.  Older versions loaded every reception report from the live window
    into Python and only then grouped by station.  On a busy feed that can mean
    tens of thousands of Python/SQLite row objects for one dashboard click.

    Rarity learning is handled by the dedicated Rare-DX path/background cache;
    Live-DX only reads the already learned rarity metadata and therefore stays
    read-mostly and fast.
    """
    now = int(now or time.time())
    minutes = max(1, int(minutes or settings.dx_live_minutes))
    limit = max(1, min(int(limit or settings.dx_live_limit), 100))
    since = now - minutes * 60

    with connect() as con:
        psk_rows = con.execute(
            """SELECT band,
                      UPPER(tx_call) AS tx_call,
                      MAX(tx_dxcc) AS tx_dxcc,
                      MAX(tx_grid) AS tx_grid,
                      COUNT(DISTINCT rx_call) AS local_rx,
                      MAX(snr) AS best_snr,
                      AVG(tx_distance_km) AS distance_km,
                      AVG(azimuth_deg) AS azimuth_deg,
                      MAX(sector) AS sector,
                      GROUP_CONCAT(DISTINCT mode) AS modes,
                      MAX(ts) AS last_seen
               FROM spots
               WHERE source='pskreporter' AND ts>=?
                 AND tx_call IS NOT NULL AND tx_call<>''
                 AND tx_dxcc IS NOT NULL AND tx_distance_km IS NOT NULL
               GROUP BY band, UPPER(tx_call)""",
            (since,),
        ).fetchall()
        rbn_rows = con.execute(
            """SELECT band,UPPER(tx_call) AS tx_call,COUNT(DISTINCT rx_call) AS rbn_rx
               FROM spots
               WHERE source LIKE 'rbn_%' AND ts>=? AND tx_call IS NOT NULL AND tx_call<>''
               GROUP BY band,UPPER(tx_call)""",
            (since,),
        ).fetchall()

    rbn_rx_by_call = {
        (str(r["band"] or "").lower(), str(r["tx_call"] or "").upper()): int(r["rbn_rx"] or 0)
        for r in rbn_rows
    }

    # Gather DXCCs once per band so the rarity lookup stays small and indexed.
    dxcc_by_band: dict[str, set[int]] = defaultdict(set)
    candidates: list[dict[str, Any]] = []
    for row in psk_rows:
        band = str(row["band"] or "").lower().strip()
        if band not in settings.bands:
            continue
        try:
            distance_km = float(row["distance_km"] or 0)
            dxcc = int(row["tx_dxcc"])
        except (TypeError, ValueError):
            continue
        if distance_km < float(settings.min_dx_km.get(band, 1000)):
            continue
        local_rx = int(row["local_rx"] or 0)
        if local_rx < int(settings.dx_live_min_rx):
            continue
        item = dict(row)
        item["band"] = band
        item["call"] = str(row["tx_call"] or "").upper()
        item["dxcc"] = dxcc
        item["distance_km"] = distance_km
        item["local_rx"] = local_rx
        candidates.append(item)
        dxcc_by_band[band].add(dxcc)

    rarity_by_band = {
        band: personal_rarity_for_band(band, codes, now=now)
        for band, codes in dxcc_by_band.items()
    }

    all_items: list[dict[str, Any]] = []
    for item in candidates:
        band = item["band"]
        call = item["call"]
        dxcc = int(item["dxcc"])
        distance_km = float(item["distance_km"])
        grid = str(item.get("tx_grid") or "").upper()
        best_snr = float(item["best_snr"]) if item.get("best_snr") is not None else None
        azimuth = float(item["azimuth_deg"]) if item.get("azimuth_deg") is not None else None
        sector = int(item["sector"]) if item.get("sector") is not None else None
        if sector is None and azimuth is not None:
            sector = int(round(azimuth / 30.0) * 30) % 360
        region = _region_for(dxcc, grid)
        tx_lat = tx_lon = None
        if grid:
            try:
                tx_lat, tx_lon = locator_to_latlon(grid[:8])
            except ValueError:
                tx_lat = tx_lon = None
        rbn_rx = int(rbn_rx_by_call.get((band, call), 0))
        rarity_info = (rarity_by_band.get(band) or {}).get(dxcc) or {
            "stars": 0, "label": "Lernphase", "seen_days": 0, "observed_days": 0
        }
        score = _highlight_score(
            band=band,
            distance_km=distance_km,
            local_rx=int(item["local_rx"]),
            best_snr=best_snr,
            rbn_rx=rbn_rx,
            region=region,
            rarity_stars=int(rarity_info.get("stars") or 0),
        )
        if score < int(settings.dx_live_min_score):
            continue
        last_seen = int(item.get("last_seen") or 0)
        all_items.append({
            "band": band,
            "call": call,
            "dxcc": dxcc,
            "name": dxcc_name(dxcc) or f"DXCC {dxcc}",
            "tx_grid": grid or None,
            "last_seen": last_seen,
            "distance_km": int(round(distance_km)),
            "azimuth_deg": int(round(azimuth)) if azimuth is not None else None,
            "sector": sector,
            "direction_label": sector_label(sector),
            "local_rx": int(item["local_rx"]),
            "best_snr": round(best_snr, 1) if best_snr is not None else None,
            "rbn_rx": rbn_rx,
            "rbn_confirmed": bool(rbn_rx),
            "region": region,
            "tx_lat": round(float(tx_lat), 4) if tx_lat is not None else None,
            "tx_lon": round(float(tx_lon), 4) if tx_lon is not None else None,
            "modes": _split_modes(item.get("modes")),
            "highlight_score": score,
            "highlight_label": _highlight_label(score),
            "rarity_stars": int(rarity_info.get("stars") or 0),
            "rarity_label": str(rarity_info.get("label") or ""),
            "seen_days": int(rarity_info.get("seen_days") or 0),
            "observed_days": int(rarity_info.get("observed_days") or 0),
            "age_seconds": max(0, now - last_seen),
        })

    all_items.sort(key=lambda x: (
        -int(x.get("highlight_score") or 0),
        -int(x.get("local_rx") or 0),
        -int(x.get("distance_km") or 0),
        str(x.get("call") or ""),
    ))
    return {
        "qth": settings.qth_locator,
        "live_minutes": minutes,
        "min_score": settings.dx_live_min_score,
        "stations": all_items[:limit],
        "count": len(all_items),
    }


def telegram_text(snapshot: dict[str, Any], *, limit: int = 10) -> str:
    stations = snapshot.get("stations") or []
    lines = [f"🌍 LIVE DX – {settings.qth_locator}", f"letzte {int(snapshot.get('live_minutes') or settings.dx_live_minutes)} Min."]
    if not stations:
        lines.append("Aktuell keine Stationen oberhalb der Live-DX-Schwelle.")
        return "\n".join(lines)

    for item in stations[:limit]:
        km = f"{int(item.get('distance_km') or 0):,}".replace(",", ".")
        snr = item.get("best_snr")
        snr_text = "—" if snr is None else f"{float(snr):+.0f} dB"
        confirm = f" · RBN {int(item.get('rbn_rx') or 0)}" if item.get("rbn_confirmed") else ""
        rare = ""
        if int(item.get("rarity_stars") or 0) >= 2:
            rare = f" · 🦄 {'⭐' * int(item['rarity_stars'])}"
        region = f" · {item.get('region')}" if item.get("region") else ""
        lines.append(
            f"{item.get('highlight_label')} · {item.get('call')} · {item.get('band')} · {item.get('name')}{region}\n"
            f"🧭 {item.get('direction_label')} · 📏 {km} km · 📶 {snr_text} · 👂 {int(item.get('local_rx') or 0)} RX{confirm}{rare}"
        )
    return "\n\n".join(lines)
