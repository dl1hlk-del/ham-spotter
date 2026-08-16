import time

from app import db
from app.config import settings
from app.vhf_intel import tropo_evidence


def spot(key, now, band, call, rx, *, dist, mode='FT8'):
    freq = {'2m': 144174000, '70cm': 432174000, '23cm': 1296174000}[band]
    return {
        'unique_key': key, 'source': 'pskreporter', 'ts': now, 'band': band, 'mode': mode,
        'frequency_hz': freq,
        'tx_call': call, 'tx_grid': 'JO31AA', 'tx_dxcc': 223,
        'rx_call': rx, 'rx_grid': 'JO50AA',
        'rx_distance_km': 5, 'tx_distance_km': dist,
        'azimuth_deg': 280, 'sector': 270, 'snr': -8, 'raw': '{}',
    }


def with_db(tmp_path, name):
    old = settings.db_path
    settings.db_path = str(tmp_path / name)
    db.init_db()
    return old


def test_extreme_2m_paths_do_not_create_tropo(tmp_path):
    old = with_db(tmp_path, 'extreme.db')
    try:
        now = int(time.time())
        # Mirrors the real-world failure case: many ~4,000 km 2 m reports,
        # but no persistence/multi-band evidence that would justify "Tropo".
        for i in range(12):
            db.insert_spot(spot(f'x{i}', now - i * 20, '2m', f'EA{i}DX', f'DL{i}RX', dist=4018))
        out = tropo_evidence(now)
        assert out['score'] <= 20
        assert out['label'] == 'keine stabilen Hinweise'
        assert out['active_bands'] == 0
        assert out['excluded_extreme_paths'] == 12
        assert out['extreme_max_distance_km'] == 4018
        assert out['max_distance_km'] == 0
    finally:
        settings.db_path = old


def test_meteor_modes_are_strictly_excluded_from_tropo(tmp_path):
    old = with_db(tmp_path, 'meteor.db')
    try:
        now = int(time.time())
        for i in range(8):
            db.insert_spot(spot(f'm{i}', now - i * 25, '2m', f'SM{i}MS', f'DL{i}RX', dist=900, mode='MSK144'))
        out = tropo_evidence(now)
        assert out['score'] == 0
        assert out['unique_tx'] == 0
        assert out['excluded_meteor_reports'] == 8
    finally:
        settings.db_path = old


def test_transient_single_band_activity_cannot_be_probable(tmp_path):
    old = with_db(tmp_path, 'transient.db')
    try:
        now = int(time.time())
        for i in range(10):
            db.insert_spot(spot(f't{i}', now - i * 30, '2m', f'G{i}DX', f'DL{i}RX', dist=650))
        out = tropo_evidence(now)
        assert out['active_bands'] == 1
        assert out['persistent_bands'] == 0
        assert out['score'] <= 20
        assert out['label'] == 'keine stabilen Hinweise'
    finally:
        settings.db_path = old


def test_persistent_single_band_can_be_probable(tmp_path):
    old = with_db(tmp_path, 'persistent.db')
    try:
        now = int(time.time())
        # Same band has qualifying reports in the current 30 min and earlier
        # part of the 90 min window.
        for i in range(4):
            db.insert_spot(spot(f'c{i}', now - 5 * 60 - i * 30, '2m', f'G{i}AA', f'DL{i}RX', dist=700))
            db.insert_spot(spot(f'o{i}', now - 50 * 60 - i * 30, '2m', f'G{i}BB', f'DL{i+4}RX', dist=720))
        out = tropo_evidence(now)
        assert out['active_bands'] == 1
        assert out['persistent_bands'] == 1
        assert out['score'] >= 45
        assert out['label'] == 'wahrscheinlich'
    finally:
        settings.db_path = old


def test_strong_tropo_requires_persistence_and_multiband(tmp_path):
    old = with_db(tmp_path, 'strong.db')
    try:
        now = int(time.time())
        for band, dist in [('2m', 800), ('70cm', 500), ('23cm', 350)]:
            for i in range(4):
                db.insert_spot(spot(f'{band}c{i}', now - 5 * 60 - i * 20, band, f'PA{i}{band}', f'DL{i}RX', dist=dist))
                db.insert_spot(spot(f'{band}o{i}', now - 55 * 60 - i * 20, band, f'ON{i}{band}', f'DL{i+4}RX', dist=dist + 20))
        out = tropo_evidence(now)
        assert out['active_bands'] == 3
        assert out['persistent_bands'] == 3
        assert out['score'] >= 70
        assert out['label'] == 'stark'
    finally:
        settings.db_path = old
