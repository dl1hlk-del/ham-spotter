from __future__ import annotations

import tempfile

from app.config import settings
from app.db import init_db, opening_history, opening_stats, sync_opening_event


def test_opening_history_and_direction_split():
    original = settings.db_path
    settings.db_path = tempfile.mktemp(suffix=".db")
    try:
        init_db()
        details = {
            "direction_reliable": True,
            "dominant_region": "Nordamerika",
            "countries": [{"dxcc": 291, "name": "USA", "calls": 12}],
            "target_median_dx_km": 7200,
            "psk_unique_tx": 20,
            "psk_unique_rx": 8,
            "rbn_unique_tx": 10,
            "rbn_unique_rx": 3,
        }
        t0 = 1_800_000_000
        sync_opening_event("17m", "OPEN", 70, 300, "WNW/NW 300°", details, now=t0)
        sync_opening_event("17m", "STRONG", 88, 300, "WNW/NW 300°", details, now=t0 + 120)
        sync_opening_event("17m", "WATCH", 40, 300, "WNW/NW 300°", details, now=t0 + 600)
        history = opening_history()
        assert len(history) == 1
        assert history[0]["max_state"] == "STRONG"
        assert history[0]["duration_seconds"] == 600
        assert history[0]["dominant_region"] == "Nordamerika"
        stats = opening_stats(3650)
        row = next(x for x in stats["bands"] if x["band"] == "17m")
        assert row["events"] == 1
        assert row["strong_events"] == 1
    finally:
        settings.db_path = original
