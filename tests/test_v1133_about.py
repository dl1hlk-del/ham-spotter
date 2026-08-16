from pathlib import Path
import importlib.util

def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod

def test_manager_about_contains_project_attribution(tmp_path, capsys):
    root = Path(__file__).resolve().parents[1]
    mod = _load_module(root / "tools" / "hamspotter_manager.py", "hamspotter_manager_about_test")
    version_file = tmp_path / "VERSION"
    version_file.write_text("1.13.3\n", encoding="utf-8")
    mod.VERSION_FILE = version_file
    mod.about()
    out = capsys.readouterr().out
    assert "HAM Spotter" in out
    assert "1.13.3" in out
    assert "DL1HLK" in out
    assert "GPL-3.0-only" in out
    assert "github.com/dl1hlk-del/ham-spotter" in out

def test_dashboard_contains_discreet_copyright_footer():
    root = Path(__file__).resolve().parents[1]
    text = (root / "app" / "main.py").read_text(encoding="utf-8")
    assert "Copyright © 2026 DL1HLK" in text
    assert "GNU GPL v3.0 only" in text
    assert "github.com/dl1hlk-del/ham-spotter" in text

def test_bilingual_about_translation_is_present():
    root = Path(__file__).resolve().parents[1]
    text = (root / "tools" / "hamspotter_manager_i18n.py").read_text(encoding="utf-8")
    assert '("Über HAM Spotter", "About HAM Spotter")' in text
    assert '("Urheber / Maintainer", "Author / Maintainer")' in text
