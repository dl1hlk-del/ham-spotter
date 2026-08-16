from __future__ import annotations

import json
import logging
import ssl
import threading
import time

import paho.mqtt.client as mqtt

from ..config import settings
from ..db import insert_spot, set_health
from ..geo import haversine_km, initial_bearing_deg, local_locator4_squares, locator_to_latlon, sector30

log = logging.getLogger(__name__)


class PSKReporterCollector:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._client: mqtt.Client | None = None
        self.q_lat, self.q_lon = locator_to_latlon(settings.qth_locator)
        self.rx_grids = local_locator4_squares(settings.qth_locator, settings.local_rx_radius_km)

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name="psk-reporter", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass

    def run(self) -> None:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"hamspotter-{settings.callsign.lower()}-{int(time.time())}")
        self._client = client
        client.reconnect_delay_set(min_delay=2, max_delay=60)
        if settings.pskr_tls:
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        set_health("pskreporter", "CONNECTING")
        try:
            client.connect(settings.pskr_host, settings.pskr_port, keepalive=60)
            client.loop_forever(retry_first_connection=True)
        except Exception as exc:
            log.exception("PSK Reporter collector stopped")
            set_health("pskreporter", "ERROR", error=str(exc))

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            set_health("pskreporter", "ERROR", error=f"MQTT connect reason={reason_code}")
            return
        topics = []
        for band in settings.bands:
            for grid in self.rx_grids:
                topic = f"pskr/filter/v2/{band}/+/+/+/+/{grid}/+/+"
                topics.append((topic, 0))
        client.subscribe(topics)
        set_health("pskreporter", "LIVE", seen=True)
        log.info("PSK Reporter connected; %d subscriptions (%d local RX squares)", len(topics), len(self.rx_grids))

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        if not self._stop.is_set():
            set_health("pskreporter", "RECONNECTING", error=f"disconnect reason={reason_code}")

    def _on_message(self, client, userdata, msg) -> None:
        try:
            data = json.loads(msg.payload.decode("utf-8", errors="replace"))
            band = str(data.get("b", "")).lower()
            if band not in settings.bands:
                return
            rx_grid = str(data.get("rl", "") or "").upper()
            tx_grid = str(data.get("sl", "") or "").upper()
            if len(rx_grid) < 4:
                return
            rx_lat, rx_lon = locator_to_latlon(rx_grid[:8])
            rx_dist = haversine_km(self.q_lat, self.q_lon, rx_lat, rx_lon)
            if rx_dist > settings.local_rx_radius_km:
                return

            tx_dist = az = sec = None
            if len(tx_grid) >= 4:
                try:
                    tx_lat, tx_lon = locator_to_latlon(tx_grid[:8])
                    tx_dist = haversine_km(self.q_lat, self.q_lon, tx_lat, tx_lon)
                    az = initial_bearing_deg(self.q_lat, self.q_lon, tx_lat, tx_lon)
                    sec = sector30(az)
                except ValueError:
                    pass

            ts = int(data.get("t_tx") or data.get("t") or time.time())
            unique_key = f"psk:{data.get('sq', '')}:{data.get('sc','')}:{data.get('rc','')}:{data.get('f','')}:{ts}"
            inserted = insert_spot({
                "unique_key": unique_key,
                "source": "pskreporter",
                "ts": ts,
                "band": band,
                "mode": str(data.get("md", "") or ""),
                "frequency_hz": int(data.get("f") or 0) or None,
                "tx_call": str(data.get("sc", "") or "").upper(),
                "tx_grid": tx_grid or None,
                "tx_dxcc": int(data["sa"]) if data.get("sa") is not None else None,
                "rx_call": str(data.get("rc", "") or "").upper(),
                "rx_grid": rx_grid,
                "rx_distance_km": round(rx_dist, 1),
                "tx_distance_km": round(tx_dist, 1) if tx_dist is not None else None,
                "azimuth_deg": round(az, 1) if az is not None else None,
                "sector": sec,
                "snr": float(data["rp"]) if data.get("rp") is not None else None,
                "raw": json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            })
            if inserted:
                set_health("pskreporter", "LIVE", seen=True)
        except Exception as exc:
            log.debug("PSK message ignored: %s", exc)
