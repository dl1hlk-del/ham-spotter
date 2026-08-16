from __future__ import annotations

import asyncio
import json
import logging
import re
import time

import telnetlib3

from ..config import settings
from ..db import insert_spot, set_health

log = logging.getLogger(__name__)

DX_RE = re.compile(
    # DXSpider may append a CQ/ITU zone, grid square or other short field after
    # the UTC stamp.  V1.9.0 anchored the line exactly at "HHMMZ", which could
    # silently discard otherwise valid human spots.
    r"^DX de\s+(?P<spotter>\S+?):\s+(?P<freq>\d+(?:\.\d+)?)\s+(?P<call>\S+)\s+(?P<comment>.*?)\s+(?P<hhmm>\d{4})Z(?:\s+.*)?$",
    re.I,
)

# Common digital watering holes which may lie inside otherwise voice-capable
# frequency ranges. We deliberately reject a small window around them.
_DIGITAL_KHZ = {
    "23cm": (1296174.0,),
    "70cm": (432174.0,),
    "2m": (144174.0,),
    "4m": (70154.0,),
    "6m": (50313.0, 50318.0),
    "10m": (28074.0, 28180.0),
    "12m": (24915.0, 24919.0),
    "15m": (21074.0, 21140.0),
    "17m": (18100.0, 18104.0),
    "20m": (14074.0, 14080.0),
    "40m": (7047.5, 7074.0),
    "60m": (5357.0,),
    "80m": (3573.0, 3575.0),
}

# Practical Region-1 voice segments. They are used as a mode heuristic only;
# an explicit mode in the DX-cluster comment always wins.
_SSB_RANGES_KHZ = {
    # Narrow weak-signal/voice windows for VHF/UHF/SHF.  Explicit mode text
    # in the cluster comment always wins; these ranges are only a fallback.
    "23cm": (1296050.0, 1296400.0),
    "70cm": (432050.0, 432400.0),
    "2m": (144150.0, 144400.0),
    "4m": (70100.0, 70300.0),
    "6m": (50100.0, 52000.0),
    "10m": (28300.0, 29700.0),
    "12m": (24931.0, 24990.0),
    "15m": (21151.0, 21450.0),
    "17m": (18111.0, 18168.0),
    "20m": (14101.0, 14350.0),
    "40m": (7050.0, 7200.0),
    "60m": (5351.5, 5366.5),
    "80m": (3600.0, 3800.0),
}

_DIGITAL_WORDS = re.compile(r"\b(FT8|FT4|RTTY|PSK(?:31|63)?|JT65|JT9|JS8|MFSK|OLIVIA|DIGI(?:TAL)?)\b", re.I)
_CW_WORDS = re.compile(r"\b(CW|MORSE)\b", re.I)
_SSB_WORDS = re.compile(r"\b(SSB|USB|LSB|PHONE|PHONe|VOICE)\b", re.I)


def normalize_call(call: str) -> str:
    value = str(call or "").upper().strip().rstrip(":")
    return re.sub(r"-(?:#|\d+)$", "", value)


def band_from_khz(khz: float) -> str | None:
    if 1240000 <= khz <= 1300000: return "23cm"
    if 420000 <= khz <= 450000: return "70cm"
    if 144000 <= khz <= 148000: return "2m"
    if 70000 <= khz <= 70500: return "4m"
    if 50000 <= khz <= 54000: return "6m"
    if 28000 <= khz <= 29700: return "10m"
    if 24890 <= khz <= 24990: return "12m"
    if 21000 <= khz <= 21450: return "15m"
    if 18068 <= khz <= 18168: return "17m"
    if 14000 <= khz <= 14350: return "20m"
    if 7000 <= khz <= 7300: return "40m"
    if 5351.5 <= khz <= 5366.5: return "60m"
    if 3500 <= khz <= 3800: return "80m"
    return None


def infer_mode(band: str, khz: float, comment: str) -> str:
    text = str(comment or "")
    if _DIGITAL_WORDS.search(text):
        return "DIGITAL"
    if _CW_WORDS.search(text):
        return "CW"
    if _SSB_WORDS.search(text):
        return "SSB"
    for centre in _DIGITAL_KHZ.get(band, ()):  # reject typical FT8/FT4 channels
        if abs(float(khz) - float(centre)) <= 3.0:
            return "DIGITAL"
    low, high = _SSB_RANGES_KHZ.get(band, (0.0, 0.0))
    if low <= khz <= high:
        return "SSB"
    return "OTHER"


def parse_dxcluster_line(line: str) -> dict | None:
    clean = line.replace("\x00", "").strip()
    match = DX_RE.match(clean)
    if not match:
        return None
    khz = float(match.group("freq"))
    band = band_from_khz(khz)
    if band not in settings.bands:
        return None
    comment = match.group("comment").strip()
    mode = infer_mode(band, khz, comment)
    if mode != "SSB":
        return None
    return {
        "spotter": normalize_call(match.group("spotter")),
        "tx_call": normalize_call(match.group("call")),
        "frequency_hz": int(round(khz * 1000)),
        "band": band,
        "mode": "SSB",
        "comment": comment,
        "line": clean,
    }


async def run_dxcluster_stream(stop_event: asyncio.Event) -> None:
    if not settings.dxcluster_enabled:
        set_health("dxcluster_ssb", "DISABLED")
        return

    delay = 2
    while not stop_event.is_set():
        writer = None
        try:
            set_health("dxcluster_ssb", "CONNECTING")
            reader, writer = await telnetlib3.open_connection(
                host=settings.dxcluster_host,
                port=settings.dxcluster_port,
                connect_minwait=0.5,
                connect_maxwait=3.0,
            )

            # DXSpider nodes normally ask for Login:. Read briefly, then identify
            # once. We do not enable RBN/skimmer feeds; this collector is for
            # human DX-cluster reports only.
            preamble = ""
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    chunk = await asyncio.wait_for(reader.read(256), timeout=1.2)
                except asyncio.TimeoutError:
                    break
                if not chunk:
                    break
                preamble += chunk
                if "login" in preamble.lower() or "call" in preamble.lower():
                    break
            writer.write((settings.dxcluster_login or settings.callsign) + "\n")
            await writer.drain()
            # DXSpider user settings can persist between sessions.  Explicitly
            # keep the RBN/skimmer stream disabled so this collector contains
            # human DX-cluster reports only, even if the callsign enabled it in
            # an earlier session.
            await asyncio.sleep(0.5)
            writer.write("unset/skimmer\n")
            await writer.drain()
            set_health("dxcluster_ssb", "LIVE", seen=True)
            log.info("DX cluster SSB connected to %s:%d as %s", settings.dxcluster_host, settings.dxcluster_port, settings.dxcluster_login or settings.callsign)
            delay = 2

            while not stop_event.is_set():
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout=max(60, settings.dxcluster_silence_seconds))
                except asyncio.TimeoutError:
                    raise ConnectionError(f"DX cluster silent for {settings.dxcluster_silence_seconds}s")
                if not line:
                    raise ConnectionError("DX cluster connection closed")
                parsed = parse_dxcluster_line(line)
                if not parsed:
                    continue
                now = int(time.time())
                # A 60-second bucket removes repeated network copies of the same
                # report while preserving independent spotters.
                unique_key = f"dxcluster_ssb:{parsed['spotter']}:{parsed['tx_call']}:{parsed['frequency_hz']}:{now//60}"
                inserted = insert_spot({
                    "unique_key": unique_key,
                    "source": "dxcluster_ssb",
                    "ts": now,
                    "band": parsed["band"],
                    "mode": "SSB",
                    "frequency_hz": parsed["frequency_hz"],
                    "tx_call": parsed["tx_call"],
                    "tx_grid": None,
                    "tx_dxcc": None,
                    "rx_call": parsed["spotter"],
                    "rx_grid": None,
                    "rx_distance_km": None,
                    "tx_distance_km": None,
                    "azimuth_deg": None,
                    "sector": None,
                    "snr": None,
                    "raw": json.dumps({"line": parsed["line"], "comment": parsed["comment"]}, ensure_ascii=False),
                })
                if inserted:
                    set_health("dxcluster_ssb", "LIVE", seen=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("DX cluster SSB connection error: %s", exc)
            set_health("dxcluster_ssb", "RECONNECTING", error=str(exc))
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
