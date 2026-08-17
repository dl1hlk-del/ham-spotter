import time

from app import cty_prefixes
from app.config import settings
from app.db import init_db, insert_spot
from app.mode_live import live_mode_snapshot


def test_fallback_resolves_observed_examples():
    cty_prefixes._install_fallback()
    assert cty_prefixes.lookup_call("S51DX").entity == "Slovenia"
    assert cty_prefixes.lookup_call("G3XKQ").entity == "England"
    assert cty_prefixes.lookup_call("9A4OM").entity == "Croatia"


def test_cty_parser_prefers_exact_and_longest_prefix():
    sample = """Testland:  14:  28: EU:  50.00:  -10.00:  -1.0: T:\n    T,TA,=TA1SPECIAL;\nOtherland:  15:  29: AS:  35.00:  -20.00:  -2.0: TA1:\n    TA1;\n"""
    exact, prefixes = cty_prefixes.parse_cty(sample)
    assert exact["TA1SPECIAL"].entity == "Testland"
    assert prefixes[0].key == "TA1"
    assert prefixes[0].entity == "Otherland"


def test_ssb_live_snapshot_uses_prefix_country_without_psk(tmp_path):
    old_db = settings.db_path
    old_callsign = settings.callsign
    try:
        settings.db_path = str(tmp_path / "ham.db")
        settings.callsign = "DL1HLK"
        init_db()
        cty_prefixes._install_fallback()
        now = int(time.time())
        insert_spot({
            "unique_key": "v193-ssb-s51dx",
            "source": "dxcluster_ssb",
            "ts": now,
            "band": "20m",
            "mode": "SSB",
            "frequency_hz": 14310000,
            "tx_call": "S51DX",
            "rx_call": "DL1ABC",
            "raw": "test",
        })
        snap = live_mode_snapshot("ssb", now=now, minutes=15)
        station = next(x for x in snap["stations"] if x["call"] == "S51DX")
        assert station["name"] == "Slowenien" or station["name"] == "Slovenia"
        assert station["region"] == "Europa"
        assert station["distance_km"] is None
        assert station["location_accuracy"] == "entity-only"
        assert station["entity_source"] in {"fallback", "cty.dat cache", "cty.dat live"}
    finally:
        settings.db_path = old_db
        settings.callsign = old_callsign
