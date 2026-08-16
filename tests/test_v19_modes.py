from app.collectors.dxcluster import parse_dxcluster_line
from app.mode_scores import spotter_region_weight


def test_ssb_cluster_parser_accepts_voice_range():
    row = parse_dxcluster_line("DX de DL1ABC: 18145.0 K1ABC CQ NA 2250Z")
    assert row is not None
    assert row["band"] == "17m"
    assert row["mode"] == "SSB"
    assert row["tx_call"] == "K1ABC"


def test_ssb_cluster_parser_rejects_ft8():
    assert parse_dxcluster_line("DX de DL1ABC: 18100.0 K1ABC FT8 -10 dB 2250Z") is None
    assert parse_dxcluster_line("DX de SP3ABC: 14074.0 JA1ABC FT8 2251Z") is None


def test_regional_spotter_weighting():
    assert spotter_region_weight("DL1ABC") == 1.0
    assert spotter_region_weight("OK2ABC") > 0.0
    assert spotter_region_weight("K1ABC") == 0.0
