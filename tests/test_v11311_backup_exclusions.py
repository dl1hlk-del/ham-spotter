from __future__ import annotations

import importlib.util
import sqlite3
import tarfile
import tempfile
from pathlib import Path


def load_manager(root: Path):
    src = root / "tools" / "hamspotter_manager.py"
    spec = importlib.util.spec_from_file_location("hamspotter_manager_v11311_test", src)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def create_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE spots(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, band TEXT, tx_call TEXT);
        CREATE INDEX idx_spots_ts ON spots(ts);
        CREATE TABLE opening_events(id INTEGER PRIMARY KEY AUTOINCREMENT, band TEXT, peak_score INTEGER);
        """
    )
    con.executemany(
        "INSERT INTO spots(ts,band,tx_call) VALUES(?,?,?)",
        [(i, "17m", f"K{i}") for i in range(10)],
    )
    con.execute("INSERT INTO opening_events(band,peak_score) VALUES('17m',88)")
    con.commit()
    con.close()


def archive_names(path: Path) -> set[str]:
    with tarfile.open(path, "r:gz") as tf:
        return set(tf.getnames())


def test_compact_and_full_backups_exclude_nested_maintenance_snapshots():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        data = root / "data"
        backups = root / "backups"
        data.mkdir()
        backups.mkdir()

        repo = Path(__file__).resolve().parents[1]
        mod = load_manager(repo)
        mod.ROOT = root
        mod.ENV_FILE = root / ".env"
        mod.DATA_DIR = data
        mod.BACKUP_DIR = backups
        mod.VERSION_FILE = root / "VERSION"

        mod.ENV_FILE.write_text("CALLSIGN=DL1TEST\n", encoding="utf-8")
        mod.VERSION_FILE.write_text("1.13.11\n", encoding="utf-8")
        create_db(data / "hamspotter.db")

        upgrade = data / "upgrade-v1.10-test"
        maintenance = data / "maintenance-backup-test"
        runtime = data / "runtime-cache"
        upgrade.mkdir()
        maintenance.mkdir()
        runtime.mkdir()

        (upgrade / "hamspotter.db.before-v1.10").write_bytes(b"x" * 1024)
        (maintenance / "hamspotter.db").write_bytes(b"y" * 1024)
        (runtime / "keep.txt").write_text("keep", encoding="utf-8")

        compact = mod.backup()
        full = mod.backup(full=True)

        for archive in (compact, full):
            names = archive_names(archive)
            assert "data/runtime-cache/keep.txt" in names
            assert not any(name.startswith("data/upgrade-v1.10-test") for name in names)
            assert not any(name.startswith("data/maintenance-backup-test") for name in names)
