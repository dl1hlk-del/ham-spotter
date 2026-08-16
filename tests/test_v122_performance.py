import time

from app import db
from app.config import settings
from app.decision_layer import best_dx_today
from app.perf_cache import clear, get_or_build


def test_ttl_cache_avoids_rebuild_and_returns_copy():
    clear()
    calls = {"n": 0}

    def build():
        calls["n"] += 1
        return {"items": [1, 2, 3]}

    a = get_or_build(("x",), 30, build)
    a["items"].append(4)
    b = get_or_build(("x",), 30, build)
    assert calls["n"] == 1
    assert b == {"items": [1, 2, 3]}


def test_v122_indexes_exist(tmp_path):
    old = settings.db_path
    settings.db_path = str(tmp_path / "idx.db")
    try:
        db.init_db()
        with db.connect() as con:
            names = {r[1] for r in con.execute("PRAGMA index_list('spots')").fetchall()}
        assert "idx_spots_source_txcall_ts" in names
        assert "idx_spots_band_source_ts" in names
        assert "idx_spots_ts_band_source" in names
    finally:
        settings.db_path = old


def test_best_dx_groups_busy_stream_before_enrichment(tmp_path):
    old = settings.db_path
    settings.db_path = str(tmp_path / "bestdx.db")
    try:
        db.init_db()
        now = int(time.time())
        # Many repeated SSB reports of the same calls should still collapse to
        # one station per band/callsign, while PSK enrichment supplies geometry.
        for i in range(80):
            db.insert_spot({
                "unique_key": f"s{i}", "source": "dxcluster_ssb", "ts": now - i,
                "band": "17m", "mode": "SSB", "frequency_hz": 18145000,
                "tx_call": "K1ABC", "tx_grid": None, "tx_dxcc": None,
                "rx_call": f"DL{i%5}RX", "rx_grid": None, "rx_distance_km": None,
                "tx_distance_km": None, "azimuth_deg": None, "sector": None,
                "snr": None, "raw": "{}",
            })
        db.insert_spot({
            "unique_key": "p1", "source": "pskreporter", "ts": now, "band": "17m", "mode": "FT8",
            "frequency_hz": 18100000, "tx_call": "K1ABC", "tx_grid": "FN31", "tx_dxcc": 291,
            "rx_call": "DL1TEST", "rx_grid": "JO50AA", "rx_distance_km": 5,
            "tx_distance_km": 6200, "azimuth_deg": 295, "sector": 300, "snr": -5, "raw": "{}",
        })
        out = best_dx_today("ssb", ("17m",), now=now)
        assert out["count"] == 1
        assert out["stations"][0]["call"] == "K1ABC"
        assert out["stations"][0]["distance_km"] == 6200
    finally:
        settings.db_path = old
