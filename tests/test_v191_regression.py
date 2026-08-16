import os
import sqlite3
import tempfile
import time

from app.collectors.dxcluster import parse_dxcluster_line


def test_dxcluster_accepts_trailing_zone_or_grid():
    row = parse_dxcluster_line("DX de DL1ABC: 18145.0 K1ABC CQ NA 2250Z 14")
    assert row is not None
    assert row["band"] == "17m"
    assert row["mode"] == "SSB"


def test_dxcluster_still_rejects_digital():
    assert parse_dxcluster_line("DX de DL1ABC: 18100.0 K1ABC FT8 -10 dB 2250Z 14") is None
