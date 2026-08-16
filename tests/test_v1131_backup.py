from __future__ import annotations

import importlib.util
import io
import sqlite3
import tarfile
import tempfile
from contextlib import redirect_stdout
from pathlib import Path


def load_manager(root: Path):
    src = root / 'tools' / 'hamspotter_manager.py'
    spec = importlib.util.spec_from_file_location('hamspotter_manager_test', src)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    mod.ROOT = root
    mod.ENV_FILE = root / '.env'
    mod.DATA_DIR = root / 'data'
    mod.BACKUP_DIR = root / 'backups'
    mod.VERSION_FILE = root / 'VERSION'
    return mod


def create_db(path: Path, spots=100):
    con = sqlite3.connect(path)
    con.executescript('''
      CREATE TABLE spots(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, band TEXT, tx_call TEXT);
      CREATE INDEX idx_spots_ts ON spots(ts);
      CREATE TABLE opening_events(id INTEGER PRIMARY KEY AUTOINCREMENT, band TEXT, peak_score INTEGER);
      CREATE TABLE rare_observations(id INTEGER PRIMARY KEY AUTOINCREMENT, dxcc INTEGER, band TEXT);
    ''')
    con.executemany('INSERT INTO spots(ts,band,tx_call) VALUES(?,?,?)', [(i,'17m',f'K{i}') for i in range(spots)])
    con.executemany('INSERT INTO opening_events(band,peak_score) VALUES(?,?)', [('17m',88),('20m',75)])
    con.execute('INSERT INTO rare_observations(dxcc,band) VALUES(291,"17m")')
    con.commit(); con.close()


def test_compact_backup_preserves_history_and_omits_raw_spots():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); (root/'tools').mkdir(); (root/'data').mkdir(); (root/'backups').mkdir()
        # load actual manager from repository before changing ROOT globals
        repo=Path(__file__).resolve().parents[1]
        mod=load_manager(repo)
        mod.ROOT=root; mod.ENV_FILE=root/'.env'; mod.DATA_DIR=root/'data'; mod.BACKUP_DIR=root/'backups'; mod.VERSION_FILE=root/'VERSION'
        (root/'.env').write_text('CALLSIGN=DL1TEST\n', encoding='utf-8'); (root/'VERSION').write_text('1.13.1\n')
        create_db(root/'data'/'hamspotter.db', spots=500)
        out=io.StringIO()
        with redirect_stdout(out):
            archive=mod.backup()
        text=out.getvalue()
        assert 'Backup wird erstellt' in text and 'Archiv wird komprimiert' in text and 'Backup fertig' in text
        extract=root/'restore'; extract.mkdir()
        with tarfile.open(archive,'r:gz') as tf: tf.extractall(extract)
        con=sqlite3.connect(extract/'data'/'hamspotter.db')
        assert con.execute('select count(*) from spots').fetchone()[0] == 0
        assert con.execute('select count(*) from opening_events').fetchone()[0] == 2
        assert con.execute('select count(*) from rare_observations').fetchone()[0] == 1
        assert con.execute('pragma quick_check').fetchone()[0] == 'ok'
        con.close()


def test_full_backup_keeps_raw_spots():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); (root/'data').mkdir(); (root/'backups').mkdir()
        repo=Path(__file__).resolve().parents[1]
        mod=load_manager(repo)
        mod.ROOT=root; mod.ENV_FILE=root/'.env'; mod.DATA_DIR=root/'data'; mod.BACKUP_DIR=root/'backups'; mod.VERSION_FILE=root/'VERSION'
        (root/'.env').write_text('CALLSIGN=DL1TEST\n', encoding='utf-8'); (root/'VERSION').write_text('1.13.1\n')
        create_db(root/'data'/'hamspotter.db', spots=33)
        archive=mod.backup(full=True)
        extract=root/'restore'; extract.mkdir()
        with tarfile.open(archive,'r:gz') as tf: tf.extractall(extract)
        con=sqlite3.connect(extract/'data'/'hamspotter.db')
        assert con.execute('select count(*) from spots').fetchone()[0] == 33
        con.close()
