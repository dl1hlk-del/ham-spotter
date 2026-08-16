from __future__ import annotations

import asyncio
import json
import logging
import re
import time

import telnetlib3

from ..config import settings
from ..db import get_rbn_node, insert_spot, set_health

log = logging.getLogger(__name__)

DX_RE = re.compile(
    r"^DX de\s+(?P<spotter>\S+?):\s+(?P<freq>\d+(?:\.\d+)?)\s+(?P<call>\S+)\s+(?P<comment>.*?)\s+(?P<hhmm>\d{4})Z\s*$",
    re.I,
)
SNR_RE = re.compile(r"(?<!\d)(-?\d+(?:\.\d+)?)\s*dB\b", re.I)
MODE_RE = re.compile(r"\b(CW|RTTY|FT8|FT4)\b", re.I)


def normalize_spotter(call: str) -> str:
    c = call.strip().upper().rstrip(":")
    return re.sub(r"-(?:#|\d+)$", "", c)


def band_from_khz(khz: float) -> str | None:
    # Broad amateur-band receive ranges. The RBN stream itself contains
    # amateur spots; these limits only classify the incoming frequency.
    if 1240000 <= khz <= 1300000:
        return "23cm"
    if 420000 <= khz <= 450000:
        return "70cm"
    if 144000 <= khz <= 148000:
        return "2m"
    if 70000 <= khz <= 70500:
        return "4m"
    if 50000 <= khz <= 54000:
        return "6m"
    if 28000 <= khz <= 29700:
        return "10m"
    if 24890 <= khz <= 24990:
        return "12m"
    if 21000 <= khz <= 21450:
        return "15m"
    if 18068 <= khz <= 18168:
        return "17m"
    if 14000 <= khz <= 14350:
        return "20m"
    if 7000 <= khz <= 7300:
        return "40m"
    if 5250 <= khz <= 5450:
        return "60m"
    if 3500 <= khz <= 4000:
        return "80m"
    return None


def parse_rbn_line(line: str, source_name: str) -> dict | None:
    clean = line.replace("\x00", "").strip()
    m = DX_RE.match(clean)
    if not m:
        return None
    khz = float(m.group("freq"))
    band = band_from_khz(khz)
    if band not in settings.bands:
        return None
    comment = m.group("comment")
    snr_m = SNR_RE.search(comment)
    mode_m = MODE_RE.search(comment)
    mode = mode_m.group(1).upper() if mode_m else ("FT8" if source_name == "rbn_ft8" else "CW/RTTY")
    return {
        "spotter": normalize_spotter(m.group("spotter")),
        "frequency_hz": int(round(khz * 1000)),
        "tx_call": m.group("call").upper(),
        "comment": comment,
        "snr": float(snr_m.group(1)) if snr_m else None,
        "mode": mode,
        "band": band,
        "line": clean,
    }


async def run_rbn_stream(port: int, source_name: str, stop_event: asyncio.Event) -> None:
    delay = 2
    while not stop_event.is_set():
        writer = None
        try:
            set_health(source_name, "CONNECTING")
            reader, writer = await telnetlib3.open_connection(
                host=settings.rbn_host,
                port=port,
                connect_minwait=0.5,
                connect_maxwait=2.0,
            )
            # RBN prompts for a callsign. Read a little, then identify once.
            prompt = ""
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and "call" not in prompt.lower():
                try:
                    chunk = await asyncio.wait_for(reader.read(256), timeout=1.5)
                except asyncio.TimeoutError:
                    break
                if not chunk:
                    break
                prompt += chunk
            writer.write(settings.callsign + "\n")
            await writer.drain()
            set_health(source_name, "LIVE", seen=True)
            log.info("%s connected to %s:%d", source_name, settings.rbn_host, port)
            delay = 2

            while not stop_event.is_set():
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout=90)
                except asyncio.TimeoutError:
                    raise ConnectionError("RBN stream silent for 90 seconds")
                if not line:
                    raise ConnectionError("RBN connection closed")
                parsed = parse_rbn_line(line, source_name)
                if not parsed:
                    continue
                node = get_rbn_node(parsed["spotter"])
                if not node:
                    continue
                rx_dist = float(node["distance_km"])
                if rx_dist > settings.local_rx_radius_km:
                    continue
                now = int(time.time())
                unique_key = f"{source_name}:{parsed['spotter']}:{parsed['tx_call']}:{parsed['frequency_hz']}:{now//10}"
                inserted = insert_spot({
                    "unique_key": unique_key,
                    "source": source_name,
                    "ts": now,
                    "band": parsed["band"],
                    "mode": parsed["mode"],
                    "frequency_hz": parsed["frequency_hz"],
                    "tx_call": parsed["tx_call"],
                    "tx_grid": None,
                    "tx_dxcc": None,
                    "rx_call": parsed["spotter"],
                    "rx_grid": node["grid"],
                    "rx_distance_km": rx_dist,
                    "tx_distance_km": None,
                    "azimuth_deg": None,
                    "sector": None,
                    "snr": parsed["snr"],
                    "raw": json.dumps({"line": parsed["line"]}, ensure_ascii=False),
                })
                if inserted:
                    set_health(source_name, "LIVE", seen=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("%s connection error: %s", source_name, exc)
            set_health(source_name, "RECONNECTING", error=str(exc))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, 60)
        finally:
            if writer:
                try:
                    writer.close()
                except Exception:
                    pass
