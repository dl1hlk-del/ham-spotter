from __future__ import annotations

import asyncio
import html
import json
import logging
import time
import statistics
from collections import Counter
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .collectors.psk import PSKReporterCollector
from .collectors.rbn import run_rbn_stream
from .collectors.dxcluster import run_dxcluster_stream
from .config import settings
from .db import (
    close_stale_opening_events,
    init_db,
    opening_history,
    opening_stats,
    status_snapshot,
    band_activity_history,
)
from .dxcc import refresh_loop as dxcc_refresh_loop, refresh_once as dxcc_refresh_once
from .cty_prefixes import refresh_loop as cty_refresh_loop, refresh_once as cty_refresh_once
from .engine import engine_loop
from .geo import local_locator4_squares, locator_to_latlon, sector_label
from .rbn_nodes import refresh_loop
from .telegram import Telegram
from .rarity import backfill_from_spots, live_snapshot
from .live_dx import live_dx_snapshot
from .mode_live import live_mode_snapshot
from .space_weather import refresh_loop as space_weather_refresh_loop, snapshot as space_weather_snapshot
from .dashboard_intel import comparison_snapshot, highlight_snapshot, opening_timeline_today
from .band_layers import configured_layer_bands, layer_label, normalize_layer
from .decision_layer import decision_snapshot
from .vhf_intel import vhf_intel_snapshot
from .perf_cache import get_or_build as perf_cached

VERSION = "1.13.1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("ham-spotter")

stop_event = asyncio.Event()
psk = PSKReporterCollector()
telegram = Telegram()
tasks: list[asyncio.Task] = []


def _cached_decision(snap: dict, bands, mode: str, layer: str):
    return perf_cached(
        ("decision", str(layer), str(mode)),
        settings.dashboard_decision_cache_seconds,
        lambda: decision_snapshot(snap, bands, mode=mode),
    )


def _cached_vhf_intel():
    return perf_cached(
        ("vhf-intel",),
        settings.vhf_intel_cache_seconds,
        vhf_intel_snapshot,
    )


def _cached_live_mode(mode: str):
    return perf_cached(
        ("live-mode", str(mode), int(settings.dx_live_minutes)),
        min(10, max(3, settings.dashboard_secondary_cache_seconds)),
        lambda: live_mode_snapshot(mode, limit=100, minutes=settings.dx_live_minutes),
    )


def _cached_activity(mode: str):
    return perf_cached(
        ("activity", str(mode), 6, 300),
        settings.dashboard_secondary_cache_seconds,
        lambda: band_activity_history(hours=6, bucket_seconds=300, mode=mode),
    )


def _cached_timeline(layer: str, bands):
    return perf_cached(
        ("timeline", str(layer)),
        max(15, settings.dashboard_secondary_cache_seconds),
        lambda: opening_timeline_today(bands=bands),
    )


def _cached_comparison(layer: str, bands):
    return perf_cached(
        ("comparison", str(layer)),
        max(30, settings.dashboard_secondary_cache_seconds * 2),
        lambda: comparison_snapshot(bands=bands),
    )


def _cached_stats(days: int):
    return perf_cached(
        ("opening-stats", int(days)),
        max(30, settings.dashboard_secondary_cache_seconds * 2),
        lambda: opening_stats(days=days),
    )


def _cached_history():
    return perf_cached(
        ("opening-history", 100),
        max(15, settings.dashboard_secondary_cache_seconds),
        lambda: opening_history(limit=100),
    )


def _cached_rare():
    return perf_cached(
        ("rare-live", 100),
        max(20, settings.dashboard_secondary_cache_seconds),
        lambda: live_snapshot(limit=100),
    )


def _warm_default_performance_cache() -> None:
    """Keep the default dashboard's expensive blocks warm between page loads."""
    layer = normalize_layer(settings.dashboard_default_layer)
    mode = settings.primary_prop_mode if settings.primary_prop_mode in {"ssb", "cw", "digital"} else "ssb"
    bands = configured_layer_bands(layer)
    snap = status_snapshot()
    _cached_decision(snap, bands, mode, layer)
    _cached_live_mode(mode)
    _cached_activity(mode)
    _cached_timeline(layer, bands)
    _cached_comparison(layer, bands)
    _cached_stats(30)
    _cached_history()
    _cached_rare()
    # VHF intelligence is also precomputed so switching to the VHF layer does
    # not trigger a multi-second first calculation.
    _cached_vhf_intel()


async def _performance_warm_loop(stop: asyncio.Event) -> None:
    await asyncio.sleep(2)
    while not stop.is_set():
        try:
            await asyncio.to_thread(_warm_default_performance_cache)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Dashboard cache warm-up failed: %s", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    stale = close_stale_opening_events(max_age_seconds=max(600, settings.analyse_interval_seconds * 10))
    rarity_backfill = backfill_from_spots()
    if rarity_backfill:
        log.info("Rare DX memory backfilled from %d retained PSK rows", rarity_backfill)
    if stale:
        log.info("Closed %d stale opening event(s) after previous downtime", stale)
    qlat, qlon = locator_to_latlon(settings.qth_locator)
    log.info("HAM Spotter starting: QTH=%s centre=%.5f,%.5f bands=%s", settings.qth_locator, qlat, qlon, settings.bands)
    log.info("PSKR local 4-char RX grids: %s", ",".join(local_locator4_squares(settings.qth_locator, settings.local_rx_radius_km)))
    try:
        await dxcc_refresh_once()
    except Exception as exc:
        log.warning("DXCC catalogue warm-up failed; fallback remains active: %s", exc)
    try:
        await cty_refresh_once()
    except Exception as exc:
        log.warning("CTY prefix warm-up failed; fallback remains active: %s", exc)
    psk.start()
    tasks.extend([
        asyncio.create_task(refresh_loop(stop_event), name="rbn-nodes"),
        asyncio.create_task(dxcc_refresh_loop(stop_event), name="adif-dxcc"),
        asyncio.create_task(cty_refresh_loop(stop_event), name="cty-prefixes"),
        asyncio.create_task(space_weather_refresh_loop(stop_event), name="noaa-swpc"),
        asyncio.create_task(run_rbn_stream(settings.rbn_cw_port, "rbn_cw", stop_event), name="rbn-cw"),
        asyncio.create_task(run_rbn_stream(settings.rbn_ft8_port, "rbn_ft8", stop_event), name="rbn-ft8"),
        asyncio.create_task(run_dxcluster_stream(stop_event), name="dxcluster-ssb"),
        asyncio.create_task(engine_loop(stop_event, telegram), name="opening-engine"),
        asyncio.create_task(telegram.command_loop(stop_event), name="telegram"),
        asyncio.create_task(_performance_warm_loop(stop_event), name="dashboard-cache"),
    ])
    yield
    stop_event.set()
    psk.stop()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="HAM Spotter", version=VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")


@app.get("/health")
def health():
    snap = status_snapshot()
    return {
        "ok": True,
        "version": VERSION,
        "qth": settings.qth_locator,
        "bands": settings.bands,
        "band_layers": {"hf": configured_layer_bands("hf"), "vhf": configured_layer_bands("vhf")},
        "sources": snap["sources"],
        "rbn_nodes": snap["rbn_nodes"],
        "spots_last_hour": snap["spots_last_hour"],
        "time": int(time.time()),
    }


@app.get("/api/status")
def api_status():
    snap = status_snapshot()
    snap.update({
        "qth": settings.qth_locator, "callsign": settings.callsign, "version": VERSION,
        "primary_mode": settings.primary_prop_mode, "propagation_modes": ["ssb", "cw", "digital"],
        "band_layers": {"hf": list(configured_layer_bands("hf")), "vhf": list(configured_layer_bands("vhf"))},
        "dashboard_default_layer": normalize_layer(settings.dashboard_default_layer),
    })
    return snap


@app.get("/api/history")
def api_history(limit: int = 50, band: str | None = None):
    band = band.lower().strip() if band else None
    if band and band not in settings.bands:
        band = None
    return {
        "qth": settings.qth_locator,
        "callsign": settings.callsign,
        "version": VERSION,
        "events": opening_history(limit=limit, band=band),
    }


@app.get("/api/dx")
def api_dx(limit: int = 20, minutes: int | None = None, mode: str = "ssb", layer: str | None = None):
    mode = str(mode or "ssb").lower()
    if mode not in {"ssb", "cw", "digital"}:
        mode = "ssb"
    result = live_mode_snapshot(mode, limit=max(limit, 100) if layer else limit, minutes=minutes or settings.dx_live_minutes)
    if layer is not None:
        selected = normalize_layer(layer)
        allowed = set(configured_layer_bands(selected))
        result["stations"] = [x for x in (result.get("stations") or []) if str(x.get("band") or "").lower() in allowed][:max(1, min(int(limit), 100))]
        result["count"] = len(result["stations"])
        result["layer"] = selected
        result["layer_bands"] = list(configured_layer_bands(selected))
    result.update({"callsign": settings.callsign, "version": VERSION})
    return result


@app.get("/api/rare")
def api_rare(limit: int = 20, minutes: int | None = None):
    result = live_snapshot(limit=limit, minutes=minutes)
    result.update({"callsign": settings.callsign, "version": VERSION})
    return result


@app.get("/api/stats")
def api_stats(days: int = 30):
    result = opening_stats(days=days)
    result.update({"qth": settings.qth_locator, "callsign": settings.callsign, "version": VERSION})
    return result

@app.get("/api/space-weather")
def api_space_weather():
    result = space_weather_snapshot()
    result.update({"qth": settings.qth_locator, "callsign": settings.callsign, "version": VERSION})
    return result


@app.get("/api/activity")
def api_activity(hours: int = 6, mode: str = "ssb", layer: str | None = None):
    result = band_activity_history(hours=hours, bucket_seconds=300, mode=mode)
    if layer is not None:
        selected = normalize_layer(layer)
        allowed = set(configured_layer_bands(selected))
        result["bands"] = [x for x in (result.get("bands") or []) if str(x.get("band") or "").lower() in allowed]
        result["layer"] = selected
        result["layer_bands"] = list(configured_layer_bands(selected))
    result.update({"qth": settings.qth_locator, "callsign": settings.callsign, "version": VERSION})
    return result


@app.get("/api/timeline")
def api_timeline(layer: str | None = None):
    selected_bands = configured_layer_bands(normalize_layer(layer)) if layer is not None else settings.bands
    result = opening_timeline_today(bands=selected_bands)
    if layer is not None:
        result["layer"] = normalize_layer(layer)
        result["layer_bands"] = list(selected_bands)
    result.update({"qth": settings.qth_locator, "callsign": settings.callsign, "version": VERSION})
    return result


@app.get("/api/compare")
def api_compare(layer: str | None = None):
    selected_bands = configured_layer_bands(normalize_layer(layer)) if layer is not None else settings.bands
    result = comparison_snapshot(bands=selected_bands)
    if layer is not None:
        result["layer"] = normalize_layer(layer)
        result["layer_bands"] = list(selected_bands)
    result.update({"qth": settings.qth_locator, "callsign": settings.callsign, "version": VERSION})
    return result


@app.get("/api/highlights")
def api_highlights(mode: str = "ssb", layer: str | None = None):
    mode = str(mode or settings.primary_prop_mode or "ssb").lower()
    if mode not in {"ssb", "cw", "digital"}:
        mode = "ssb"
    selected_layer = normalize_layer(layer) if layer is not None else None
    allowed = set(configured_layer_bands(selected_layer)) if selected_layer is not None else set(settings.bands)
    snap = status_snapshot()
    display_snap = dict(snap)
    display_bands = []
    for row in snap.get("bands", []):
        if str(row.get("band") or "").lower() not in allowed:
            continue
        clone = dict(row)
        base = row.get("details") or {}
        md = ((base.get("mode_scores") or {}).get(mode) or {})
        if md:
            clone["state"] = md.get("state") or "CLOSED"
            clone["score"] = int(md.get("score") or 0)
            clone["direction_label"] = md.get("direction_label") or "unbekannt"
            merged = dict(base)
            merged.update(md)
            clone["details"] = merged
        display_bands.append(clone)
    display_snap["bands"] = display_bands
    live_dx = live_mode_snapshot(mode, limit=100, minutes=settings.dx_live_minutes)
    live_dx["stations"] = [x for x in (live_dx.get("stations") or []) if str(x.get("band") or "").lower() in allowed]
    live_dx["count"] = len(live_dx["stations"])
    weather = space_weather_snapshot()
    result = highlight_snapshot(display_snap, live_dx, weather, mode=mode)
    result.update({"qth": settings.qth_locator, "callsign": settings.callsign, "version": VERSION, "mode": mode})
    if selected_layer is not None:
        result.update({"layer": selected_layer, "layer_bands": list(configured_layer_bands(selected_layer))})
    return result


@app.get("/api/decision")
def api_decision(mode: str = "ssb", layer: str | None = None):
    mode = str(mode or settings.primary_prop_mode or "ssb").lower()
    if mode not in {"ssb", "cw", "digital"}:
        mode = "ssb"
    selected = normalize_layer(layer)
    bands = configured_layer_bands(selected)
    snap = status_snapshot()
    result = _cached_decision(snap, bands, mode, selected)
    result.update({
        "qth": settings.qth_locator, "callsign": settings.callsign, "version": VERSION,
        "mode": mode, "layer": selected, "layer_bands": list(bands),
    })
    return result


@app.get("/api/vhf-intel")
def api_vhf_intel():
    result = _cached_vhf_intel()
    result.update({"qth": settings.qth_locator, "callsign": settings.callsign, "version": VERSION})
    return result


def _duration(seconds: int | float | None) -> str:
    try:
        value = max(0, int(seconds or 0))
    except (TypeError, ValueError):
        return "—"
    hours, rem = divmod(value, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _country_chips(countries: list[dict], limit: int = 4) -> str:
    out = []
    for country in countries[:limit]:
        name = html.escape(str(country.get("name") or ""))
        calls = int(country.get("calls") or 0)
        if name:
            out.append(f"<span class='chip'>{name}{f' {calls}' if calls else ''}</span>")
    return "".join(out) or "<span class='muted'>keine DXCC-/Länderdaten im aktuellen Fenster</span>"


def _live_band_context(stations: list[dict], band: str) -> dict:
    """Derive safe display context from already enriched live stations.

    Country/region may come from CTY.DAT even when no station locator is known.
    Distance/direction are used only from rows that actually carry geolocation.
    """
    rows = [x for x in (stations or []) if str(x.get("band") or "").lower() == str(band).lower()]
    countries: Counter[str] = Counter()
    regions: Counter[str] = Counter()
    sectors: Counter[int] = Counter()
    sector_labels: dict[int, str] = {}
    distances: list[float] = []

    for item in rows:
        name = str(item.get("name") or "").strip()
        if name and name not in {"—", "DX-Station", "unbekannt"}:
            countries[name] += 1
        region = str(item.get("region") or "").strip()
        if region and region not in {"—", "unbekannt"}:
            regions[region] += 1
        try:
            dist = float(item.get("distance_km"))
            if dist > 0:
                distances.append(dist)
        except (TypeError, ValueError):
            pass
        sector = item.get("sector")
        if sector is not None:
            try:
                sec = int(sector) % 360
            except (TypeError, ValueError):
                continue
            sectors[sec] += 1
            label = str(item.get("direction_label") or "").strip()
            if label and label not in {"unbekannt", "—"}:
                sector_labels[sec] = label

    top_sector = None
    direction_label = None
    confidence_pct = 0
    locator_rows = sum(sectors.values())
    if sectors:
        top_sector, top_count = sectors.most_common(1)[0]
        direction_label = sector_labels.get(top_sector) or sector_label(top_sector)
        confidence_pct = int(round(100 * top_count / max(1, locator_rows)))

    return {
        "countries": [{"name": name, "calls": count} for name, count in countries.most_common(8)],
        "dominant_region": regions.most_common(1)[0][0] if regions else None,
        "target_median_dx_km": int(round(statistics.median(distances))) if distances else 0,
        "direction_label": direction_label,
        "top_sector": top_sector,
        "direction_confidence_pct": confidence_pct,
        "locator_rows": locator_rows,
        "live_rows": len(rows),
    }


def _state_icon(state: str) -> str:
    return {"CLOSED": "🔴", "WATCH": "🟡", "OPEN": "🟢", "STRONG": "🔥"}.get(state, "⚪")


def _score_sparkline(points: list[dict], *, width: int = 280, height: int = 82) -> str:
    if not points:
        return "<div class='trend-empty'>Noch keine Verlaufsdaten – wird ab jetzt automatisch gesammelt.</div>"
    values = [max(0, min(100, int(p.get("score") or 0))) for p in points]
    if len(values) == 1:
        values = [values[0], values[0]]
    pad = 4
    usable_w = width - pad * 2
    usable_h = height - pad * 2
    coords = []
    for idx, value in enumerate(values):
        x = pad + (usable_w * idx / max(1, len(values) - 1))
        y = pad + usable_h * (1 - value / 100.0)
        coords.append(f"{x:.1f},{y:.1f}")
    def y_for(score: int) -> float:
        return pad + usable_h * (1 - max(0, min(100, score)) / 100.0)
    return (
        f"<svg class='trend-svg' viewBox='0 0 {width} {height}' preserveAspectRatio='none' aria-label='Score-Verlauf'>"
        f"<line class='thr watch' x1='0' y1='{y_for(settings.watch_score):.1f}' x2='{width}' y2='{y_for(settings.watch_score):.1f}'/>"
        f"<line class='thr open' x1='0' y1='{y_for(settings.open_score):.1f}' x2='{width}' y2='{y_for(settings.open_score):.1f}'/>"
        f"<line class='thr strong' x1='0' y1='{y_for(settings.strong_score):.1f}' x2='{width}' y2='{y_for(settings.strong_score):.1f}'/>"
        f"<polyline class='score-line' points='{' '.join(coords)}'/></svg>"
    )


def _point_delta(points: list[dict], seconds: int) -> int | None:
    if len(points) < 2:
        return None
    latest_ts = int(points[-1].get("ts") or 0)
    latest_score = int(points[-1].get("score") or 0)
    target = latest_ts - int(seconds)
    candidates = [p for p in points if int(p.get("ts") or 0) <= target]
    base = candidates[-1] if candidates else points[0]
    return latest_score - int(base.get("score") or 0)


def _delta_badge(value: float | int | None) -> str:
    if value is None:
        return "<span class='cmp-neutral'>neu</span>"
    v = float(value)
    cls = "cmp-up" if v > 0 else ("cmp-down" if v < 0 else "cmp-neutral")
    arrow = "▲" if v > 0 else ("▼" if v < 0 else "●")
    return f"<span class='{cls}'>{arrow} {abs(v):.0f}%</span>"


@app.get("/", response_class=HTMLResponse)
def dashboard(days: int = 30, mode: str = "ssb", layer: str | None = None):
    days = max(1, min(int(days), 365))
    mode = str(mode or settings.primary_prop_mode or "ssb").lower()
    if mode not in {"ssb", "cw", "digital"}:
        mode = "ssb"
    layer = normalize_layer(layer)
    layer_bands = configured_layer_bands(layer)
    allowed_bands = set(layer_bands)
    snap = status_snapshot()
    snap["bands"] = [x for x in (snap.get("bands") or []) if str(x.get("band") or "").lower() in allowed_bands]
    stats = _cached_stats(days)
    stats["bands"] = [x for x in (stats.get("bands") or []) if str(x.get("band") or "").lower() in allowed_bands]
    stats["total_events"] = sum(int(x.get("events") or 0) for x in stats["bands"])
    history = [x for x in _cached_history() if str(x.get("band") or "").lower() in allowed_bands][:30]
    live_dx = _cached_live_mode(mode)
    live_dx["stations"] = [x for x in (live_dx.get("stations") or []) if str(x.get("band") or "").lower() in allowed_bands]
    live_dx["count"] = len(live_dx["stations"])
    rare = _cached_rare()
    rare["stations"] = [x for x in (rare.get("stations") or []) if str(x.get("band") or "").lower() in allowed_bands][:16]
    if isinstance(rare.get("learning"), dict):
        rare["learning"] = {k:v for k,v in rare["learning"].items() if str(k).lower() in allowed_bands}
    weather = space_weather_snapshot()
    activity = _cached_activity(mode)
    activity["bands"] = [x for x in (activity.get("bands") or []) if str(x.get("band") or "").lower() in allowed_bands]
    timeline = _cached_timeline(layer, layer_bands)
    comparison = _cached_comparison(layer, layer_bands)
    decision = _cached_decision(snap, layer_bands, mode, layer)
    vhf_intel = _cached_vhf_intel() if layer == "vhf" else None
    # The highlight center and open-band counter follow the currently selected
    # dashboard mode, while opening history remains based on PRIMARY_PROP_MODE.
    display_snap = dict(snap)
    display_bands = []
    for row in snap.get("bands", []):
        clone = dict(row)
        base = row.get("details") or {}
        md = ((base.get("mode_scores") or {}).get(mode) or {})
        if md:
            clone["state"] = md.get("state") or "CLOSED"
            clone["score"] = int(md.get("score") or 0)
            clone["direction_label"] = md.get("direction_label") or "unbekannt"
            merged = dict(base); merged.update(md); clone["details"] = merged
        display_bands.append(clone)
    display_snap["bands"] = display_bands
    highlights = highlight_snapshot(display_snap, live_dx, weather, mode=mode)
    open_now = sum(1 for b in display_bands if b.get("state") in {"OPEN", "STRONG"})
    total_spots = sum(int(v or 0) for v in snap.get("spots_last_hour", {}).values())

    def _wv(value, suffix=""):
        return "—" if value is None else f"{value}{suffix}"

    def _wx_tile(label: str, value: str, help_text: str, value_class: str = "") -> str:
        cls = f" {value_class}" if value_class else ""
        return (
            "<div class='wx-tip' tabindex='0'>"
            f"<span>{html.escape(label)}</span>"
            f"<strong class='{html.escape(cls.strip())}'>{html.escape(str(value))}</strong>"
            "<em class='wx-info' aria-hidden='true'>i</em>"
            f"<div class='wx-pop' role='tooltip'><b>{html.escape(label)}</b>{html.escape(help_text)}</div>"
            "</div>"
        )

    weather_available = bool(weather.get("available"))
    weather_age = weather.get("age_seconds")
    weather_age_text = "—" if weather_age is None else (f"{int(weather_age)//60} Min." if int(weather_age) >= 60 else "<1 Min.")
    weather_stale = bool(weather.get("stale"))
    weather_assessment = weather.get("assessment") or {}
    kp = weather.get("kp")
    sfi = weather.get("sfi")
    bz = weather.get("bz_nt")
    kp_cls = "wx-good" if kp is not None and float(kp) < 3 else ("wx-warn" if kp is not None and float(kp) < 5 else "wx-bad")
    sfi_cls = "wx-good" if sfi is not None and float(sfi) >= 115 else "wx-warn"
    bz_cls = "wx-bad" if bz is not None and float(bz) <= -8 else ("wx-warn" if bz is not None and float(bz) < 0 else "wx-good")
    if weather_available:
        noaa_value = f"R{int(weather.get('r_scale') or 0)} / S{int(weather.get('s_scale') or 0)} / G{int(weather.get('g_scale') or 0)}"
        weather_tiles = "".join([
            _wx_tile(
                "SFI", _wv(sfi),
                "Solar Flux Index bei 10,7 cm. Er ist ein Maß für die solare Aktivität und ein guter Langzeit-Hinweis auf die Ionisation der F-Schicht. Höhere Werte begünstigen oft die höheren KW-Bänder, garantieren aber keine konkrete Öffnung.",
                sfi_cls,
            ),
            _wx_tile(
                "SSN", _wv(weather.get('ssn')),
                "Sunspot Number / Sonnenfleckenzahl. Viele Sonnenflecken gehen meist mit höherer solarer Aktivität einher. Für die Ausbreitung ist eher der Trend über Tage als ein einzelner Momentwert interessant.",
            ),
            _wx_tile(
                "Kp", _wv(kp),
                "Planetarer Kp-Index von 0 bis 9. Kleine Werte bedeuten ein ruhiges Erdmagnetfeld. Ab Kp 5 spricht man von geomagnetischem Sturm; dann können HF-Bedingungen besonders auf höheren Breiten unruhiger werden.",
                kp_cls,
            ),
            _wx_tile(
                "A-Index", _wv(weather.get('a_index')),
                "Tagesmaß für geomagnetische Aktivität, abgeleitet aus K-Werten. Je niedriger, desto ruhiger ist das Erdmagnetfeld. Der A-Index reagiert träger als Kp und eignet sich gut zur Einordnung des Tagesverlaufs.",
            ),
            _wx_tile(
                "Solarwind", _wv(weather.get('solar_wind_kms'), ' km/s'),
                "Geschwindigkeit des Sonnenwinds. Ein schneller Sonnenwind ist nicht automatisch schlecht, kann aber zusammen mit einem deutlich negativen Bz das Erdmagnetfeld stärker anregen.",
            ),
            _wx_tile(
                "Bz", _wv(bz, ' nT'),
                "Nord/Süd-Komponente des interplanetaren Magnetfelds. Stark negative Werte (südwärts) koppeln besonders gut an das Erdmagnetfeld und können geomagnetische Aktivität auslösen. Positive Werte sind meist weniger geoeffektiv.",
                bz_cls,
            ),
            _wx_tile(
                "X-Ray", str(weather.get('xray_class') or '—'),
                "Aktuelle GOES-Röntgenklasse der Sonne: A, B, C, M, X. M- und besonders X-Flares können auf der sonnenbeschienenen Erdseite kurzfristige HF-Radio-Blackouts verursachen.",
            ),
            _wx_tile(
                "NOAA R/S/G", noaa_value,
                "NOAA-Warnskalen: R = Radio Blackout, S = Solar Radiation Storm, G = Geomagnetic Storm. 0 bedeutet keine Warnlage; 1 bis 5 steht für zunehmende Stärke.",
            ),
        ])
        weather_html = f"""
        <section class='weather-bar' id='funkwetter'>
          <div class='weather-title'><div><b>☀️ Funkwetter</b><span>NOAA SWPC · Maus über einen Wert = Erklärung</span></div><div class='weather-age {'stale' if weather_stale else ''}'>{'⚠️ ' if weather_stale else ''}Update {weather_age_text} alt</div></div>
          <div class='weather-values'>{weather_tiles}</div>
          <div class='weather-assess'>
            <span><b>Low Bands</b> {html.escape(str(weather_assessment.get('low_bands') or '—'))}</span>
            <span><b>High Bands</b> {html.escape(str(weather_assessment.get('high_bands') or '—'))}</span>
            <span><b>6 m</b> {html.escape(str(weather_assessment.get('six_m') or '—'))}</span>
            <span><b>Geomagnetik</b> {html.escape(str(weather_assessment.get('geomagnetic') or '—'))}</span>
          </div>
        </section>"""
    else:
        weather_html = "<section class='weather-bar'><div class='weather-title'><div><b>☀️ Funkwetter</b><span>NOAA SWPC</span></div><div class='weather-age stale'>⚪ Daten werden geladen …</div></div></section>"

    cards: list[str] = []
    live_stations = list(live_dx.get("stations") or [])
    live_context_by_band = {
        str(b.get("band") or "").lower(): _live_band_context(live_stations, str(b.get("band") or ""))
        for b in snap.get("bands", [])
    }
    for b in snap["bands"]:
        base_details = b.get("details") or {}
        mode_data = ((base_details.get("mode_scores") or {}).get(mode) or {})
        d = dict(base_details)
        d.update(mode_data)
        live_ctx = live_context_by_band.get(str(b.get("band") or "").lower(), {})
        state = str(mode_data.get("state") or (b.get("state") if mode == settings.primary_prop_mode else "CLOSED") or "CLOSED")
        score = int(mode_data.get("score") if mode_data.get("score") is not None else (b.get("score") or 0))

        # CTY.DAT-derived country/region information remains useful even when
        # SSB/CW has no exact locator.  Exact direction/distance, however, is
        # only filled from live rows with real geolocation.
        region_raw = d.get("dominant_region") or live_ctx.get("dominant_region") or "—"
        region = html.escape(str(region_raw))
        direction_raw = d.get("direction_label")
        if not direction_raw or str(direction_raw).lower() in {"unbekannt", "none", "—"}:
            direction_raw = live_ctx.get("direction_label")
        direction = html.escape(str(direction_raw or "keine Locator-Daten"))
        target_dx = int(d.get("target_median_dx_km") or live_ctx.get("target_median_dx_km") or 0)
        trend = d.get("trend_ratio")
        try:
            trend_pct = int(round((float(trend) - 1.0) * 100))
            trend_text = f"{trend_pct:+d}%"
        except (TypeError, ValueError):
            trend_text = "—"
        conf_raw = str(d.get("direction_confidence") or "").upper()
        conf_pct = int(d.get("direction_confidence_pct") or 0)
        if conf_raw in {"", "NONE"} or conf_pct <= 0:
            live_conf = int(live_ctx.get("direction_confidence_pct") or 0)
            if live_conf > 0 and int(live_ctx.get("locator_rows") or 0) > 0:
                conf_display = f"Locator {live_conf}%"
            else:
                conf_display = "keine Richtungsdaten"
        else:
            conf_display = f"{conf_raw} {conf_pct}%"
        countries_display = d.get("countries") or live_ctx.get("countries") or []
        if mode == "ssb":
            source_lines = f"<div class='source-line'>🎙️ SSB {int(d.get('unique_tx') or 0)} DX / {int(d.get('unique_rx') or 0)} regionale Spotter · bestätigt {int(d.get('confirmed_tx') or 0)}</div><div class='source-line'>📡 Digital-Kontext {int(base_details.get('digital_context_score') or 0)}/100</div>"
        elif mode == "cw":
            source_lines = f"<div class='source-line'>📻 CW {int(d.get('unique_tx') or 0)} Calls / {int(d.get('unique_rx') or 0)} lokale RBN-Skimmer</div><div class='source-line'>📡 Richtung nur bei PSK-Korrelation</div>"
        else:
            source_lines = f"<div class='source-line'>📡 PSK {int(d.get('psk_unique_tx') or 0)} DX / {int(d.get('psk_unique_rx') or 0)} RX</div><div class='source-line'>📻 RBN FT8 {int(d.get('rbn_unique_tx') or 0)} Calls / {int(d.get('rbn_unique_rx') or 0)} Skimmer</div>"
        cards.append(f"""
        <section class='band-card state-{html.escape(state.lower())}'>
          <div class='card-top'><div><div class='band'>{html.escape(str(b['band']).upper())}</div><div class='state'>{_state_icon(state)} {html.escape(state)}</div></div><div class='score'>{score}</div></div>
          <div class='meter'><i style='width:{max(0,min(score,100))}%'></i></div>
          <div class='target'>{region}</div>
          <div class='facts'>
            <span>🧭 {direction}</span>
            <span>📏 {f'{target_dx:,}'.replace(',', '.') + ' km' if target_dx else '—'}</span>
            <span>🎯 {html.escape(conf_display)}</span>
            <span>📈 {trend_text}</span>
          </div>
          <div class='chips'>{_country_chips(countries_display)}</div>
          {source_lines}
        </section>""")

    dx_cards: list[str] = []
    for item in (live_dx.get("stations", []) or [])[:int(settings.dx_live_limit)]:
        snr = item.get("best_snr")
        snr_text = f"{float(snr):+.0f} dB" if snr is not None else "—"
        dist = item.get("distance_km")
        dist_text = f"{int(dist):,} km".replace(",", ".") if dist else "—"
        age = int(item.get("age_seconds") or 0)
        age_text = f"{age}s" if age < 60 else f"{age//60}m"
        rbn = int(item.get("rbn_rx") or 0)
        rbn_text = f" · 📻 RBN {rbn}" if rbn else ""
        rarity_stars = int(item.get("rarity_stars") or 0)
        rare_badge = f"<span class='dx-rare'>🦄 {'⭐' * rarity_stars}</span>" if rarity_stars >= 2 else ""
        region = html.escape(str(item.get("region") or "—"))
        modes = "/".join(str(x) for x in (item.get("modes") or [])) or "—"
        dx_cards.append(f"""
        <article class='dx-card'>
          <div class='dx-top'><span class='dx-label'>{html.escape(str(item.get('highlight_label') or '✨ interessant'))}</span><span class='dx-band'>{html.escape(str(item.get('band') or '').upper())}</span></div>
          <div class='dx-call'>{html.escape(str(item.get('call') or '—'))}</div>
          <div class='dx-country'>{html.escape(str(item.get('name') or '—'))} · {region}</div>
          <div class='dx-meta'>🧭 {html.escape(str(item.get('direction_label') or 'keine Locator-Daten'))} · 📏 {dist_text}</div>
          <div class='dx-meta'>📶 {snr_text} · 👂 {int(item.get('local_rx') or 0)} {'regionale Spotter' if mode == 'ssb' else ('lokale Skimmer' if mode == 'cw' else 'lokale RX')}{rbn_text}</div>
          <div class='dx-foot'><span>{html.escape(modes)} · vor {age_text} · Highlight {int(item.get('highlight_score') or 0)}/100</span>{rare_badge}</div>
        </article>""")
    if dx_cards:
        dx_body = "<div class='dx-grid'>" + "".join(dx_cards) + "</div>"
    else:
        dx_body = f"<div class='rare-learning'>Aktuell keine {html.escape(mode.upper())}-DX-Stationen in den letzten {int(settings.dx_live_minutes)} Minuten erkannt.</div>"

    rare_cards: list[str] = []
    for item in rare.get("stations", []):
        stars = "⭐" * int(item.get("stars") or 0)
        snr = item.get("best_snr")
        snr_text = f"{float(snr):+.0f} dB" if snr is not None else "—"
        dist = item.get("distance_km")
        dist_text = f"{int(dist):,} km".replace(",", ".") if dist else "—"
        rare_cards.append(f"""
        <article class='rare-card'>
          <div class='rare-top'><span class='rare-stars'>{html.escape(stars)}</span><span class='rare-band'>{html.escape(str(item.get('band') or '').upper())}</span></div>
          <div class='rare-call'>{html.escape(str(item.get('call') or '—'))}</div>
          <div class='rare-country'>{html.escape(str(item.get('name') or '—'))}</div>
          <div class='rare-meta'>🧭 {html.escape(str(item.get('direction_label') or 'keine Locator-Daten'))} · 📏 {dist_text}</div>
          <div class='rare-meta'>📶 {snr_text} · 👂 {int(item.get('local_rx') or 0)} lokale RX</div>
          <div class='rare-foot'>{html.escape(str(item.get('rarity_label') or ''))} · gesehen an {int(item.get('seen_days') or 0)}/{int(item.get('observed_days') or 0)} Tagen</div>
        </article>""")

    learning_days = max([int(v.get("observed_days") or 0) for v in (rare.get("learning") or {}).values()] or [0])
    if rare_cards:
        rare_body = "<div class='rare-grid'>" + "".join(rare_cards) + "</div>"
    elif learning_days < settings.rare_min_learning_days and not settings.rare_watch_dxcc:
        rare_body = f"<div class='rare-learning'>🧠 Lernphase: aktuell bis zu <b>{learning_days}/{settings.rare_min_learning_days}</b> Beobachtungstage. Die persönliche Rarität wird danach automatisch belastbarer.</div>"
    else:
        rare_body = f"<div class='rare-learning'>Keine seltenen DX-Stationen in den letzten {int(settings.rare_live_minutes)} Minuten erkannt.</div>"

    # Dashboard Intelligence: alert/highlight center.
    alert_cards: list[str] = []
    for item in highlights.get("items", []):
        severity = max(1, min(4, int(item.get("severity") or 1)))
        alert_cards.append(
            f"<article class='alert-card sev-{severity}'><b>{html.escape(str(item.get('title') or 'Hinweis'))}</b>"
            f"<span>{html.escape(str(item.get('text') or ''))}</span></article>"
        )
    alert_body = "".join(alert_cards)

    # Six-hour score history with five-minute buckets.
    activity_cards: list[str] = []
    for band_info in activity.get("bands", []):
        points = band_info.get("points") or []
        current_score = band_info.get("current_score")
        d30 = _point_delta(points, 30 * 60)
        d2h = _point_delta(points, 2 * 3600)
        d30_text = "—" if d30 is None else f"{d30:+d}"
        d2h_text = "—" if d2h is None else f"{d2h:+d}"
        current_text = "—" if current_score is None else str(int(current_score))
        avg_text = "—" if band_info.get("average_score") is None else f"{float(band_info['average_score']):.0f}"
        activity_cards.append(f"""
        <article class='trend-card'>
          <div class='trend-head'><b>{html.escape(str(band_info.get('band') or '').upper())}</b><strong>{current_text}</strong></div>
          {_score_sparkline(points)}
          <div class='trend-meta'><span>30m <b>{d30_text}</b></span><span>2h <b>{d2h_text}</b></span><span>Ø <b>{avg_text}</b></span><span>Max <b>{int(band_info.get('max_score') or 0)}</b></span></div>
        </article>""")
    activity_body = "".join(activity_cards)

    # Opening timeline for the current local day.
    timeline_rows: list[str] = []
    for event in timeline.get("events", []):
        countries = ", ".join(str(c.get("name") or "") for c in (event.get("countries") or [])[:2] if c.get("name"))
        region = str(event.get("dominant_region") or "—")
        direction = str(event.get("direction_label") or "—")
        if countries:
            region = f"{region} · {countries}"
        end_text = "<span class='live-badge'>LIVE</span>" if event.get("active") else f"<time data-ts='{int(event.get('end_ts') or 0)}' data-timeonly='1'>—</time>"
        timeline_state = str(event.get("max_state") or "OPEN").lower()
        timeline_rows.append(f"""
        <div class='timeline-item'>
          <div class='timeline-dot tl-{html.escape(timeline_state)}'></div>
          <div class='timeline-time'><time data-ts='{int(event.get('start_ts') or 0)}' data-timeonly='1'>—</time><small>{end_text}</small></div>
          <div class='timeline-main'><b>{html.escape(str(event.get('band') or '').upper())} · {html.escape(str(event.get('max_state') or 'OPEN'))}</b><span>{html.escape(region)}</span><small>🧭 {html.escape(direction)} · Peak {int(event.get('max_score') or 0)}/100 · {_duration(event.get('duration_seconds'))}</small></div>
        </div>""")
    timeline_body = "".join(timeline_rows) if timeline_rows else "<div class='rare-learning'>Heute wurde noch kein OPEN/STRONG-Ereignis gespeichert.</div>"

    # Day/week comparison. Compare equal elapsed periods, not a partial day against a full day.
    def _comparison_block(title: str, data: dict, previous_label: str) -> str:
        metrics = [
            ("Openings", "events", lambda v: str(int(v))),
            ("STRONG", "strong_events", lambda v: str(int(v))),
            ("Offen gesamt", "open_seconds", lambda v: _duration(v)),
            ("Max Score", "max_score", lambda v: f"{int(v)}/100"),
        ]
        cards = []
        for label, key, formatter in metrics:
            item = data.get(key) or {}
            cards.append(
                f"<div class='cmp-card'><span>{html.escape(label)}</span><strong>{html.escape(formatter(item.get('current') or 0))}</strong>"
                f"<small>{html.escape(previous_label)}: {html.escape(formatter(item.get('previous') or 0))} {_delta_badge(item.get('pct'))}</small></div>"
            )
        return f"<div class='cmp-group'><h3>{html.escape(title)}</h3><div class='cmp-grid'>{''.join(cards)}</div></div>"

    compare_body = _comparison_block("Heute", comparison.get("day") or {}, "gestern bis jetzt") + _comparison_block("Diese Woche", comparison.get("week") or {}, "Vorwoche bis jetzt")
    day_bands = {x.get("band"): x for x in (comparison.get("day") or {}).get("bands", [])}
    week_bands = {x.get("band"): x for x in (comparison.get("week") or {}).get("bands", [])}
    compare_rows: list[str] = []
    for band in layer_bands:
        d = day_bands.get(band) or {}
        w = week_bands.get(band) or {}
        compare_rows.append(
            f"<tr><td><b>{html.escape(band.upper())}</b></td>"
            f"<td>{int(d.get('events') or 0)} / {int(d.get('events_previous') or 0)}</td>"
            f"<td>{_duration(d.get('open_seconds'))} / {_duration(d.get('open_seconds_previous'))}</td>"
            f"<td>{int(w.get('events') or 0)} / {int(w.get('events_previous') or 0)}</td>"
            f"<td>{_duration(w.get('open_seconds'))} / {_duration(w.get('open_seconds_previous'))}</td></tr>"
        )
    compare_table = "".join(compare_rows)

    # Live DX map points. The map stays useful with zero spots because QTH and grayline still render.
    qlat, qlon = locator_to_latlon(settings.qth_locator)
    mode_labels = {"ssb": "🎙️ SSB", "cw": "📻 CW", "digital": "💻 DIGITAL"}
    mode_switch = "<div class='period mode-switch'>" + "".join(
        f"<a class='{'active' if mode == key else ''}' href='/?days={days}&mode={key}&layer={layer}#live'>{label}</a>"
        for key, label in mode_labels.items()
    ) + "</div>"
    layer_labels = {
        "hf": "🌐 HF + 6 m",
        "vhf": "📡 4 m · 2 m · 70 cm · 23 cm",
    }
    layer_switch = "<div class='period layer-switch'>" + "".join(
        f"<a class='{'active' if layer == key else ''}' href='/?days={days}&mode={mode}&layer={key}#live'>{label}</a>"
        for key, label in layer_labels.items()
    ) + "</div>"
    map_points = []
    for item in live_dx.get("stations", []) or []:
        if item.get("tx_lat") is None or item.get("tx_lon") is None:
            continue
        map_points.append({
            "call": str(item.get("call") or "—"),
            "band": str(item.get("band") or "").lower(),
            "name": str(item.get("name") or "DX"),
            "lat": float(item["tx_lat"]),
            "lon": float(item["tx_lon"]),
            "distance_km": int(item.get("distance_km") or 0),
            "highlight_score": int(item.get("highlight_score") or 0),
            "local_rx": int(item.get("local_rx") or 0),
            "best_snr": item.get("best_snr"),
        })
    map_points_json = json.dumps(map_points, ensure_ascii=False).replace("</", "<\\/")
    map_filters = "".join(["<button class='map-filter active' data-band='all'>Alle</button>"] + [
        f"<button class='map-filter' data-band='{html.escape(b)}'>{html.escape(b.upper())}</button>" for b in layer_bands
    ])

    # V1.11 decision layer: radar, all-mode matrix, compass and today's best DX.
    radar_cards: list[str] = []
    for idx, item in enumerate((decision.get("radar") or {}).get("items", [])):
        delta = item.get("delta_30m")
        delta_text = "neu" if delta is None else f"{int(delta):+d} in 30m"
        badge = "🏆 BESTE CHANCE" if idx == 0 else html.escape(str(item.get("mode_label") or item.get("mode") or ""))
        radar_cards.append(
            f"<article class='radar-card {'radar-best' if idx == 0 else ''}'>"
            f"<div class='radar-top'><span>{badge}</span><b>{html.escape(str(item.get('band') or '').upper())} · {html.escape(str(item.get('state') or 'CLOSED'))}</b></div>"
            f"<div class='radar-score'>{int(item.get('score') or 0)}<small>/100</small></div>"
            f"<div class='radar-meta'>{html.escape(str(item.get('mode_label') or ''))} · 📈 {html.escape(delta_text)}</div>"
            f"<div class='radar-meta'>🌍 {html.escape(str(item.get('region') or '—'))} · 🧭 {html.escape(str(item.get('direction_label') or 'keine Locator-Daten'))}</div>"
            f"<div class='radar-meta'>📡 {int(item.get('unique_tx') or 0)} Stationen · {int(item.get('unique_rx') or 0)} Quellen</div></article>"
        )
    radar_body = "".join(radar_cards) if radar_cards else "<div class='rare-learning'>Aktuell keine Band-/Mode-Kombination oberhalb der Radar-Mindestschwelle.</div>"

    matrix_rows: list[str] = []
    for row in (decision.get("matrix") or {}).get("bands", []):
        cells = []
        for m in ("ssb", "cw", "digital"):
            cell = (row.get("modes") or {}).get(m) or {}
            state = str(cell.get("state") or "CLOSED")
            score = int(cell.get("score") or 0)
            selected_class = " matrix-selected" if m == mode else ""
            cells.append(f"<td class='matrix-cell matrix-{html.escape(state.lower())}{selected_class}'><b>{_state_icon(state)} {score}</b><small>{html.escape(state)}</small></td>")
        matrix_rows.append(f"<tr><td><b>{html.escape(str(row.get('band') or '').upper())}</b></td>{''.join(cells)}</tr>")
    matrix_body = "".join(matrix_rows)

    compass_cards: list[str] = []
    for item in (decision.get("compass") or {}).get("items", []):
        deg = int(item.get("sector") or 0)
        compass_cards.append(
            f"<article class='compass-card'><div class='compass-face'><span class='north'>N</span><i class='compass-arrow' style='transform:rotate({deg}deg)'>↑</i><span class='compass-deg'>{deg}°</span></div>"
            f"<div><b>{html.escape(str(item.get('band') or '').upper())} · {html.escape(str(item.get('mode_label') or ''))}</b>"
            f"<span>{html.escape(str(item.get('direction_label') or 'keine Locator-Daten'))}</span><small>{html.escape(str(item.get('region') or '—'))} · Confidence {int(item.get('confidence_pct') or 0)}% · Score {int(item.get('score') or 0)}</small></div></article>"
        )
    compass_body = "".join(compass_cards) if compass_cards else "<div class='rare-learning'>Noch keine ausreichend belastbare Richtung für den Richtungs-Kompass.</div>"

    best_dx_rows: list[str] = []
    for item in (decision.get("best_dx") or {}).get("stations", []):
        dist = item.get("distance_km")
        dist_text = f"{int(dist):,} km".replace(",", ".") if dist else "—"
        best_dx_rows.append(
            f"<tr><td><b>{html.escape(str(item.get('band') or '').upper())}</b></td><td><b>{html.escape(str(item.get('call') or '—'))}</b></td>"
            f"<td>{html.escape(str(item.get('name') or '—'))}</td><td>{html.escape(str(item.get('region') or '—'))}</td>"
            f"<td>{dist_text}</td><td>{html.escape(str(item.get('direction_label') or 'keine Locator-Daten'))}</td></tr>"
        )
    best_dx_body = "".join(best_dx_rows) if best_dx_rows else "<tr><td colspan='6' class='muted'>Heute noch keine passenden Stationen mit verwertbaren Daten.</td></tr>"

    vhf_intel_html = ""
    if vhf_intel:
        mechanisms = [
            ("🌫️ Tropo", vhf_intel.get("tropo") or {}, "Fernverbindungen auf 2 m / 70 cm / 23 cm"),
            ("⚡ Sporadic-E", vhf_intel.get("sporadic_e") or {}, "Distanzmuster auf 4 m / 2 m"),
            ("☄️ Meteor Scatter", vhf_intel.get("meteor_scatter") or {}, "MSK144 / FSK441 / JT6M"),
            ("🌌 Aurora-Potenzial", vhf_intel.get("aurora") or {}, "NOAA-Geomagnetik + nördliche VHF-Aktivität"),
        ]
        mech_cards = []
        for title, data, subtitle in mechanisms:
            score = int(data.get("score") or 0)
            label = str(data.get("label") or "—")
            extra = ""
            if title.startswith("🌫️"):
                extra = (
                    f"{int(data.get('unique_tx') or 0)} Fernstationen · "
                    f"max plausibel {int(data.get('max_distance_km') or 0):,} km · "
                    f"Persistenz {int(data.get('persistent_bands') or 0)} Band/Bänder"
                ).replace(",", ".")
                excluded_extreme = int(data.get('excluded_extreme_paths') or 0)
                if excluded_extreme:
                    extra += (
                        f" · {excluded_extreme} extreme Pfade separat (max "
                        f"{int(data.get('extreme_max_distance_km') or 0):,} km)"
                    ).replace(",", ".")
            elif title.startswith("⚡"):
                bands_txt = ", ".join(str(x).upper() for x in (data.get("bands") or [])) or "—"
                extra = f"{int(data.get('unique_tx') or 0)} Stationen · {bands_txt} · {html.escape(str(data.get('direction_label') or 'unbekannt'))}"
            elif title.startswith("☄️"):
                extra = f"{int(data.get('reports') or 0)} Reports · {int(data.get('unique_tx') or 0)} Stationen"
            else:
                extra = f"Kp {float(data.get('kp') or 0):.1f} · G{int(data.get('g_scale') or 0)} · nördliche VHF-TX {int(data.get('north_vhf_tx') or 0)}"
            mech_cards.append(
                f"<article class='vhf-mech'><div class='vhf-mech-head'><b>{html.escape(title)}</b><strong>{score}<small>/100</small></strong></div>"
                f"<div class='vhf-mech-label'>{html.escape(label)}</div><div class='vhf-mech-sub'>{html.escape(subtitle)}</div>"
                f"<div class='vhf-mech-extra'>{html.escape(extra)}</div><div class='vhf-mech-basis'>{html.escape(str(data.get('basis') or ''))}</div></article>"
            )
        beacon_rows = []
        for bcn in (vhf_intel.get("beacons") or {}).get("beacons", []):
            age = int(bcn.get("age_seconds") or 0)
            age_text = f"{age//60} Min." if age >= 60 else f"{age}s"
            dist = bcn.get("distance_km")
            dist_text = f"{int(dist):,} km".replace(",", ".") if dist else "—"
            freq = bcn.get("frequency_khz")
            freq_text = f"{float(freq):.1f} kHz" if freq else "—"
            beacon_rows.append(
                f"<tr><td><b>{html.escape(str(bcn.get('band') or '').upper())}</b></td><td><b>{html.escape(str(bcn.get('call') or '—'))}</b></td>"
                f"<td>{html.escape(str(bcn.get('name') or '—'))}</td><td>{freq_text}</td><td>{dist_text}</td>"
                f"<td>{html.escape(str(bcn.get('direction_label') or 'unbekannt'))}</td><td>{int(bcn.get('unique_rx') or 0)}</td><td>vor {age_text}</td></tr>"
            )
        beacon_body = "".join(beacon_rows) if beacon_rows else "<tr><td colspan='8' class='muted'>In den letzten 24 Stunden wurde kein eindeutig als Beacon erkennbarer Spot gefunden.</td></tr>"
        strongest = vhf_intel.get("strongest_hint") or {}
        vhf_intel_html = (
            "<section class='panel' id='vhf-intel'><div class='panel-head'><div><h2>📡 VHF Propagation Intelligence</h2>"
            "<span class='muted'>Beobachtungsbasierte Hinweise für 4 m / 2 m / 70 cm / 23 cm · keine Mechanismus-Diagnose wird erfunden</span></div>"
            f"<div class='vhf-best'>Stärkster Hinweis: <b>{html.escape(str(strongest.get('mechanism') or '—'))}</b> · {int(strongest.get('score') or 0)}/100</div></div>"
            f"<div class='vhf-mech-grid'>{''.join(mech_cards)}</div>"
            "<div class='panel-head vhf-beacon-head'><div><h3>📡 Beacon Monitor · letzte 24 h</h3><span class='muted'>Nur explizite /B-, /BEACON- oder als Beacon bezeichnete Spots</span></div></div>"
            f"<div class='table-wrap'><table><thead><tr><th>Band</th><th>Beacon</th><th>DXCC</th><th>Frequenz</th><th>Entfernung</th><th>Richtung</th><th>RX</th><th>Zuletzt</th></tr></thead><tbody>{beacon_body}</tbody></table></div>"
            f"<div class='vhf-disclaimer'>{html.escape(str(vhf_intel.get('disclaimer') or ''))}</div></section>"
        )

    stat_rows: list[str] = []
    for s in stats["bands"]:
        top_sector = s.get("top_sector")
        sector = sector_label(top_sector) if top_sector is not None else "—"
        hour = s.get("top_start_hour_utc")
        stat_rows.append(f"""
        <tr>
          <td><b>{html.escape(str(s['band']).upper())}</b></td>
          <td>{int(s['events'])}</td>
          <td>{int(s['strong_events'])}</td>
          <td>{_duration(s['total_seconds'])}</td>
          <td>{_duration(s['average_duration_seconds'])}</td>
          <td>{int(s['max_score'])}/100</td>
          <td>{html.escape(str(s.get('top_region') or '—'))}</td>
          <td>{html.escape(sector)}</td>
          <td>{f'{int(hour):02d}:00 UTC' if hour is not None else '—'}</td>
        </tr>""")

    hist_rows: list[str] = []
    for e in history:
        countries = ", ".join(str(c.get("name") or "") for c in (e.get("countries") or [])[:3] if c.get("name")) or "—"
        end_cell = "<span class='live-badge'>LIVE</span>" if e.get("active") else f"<time data-ts='{int(e.get('end_ts') or 0)}'>—</time>"
        hist_rows.append(f"""
        <tr>
          <td><b>{html.escape(str(e['band']).upper())}</b></td>
          <td>{_state_icon(str(e.get('max_state') or 'OPEN'))} {html.escape(str(e.get('max_state') or 'OPEN'))}</td>
          <td><time data-ts='{int(e['start_ts'])}'>—</time></td>
          <td>{end_cell}</td>
          <td>{_duration(e.get('duration_seconds'))}</td>
          <td>{int(e.get('max_score') or 0)}/100</td>
          <td>{html.escape(str(e.get('dominant_region') or '—'))}</td>
          <td>{html.escape(str(e.get('direction_label') or '—'))}</td>
          <td>{html.escape(countries)}</td>
        </tr>""")

    source_cards = []
    for s in snap["sources"]:
        status = str(s.get("status") or "UNKNOWN")
        cls = "ok" if status == "LIVE" else ("warn" if status in {"DEGRADED", "DISABLED"} else "bad")
        source_cards.append(
            f"<div class='source {cls}'><b>{html.escape(str(s['source']))}</b><span>{html.escape(status)}</span></div>"
        )

    return f"""<!doctype html>
<html lang='de'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>HAM Spotter {html.escape(settings.qth_locator)}</title>
<style>
:root{{--bg:#0c0f13;--panel:#151a21;--panel2:#11161c;--border:#28313c;--text:#eef3f7;--muted:#91a0af;--good:#38d17a;--watch:#f1c84b;--bad:#ef5866;--fire:#ff8a3d;--accent:#67b7ff}}
*{{box-sizing:border-box}} body{{margin:0;background:linear-gradient(180deg,#0a0d11,#0f1318 38%,#0c0f13);color:var(--text);font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif}}
.wrap{{max-width:1280px;margin:auto;padding:22px}} header{{display:flex;gap:18px;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;margin-bottom:20px}}
h1{{margin:0;font-size:2rem}} .subtitle,.muted{{color:var(--muted)}} .nav a{{color:#cfe8ff;text-decoration:none;margin-left:16px}} .nav a:hover{{text-decoration:underline}}
.summary{{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:12px;margin-bottom:18px}} .summary .box{{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:15px}}
.summary strong{{display:block;font-size:1.55rem;margin-top:4px}} .eyebrow{{font-size:.78rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}}
.weather-bar{{background:linear-gradient(135deg,#171d24,#121820);border:1px solid #32404e;border-radius:14px;padding:13px 15px;margin-bottom:18px}} .weather-title{{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px}} .weather-title b{{font-size:1.02rem}} .weather-title span{{color:var(--muted);font-size:.76rem;margin-left:8px}} .weather-age{{font-size:.75rem;color:#8fbd9f}} .weather-age.stale{{color:#f3c95f}}
.weather-values{{display:grid;grid-template-columns:repeat(8,minmax(92px,1fr));gap:8px;position:relative}} .weather-values>div{{background:#0f141a;border:1px solid #27323d;border-radius:10px;padding:8px 9px;min-width:0}} .weather-values span{{display:block;color:var(--muted);font-size:.68rem;text-transform:uppercase;letter-spacing:.05em}} .weather-values strong{{display:block;font-size:.92rem;margin-top:3px;white-space:nowrap}} .wx-good{{color:#73e4a1}} .wx-warn{{color:#f1c84b}} .wx-bad{{color:#ff7884}}
.wx-tip{{position:relative;cursor:help;outline:none;transition:border-color .15s,background .15s}} .wx-tip:hover,.wx-tip:focus{{border-color:#587a98;background:#121a22}} .wx-info{{position:absolute;right:7px;top:6px;width:16px;height:16px;border-radius:50%;display:grid;place-items:center;font-style:normal;font-size:.66rem;font-weight:800;color:#9fcfff;border:1px solid #42617d;background:#182532}} .wx-pop{{display:none;position:absolute;z-index:50;left:50%;top:calc(100% + 9px);transform:translateX(-50%);width:min(320px,80vw);background:#081017;border:1px solid #4d6a83;border-radius:10px;padding:10px 11px;color:#dce7ef;font-size:.78rem;line-height:1.42;box-shadow:0 12px 30px rgba(0,0,0,.45);text-transform:none;letter-spacing:0}} .wx-pop b{{display:block;color:#9fd1ff;margin-bottom:4px;font-size:.8rem}} .wx-pop:before{{content:'';position:absolute;left:50%;top:-6px;transform:translateX(-50%) rotate(45deg);width:10px;height:10px;background:#081017;border-left:1px solid #4d6a83;border-top:1px solid #4d6a83}} .wx-tip:hover .wx-pop,.wx-tip:focus .wx-pop{{display:block}}
.weather-assess{{display:grid;grid-template-columns:repeat(4,minmax(160px,1fr));gap:7px;margin-top:9px;color:#cbd6df;font-size:.76rem}} .weather-assess span{{background:#10161c;border-radius:8px;padding:6px 8px}}
.alert-center{{background:linear-gradient(135deg,#151c24,#111820);border:1px solid #344353;border-radius:15px;padding:16px;margin:0 0 18px}} .alert-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:9px}} .alert-card{{display:flex;flex-direction:column;gap:5px;background:#0f151b;border:1px solid #293743;border-left:4px solid #526575;border-radius:10px;padding:11px 12px}} .alert-card span{{color:#bdc9d4;font-size:.8rem;line-height:1.35}} .alert-card.sev-2{{border-left-color:#e2be4e}} .alert-card.sev-3{{border-left-color:#ff8a3d}} .alert-card.sev-4{{border-left-color:#ef5866;background:#1b1115}}
.activity-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:11px}} .trend-card{{background:#10161c;border:1px solid #2a3540;border-radius:12px;padding:12px}} .trend-head{{display:flex;justify-content:space-between;align-items:center}} .trend-head b{{font-size:1.05rem}} .trend-head strong{{font-size:1.4rem}} .trend-svg{{width:100%;height:82px;display:block;margin:8px 0 4px;background:linear-gradient(180deg,rgba(56,209,122,.04),transparent)}} .score-line{{fill:none;stroke:#77bfff;stroke-width:2.3;vector-effect:non-scaling-stroke}} .thr{{stroke-width:.7;stroke-dasharray:4 5;vector-effect:non-scaling-stroke;opacity:.5}} .thr.watch{{stroke:#f1c84b}} .thr.open{{stroke:#38d17a}} .thr.strong{{stroke:#ff8a3d}} .trend-meta{{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;font-size:.72rem;color:var(--muted)}} .trend-meta b{{color:#dce8f2}} .trend-empty{{height:82px;display:grid;place-items:center;text-align:center;color:var(--muted);font-size:.75rem}}
.map-controls{{display:flex;gap:6px;flex-wrap:wrap;margin:-3px 0 10px}} .map-filter{{appearance:none;background:#121a22;border:1px solid #344556;color:#cfe8ff;border-radius:999px;padding:5px 10px;cursor:pointer}} .map-filter.active{{background:#29425a;border-color:#6091bc;color:white}} .world-map{{position:relative;aspect-ratio:2/1;max-height:620px;background:#0d141c;border:1px solid #2b3d4b;border-radius:12px;overflow:hidden}} .world-map img,.world-map canvas{{position:absolute;inset:0;width:100%;height:100%;display:block}} .world-map canvas{{z-index:2}} .map-legend{{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:.75rem;margin-top:8px}} .map-tip{{position:absolute;z-index:5;display:none;pointer-events:none;background:#071018;border:1px solid #5c7183;border-radius:8px;padding:8px 9px;font-size:.75rem;box-shadow:0 8px 22px rgba(0,0,0,.45);max-width:230px}}
.timeline{{position:relative;margin-left:7px}} .timeline:before{{content:'';position:absolute;left:8px;top:4px;bottom:4px;width:1px;background:#344350}} .timeline-item{{position:relative;display:grid;grid-template-columns:28px 105px 1fr;gap:8px;padding:7px 0}} .timeline-dot{{width:17px;height:17px;border-radius:50%;background:#284154;border:3px solid #79bce8;z-index:1;margin-top:3px;box-shadow:0 0 0 3px #151a21}} .timeline-dot.tl-open{{border-color:#38d17a}} .timeline-dot.tl-strong{{border-color:#ff8a3d;background:#4c2a19}} .timeline-time{{display:flex;flex-direction:column;font-weight:750}} .timeline-time small{{margin-top:2px;color:var(--muted);font-weight:400}} .timeline-main{{display:flex;flex-direction:column;gap:2px}} .timeline-main span{{color:#cbd6df;font-size:.84rem}} .timeline-main small{{color:var(--muted)}}
.compare-wrap{{display:grid;grid-template-columns:1fr 1fr;gap:14px}} .cmp-group{{background:#10161c;border:1px solid #2a3540;border-radius:12px;padding:12px}} .cmp-group h3{{margin:0 0 9px;font-size:.96rem}} .cmp-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px}} .cmp-card{{background:#0c1218;border:1px solid #24313c;border-radius:9px;padding:9px}} .cmp-card>span{{display:block;color:var(--muted);font-size:.72rem}} .cmp-card strong{{display:block;font-size:1.25rem;margin:2px 0}} .cmp-card small{{color:#9aa9b7}} .cmp-up{{color:#72e69f}} .cmp-down{{color:#ff8b95}} .cmp-neutral{{color:#b8c5d1}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:13px}} .band-card{{background:var(--panel);border:1px solid var(--border);border-top:3px solid #596574;border-radius:15px;padding:16px;min-width:0}}
.state-open{{border-top-color:var(--good)}} .state-strong{{border-top-color:var(--fire)}} .state-watch{{border-top-color:var(--watch)}} .state-closed{{border-top-color:var(--bad)}}
.card-top{{display:flex;justify-content:space-between;gap:12px}} .band{{font-size:1.55rem;font-weight:800}} .state{{font-weight:700;margin-top:3px}} .score{{font-size:2rem;font-weight:800}}
.meter{{height:7px;background:#222b34;border-radius:999px;overflow:hidden;margin:12px 0}} .meter i{{display:block;height:100%;background:linear-gradient(90deg,#5d9cff,#51d07e)}} .target{{font-size:1.05rem;font-weight:750;margin:7px 0 10px}}
.facts{{display:grid;grid-template-columns:1fr 1fr;gap:6px;color:#cbd6df;font-size:.88rem}} .chips{{display:flex;gap:5px;flex-wrap:wrap;margin:12px 0 9px}} .chip{{font-size:.75rem;background:#222a33;border:1px solid #34404c;padding:3px 7px;border-radius:999px}}
.source-line{{font-size:.8rem;color:var(--muted);margin-top:4px}}
.dx-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:11px}} .dx-card{{background:linear-gradient(145deg,#121a22,#11161c);border:1px solid #314253;border-radius:13px;padding:14px}} .dx-top{{display:flex;justify-content:space-between;gap:8px;align-items:center}} .dx-label{{font-size:.76rem;font-weight:800;color:#ffd477}} .dx-band{{font-weight:850;color:#9fd1ff}} .dx-call{{font-size:1.3rem;font-weight:900;margin-top:8px;letter-spacing:.02em}} .dx-country{{font-weight:700;margin:2px 0 9px}} .dx-meta{{font-size:.82rem;color:#cbd6df;margin-top:4px}} .dx-foot{{display:flex;justify-content:space-between;gap:8px;align-items:center;font-size:.73rem;color:var(--muted);margin-top:9px}} .dx-rare{{white-space:nowrap;color:#ffd17a}}
.rare-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:11px}} .rare-card{{background:var(--panel2);border:1px solid #3d3546;border-radius:13px;padding:14px}} .rare-top{{display:flex;justify-content:space-between;gap:8px}} .rare-stars{{letter-spacing:1px}} .rare-band{{font-weight:800;color:#cfe8ff}} .rare-call{{font-size:1.25rem;font-weight:850;margin-top:8px}} .rare-country{{font-weight:700;margin:2px 0 9px}} .rare-meta{{font-size:.82rem;color:#cbd6df;margin-top:4px}} .rare-foot{{font-size:.75rem;color:var(--muted);margin-top:9px}} .rare-learning{{padding:14px;background:var(--panel2);border:1px dashed #3c4652;border-radius:12px;color:#cbd6df}}
.decision-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:11px}}.radar-card{{background:#101821;border:1px solid #30404f;border-radius:13px;padding:14px}}.radar-card.radar-best{{border-color:#d4a94b;box-shadow:0 0 0 1px rgba(212,169,75,.18)}}.radar-top{{display:flex;justify-content:space-between;gap:8px;font-size:.78rem;color:#aebdca}}.radar-score{{font-size:2rem;font-weight:900;margin:8px 0 3px}}.radar-score small{{font-size:.8rem;color:var(--muted)}}.radar-meta{{font-size:.8rem;color:#cbd6df;margin-top:4px}}.matrix-table{{min-width:560px}}.matrix-cell{{text-align:center;border-left:1px solid #26303a}}.matrix-cell b{{display:block}}.matrix-cell small{{display:block;font-size:.68rem;color:var(--muted);margin-top:2px}}.matrix-watch{{background:rgba(241,200,75,.06)}}.matrix-open{{background:rgba(56,209,122,.06)}}.matrix-strong{{background:rgba(255,138,61,.08)}}.matrix-selected{{outline:1px solid rgba(119,191,255,.35);outline-offset:-2px}}.compass-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:11px}}.compass-card{{display:grid;grid-template-columns:96px 1fr;gap:12px;align-items:center;background:#10161c;border:1px solid #2a3540;border-radius:13px;padding:12px}}.compass-card>div:last-child{{display:flex;flex-direction:column;gap:4px}}.compass-card small{{color:var(--muted)}}.compass-face{{position:relative;width:88px;height:88px;border:1px solid #4b5967;border-radius:50%;background:radial-gradient(circle,#18232d 0 38%,#10171d 39% 100%)}}.compass-face:before,.compass-face:after{{content:'';position:absolute;background:#465563;opacity:.55}}.compass-face:before{{left:43px;top:9px;width:1px;height:70px}}.compass-face:after{{top:43px;left:9px;width:70px;height:1px}}.compass-face .north{{position:absolute;top:2px;left:39px;font-size:.65rem;font-weight:800}}.compass-arrow{{position:absolute;left:34px;top:14px;width:20px;height:60px;transform-origin:10px 30px;font-size:2.5rem;line-height:54px;font-style:normal;text-align:center;color:#ffbd64;text-shadow:0 0 10px rgba(255,189,100,.3)}}.compass-deg{{position:absolute;bottom:4px;left:0;right:0;text-align:center;font-size:.65rem;color:#dce8f2}}.best-dx-note{{color:var(--muted);font-size:.76rem;margin-top:8px}}.vhf-mech-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:11px}}.vhf-mech{{background:#101821;border:1px solid #31404d;border-radius:13px;padding:14px}}.vhf-mech-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}}.vhf-mech-head strong{{font-size:1.5rem}}.vhf-mech-head small{{font-size:.7rem;color:var(--muted)}}.vhf-mech-label{{font-weight:800;margin-top:5px}}.vhf-mech-sub,.vhf-mech-extra{{font-size:.78rem;color:#cbd6df;margin-top:5px}}.vhf-mech-basis{{font-size:.7rem;color:var(--muted);margin-top:8px;line-height:1.35}}.vhf-best{{background:#18222c;border:1px solid #344353;border-radius:999px;padding:7px 11px;font-size:.78rem}}.vhf-beacon-head{{margin-top:18px}}.vhf-beacon-head h3{{margin:0 0 5px}}.vhf-disclaimer{{font-size:.72rem;color:var(--muted);margin-top:10px}}section.panel{{background:var(--panel);border:1px solid var(--border);border-radius:15px;padding:18px;margin-top:18px}} .panel-head{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}} h2{{margin:0 0 14px;font-size:1.2rem}}
.period a{{display:inline-block;color:#cfe8ff;text-decoration:none;border:1px solid #34404c;border-radius:999px;padding:5px 10px;margin-left:6px}} .period a.active{{background:#263545}}
.table-wrap{{overflow-x:auto}} table{{width:100%;border-collapse:collapse;min-width:850px}} th,td{{padding:10px 9px;border-bottom:1px solid #26303a;text-align:left;white-space:nowrap}} th{{color:#9eacb9;font-size:.77rem;text-transform:uppercase;letter-spacing:.05em}} td{{font-size:.88rem}}
.live-badge{{display:inline-block;background:#173d2a;color:#71e9a2;border:1px solid #2a6a47;padding:2px 7px;border-radius:999px;font-weight:700;font-size:.74rem}}
.sources{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:9px}} .source{{background:var(--panel2);border:1px solid var(--border);padding:11px;border-radius:10px;display:flex;justify-content:space-between;gap:8px}} .source.ok span{{color:#75e9a4}} .source.warn span{{color:#f5ce58}} .source.bad span{{color:#ff7884}}
.footer{{color:var(--muted);font-size:.78rem;margin:18px 2px}} code{{color:#cfe8ff}}
@media(max-width:1050px){{.grid{{grid-template-columns:repeat(3,1fr)}} .weather-values{{grid-template-columns:repeat(4,1fr)}} .weather-assess{{grid-template-columns:1fr 1fr}} .compare-wrap{{grid-template-columns:1fr}}}} @media(max-width:720px){{.wrap{{padding:14px}} .summary{{grid-template-columns:1fr 1fr}} .grid{{grid-template-columns:1fr}} .nav{{width:100%}} .nav a{{margin:0 12px 0 0}} .weather-values{{grid-template-columns:1fr 1fr}} .weather-assess{{grid-template-columns:1fr}} .wx-pop{{position:fixed;left:14px;right:14px;top:auto;bottom:18px;transform:none;width:auto}} .wx-pop:before{{display:none}} .timeline-item{{grid-template-columns:28px 72px 1fr}} .cmp-grid{{grid-template-columns:1fr}} .map-controls{{max-height:68px;overflow:auto}}}}
</style></head>
<body><div class='wrap'>
<header><div><h1>📡 HAM Spotter</h1><div class='subtitle'>{html.escape(settings.callsign)} · QTH {html.escape(settings.qth_locator)} · V{VERSION}</div></div><nav class='nav'><a href='#radar'>Radar</a><a href='#vhf-intel'>VHF</a><a href='#matrix'>Matrix</a><a href='#highlights'>Jetzt</a><a href='#funkwetter'>Funkwetter</a><a href='#live'>Bänder</a><a href='#activity'>Verlauf</a><a href='#map'>DX-Karte</a><a href='#timeline'>Timeline</a><a href='#compare'>Vergleich</a><a href='#dx'>Live DX</a><a href='#rare'>Rare</a><a href='#sources'>Quellen</a></nav></header>

<div class='summary'>
  <div class='box'><span class='eyebrow'>Bänder offen · {html.escape(layer_label(layer))}</span><strong>{open_now}/{len(snap['bands'])}</strong></div>
  <div class='box'><span class='eyebrow'>Spots letzte Stunde</span><strong>{f'{total_spots:,}'.replace(',', '.')}</strong></div>
  <div class='box'><span class='eyebrow'>RBN Nodes</span><strong>{int(snap['rbn_nodes'])}</strong></div>
  <div class='box'><span class='eyebrow'>Openings · {days} Tage</span><strong>{int(stats['total_events'])}</strong></div>
</div>

<section class='panel layer-panel'><div class='panel-head'><div><h2>🧭 Band-Schicht · {html.escape(layer_label(layer))}</h2><span class='muted'>Getrennte Ansicht: bisherige HF/6-m-Ebene oder 4 m / 2 m / 70 cm / 23 cm. Daten und Historien bleiben getrennt nach Band.</span></div>{layer_switch}</div></section>

<section class='panel mode-panel'><div class='panel-head'><div><h2>🎛️ Ausbreitungsmodus</h2><span class='muted'>SSB = echte DX-Cluster-Spots · CW = lokale RBN-Skimmer · DIGITAL = PSK Reporter + RBN FT8</span></div>{mode_switch}</div></section>

{weather_html}

{vhf_intel_html}

<section class='panel' id='radar'><div class='panel-head'><div><h2>🎯 Propagation Radar · Was lohnt sich jetzt?</h2><span class='muted'>Automatische Rangliste über SSB, CW und DIGITAL · Score + Zustand + 30-Minuten-Trend</span></div></div><div class='decision-grid'>{radar_body}</div></section>

<section class='panel' id='matrix'><div class='panel-head'><div><h2>📊 Bandmatrix · {html.escape(layer_label(layer))}</h2><span class='muted'>Alle drei Betriebsarten auf einen Blick · ausgewählter Dashboard-Modus ist markiert</span></div></div><div class='table-wrap'><table class='matrix-table'><thead><tr><th>Band</th><th>🎙️ SSB</th><th>📻 CW</th><th>💻 Digital</th></tr></thead><tbody>{matrix_body}</tbody></table></div></section>

<section class='panel' id='compass'><div class='panel-head'><div><h2>🧭 Richtungs-Kompass</h2><span class='muted'>Nur Richtungen mit ausreichender Datenbasis · keine Richtung wird geraten</span></div></div><div class='compass-grid'>{compass_body}</div></section>

<section class='panel' id='bestdx'><div class='panel-head'><div><h2>🏆 Best DX heute · {html.escape(mode.upper())}</h2><span class='muted'>Heutige Stationen der ausgewählten Betriebsart; Entfernung nur bei belastbarer Locator-Korrelation</span></div></div><div class='table-wrap'><table><thead><tr><th>Band</th><th>Call</th><th>DXCC</th><th>Region</th><th>Entfernung</th><th>Richtung</th></tr></thead><tbody>{best_dx_body}</tbody></table></div><div class='best-dx-note'>Bei SSB/CW kann die Entfernung fehlen, wenn für das Rufzeichen kein verlässlicher Locator aus den vorhandenen Empfangsdaten vorliegt.</div></section>

<section class='alert-center' id='highlights'><div class='panel-head'><h2>🚨 Jetzt interessant</h2><span class='muted'>Automatisch aus Bandstatus, Trend, Live DX und Funkwetter</span></div><div class='alert-grid'>{alert_body}</div></section>

<section id='live'><div class='grid'>{''.join(cards)}</div></section>

<section class='panel' id='activity'><div class='panel-head'><h2>📈 Band-Aktivitätsverlauf · {html.escape(layer_label(layer))} · {html.escape(mode.upper())} · 6 Stunden</h2><span class='muted'>Score-Verlauf · 5-Minuten-Buckets · Schwellen WATCH/OPEN/STRONG</span></div><div class='activity-grid'>{activity_body}</div></section>

<section class='panel' id='map'><div class='panel-head'><h2>🌍 DX-Weltkarte + Grayline · {html.escape(layer_label(layer))}</h2><span class='muted'>Live-DX-Highlights rund um {html.escape(settings.qth_locator)} · Tag/Nacht wird lokal berechnet</span></div><div class='map-controls'>{map_filters}</div><div class='world-map' id='world-map'><img src='/static/world-map.png' alt='Weltkarte'><canvas id='dx-map-canvas' width='1200' height='600'></canvas><div class='map-tip' id='map-tip'></div></div><div class='map-legend'><span>● Live DX</span><span>◆ QTH {html.escape(settings.qth_locator)}</span><span>🌗 dunkle Fläche = Nachtseite · Übergang = Grayline</span><span>{len(map_points)} Stationen auf Karte</span></div></section>

<section class='panel' id='dx'><div class='panel-head'><h2>🌍 Live DX Highlights · {html.escape(layer_label(layer))} · {html.escape(mode.upper())}</h2><span class='muted'>Interessante Stationen jetzt · letzte {int(settings.dx_live_minutes)} Min. · keine Lernphase nötig</span></div>{dx_body}</section>

<section class='panel' id='timeline'><div class='panel-head'><h2>🕒 Opening-Timeline · {html.escape(layer_label(layer))} · Heute</h2><span class='muted'>{html.escape(str(timeline.get('timezone') or settings.dashboard_timezone))} · OPEN/STRONG chronologisch</span></div><div class='timeline'>{timeline_body}</div></section>

<section class='panel' id='compare'><div class='panel-head'><h2>📊 Tages-/Wochenvergleich · {html.escape(layer_label(layer))}</h2><span class='muted'>Faire Vergleichszeiträume: jeweils bis zur gleichen Uhrzeit / zum gleichen Wochenfortschritt</span></div><div class='compare-wrap'>{compare_body}</div><div class='table-wrap' style='margin-top:12px'><table><thead><tr><th>Band</th><th>Heute / gestern</th><th>Offen heute / gestern</th><th>Woche / Vorwoche</th><th>Offen Woche / Vorwoche</th></tr></thead><tbody>{compare_table}</tbody></table></div></section>

<section class='panel' id='rare'><div class='panel-head'><h2>🦄 Persönlich selten – Lernmodell</h2><span class='muted'>Zusatzbewertung für {html.escape(settings.qth_locator)} · blockiert die Live-DX-Anzeige nicht</span></div>{rare_body}</section>



<section class='panel' id='stats'><div class='panel-head'><h2>📈 Opening-Statistik · {html.escape(layer_label(layer))}</h2><div class='period'><a class='{'active' if days==7 else ''}' href='/?days=7&mode={mode}&layer={layer}#stats'>7 Tage</a><a class='{'active' if days==30 else ''}' href='/?days=30&mode={mode}&layer={layer}#stats'>30 Tage</a><a class='{'active' if days==90 else ''}' href='/?days=90&mode={mode}&layer={layer}#stats'>90 Tage</a></div></div>
<div class='table-wrap'><table><thead><tr><th>Band</th><th>Openings</th><th>Strong</th><th>Gesamt offen</th><th>Ø Dauer</th><th>Max Score</th><th>Top Region</th><th>Top Richtung</th><th>Häufigster Start</th></tr></thead><tbody>{''.join(stat_rows)}</tbody></table></div></section>

<section class='panel' id='history'><div class='panel-head'><h2>🕘 Letzte Opening-Ereignisse · {html.escape(layer_label(layer))}</h2><span class='muted'>Richtungswechsel ≥60° werden als neues Segment gespeichert.</span></div>
<div class='table-wrap'><table><thead><tr><th>Band</th><th>Peak</th><th>Start</th><th>Ende</th><th>Dauer</th><th>Max Score</th><th>Zielgebiet</th><th>Richtung</th><th>DXCC</th></tr></thead><tbody>{''.join(hist_rows) if hist_rows else '<tr><td colspan=9 class=muted>Noch keine gespeicherten Opening-Ereignisse.</td></tr>'}</tbody></table></div></section>

<section class='panel' id='sources'><h2>🩺 Datenquellen</h2><div class='sources'>{''.join(source_cards)}</div><div class='footer'>Spots letzte Stunde: <code>{html.escape(json.dumps(snap['spots_last_hour'], ensure_ascii=False))}</code></div></section>
<div class='footer'>Auto-Refresh alle 30 Sekunden · Historie bleibt in SQLite erhalten, auch wenn Roh-Spots nach {int(settings.retention_hours)} Stunden gelöscht werden.</div>
</div>
<script>
for (const el of document.querySelectorAll('time[data-ts]')) {{
  const ts = Number(el.dataset.ts || 0);
  if (ts > 0) {{
    const opts = el.dataset.timeonly ? {{hour:'2-digit',minute:'2-digit'}} : {{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}};
    el.textContent = new Date(ts * 1000).toLocaleString('de-DE', opts);
  }}
}}

const DX_POINTS = {map_points_json};
const QTH = {{lat:{qlat:.5f}, lon:{qlon:.5f}, label:{json.dumps(settings.qth_locator)}}};
const BAND_COLORS = {{'4m':'#ff6b6b','2m':'#ff9f43','70cm':'#54a0ff','23cm':'#5f27cd','6m':'#ff8a3d','10m':'#f1c84b','12m':'#9de36b','15m':'#55d79a','17m':'#56c9df','20m':'#67b7ff','40m':'#a58cff','60m':'#d58aff','80m':'#ef83ba'}};
let activeMapBand = 'all';
let mapMarkers = [];
const mapCanvas = document.getElementById('dx-map-canvas');
const mapTip = document.getElementById('map-tip');
const mapWrap = document.getElementById('world-map');

function deg2rad(v) {{ return v * Math.PI / 180; }}
function normLon(v) {{ while(v > 180) v -= 360; while(v < -180) v += 360; return v; }}
function julianDay(d) {{ return d.getTime()/86400000 + 2440587.5; }}
function subsolarPoint(d) {{
  const jd = julianDay(d), n = jd - 2451545.0;
  const L = deg2rad((280.460 + 0.9856474*n) % 360);
  const g = deg2rad((357.528 + 0.9856003*n) % 360);
  const lambda = L + deg2rad(1.915)*Math.sin(g) + deg2rad(0.020)*Math.sin(2*g);
  const eps = deg2rad(23.439 - 0.0000004*n);
  const ra = Math.atan2(Math.cos(eps)*Math.sin(lambda), Math.cos(lambda));
  const dec = Math.asin(Math.sin(eps)*Math.sin(lambda));
  const gmst = deg2rad((280.46061837 + 360.98564736629*(jd-2451545.0)) % 360);
  return {{lat: dec*180/Math.PI, lon: normLon((ra-gmst)*180/Math.PI)}};
}}
function xyFor(lat, lon, w, h) {{ return {{x:(lon+180)/360*w, y:(90-lat)/180*h}}; }}
function esc(v) {{ return String(v ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]); }}

function drawDxMap() {{
  if (!mapCanvas) return;
  const ctx = mapCanvas.getContext('2d');
  const w = mapCanvas.width, h = mapCanvas.height;
  ctx.clearRect(0,0,w,h);
  const sun = subsolarPoint(new Date());
  const dec = deg2rad(sun.lat), step = 8;
  for (let y=0; y<h; y+=step) {{
    const lat = 90 - (y + step/2) / h * 180;
    const latr = deg2rad(lat);
    for (let x=0; x<w; x+=step) {{
      const lon = (x + step/2) / w * 360 - 180;
      const hourAngle = deg2rad(normLon(lon - sun.lon));
      const cosz = Math.sin(latr)*Math.sin(dec) + Math.cos(latr)*Math.cos(dec)*Math.cos(hourAngle);
      if (cosz < 0) {{
        const alpha = Math.min(0.58, 0.20 + Math.min(0.38, -cosz * 0.50));
        ctx.fillStyle = `rgba(1,5,11,${{alpha}})`;
        ctx.fillRect(x,y,step+1,step+1);
      }} else if (cosz < 0.055) {{
        ctx.fillStyle = 'rgba(255,185,82,.08)';
        ctx.fillRect(x,y,step+1,step+1);
      }}
    }}
  }}

  const q = xyFor(QTH.lat,QTH.lon,w,h);
  ctx.save(); ctx.translate(q.x,q.y); ctx.rotate(Math.PI/4);
  ctx.fillStyle='#ffffff'; ctx.strokeStyle='#0b1117'; ctx.lineWidth=2; ctx.fillRect(-6,-6,12,12); ctx.strokeRect(-6,-6,12,12); ctx.restore();
  ctx.font='bold 15px system-ui'; ctx.fillStyle='#ffffff'; ctx.fillText(QTH.label, q.x+10, q.y-9);

  mapMarkers = [];
  const visible = DX_POINTS.filter(p => activeMapBand === 'all' || p.band === activeMapBand);
  for (const p of visible) {{
    const pt = xyFor(p.lat,p.lon,w,h);
    const radius = 5 + Math.min(5, Math.max(0,(Number(p.highlight_score||45)-45)/11));
    ctx.beginPath(); ctx.arc(pt.x,pt.y,radius,0,Math.PI*2);
    ctx.fillStyle = BAND_COLORS[p.band] || '#ffd477'; ctx.fill();
    ctx.lineWidth=1.6; ctx.strokeStyle='#081018'; ctx.stroke();
    mapMarkers.push({{x:pt.x,y:pt.y,r:Math.max(9,radius+4),data:p}});
  }}
}}

document.querySelectorAll('.map-filter').forEach(btn => btn.addEventListener('click', () => {{
  activeMapBand = btn.dataset.band || 'all';
  document.querySelectorAll('.map-filter').forEach(x => x.classList.toggle('active', x === btn));
  if (mapTip) mapTip.style.display='none';
  drawDxMap();
}}));

if (mapCanvas && mapWrap && mapTip) {{
  mapCanvas.addEventListener('mousemove', ev => {{
    const rect = mapCanvas.getBoundingClientRect();
    const x=(ev.clientX-rect.left)*mapCanvas.width/rect.width, y=(ev.clientY-rect.top)*mapCanvas.height/rect.height;
    const hit = mapMarkers.find(m => Math.hypot(m.x-x,m.y-y) <= m.r);
    if (!hit) {{ mapTip.style.display='none'; return; }}
    const p=hit.data, snr=(p.best_snr===null||p.best_snr===undefined)?'—':`${{Number(p.best_snr).toFixed(0)}} dB`;
    mapTip.innerHTML=`<b>${{esc(p.call)}} · ${{esc(String(p.band).toUpperCase())}}</b><br>${{esc(p.name)}}<br>📏 ${{Number(p.distance_km||0).toLocaleString('de-DE')}} km · 👂 ${{Number(p.local_rx||0)}} RX<br>📶 ${{snr}} · Highlight ${{Number(p.highlight_score||0)}}/100`;
    const wr=mapWrap.getBoundingClientRect();
    mapTip.style.left=Math.min(wr.width-235, Math.max(6, ev.clientX-wr.left+12))+'px';
    mapTip.style.top=Math.min(wr.height-95, Math.max(6, ev.clientY-wr.top+12))+'px';
    mapTip.style.display='block';
  }});
  mapCanvas.addEventListener('mouseleave',()=>mapTip.style.display='none');
  drawDxMap();
}}

setTimeout(()=>location.reload(),30000);
</script></body></html>"""
