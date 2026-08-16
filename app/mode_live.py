from __future__ import annotations

import collections
import statistics
import time
from typing import Any

from .config import settings
from .db import connect
from .dxcc import dxcc_name, entity_display_name, geo_region
from .cty_prefixes import lookup_call
from .geo import locator_to_latlon, sector_label
from .live_dx import live_dx_snapshot
from .mode_scores import spotter_region_weight


def _enrichment(con, band: str, calls: set[str], since: int) -> dict[str, Any]:
    if not calls:
        return {}
    placeholders = ",".join("?" for _ in calls)
    rows = con.execute(
        f"""SELECT * FROM spots WHERE source='pskreporter' AND band=? AND ts>=? AND tx_call IN ({placeholders}) ORDER BY ts DESC""",
        (band, since, *sorted(calls)),
    ).fetchall()
    out = {}
    for r in rows:
        call = str(r["tx_call"] or "").upper()
        if call and call not in out:
            out[call] = r
    return out


def live_mode_snapshot(mode: str, *, now: int | None = None, minutes: int = 15, limit: int = 30) -> dict[str, Any]:
    mode = str(mode or "ssb").lower()
    if mode == "digital":
        result = live_dx_snapshot(now=now, minutes=minutes, limit=limit)
        result["mode"] = "digital"
        return result
    now = int(now or time.time())
    since = now - max(1, minutes) * 60
    source = "dxcluster_ssb" if mode == "ssb" else "rbn_cw"
    with connect() as con:
        rows = con.execute("SELECT * FROM spots WHERE source=? AND ts>=? ORDER BY ts DESC", (source, since)).fetchall()
        calls_by_band: dict[str, set[str]] = collections.defaultdict(set)
        for r in rows:
            if r["tx_call"]:
                calls_by_band[str(r["band"])].add(str(r["tx_call"]).upper())
        enrich = {}
        for band, calls in calls_by_band.items():
            enrich.update({(band,k):v for k,v in _enrichment(con, band, calls, now-3600).items()})

    grouped: dict[tuple[str,str], dict[str,Any]] = {}
    for r in rows:
        band = str(r["band"] or "")
        call = str(r["tx_call"] or "").upper()
        spotter = str(r["rx_call"] or "").upper()
        if not band or not call:
            continue
        if mode == "ssb" and spotter_region_weight(spotter) <= 0:
            continue
        key=(band,call)
        item=grouped.setdefault(key,{"band":band,"call":call,"spotters":set(),"freqs":[],"last_seen":0})
        if spotter: item["spotters"].add(spotter)
        if r["frequency_hz"]: item["freqs"].append(int(r["frequency_hz"]))
        item["last_seen"]=max(item["last_seen"],int(r["ts"] or 0))

    items=[]
    for (band,call), item in grouped.items():
        rx=len(item.pop("spotters"))
        freq=round(statistics.median(item.pop("freqs"))/1000.0,1) if item["freqs"] else None
        e=enrich.get((band,call))
        cty = lookup_call(call)
        dxcc = int(e["tx_dxcc"]) if e is not None and e["tx_dxcc"] is not None else (cty.dxcc if cty else None)
        grid = str(e["tx_grid"] or "") if e is not None else ""
        distance = int(round(float(e["tx_distance_km"]))) if e is not None and e["tx_distance_km"] is not None else None
        az = int(round(float(e["azimuth_deg"]))) if e is not None and e["azimuth_deg"] is not None else None
        sector = int(e["sector"]) if e is not None and e["sector"] is not None else None
        lat=lon=None
        region=None
        if grid:
            try:
                lat,lon=locator_to_latlon(grid[:8]); region=geo_region(lat,lon)
            except ValueError:
                pass
        if region is None and cty is not None:
            region = {
                "EU": "Europa", "NA": "Nordamerika", "SA": "Südamerika",
                "AF": "Afrika", "AS": "Asien", "OC": "Ozeanien/Pazifik",
                "AN": "Antarktis",
            }.get(str(cty.continent or "").upper())
        country_name = dxcc_name(dxcc) or (entity_display_name(cty.entity) if cty else None) or "DX-Station"
        entity_source = "PSK Reporter" if e is not None and (e["tx_dxcc"] is not None or grid) else (cty.source if cty else None)
        score=min(100, 38 + rx*12 + (10 if distance and distance>=5000 else 0))
        items.append({
            "band":band,"call":call,"dxcc":dxcc,"name":country_name,
            "frequency_khz":freq,"local_rx":rx,"regional_spotters":rx,"last_seen":item["last_seen"],
            "age_seconds":max(0,now-item["last_seen"]),"distance_km":distance,"azimuth_deg":az,
            "sector":sector,"direction_label":sector_label(sector),"tx_lat":round(lat,4) if lat is not None else None,
            "tx_lon":round(lon,4) if lon is not None else None,"region":region,"modes":[mode.upper()],
            "highlight_score":score,"highlight_label":"🎙️ SSB live" if mode=="ssb" else "📻 CW live",
            "best_snr":None,"rbn_rx":rx if mode=="cw" else 0,"rbn_confirmed":mode=="cw" and rx>0,
            "rarity_stars":0,"entity_source":entity_source,
            "location_accuracy":"station-grid" if grid else ("entity-only" if cty else None),
        })
    items.sort(key=lambda x:(-int(x["local_rx"]),-int(x["highlight_score"]),-int(x["last_seen"])))
    return {"qth":settings.qth_locator,"mode":mode,"live_minutes":minutes,"stations":items[:limit],"count":len(items)}
