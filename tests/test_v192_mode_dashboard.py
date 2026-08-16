from pathlib import Path
import ast

MAIN = Path(__file__).resolve().parents[1] / "app" / "main.py"


def test_dashboard_live_dx_follows_selected_mode():
    src = MAIN.read_text(encoding="utf-8")
    tree = ast.parse(src)
    dashboard = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "dashboard")
    text = ast.get_source_segment(src, dashboard)
    assert "live_mode_snapshot(mode, limit=100, minutes=settings.dx_live_minutes)" in text
    assert "live_dx = live_dx_snapshot(limit=100)" not in text


def test_highlights_endpoint_accepts_mode_and_uses_mode_snapshot():
    src = MAIN.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "api_highlights")
    text = ast.get_source_segment(src, fn)
    assert "mode: str = \"ssb\"" in text
    assert "live_mode_snapshot(mode" in text
    assert '"mode": mode' in text


def test_version_192():
    src = MAIN.read_text(encoding="utf-8")
    assert 'VERSION = "1.9.2"' in src
