import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / 'app' / 'main.py').read_text()

def test_main_compiles():
    ast.parse(MAIN)

def test_raw_none_removed_from_band_card():
    assert 'conf_display = "keine Richtungsdaten"' in MAIN
    assert 'direction_raw or "keine Locator-Daten"' in MAIN

def test_live_enrichment_present():
    assert 'def _live_band_context' in MAIN
    assert 'countries_display = d.get("countries") or live_ctx.get("countries") or []' in MAIN
    assert 'dominant_region") or live_ctx.get("dominant_region")' in MAIN

def test_no_country_centroid_guessing():
    # The helper only consumes station distance/sector already present in live data.
    helper = MAIN.split('def _live_band_context',1)[1].split('def _state_icon',1)[0]
    assert 'lookup_call' not in helper
    assert 'tx_lat' not in helper
    assert 'tx_lon' not in helper
