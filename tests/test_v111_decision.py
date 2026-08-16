import json
import time

from app import db
from app.config import settings
from app.decision_layer import matrix_snapshot, compass_snapshot, best_dx_today


def _status():
    return {
        "bands": [{
            "band": "17m", "state": "OPEN", "score": 70, "direction_sector": 300,
            "direction_label": "WNW/NW 300°",
            "details": {"mode_scores": {
                "ssb": {"score": 72, "state": "OPEN", "unique_tx": 7, "unique_rx": 4, "top_sector": 300, "direction_label": "WNW/NW 300°", "direction_confidence_pct": 70, "dominant_region": "Nordamerika"},
                "cw": {"score": 58, "state": "WATCH", "unique_tx": 10, "unique_rx": 3},
                "digital": {"score": 91, "state": "STRONG", "unique_tx": 40, "unique_rx": 12, "top_sector": 300, "direction_label": "WNW/NW 300°", "direction_confidence_pct": 80, "dominant_region": "Nordamerika"},
            }}
        }]
    }


def test_matrix_and_compass():
    status = _status()
    matrix = matrix_snapshot(status, ("17m",))
    assert matrix["bands"][0]["modes"]["ssb"]["score"] == 72
    assert matrix["bands"][0]["modes"]["digital"]["state"] == "STRONG"
    compass = compass_snapshot(status, ("17m",))
    assert compass["items"][0]["sector"] == 300


def test_best_dx_today_correlates_psk_locator(tmp_path):
    old = settings.db_path
    settings.db_path = str(tmp_path / "ham.db")
    try:
        db.init_db()
        now = int(time.time())
        db.insert_spot({
            "unique_key":"p1","source":"pskreporter","ts":now,"band":"17m","mode":"FT8","frequency_hz":18100000,
            "tx_call":"K1ABC","tx_grid":"FN31","tx_dxcc":291,"rx_call":"DL1TEST","rx_grid":"JO50AA",
            "rx_distance_km":5,"tx_distance_km":6200,"azimuth_deg":295,"sector":300,"snr":-5,"raw":"{}",
        })
        db.insert_spot({
            "unique_key":"s1","source":"dxcluster_ssb","ts":now,"band":"17m","mode":"SSB","frequency_hz":18145000,
            "tx_call":"K1ABC","tx_grid":None,"tx_dxcc":None,"rx_call":"DL2AAA","rx_grid":None,
            "rx_distance_km":None,"tx_distance_km":None,"azimuth_deg":None,"sector":None,"snr":None,"raw":"{}",
        })
        out = best_dx_today("ssb", ("17m",), now=now)
        assert out["stations"]
        assert out["stations"][0]["call"] == "K1ABC"
        assert out["stations"][0]["distance_km"] == 6200
    finally:
        settings.db_path = old


