from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .config import settings
from .db import connect
from .dxcc import dxcc_name
from .geo import sector_label


def _day_utc(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def _since_day(days: int, now: int | None = None) -> str:
    now_dt = datetime.fromtimestamp(int(now or time.time()), tz=timezone.utc)
    return (now_dt - timedelta(days=max(1, int(days)) - 1)).strftime("%Y-%m-%d")


def record_rows(band: str, rows: Iterable[Any]) -> None:
    grouped: dict[tuple[str, int], set[str]] = defaultdict(set)
    first_last: dict[tuple[str, int], list[int]] = {}

    for row in rows:
        try:
            dxcc = int(row["tx_dxcc"])
        except (TypeError, ValueError):
            continue
        call = str(row["tx_call"] or "").upper().strip()
        if not call or dxcc <= 0:
            continue
        ts = int(row["ts"] or time.time())
        key = (_day_utc(ts), dxcc)
        grouped[key].add(call)
        if key not in first_last:
            first_last[key] = [ts, ts]
        else:
            first_last[key][0] = min(first_last[key][0], ts)
            first_last[key][1] = max(first_last[key][1], ts)

    if not grouped:
        return

    with connect() as con:
        for (day, dxcc), calls in grouped.items():
            old = con.execute(
                "SELECT calls_json,first_seen,last_seen FROM dxcc_seen_days WHERE band=? AND dxcc=? AND day_utc=?",
                (band, dxcc, day),
            ).fetchone()
            merged = set(calls)
            first_seen, last_seen = first_last[(day, dxcc)]
            if old:
                try:
                    merged |= {str(x).upper() for x in json.loads(old["calls_json"] or "[]")}
                except Exception:
                    pass
                first_seen = min(first_seen, int(old["first_seen"] or first_seen))
                last_seen = max(last_seen, int(old["last_seen"] or last_seen))
            con.execute(
                """INSERT INTO dxcc_seen_days(band,dxcc,day_utc,first_seen,last_seen,calls_json)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(band,dxcc,day_utc) DO UPDATE SET
                     first_seen=excluded.first_seen,last_seen=excluded.last_seen,calls_json=excluded.calls_json""",
                (band, dxcc, day, first_seen, last_seen, json.dumps(sorted(merged), ensure_ascii=False)),
            )
        con.commit()


def backfill_from_spots() -> int:
    """Backfill rarity memory from retained PSK Reporter rows (normally up to 72h)."""
    cutoff = int(time.time()) - settings.retention_hours * 3600
    with connect() as con:
        rows = con.execute(
            """SELECT band,ts,tx_call,tx_dxcc,tx_distance_km FROM spots
               WHERE source='pskreporter' AND ts>=? AND tx_dxcc IS NOT NULL AND tx_call IS NOT NULL
               ORDER BY band,ts""",
            (cutoff,),
        ).fetchall()
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        band = str(row["band"])
        min_dx = float(settings.min_dx_km.get(band, 1000))
        if row["tx_distance_km"] is None or float(row["tx_distance_km"]) < min_dx:
            continue
        grouped[band].append(row)
    for band, band_rows in grouped.items():
        record_rows(band, band_rows)
    return len(rows)


def _rarity_grade(seen_days: int, observed_days: int, *, watchlisted: bool = False) -> tuple[int, str]:
    if watchlisted:
        return 5, "Watchlist"
    if observed_days < settings.rare_min_learning_days:
        return 0, "Lernphase"
    ratio = seen_days / max(observed_days, 1)
    if seen_days == 1 and observed_days >= 30:
        return 5, "außergewöhnlich"
    if seen_days <= 2 and observed_days >= 14:
        return 4, "sehr selten"
    if ratio <= 0.25:
        return 3, "selten"
    if ratio <= 0.40:
        return 2, "ungewöhnlich"
    return 0, "häufig"


def _history_map(band: str, now: int | None = None) -> tuple[int, dict[int, int]]:
    since = _since_day(settings.rare_lookback_days, now)
    with connect() as con:
        observed_days = int(con.execute(
            "SELECT COUNT(DISTINCT day_utc) AS n FROM dxcc_seen_days WHERE band=? AND day_utc>=?",
            (band, since),
        ).fetchone()["n"] or 0)
        rows = con.execute(
            "SELECT dxcc,COUNT(*) AS n FROM dxcc_seen_days WHERE band=? AND day_utc>=? GROUP BY dxcc",
            (band, since),
        ).fetchall()
    return observed_days, {int(r["dxcc"]): int(r["n"]) for r in rows}


def live_for_band(band: str, rows: Iterable[Any], *, now: int | None = None, limit: int = 8) -> dict[str, Any]:
    now = int(now or time.time())
    min_dx = float(settings.min_dx_km.get(band, 1000))
    rows = [
        r for r in rows
        if str(r["source"]) == "pskreporter"
        and r["tx_dxcc"] is not None
        and r["tx_call"]
        and r["tx_distance_km"] is not None
        and float(r["tx_distance_km"]) >= min_dx
    ]
    record_rows(band, rows)
    observed_days, seen_map = _history_map(band, now)

    grouped: dict[tuple[str, int], dict[str, Any]] = {}
    for r in rows:
        call = str(r["tx_call"] or "").upper().strip()
        try:
            dxcc = int(r["tx_dxcc"])
        except (TypeError, ValueError):
            continue
        key = (call, dxcc)
        item = grouped.setdefault(key, {
            "call": call,
            "dxcc": dxcc,
            "name": dxcc_name(dxcc) or f"DXCC {dxcc}",
            "rx": set(),
            "best_snr": None,
            "distance_km": None,
            "azimuth_deg": None,
            "sector": None,
            "last_seen": 0,
        })
        if r["rx_call"]:
            item["rx"].add(str(r["rx_call"]).upper())
        if r["snr"] is not None:
            snr = float(r["snr"])
            item["best_snr"] = snr if item["best_snr"] is None else max(float(item["best_snr"]), snr)
        if r["tx_distance_km"] is not None:
            item["distance_km"] = float(r["tx_distance_km"])
        if r["azimuth_deg"] is not None:
            item["azimuth_deg"] = float(r["azimuth_deg"])
        if r["sector"] is not None:
            item["sector"] = int(r["sector"])
        item["last_seen"] = max(int(item["last_seen"]), int(r["ts"] or 0))

    watch = set(settings.rare_watch_dxcc)
    stations: list[dict[str, Any]] = []
    for (_, dxcc), item in grouped.items():
        seen_days = int(seen_map.get(dxcc, 1))
        stars, label = _rarity_grade(seen_days, observed_days, watchlisted=dxcc in watch)
        rx_count = len(item.pop("rx"))
        if rx_count < settings.rare_live_min_rx:
            continue
        if stars < settings.rare_min_stars and dxcc not in watch:
            continue
        item.update({
            "stars": stars,
            "rarity_label": label,
            "seen_days": seen_days,
            "observed_days": observed_days,
            "local_rx": rx_count,
            "direction_label": sector_label(item.get("sector")),
            "distance_km": int(round(item["distance_km"])) if item.get("distance_km") else None,
            "azimuth_deg": int(round(item["azimuth_deg"])) if item.get("azimuth_deg") is not None else None,
            "best_snr": round(float(item["best_snr"]), 1) if item.get("best_snr") is not None else None,
        })
        stations.append(item)

    stations.sort(key=lambda x: (-int(x["stars"]), int(x["seen_days"]), -int(x["local_rx"]), -(int(x.get("distance_km") or 0))))
    return {
        "band": band,
        "lookback_days": settings.rare_lookback_days,
        "observed_days": observed_days,
        "learning": observed_days < settings.rare_min_learning_days,
        "learning_target_days": settings.rare_min_learning_days,
        "stations": stations[:max(1, min(int(limit), 50))],
    }


def live_snapshot(*, now: int | None = None, minutes: int | None = None, limit: int = 16) -> dict[str, Any]:
    now = int(now or time.time())
    minutes = int(minutes or settings.rare_live_minutes)
    since = now - max(1, minutes) * 60
    all_items: list[dict[str, Any]] = []
    learning: dict[str, dict[str, Any]] = {}
    for band in settings.bands:
        with connect() as con:
            rows = con.execute(
                """SELECT * FROM spots WHERE band=? AND source='pskreporter' AND ts>=? AND tx_dxcc IS NOT NULL
                   AND tx_distance_km IS NOT NULL AND tx_distance_km>=?
                   ORDER BY ts DESC""",
                (band, since, float(settings.min_dx_km.get(band, 1000))),
            ).fetchall()
        snap = live_for_band(band, rows, now=now, limit=limit)
        learning[band] = {
            "observed_days": snap["observed_days"],
            "learning": snap["learning"],
            "learning_target_days": snap["learning_target_days"],
        }
        for item in snap["stations"]:
            item = dict(item)
            item["band"] = band
            all_items.append(item)

    all_items.sort(key=lambda x: (-int(x["stars"]), int(x["seen_days"]), -int(x["local_rx"]), -(int(x.get("distance_km") or 0))))
    return {
        "qth": settings.qth_locator,
        "lookback_days": settings.rare_lookback_days,
        "live_minutes": minutes,
        "stations": all_items[:max(1, min(int(limit), 100))],
        "learning": learning,
    }


def rarity_text(snapshot: dict[str, Any], *, limit: int = 8) -> str:
    stations = snapshot.get("stations") or []
    lines = [f"⭐ SELTENE DX – {settings.qth_locator}"]
    if not stations:
        learned = [v.get("observed_days", 0) for v in (snapshot.get("learning") or {}).values()]
        best = max(learned or [0])
        if best < settings.rare_min_learning_days and not settings.rare_watch_dxcc:
            lines.append(
                f"Raritätsmodell lernt noch: max. {best}/{settings.rare_min_learning_days} Beobachtungstage."
            )
            lines.append("Danach werden seltene DXCC für dein QTH zuverlässig markiert.")
        else:
            lines.append(f"Keine seltenen Stationen in den letzten {snapshot.get('live_minutes', settings.rare_live_minutes)} Minuten.")
        return "\n".join(lines)

    for item in stations[:limit]:
        stars = "⭐" * int(item.get("stars") or 0)
        km = item.get("distance_km")
        dist = f" · {int(km):,} km".replace(",", ".") if km else ""
        lines.append(
            f"{stars} {item.get('call')} · {item.get('band')} · {item.get('name')} · "
            f"{item.get('direction_label')}{dist} · {item.get('local_rx')} RX · "
            f"{item.get('seen_days')}/{item.get('observed_days')} Tage"
        )
    return "\n".join(lines)


def personal_rarity_for_band(band: str, dxcc_codes: Iterable[int], *, now: int | None = None) -> dict[int, dict[str, Any]]:
    """Return personal rarity metadata without filtering live stations.

    This is intentionally non-blocking for Live DX Highlights: during the
    learning phase stars are 0, but stations can still be shown by the live
    interest score.
    """
    observed_days, seen_map = _history_map(band, now)
    watch = set(settings.rare_watch_dxcc)
    out: dict[int, dict[str, Any]] = {}
    for code_raw in dxcc_codes:
        try:
            code = int(code_raw)
        except (TypeError, ValueError):
            continue
        seen_days = int(seen_map.get(code, 0))
        stars, label = _rarity_grade(seen_days, observed_days, watchlisted=code in watch)
        out[code] = {
            "stars": stars,
            "label": label,
            "seen_days": seen_days,
            "observed_days": observed_days,
            "learning": observed_days < settings.rare_min_learning_days,
        }
    return out
