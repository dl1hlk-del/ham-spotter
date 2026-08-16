from __future__ import annotations

import collections
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings

_lock = threading.RLock()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS spots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  unique_key TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL,
  ts INTEGER NOT NULL,
  band TEXT NOT NULL,
  mode TEXT,
  frequency_hz INTEGER,
  tx_call TEXT,
  tx_grid TEXT,
  tx_dxcc INTEGER,
  rx_call TEXT,
  rx_grid TEXT,
  rx_distance_km REAL,
  tx_distance_km REAL,
  azimuth_deg REAL,
  sector INTEGER,
  snr REAL,
  raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_spots_band_ts ON spots(band, ts);
CREATE INDEX IF NOT EXISTS idx_spots_source_ts ON spots(source, ts);
CREATE INDEX IF NOT EXISTS idx_spots_rx_call ON spots(rx_call);
CREATE INDEX IF NOT EXISTS idx_spots_source_txcall_ts ON spots(source, tx_call, ts DESC);
CREATE INDEX IF NOT EXISTS idx_spots_band_source_ts ON spots(band, source, ts DESC);
CREATE INDEX IF NOT EXISTS idx_spots_ts_band_source ON spots(ts DESC, band, source);

CREATE TABLE IF NOT EXISTS source_health (
  source TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  last_seen INTEGER,
  last_error TEXT,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS rbn_nodes (
  callsign TEXT PRIMARY KEY,
  grid TEXT NOT NULL,
  distance_km REAL NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS band_state (
  band TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  score INTEGER NOT NULL,
  direction_sector INTEGER,
  direction_label TEXT,
  details TEXT,
  updated_at INTEGER NOT NULL,
  alerted_at INTEGER,
  alerted_state TEXT,
  alerted_sector INTEGER
);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  band TEXT NOT NULL,
  state TEXT NOT NULL,
  score INTEGER NOT NULL,
  direction_sector INTEGER,
  message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opening_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  band TEXT NOT NULL,
  start_ts INTEGER NOT NULL,
  end_ts INTEGER,
  duration_seconds INTEGER,
  start_state TEXT NOT NULL,
  last_state TEXT NOT NULL,
  max_state TEXT NOT NULL,
  max_score INTEGER NOT NULL,
  direction_sector INTEGER,
  direction_label TEXT,
  dominant_region TEXT,
  countries_json TEXT,
  target_median_dx_km REAL,
  psk_unique_tx_max INTEGER NOT NULL DEFAULT 0,
  psk_unique_rx_max INTEGER NOT NULL DEFAULT 0,
  rbn_unique_tx_max INTEGER NOT NULL DEFAULT 0,
  rbn_unique_rx_max INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_opening_events_band_start ON opening_events(band, start_ts DESC);
CREATE INDEX IF NOT EXISTS idx_opening_events_start ON opening_events(start_ts DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_opening_events_active_band ON opening_events(band) WHERE end_ts IS NULL;

CREATE TABLE IF NOT EXISTS dxcc_seen_days (
  band TEXT NOT NULL,
  dxcc INTEGER NOT NULL,
  day_utc TEXT NOT NULL,
  first_seen INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  calls_json TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (band, dxcc, day_utc)
);
CREATE INDEX IF NOT EXISTS idx_dxcc_seen_days_band_day ON dxcc_seen_days(band, day_utc);
CREATE INDEX IF NOT EXISTS idx_dxcc_seen_days_dxcc ON dxcc_seen_days(dxcc, day_utc);

CREATE TABLE IF NOT EXISTS space_weather_cache (
  id INTEGER PRIMARY KEY CHECK (id=1),
  payload TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS band_activity_samples (
  band TEXT NOT NULL,
  ts INTEGER NOT NULL,
  state TEXT NOT NULL,
  score INTEGER NOT NULL,
  direction_sector INTEGER,
  dominant_region TEXT,
  psk_unique_tx INTEGER NOT NULL DEFAULT 0,
  psk_unique_rx INTEGER NOT NULL DEFAULT 0,
  rbn_unique_tx INTEGER NOT NULL DEFAULT 0,
  rbn_unique_rx INTEGER NOT NULL DEFAULT 0,
  digital_score INTEGER,
  cw_score INTEGER,
  ssb_score INTEGER,
  digital_unique_tx INTEGER,
  digital_unique_rx INTEGER,
  cw_unique_tx INTEGER,
  cw_unique_rx INTEGER,
  ssb_unique_tx INTEGER,
  ssb_unique_rx INTEGER,
  PRIMARY KEY (band, ts)
);
CREATE INDEX IF NOT EXISTS idx_band_activity_ts ON band_activity_samples(ts);
CREATE INDEX IF NOT EXISTS idx_band_activity_band_ts ON band_activity_samples(band, ts);
"""


def init_db() -> None:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript(SCHEMA)
        # V1.9 migration: add mode-specific score columns to an existing V1.8
        # database without deleting historical samples.
        cols = {str(r[1]) for r in con.execute("PRAGMA table_info(band_activity_samples)").fetchall()}
        for col in (
            "digital_score", "cw_score", "ssb_score",
            "digital_unique_tx", "digital_unique_rx",
            "cw_unique_tx", "cw_unique_rx",
            "ssb_unique_tx", "ssb_unique_rx",
        ):
            if col not in cols:
                con.execute(f"ALTER TABLE band_activity_samples ADD COLUMN {col} INTEGER")
        now = int(time.time())
        for band in settings.bands:
            con.execute(
                "INSERT OR IGNORE INTO band_state(band,state,score,updated_at) VALUES(?,?,?,?)",
                (band, "CLOSED", 0, now),
            )
        sample_seconds = max(30, int(settings.activity_sample_seconds))
        sample_ts = now - (now % sample_seconds)
        con.execute(
            """INSERT OR IGNORE INTO band_activity_samples(band,ts,state,score,direction_sector)
               SELECT band,?,state,score,direction_sector FROM band_state""",
            (sample_ts,),
        )
        con.commit()


@contextmanager
def connect():
    with _lock:
        con = sqlite3.connect(settings.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
        finally:
            con.close()



def save_space_weather(payload: dict[str, Any]) -> None:
    now = int(payload.get("updated_at") or time.time())
    with connect() as con:
        con.execute(
            """INSERT INTO space_weather_cache(id,payload,updated_at) VALUES(1,?,?)
               ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at""",
            (json.dumps(payload, ensure_ascii=False), now),
        )
        con.commit()


def load_space_weather() -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT payload,updated_at FROM space_weather_cache WHERE id=1").fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["payload"] or "{}")
        if isinstance(data, dict):
            data.setdefault("updated_at", int(row["updated_at"] or 0))
            return data
    except Exception:
        pass
    return None

def set_health(source: str, status: str, *, seen: bool = False, error: str | None = None) -> None:
    now = int(time.time())
    with connect() as con:
        old = con.execute("SELECT last_seen FROM source_health WHERE source=?", (source,)).fetchone()
        last_seen = now if seen else (old["last_seen"] if old else None)
        con.execute(
            """INSERT INTO source_health(source,status,last_seen,last_error,updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(source) DO UPDATE SET
                 status=excluded.status,last_seen=excluded.last_seen,
                 last_error=excluded.last_error,updated_at=excluded.updated_at""",
            (source, status, last_seen, error, now),
        )
        con.commit()


def insert_spot(spot: dict[str, Any]) -> bool:
    cols = [
        "unique_key", "source", "ts", "band", "mode", "frequency_hz", "tx_call", "tx_grid", "tx_dxcc",
        "rx_call", "rx_grid", "rx_distance_km", "tx_distance_km", "azimuth_deg", "sector", "snr", "raw",
    ]
    values = [spot.get(c) for c in cols]
    with connect() as con:
        cur = con.execute(
            f"INSERT OR IGNORE INTO spots({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",
            values,
        )
        con.commit()
        return cur.rowcount > 0


def cleanup_old_spots() -> int:
    now = int(time.time())
    cutoff = now - settings.retention_hours * 3600
    activity_cutoff = now - max(1, int(settings.activity_retention_days)) * 86400
    with connect() as con:
        cur = con.execute("DELETE FROM spots WHERE ts < ?", (cutoff,))
        con.execute("DELETE FROM band_activity_samples WHERE ts < ?", (activity_cutoff,))
        con.commit()
        return cur.rowcount


def save_rbn_nodes(nodes: list[tuple[str, str, float]]) -> None:
    now = int(time.time())
    with connect() as con:
        con.executemany(
            """INSERT INTO rbn_nodes(callsign,grid,distance_km,updated_at) VALUES(?,?,?,?)
               ON CONFLICT(callsign) DO UPDATE SET grid=excluded.grid,distance_km=excluded.distance_km,updated_at=excluded.updated_at""",
            [(call, grid, dist, now) for call, grid, dist in nodes],
        )
        con.commit()


def get_rbn_node(callsign: str):
    with connect() as con:
        return con.execute("SELECT * FROM rbn_nodes WHERE callsign=?", (callsign,)).fetchone()


def get_band_rows(band: str, since_ts: int):
    with connect() as con:
        return con.execute("SELECT * FROM spots WHERE band=? AND ts>=?", (band, since_ts)).fetchall()


def get_band_state(band: str):
    with connect() as con:
        return con.execute("SELECT * FROM band_state WHERE band=?", (band,)).fetchone()


def set_band_state(band: str, state: str, score: int, sector: int | None, label: str, details: dict[str, Any]) -> None:
    now = int(time.time())
    sample_seconds = max(30, int(settings.activity_sample_seconds))
    sample_ts = now - (now % sample_seconds)
    with connect() as con:
        con.execute(
            """UPDATE band_state SET state=?,score=?,direction_sector=?,direction_label=?,details=?,updated_at=? WHERE band=?""",
            (state, score, sector, label, json.dumps(details, ensure_ascii=False), now, band),
        )
        con.execute(
            """INSERT INTO band_activity_samples(
                   band,ts,state,score,direction_sector,dominant_region,
                   psk_unique_tx,psk_unique_rx,rbn_unique_tx,rbn_unique_rx,
                   digital_score,cw_score,ssb_score,
                   digital_unique_tx,digital_unique_rx,cw_unique_tx,cw_unique_rx,ssb_unique_tx,ssb_unique_rx
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(band,ts) DO UPDATE SET
                 state=excluded.state,score=excluded.score,direction_sector=excluded.direction_sector,
                 dominant_region=excluded.dominant_region,psk_unique_tx=excluded.psk_unique_tx,
                 psk_unique_rx=excluded.psk_unique_rx,rbn_unique_tx=excluded.rbn_unique_tx,
                 rbn_unique_rx=excluded.rbn_unique_rx,digital_score=excluded.digital_score,
                 cw_score=excluded.cw_score,ssb_score=excluded.ssb_score,
                 digital_unique_tx=excluded.digital_unique_tx,digital_unique_rx=excluded.digital_unique_rx,
                 cw_unique_tx=excluded.cw_unique_tx,cw_unique_rx=excluded.cw_unique_rx,
                 ssb_unique_tx=excluded.ssb_unique_tx,ssb_unique_rx=excluded.ssb_unique_rx""",
            (
                band, sample_ts, state, int(score), sector, details.get("dominant_region"),
                int(details.get("psk_unique_tx") or 0), int(details.get("psk_unique_rx") or 0),
                int(details.get("rbn_unique_tx") or 0), int(details.get("rbn_unique_rx") or 0),
                int(((details.get("mode_scores") or {}).get("digital") or {}).get("score") or 0),
                int(((details.get("mode_scores") or {}).get("cw") or {}).get("score") or 0),
                int(((details.get("mode_scores") or {}).get("ssb") or {}).get("score") or 0),
                int(((details.get("mode_scores") or {}).get("digital") or {}).get("unique_tx") or 0),
                int(((details.get("mode_scores") or {}).get("digital") or {}).get("unique_rx") or 0),
                int(((details.get("mode_scores") or {}).get("cw") or {}).get("unique_tx") or 0),
                int(((details.get("mode_scores") or {}).get("cw") or {}).get("unique_rx") or 0),
                int(((details.get("mode_scores") or {}).get("ssb") or {}).get("unique_tx") or 0),
                int(((details.get("mode_scores") or {}).get("ssb") or {}).get("unique_rx") or 0),
            ),
        )
        con.commit()


def mark_alerted(band: str, state: str, sector: int | None, message: str, score: int) -> None:
    now = int(time.time())
    with connect() as con:
        con.execute(
            "UPDATE band_state SET alerted_at=?,alerted_state=?,alerted_sector=? WHERE band=?",
            (now, state, sector, band),
        )
        con.execute(
            "INSERT INTO alerts(ts,band,state,score,direction_sector,message) VALUES(?,?,?,?,?,?)",
            (now, band, state, score, sector, message),
        )
        con.commit()


def _angular_distance(a: int, b: int) -> int:
    return min((a - b) % 360, (b - a) % 360)


def _country_call_sum(raw: str | None) -> int:
    try:
        return sum(int(x.get("calls") or 0) for x in json.loads(raw or "[]") if isinstance(x, dict))
    except Exception:
        return 0


def _close_event(con: sqlite3.Connection, event_id: int, start_ts: int, now: int) -> None:
    duration = max(0, now - int(start_ts))
    con.execute(
        "UPDATE opening_events SET end_ts=?,duration_seconds=?,updated_at=? WHERE id=?",
        (now, duration, now, event_id),
    )


def sync_opening_event(
    band: str,
    state: str,
    score: int,
    sector: int | None,
    label: str,
    details: dict[str, Any],
    *,
    now: int | None = None,
) -> int | None:
    """Persist one opening event per band while state is OPEN/STRONG.

    A reliable bearing shift of >=60 degrees closes the current event and starts
    a new segment. This makes the history useful for later direction statistics
    without fragmenting on normal 30-degree jitter.
    """
    now = int(now or time.time())
    active_states = {"OPEN", "STRONG"}
    reliable = bool(details.get("direction_reliable"))
    reliable_sector = int(sector) if reliable and sector is not None else None
    countries = details.get("countries") or []
    countries_json = json.dumps(countries, ensure_ascii=False)
    region = details.get("dominant_region")
    target_dx = float(details.get("target_median_dx_km") or 0) or None

    psk_tx = int(details.get("psk_unique_tx") or 0)
    psk_rx = int(details.get("psk_unique_rx") or 0)
    rbn_tx = int(details.get("rbn_unique_tx") or 0)
    rbn_rx = int(details.get("rbn_unique_rx") or 0)

    with connect() as con:
        active = con.execute(
            "SELECT * FROM opening_events WHERE band=? AND end_ts IS NULL ORDER BY id DESC LIMIT 1",
            (band,),
        ).fetchone()

        if state not in active_states:
            if active:
                _close_event(con, int(active["id"]), int(active["start_ts"]), now)
                con.commit()
            return None

        if active and reliable_sector is not None and active["direction_sector"] is not None:
            if _angular_distance(reliable_sector, int(active["direction_sector"])) >= 60:
                _close_event(con, int(active["id"]), int(active["start_ts"]), now)
                active = None

        if active is None:
            cur = con.execute(
                """INSERT INTO opening_events(
                       band,start_ts,end_ts,duration_seconds,start_state,last_state,max_state,max_score,
                       direction_sector,direction_label,dominant_region,countries_json,target_median_dx_km,
                       psk_unique_tx_max,psk_unique_rx_max,rbn_unique_tx_max,rbn_unique_rx_max,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    band, now, None, None, state, state, state, int(score),
                    reliable_sector, label if reliable_sector is not None else None,
                    region if reliable_sector is not None else None,
                    countries_json if reliable_sector is not None else "[]",
                    target_dx if reliable_sector is not None else None,
                    psk_tx, psk_rx, rbn_tx, rbn_rx, now,
                ),
            )
            con.commit()
            return int(cur.lastrowid)

        event_id = int(active["id"])
        max_score = max(int(active["max_score"] or 0), int(score))
        max_state = "STRONG" if active["max_state"] == "STRONG" or state == "STRONG" else "OPEN"

        stored_sector = active["direction_sector"]
        stored_label = active["direction_label"]
        stored_region = active["dominant_region"]
        stored_countries = active["countries_json"] or "[]"
        stored_target_dx = active["target_median_dx_km"]

        # Prefer a reliable direction. Once the direction exists, refresh its
        # context when the current analysis is at least as strong or has richer
        # country evidence.
        current_country_calls = sum(int(x.get("calls") or 0) for x in countries if isinstance(x, dict))
        stored_country_calls = _country_call_sum(stored_countries)
        refresh_snapshot = bool(
            reliable_sector is not None
            and (
                stored_sector is None
                or int(score) >= int(active["max_score"] or 0)
                or current_country_calls > stored_country_calls
            )
        )
        if refresh_snapshot:
            stored_sector = reliable_sector
            stored_label = label
            stored_region = region
            stored_countries = countries_json
            stored_target_dx = target_dx

        con.execute(
            """UPDATE opening_events SET
                   last_state=?,max_state=?,max_score=?,direction_sector=?,direction_label=?,dominant_region=?,
                   countries_json=?,target_median_dx_km=?,psk_unique_tx_max=?,psk_unique_rx_max=?,
                   rbn_unique_tx_max=?,rbn_unique_rx_max=?,updated_at=?
               WHERE id=?""",
            (
                state, max_state, max_score, stored_sector, stored_label, stored_region,
                stored_countries, stored_target_dx,
                max(int(active["psk_unique_tx_max"] or 0), psk_tx),
                max(int(active["psk_unique_rx_max"] or 0), psk_rx),
                max(int(active["rbn_unique_tx_max"] or 0), rbn_tx),
                max(int(active["rbn_unique_rx_max"] or 0), rbn_rx),
                now, event_id,
            ),
        )
        con.commit()
        return event_id


def close_stale_opening_events(max_age_seconds: int = 600) -> int:
    """Close events that were left active after a prolonged process outage."""
    cutoff = int(time.time()) - max_age_seconds
    with connect() as con:
        rows = con.execute(
            "SELECT id,start_ts,updated_at FROM opening_events WHERE end_ts IS NULL AND updated_at<?",
            (cutoff,),
        ).fetchall()
        for row in rows:
            end_ts = int(row["updated_at"] or cutoff)
            _close_event(con, int(row["id"]), int(row["start_ts"]), end_ts)
        con.commit()
        return len(rows)


def _event_dict(row: sqlite3.Row, now: int) -> dict[str, Any]:
    item = dict(row)
    try:
        item["countries"] = json.loads(item.pop("countries_json", "[]") or "[]")
    except Exception:
        item["countries"] = []
    end_ts = item.get("end_ts")
    item["active"] = end_ts is None
    item["duration_seconds"] = int(item.get("duration_seconds") or max(0, (end_ts or now) - int(item["start_ts"])))
    return item


def opening_history(limit: int = 30, *, band: str | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    now = int(time.time())
    with connect() as con:
        if band:
            rows = con.execute(
                "SELECT * FROM opening_events WHERE band=? ORDER BY start_ts DESC LIMIT ?",
                (band, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM opening_events ORDER BY start_ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_event_dict(r, now) for r in rows]


def opening_stats(days: int = 30) -> dict[str, Any]:
    days = max(1, min(int(days), 3650))
    now = int(time.time())
    since = now - days * 86400
    with connect() as con:
        rows = con.execute(
            """SELECT * FROM opening_events
               WHERE start_ts<=? AND (end_ts IS NULL OR end_ts>=?)
               ORDER BY start_ts""",
            (now, since),
        ).fetchall()

    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        event = _event_dict(row, now)
        # Clip duration to the selected statistics range.
        effective_start = max(int(event["start_ts"]), since)
        effective_end = min(int(event.get("end_ts") or now), now)
        event["range_duration_seconds"] = max(0, effective_end - effective_start)
        grouped[event["band"]].append(event)

    bands: list[dict[str, Any]] = []
    for band in settings.bands:
        events = grouped.get(band, [])
        region_counts: collections.Counter[str] = collections.Counter()
        sector_counts: collections.Counter[int] = collections.Counter()
        hour_counts: collections.Counter[int] = collections.Counter()
        for e in events:
            if e.get("dominant_region"):
                region_counts[str(e["dominant_region"])] += 1
            if e.get("direction_sector") is not None:
                sector_counts[int(e["direction_sector"])] += 1
            hour_counts[datetime.fromtimestamp(int(e["start_ts"]), tz=timezone.utc).hour] += 1

        durations = [int(e["range_duration_seconds"]) for e in events]
        completed = [int(e["duration_seconds"]) for e in events if not e.get("active")]
        total_seconds = sum(durations)
        top_region = region_counts.most_common(1)[0][0] if region_counts else None
        top_sector = sector_counts.most_common(1)[0][0] if sector_counts else None
        top_hour_utc = hour_counts.most_common(1)[0][0] if hour_counts else None
        bands.append(
            {
                "band": band,
                "events": len(events),
                "strong_events": sum(1 for e in events if e.get("max_state") == "STRONG"),
                "active_events": sum(1 for e in events if e.get("active")),
                "total_seconds": total_seconds,
                "average_duration_seconds": round(sum(completed) / len(completed)) if completed else (round(total_seconds / len(events)) if events else 0),
                "longest_duration_seconds": max(durations, default=0),
                "max_score": max((int(e.get("max_score") or 0) for e in events), default=0),
                "top_region": top_region,
                "top_sector": top_sector,
                "top_start_hour_utc": top_hour_utc,
                "latest_start_ts": max((int(e["start_ts"]) for e in events), default=None),
            }
        )

    return {
        "days": days,
        "since": since,
        "generated_at": now,
        "total_events": sum(x["events"] for x in bands),
        "bands": bands,
    }


def status_snapshot() -> dict[str, Any]:
    with connect() as con:
        bands = [dict(r) for r in con.execute("SELECT * FROM band_state")]
        health = [dict(r) for r in con.execute("SELECT * FROM source_health ORDER BY source")]
        node_count = con.execute("SELECT COUNT(*) AS n FROM rbn_nodes").fetchone()["n"]
        counts = {r["source"]: r["n"] for r in con.execute("SELECT source,COUNT(*) AS n FROM spots WHERE ts>=? GROUP BY source", (int(time.time())-3600,))}
    order = {band: idx for idx, band in enumerate(settings.bands)}
    bands.sort(key=lambda row: (order.get(str(row.get("band")), len(order)), str(row.get("band"))))
    for b in bands:
        try:
            b["details"] = json.loads(b["details"] or "{}")
        except Exception:
            b["details"] = {}
    return {"bands": bands, "sources": health, "rbn_nodes": node_count, "spots_last_hour": counts}


def band_activity_history(hours: int = 6, *, bucket_seconds: int = 300, mode: str | None = None) -> dict[str, Any]:
    """Return bucketed score/activity history for all configured bands."""
    hours = max(1, min(int(hours), max(24, settings.activity_retention_days * 24)))
    bucket_seconds = max(60, min(int(bucket_seconds), 3600))
    now = int(time.time())
    since = now - hours * 3600
    mode = str(mode or settings.primary_prop_mode or "ssb").lower()
    if mode not in {"ssb", "cw", "digital"}:
        mode = str(settings.primary_prop_mode or "ssb").lower()
    score_col = {"ssb": "ssb_score", "cw": "cw_score", "digital": "digital_score"}[mode]
    with connect() as con:
        rows = con.execute(
            """SELECT band,ts,state,score,psk_unique_tx,psk_unique_rx,rbn_unique_tx,rbn_unique_rx,
                      digital_score,cw_score,ssb_score,
                      digital_unique_tx,digital_unique_rx,cw_unique_tx,cw_unique_rx,ssb_unique_tx,ssb_unique_rx
               FROM band_activity_samples WHERE ts>=? ORDER BY band,ts""",
            (since,),
        ).fetchall()

    grouped: dict[str, dict[int, list[sqlite3.Row]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for row in rows:
        bucket = int(row["ts"]) - (int(row["ts"]) % bucket_seconds)
        grouped[str(row["band"])][bucket].append(row)

    bands: list[dict[str, Any]] = []
    state_rank = {"CLOSED": 0, "WATCH": 1, "OPEN": 2, "STRONG": 3}
    for band in settings.bands:
        points: list[dict[str, Any]] = []
        for bucket, bucket_rows in sorted(grouped.get(band, {}).items()):
            # V1.8 samples have only the legacy `score`, which was the DIGITAL
            # score.  Never reinterpret those historical values as SSB/CW.
            # DIGITAL may safely fall back to the legacy score; SSB/CW history
            # begins when their dedicated columns actually contain data.
            def _selected(r):
                value = r[score_col]
                if value is not None:
                    return int(value)
                if mode == "digital":
                    return int(r["score"] or 0)
                return None

            valid_rows = [(r, _selected(r)) for r in bucket_rows]
            valid_rows = [(r, v) for r, v in valid_rows if v is not None]
            if not valid_rows:
                continue
            avg_score = round(sum(v for _, v in valid_rows) / len(valid_rows))
            max_score = max(v for _, v in valid_rows)
            can_open_state = "STRONG" if max_score >= settings.strong_score else ("OPEN" if max_score >= settings.open_score else ("WATCH" if max_score >= settings.watch_score else "CLOSED"))
            tx_col = {"digital": "digital_unique_tx", "cw": "cw_unique_tx", "ssb": "ssb_unique_tx"}[mode]
            rx_col = {"digital": "digital_unique_rx", "cw": "cw_unique_rx", "ssb": "ssb_unique_rx"}[mode]
            point = {
                "ts": bucket,
                "score": avg_score,
                "max_score": max_score,
                "state": can_open_state,
                "unique_tx": max(int(r[tx_col] or 0) for r, _ in valid_rows),
                "unique_rx": max(int(r[rx_col] or 0) for r, _ in valid_rows),
            }
            if mode == "digital":
                point.update({
                    "psk_unique_tx": max(int(r["psk_unique_tx"] or 0) for r, _ in valid_rows),
                    "psk_unique_rx": max(int(r["psk_unique_rx"] or 0) for r, _ in valid_rows),
                    "rbn_unique_tx": max(int(r["rbn_unique_tx"] or 0) for r, _ in valid_rows),
                    "rbn_unique_rx": max(int(r["rbn_unique_rx"] or 0) for r, _ in valid_rows),
                })
            points.append(point)
        current = points[-1]["score"] if points else None
        first = points[0]["score"] if points else None
        bands.append({
            "band": band,
            "points": points,
            "current_score": current,
            "delta": (int(current) - int(first)) if current is not None and first is not None else None,
            "max_score": max((int(p["max_score"]) for p in points), default=0),
            "average_score": round(sum(int(p["score"]) for p in points) / len(points), 1) if points else None,
        })
    return {"hours": hours, "bucket_seconds": bucket_seconds, "since": since, "generated_at": now, "mode": mode, "bands": bands}


def activity_score_at_or_before(band: str, ts: int, *, lookback_seconds: int = 1800) -> int | None:
    with connect() as con:
        row = con.execute(
            """SELECT score FROM band_activity_samples
               WHERE band=? AND ts<=? AND ts>=? ORDER BY ts DESC LIMIT 1""",
            (band, int(ts), int(ts) - int(lookback_seconds)),
        ).fetchone()
    return int(row["score"]) if row else None
