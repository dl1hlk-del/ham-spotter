import time

from app import db
from app.config import settings
from app.vhf_intel import tropo_evidence, meteor_scatter_activity, beacon_snapshot, sporadic_e_evidence


def spot(key, now, band, call, rx, *, dist=None, mode='FT8', sector=0, raw='{}'):
    return {
        'unique_key': key, 'source': 'pskreporter', 'ts': now, 'band': band, 'mode': mode,
        'frequency_hz': 144174000 if band == '2m' else 432174000,
        'tx_call': call, 'tx_grid': 'JO31AA', 'tx_dxcc': 223, 'rx_call': rx, 'rx_grid': 'JO50AA',
        'rx_distance_km': 5, 'tx_distance_km': dist, 'azimuth_deg': 280 if sector else 5,
        'sector': sector, 'snr': -8, 'raw': raw,
    }


def test_tropo_and_es_and_meteor(tmp_path):
    old = settings.db_path
    settings.db_path = str(tmp_path / 'vhf.db')
    try:
        db.init_db()
        now = int(time.time())
        # sustained 2m + 70cm long-distance activity
        db.insert_spot(spot('t1', now-600, '2m', 'G1AAA', 'DL1RX', dist=650))
        db.insert_spot(spot('t2', now-2400, '2m', 'G1BBB', 'DL2RX', dist=700))
        db.insert_spot(spot('t3', now-500, '70cm', 'PA1AAA', 'DL1RX', dist=420))
        # Es-like 4m path
        s = spot('e1', now-100, '4m', 'EA1AAA', 'DL3RX', dist=1600, sector=240)
        s['frequency_hz'] = 70154000
        db.insert_spot(s)
        s2 = spot('e2', now-120, '4m', 'EA2BBB', 'DL4RX', dist=1500, sector=240)
        s2['frequency_hz'] = 70154000
        db.insert_spot(s2)
        # explicit meteor-scatter mode
        db.insert_spot(spot('m1', now-30, '2m', 'SM1MS', 'DL5RX', dist=900, mode='MSK144'))
        db.insert_spot(spot('m2', now-20, '2m', 'SM2MS', 'DL6RX', dist=850, mode='MSK144'))

        tropo = tropo_evidence(now)
        assert tropo['score'] > 0
        assert tropo['bands']['2m']['max_distance_km'] == 700
        es = sporadic_e_evidence(now)
        assert es['unique_tx'] >= 2
        assert es['top_sector'] == 240
        meteor = meteor_scatter_activity(now)
        assert meteor['unique_tx'] == 2
        assert meteor['modes']['MSK144'] == 2
    finally:
        settings.db_path = old


def test_beacon_detection_is_explicit(tmp_path):
    old = settings.db_path
    settings.db_path = str(tmp_path / 'bcn.db')
    try:
        db.init_db()
        now = int(time.time())
        b = spot('b1', now, '2m', 'DB0ABC/B', 'DL1RX', dist=420, mode='CW')
        b['frequency_hz'] = 144430000
        db.insert_spot(b)
        n = spot('n1', now, '2m', 'DL1NORMAL', 'DL1RX', dist=500, mode='FT8')
        db.insert_spot(n)
        out = beacon_snapshot(now)
        assert out['count'] == 1
        assert out['beacons'][0]['call'] == 'DB0ABC/B'
    finally:
        settings.db_path = old
