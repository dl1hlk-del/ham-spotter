from app.geo import locator_to_latlon, haversine_km, initial_bearing_deg, sector30
from app.collectors.rbn import parse_rbn_line
from app.config import settings


def test_locator():
    lat, lon = locator_to_latlon("JO50AA")
    assert abs(lat - 50.0208333) < 0.001
    assert abs(lon - 10.0416667) < 0.001


def test_geo():
    lat, lon = locator_to_latlon("JO50AA")
    assert haversine_km(lat, lon, lat, lon) < 0.01
    b = initial_bearing_deg(lat, lon, 40.7, -74.0)
    assert 280 <= b <= 310
    assert sector30(299) == 300


def test_rbn_parse():
    line = "DX de W3LPL-#:  21025.1  K1ABC          CW  23 dB  28 WPM  CQ      2117Z"
    p = parse_rbn_line(line, "rbn_cw")
    assert p is not None
    assert p["band"] == "15m"
    assert p["spotter"] == "W3LPL"
    assert p["tx_call"] == "K1ABC"
    assert p["snr"] == 23


def test_rbn_node_json_parse():
    from app.rbn_nodes import parse_node_json
    payload = [
        {"call": "3B8GL", "grid": "LG89RR", "lst_age": "online"},
        {"call": "W3LPL", "grid": "FM19LG"},
        {"call": "BAD", "grid": "not-a-grid"},
    ]
    rows = dict(parse_node_json(payload))
    assert rows["3B8GL"] == "LG89RR"
    assert rows["W3LPL"] == "FM19LG"
    assert "BAD" not in rows


def test_rbn_parse_added_hf_bands():
    samples = {
        "20m": "DX de W3LPL-#:  14025.1  K1ABC          CW  18 dB  28 WPM  CQ      2117Z",
        "40m": "DX de W3LPL-#:   7025.1  K1ABC          CW  18 dB  28 WPM  CQ      2117Z",
        "60m": "DX de W3LPL-#:   5357.0  K1ABC          CW  18 dB  28 WPM  CQ      2117Z",
        "80m": "DX de W3LPL-#:   3525.1  K1ABC          CW  18 dB  28 WPM  CQ      2117Z",
    }
    for expected_band, line in samples.items():
        parsed = parse_rbn_line(line, "rbn_cw")
        assert parsed is not None
        assert parsed["band"] == expected_band


def test_default_band_set_contains_added_hf_bands():
    assert {"20m", "40m", "60m", "80m"}.issubset(set(settings.bands))
