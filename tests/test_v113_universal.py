from pathlib import Path
import importlib.util

from app.config import settings
from app.mode_scores import spotter_region_weight


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_qth_defaults_are_generic():
    assert settings.qth_locator == "JO00AA"
    assert settings.callsign == "N0CALL"


def test_ssb_region_weight_is_not_dach_hardcoded():
    old = settings.callsign
    try:
        settings.callsign = "K1ABC"
        assert spotter_region_weight("W2XYZ") >= 0.9
        assert spotter_region_weight("VE3XYZ") > 0
        assert spotter_region_weight("DL1XYZ") == 0
    finally:
        settings.callsign = old


def test_manager_env_update_preserves_other_lines(tmp_path):
    manager_path = Path(__file__).resolve().parents[1] / "tools" / "hamspotter_manager.py"
    mod = _load_module(manager_path, "hamspotter_manager_test")
    env = tmp_path / ".env"
    env.write_text("# x\nCALLSIGN=OLD\nWATCH_SCORE=40\n", encoding="utf-8")
    mod.ENV_FILE = env
    mod.set_env({"CALLSIGN": "K1ABC", "QTH_LOCATOR": "FN31PR"})
    text = env.read_text(encoding="utf-8")
    assert "CALLSIGN=K1ABC" in text
    assert "QTH_LOCATOR=FN31PR" in text
    assert "WATCH_SCORE=40" in text


def test_manager_locator_validation():
    manager_path = Path(__file__).resolve().parents[1] / "tools" / "hamspotter_manager.py"
    mod = _load_module(manager_path, "hamspotter_manager_locator")
    assert mod._valid_locator("JO50AA")
    assert mod._valid_locator("FN31")
    assert not mod._valid_locator("ZZ99ZZ")


def test_international_installer_has_english_default_and_persists_language():
    root = Path(__file__).resolve().parents[1]
    text = (root / "install.sh").read_text(encoding="utf-8")
    assert 'HAM_LANGUAGE="en"' in text
    assert "Language / Sprache:" in text
    assert "English" in text and "Deutsch" in text
    assert "HAMSPOTTER_LANGUAGE" in text
    assert "Callsign" in text
    assert "Building and starting HAM Spotter" in text


def test_bilingual_manager_frontend_translates_and_changes_language(tmp_path):
    wrapper_path = Path(__file__).resolve().parents[1] / "tools" / "hamspotter_manager_i18n.py"
    mod = _load_module(wrapper_path, "hamspotter_manager_i18n_test")
    env = tmp_path / ".env"
    mod.ENV_FILE = env

    # Existing installations without the new setting retain the original German UI.
    assert mod._language() == "de"
    assert mod._translate("Rufzeichen") == "Rufzeichen"

    mod._set_language("en")
    assert mod._language() == "en"
    assert mod._translate("Rufzeichen") == "Callsign"
    assert mod._translate("Neustart") == "Restart"

    mod._set_language("de")
    assert mod._language() == "de"
    assert "HAMSPOTTER_LANGUAGE=de" in env.read_text(encoding="utf-8")
