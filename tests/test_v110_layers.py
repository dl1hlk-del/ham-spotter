from pathlib import Path
import sys
import types

# Collector modules import telnetlib3 for runtime I/O; parser tests do not need a real socket.
sys.modules.setdefault("telnetlib3", types.SimpleNamespace())

from app.band_layers import configured_layer_bands, layer_label, normalize_layer
from app.collectors.dxcluster import band_from_khz as cluster_band_from_khz, infer_mode, parse_dxcluster_line
from app.collectors.rbn import band_from_khz as rbn_band_from_khz, parse_rbn_line
from app.config import settings
from app.formatting import band_label


def test_new_bands_are_configured_and_separated():
    assert {"4m", "2m", "70cm", "23cm"}.issubset(set(settings.bands))
    assert configured_layer_bands("hf") == ("6m", "10m", "12m", "15m", "17m", "20m", "40m", "60m", "80m")
    assert configured_layer_bands("vhf") == ("4m", "2m", "70cm", "23cm")
    assert normalize_layer("vhfuhf") == "vhf"
    assert layer_label("vhf") == "VHF/UHF/SHF"


def test_rbn_frequency_classifier_supports_vhf_uhf_shf():
    assert rbn_band_from_khz(70175.0) == "4m"
    assert rbn_band_from_khz(144300.0) == "2m"
    assert rbn_band_from_khz(432200.0) == "70cm"
    assert rbn_band_from_khz(1296200.0) == "23cm"


def test_dxcluster_frequency_classifier_and_mode_heuristic():
    assert cluster_band_from_khz(70200.0) == "4m"
    assert cluster_band_from_khz(144300.0) == "2m"
    assert cluster_band_from_khz(432200.0) == "70cm"
    assert cluster_band_from_khz(1296200.0) == "23cm"
    assert infer_mode("2m", 144300.0, "") == "SSB"
    assert infer_mode("2m", 144174.0, "FT8") == "DIGITAL"
    assert infer_mode("70cm", 432200.0, "USB") == "SSB"


def test_dxcluster_parser_accepts_vhf_ssb_and_rejects_ft8():
    row = parse_dxcluster_line("DX de DL1ABC: 144300.0 F4XYZ SSB 59 1250Z")
    assert row is not None
    assert row["band"] == "2m"
    assert row["mode"] == "SSB"
    assert parse_dxcluster_line("DX de DL1ABC: 144174.0 F4XYZ FT8 -12 1250Z") is None


def test_rbn_parser_accepts_high_bands():
    samples = {
        "4m": "DX de DL0RBN-#: 70175.0 G4ABC CW 18 dB 25 WPM CQ 1250Z",
        "2m": "DX de DL0RBN-#: 144050.0 OK1ABC CW 18 dB 25 WPM CQ 1250Z",
        "70cm": "DX de DL0RBN-#: 432050.0 PA3ABC CW 18 dB 25 WPM CQ 1250Z",
        "23cm": "DX de DL0RBN-#: 1296050.0 OZ1ABC CW 18 dB 25 WPM CQ 1250Z",
    }
    for band, line in samples.items():
        parsed = parse_rbn_line(line, "rbn_cw")
        assert parsed is not None
        assert parsed["band"] == band


def test_dashboard_source_contains_layer_switch_and_preserves_mode():
    src = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    assert 'def dashboard(days: int = 30, mode: str = "ssb", layer: str | None = None)' in src
    assert "configured_layer_bands(layer)" in src
    assert "4 m · 2 m · 70 cm · 23 cm" in src
    assert "&layer={layer}" in src


def test_band_labels_for_centimetre_bands():
    assert band_label("4m") == "4 m"
    assert band_label("2m") == "2 m"
    assert band_label("70cm") == "70 cm"
    assert band_label("23cm") == "23 cm"
