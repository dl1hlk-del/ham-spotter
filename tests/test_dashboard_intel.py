import json
import time

from app.config import settings
from app.dashboard_intel import comparison_snapshot, highlight_snapshot, opening_timeline_today
from app.db import band_activity_history, connect, init_db, set_band_state


def _use_tmp_db(tmp_path):
    settings.db_path = str(tmp_path / "hamspotter-test.db")
    init_db()


def test_activity_history_records_band_state(tmp_path):
    _use_tmp_db(tmp_path)
    set_band_state("17m", "OPEN", 72, 300, "WNW", {
        "dominant_region": "Nordamerika",
        "psk_unique_tx": 12,
        "psk_unique_rx": 4,
        "rbn_unique_tx": 2,
        "rbn_unique_rx": 1,
    })
    snap = band_activity_history(hours=6, bucket_seconds=300)
    row = next(x for x in snap["bands"] if x["band"] == "17m")
    assert row["points"]
    assert row["current_score"] >= 0


def test_timeline_and_comparison_use_existing_opening_history(tmp_path):
    _use_tmp_db(tmp_path)
    now = int(time.time())
    with connect() as con:
        con.execute(
            """INSERT INTO opening_events(
                band,start_ts,end_ts,duration_seconds,start_state,last_state,max_state,max_score,
                direction_sector,direction_label,dominant_region,countries_json,target_median_dx_km,
                psk_unique_tx_max,psk_unique_rx_max,rbn_unique_tx_max,rbn_unique_rx_max,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "17m", now-900, None, None, "OPEN", "OPEN", "STRONG", 88,
                300, "WNW", "Nordamerika", json.dumps([{"name":"United States"}]), 7000,
                20, 5, 2, 1, now,
            ),
        )
        con.commit()
    timeline = opening_timeline_today(now=now)
    assert any(x["band"] == "17m" for x in timeline["events"])
    cmp = comparison_snapshot(now=now)
    assert cmp["day"]["events"]["current"] >= 1
    assert cmp["week"]["events"]["current"] >= 1


def test_highlight_center_prioritizes_strong_opening(tmp_path):
    _use_tmp_db(tmp_path)
    status = {"bands": [{
        "band": "6m", "state": "STRONG", "score": 91, "direction_label": "SW",
        "details": {"dominant_region": "Südeuropa"},
    }]}
    live_dx = {"stations": [{
        "call": "EA8TEST", "band": "6m", "name": "Canary Islands",
        "distance_km": 3400, "local_rx": 4, "highlight_score": 89,
    }]}
    weather = {"available": True, "kp": 2.0, "r_scale": 0, "s_scale": 0, "g_scale": 0}
    out = highlight_snapshot(status, live_dx, weather)
    assert out["items"]
    assert any(x["kind"] == "opening" for x in out["items"])
