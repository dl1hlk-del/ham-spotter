import time
from app import db
from app.config import settings
from app.live_dx import live_dx_snapshot
from app.decision_layer import best_dx_today


def _insert(con, values):
    con.executemany(
        '''INSERT INTO spots(unique_key,source,ts,band,mode,frequency_hz,tx_call,tx_grid,tx_dxcc,rx_call,rx_grid,rx_distance_km,tx_distance_km,azimuth_deg,sector,snr,raw)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', values
    )


def test_digital_live_aggregates_repeated_reports(tmp_path):
    old = settings.db_path
    settings.db_path = str(tmp_path / 'fast-live.db')
    try:
        db.init_db(); now = int(time.time())
        rows=[]
        for i in range(200):
            rows.append((f'p{i}','pskreporter',now-i%100,'17m','FT8',18100000,'K1ABC','FN31',291,f'DL{i%8}RX','JO50AA',5,6200,295,300,-10+i%5,'{}'))
        with db.connect() as con:
            _insert(con, rows); con.commit()
        out=live_dx_snapshot(now=now, minutes=15, limit=20)
        station=next(x for x in out['stations'] if x['call']=='K1ABC')
        assert station['local_rx']==8
        assert station['distance_km']==6200
        assert station['modes']==['FT8']
    finally:
        settings.db_path = old


def test_best_dx_digital_uses_direct_geometry(tmp_path):
    old = settings.db_path
    settings.db_path = str(tmp_path / 'fast-best.db')
    try:
        db.init_db(); now=int(time.time())
        rows=[]
        for i,dist in enumerate((5000,9000,13000)):
            rows.append((f'd{i}','pskreporter',now-i,'17m','FT8',18100000,f'K{i}ABC','FN31',291,'DL1RX','JO50AA',5,dist,295,300,-8,'{}'))
        with db.connect() as con:
            _insert(con, rows); con.commit()
        out=best_dx_today('digital',('17m',),limit=2,now=now)
        assert out['optimized'] is True
        assert out['stations'][0]['distance_km']==13000
        assert len(out['stations'])==2
    finally:
        settings.db_path = old
