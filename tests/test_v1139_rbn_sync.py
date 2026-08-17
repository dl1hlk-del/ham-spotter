import pytest

from app.config import settings
from app.db import connect, init_db
from app.rbn_nodes import sync_rbn_nodes


def _calls() -> list[str]:
    with connect() as con:
        rows = con.execute("SELECT callsign FROM rbn_nodes ORDER BY callsign").fetchall()
    return [str(row["callsign"]) for row in rows]


def test_rbn_sync_replaces_stale_nodes(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "hamspotter.db"))
    init_db()

    sync_rbn_nodes([
        ("DL1OLD", "JO61FR", 10.0),
        ("AA0O", "EL87PS", 100.0),
    ])
    assert _calls() == ["AA0O", "DL1OLD"]

    count = sync_rbn_nodes([
        ("AA0O", "EL87PS", 101.0),
        ("W3LPL", "FM19LG", 200.0),
    ])

    assert count == 2
    assert _calls() == ["AA0O", "W3LPL"]


def test_rbn_sync_refuses_empty_snapshot_and_keeps_last_good_data(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "hamspotter.db"))
    init_db()

    sync_rbn_nodes([("AA0O", "EL87PS", 100.0)])

    with pytest.raises(ValueError, match="empty set"):
        sync_rbn_nodes([])

    assert _calls() == ["AA0O"]
