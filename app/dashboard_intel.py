from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .config import settings
from .db import band_activity_history, connect


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.dashboard_timezone)
    except Exception:
        return ZoneInfo("UTC")


def _period_metrics(start_ts: int, end_ts: int, bands: Iterable[str] | None = None) -> dict[str, Any]:
    now = int(time.time())
    end_ts = min(int(end_ts), now)
    start_ts = int(start_ts)
    with connect() as con:
        rows = con.execute(
            """SELECT * FROM opening_events
               WHERE start_ts<? AND (end_ts IS NULL OR end_ts>?)
               ORDER BY start_ts""",
            (end_ts, start_ts),
        ).fetchall()

    selected_bands = tuple(str(b).lower() for b in (bands or settings.bands))
    by_band: dict[str, dict[str, Any]] = {
        band: {"band": band, "events": 0, "strong_events": 0, "open_seconds": 0, "max_score": 0}
        for band in selected_bands
    }
    total = {"events": 0, "strong_events": 0, "open_seconds": 0, "max_score": 0}
    for row in rows:
        band = str(row["band"] or "").lower()
        if band not in by_band:
            continue
        row_start = int(row["start_ts"] or 0)
        row_end = int(row["end_ts"] or now)
        overlap = max(0, min(row_end, end_ts) - max(row_start, start_ts))
        started_here = start_ts <= row_start < end_ts
        target = by_band[band]
        if started_here:
            target["events"] += 1
            total["events"] += 1
            if str(row["max_state"] or "") == "STRONG":
                target["strong_events"] += 1
                total["strong_events"] += 1
        if overlap:
            target["open_seconds"] += overlap
            total["open_seconds"] += overlap
            target["max_score"] = max(target["max_score"], int(row["max_score"] or 0))
            total["max_score"] = max(total["max_score"], int(row["max_score"] or 0))

    return {"start_ts": start_ts, "end_ts": end_ts, "total": total, "bands": list(by_band.values())}


def _pct(current: int | float, previous: int | float) -> float | None:
    current = float(current)
    previous = float(previous)
    if previous == 0:
        return None if current == 0 else 100.0
    return round((current - previous) / previous * 100.0, 1)


def comparison_snapshot(now: int | None = None, bands: Iterable[str] | None = None) -> dict[str, Any]:
    now = int(now or time.time())
    tz = _tz()
    local_now = datetime.fromtimestamp(now, tz=tz)

    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_today = local_now - today_start
    yesterday_start = today_start - timedelta(days=1)
    yesterday_same_time = yesterday_start + elapsed_today

    week_start = today_start - timedelta(days=today_start.weekday())
    elapsed_week = local_now - week_start
    prev_week_start = week_start - timedelta(days=7)
    prev_week_same_time = prev_week_start + elapsed_week

    selected_bands = tuple(str(b).lower() for b in (bands or settings.bands))
    today = _period_metrics(int(today_start.timestamp()), now, selected_bands)
    yesterday = _period_metrics(int(yesterday_start.timestamp()), int(yesterday_same_time.timestamp()), selected_bands)
    week = _period_metrics(int(week_start.timestamp()), now, selected_bands)
    prev_week = _period_metrics(int(prev_week_start.timestamp()), int(prev_week_same_time.timestamp()), selected_bands)

    def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for key in ("events", "strong_events", "open_seconds", "max_score"):
            out[key] = {
                "current": a["total"][key],
                "previous": b["total"][key],
                "pct": _pct(a["total"][key], b["total"][key]),
            }
        current_bands = {x["band"]: x for x in a["bands"]}
        previous_bands = {x["band"]: x for x in b["bands"]}
        band_rows = []
        for band in selected_bands:
            ca = current_bands[band]
            pb = previous_bands[band]
            band_rows.append({
                "band": band,
                "events": ca["events"],
                "events_previous": pb["events"],
                "open_seconds": ca["open_seconds"],
                "open_seconds_previous": pb["open_seconds"],
                "max_score": ca["max_score"],
                "max_score_previous": pb["max_score"],
            })
        out["bands"] = band_rows
        return out

    return {
        "timezone": str(tz),
        "generated_at": now,
        "day": compare(today, yesterday),
        "week": compare(week, prev_week),
        "periods": {
            "today": [today["start_ts"], today["end_ts"]],
            "yesterday_same_time": [yesterday["start_ts"], yesterday["end_ts"]],
            "week": [week["start_ts"], week["end_ts"]],
            "previous_week_same_time": [prev_week["start_ts"], prev_week["end_ts"]],
        },
    }


def opening_timeline_today(now: int | None = None, limit: int = 80, bands: Iterable[str] | None = None) -> dict[str, Any]:
    now = int(now or time.time())
    tz = _tz()
    local_now = datetime.fromtimestamp(now, tz=tz)
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_ts = int(start.timestamp())
    with connect() as con:
        rows = con.execute(
            """SELECT * FROM opening_events
               WHERE start_ts<? AND (end_ts IS NULL OR end_ts>?)
               ORDER BY start_ts DESC LIMIT ?""",
            (now + 1, start_ts, max(1, min(int(limit), 200))),
        ).fetchall()
    selected = {str(b).lower() for b in (bands or settings.bands)}
    events = []
    for row in rows:
        if str(row["band"] or "").lower() not in selected:
            continue
        item = dict(row)
        try:
            item["countries"] = json.loads(item.pop("countries_json", "[]") or "[]")
        except Exception:
            item["countries"] = []
        end_ts = item.get("end_ts")
        item["active"] = end_ts is None
        item["duration_seconds"] = max(0, min(int(end_ts or now), now) - max(int(item["start_ts"]), start_ts))
        events.append(item)
    events.reverse()
    return {"timezone": str(tz), "day_start_ts": start_ts, "generated_at": now, "events": events}


def highlight_snapshot(status: dict[str, Any], live_dx: dict[str, Any], weather: dict[str, Any], now: int | None = None, mode: str | None = None) -> dict[str, Any]:
    now = int(now or time.time())
    activity = band_activity_history(hours=1, bucket_seconds=300, mode=mode)
    activity_by_band = {x["band"]: x for x in activity["bands"]}
    items: list[dict[str, Any]] = []

    for band_row in status.get("bands", []):
        band = str(band_row.get("band") or "").lower()
        state = str(band_row.get("state") or "CLOSED")
        score = int(band_row.get("score") or 0)
        details = band_row.get("details") or {}
        region = str(details.get("dominant_region") or "—")
        direction = str(band_row.get("direction_label") or "—")
        if state in {"OPEN", "STRONG"}:
            severity = 3 if state == "STRONG" or band == "6m" else 2
            icon = "🔥" if state == "STRONG" else "📡"
            items.append({
                "kind": "opening", "severity": severity, "band": band,
                "title": f"{icon} {band.upper()} {state}",
                "text": f"Score {score}/100 · {region} · {direction}",
                "score": score,
            })

        points = activity_by_band.get(band, {}).get("points") or []
        if len(points) >= 2:
            cutoff = now - 15 * 60
            recent = [p for p in points if int(p["ts"]) >= cutoff]
            if len(recent) >= 2:
                delta = int(recent[-1]["score"]) - int(recent[0]["score"])
                if delta >= 10 and state not in {"STRONG"}:
                    items.append({
                        "kind": "rising", "severity": 3 if delta >= 20 else 2, "band": band,
                        "title": f"📈 {band.upper()} Aktivität steigt",
                        "text": f"{delta:+d} Score-Punkte in ca. 15 Min. · aktuell {score}/100",
                        "score": min(100, score + delta),
                    })

    stations = live_dx.get("stations") or []
    if stations:
        top = stations[0]
        hs = int(top.get("highlight_score") or 0)
        if hs >= 60:
            distance = top.get("distance_km")
            distance_text = f"{int(distance):,} km".replace(",", ".") if distance else "Entfernung —"
            evidence_label = "regionale Spotter" if str(mode or "").lower() == "ssb" else ("lokale Skimmer" if str(mode or "").lower() == "cw" else "lokale RX")
            items.append({
                "kind": "dx", "severity": 3 if hs >= 75 else 2,
                "title": f"🌍 {top.get('call')} · {str(top.get('band') or '').upper()}",
                "text": f"{top.get('name') or 'DX'} · {distance_text} · {int(top.get('local_rx') or 0)} {evidence_label}",
                "score": hs,
            })

    if weather.get("available"):
        kp = float(weather.get("kp") or 0)
        r = int(weather.get("r_scale") or 0)
        s = int(weather.get("s_scale") or 0)
        g = int(weather.get("g_scale") or 0)
        if kp >= 5 or max(r, s, g) > 0:
            items.append({
                "kind": "weather", "severity": 4 if max(r, s, g) >= 3 or kp >= 7 else 3,
                "title": "☀️ Funkwetter-Warnung",
                "text": f"Kp {kp:.1f} · NOAA R{r}/S{s}/G{g}",
                "score": 100 if max(r, s, g) >= 3 else 80,
            })

    kind_order = {"weather": 0, "opening": 1, "rising": 2, "dx": 3}
    items.sort(key=lambda x: (-int(x.get("severity") or 0), -int(x.get("score") or 0), kind_order.get(str(x.get("kind")), 9)))
    if not items:
        items = [{
            "kind": "quiet", "severity": 1,
            "title": "✅ Keine besonderen Alarme",
            "text": "Der Spotter überwacht Bänder, Live DX und Funkwetter weiter.",
            "score": 0,
        }]
    return {"generated_at": now, "items": items[:6]}
