from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import settings
from .db import load_space_weather, save_space_weather, set_health

log = logging.getLogger(__name__)


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _record(payload: Any, *, last: bool = True) -> dict[str, Any]:
    if isinstance(payload, list):
        rows = [x for x in payload if isinstance(x, dict)]
        return (rows[-1] if last else rows[0]) if rows else {}
    if isinstance(payload, dict):
        # NOAA scales historically uses {"0": {...}, ...}.
        numeric = [payload[k] for k in sorted(payload, key=lambda x: int(x) if str(x).isdigit() else 999999) if str(k).isdigit() and isinstance(payload[k], dict)]
        if numeric:
            return numeric[-1] if last else numeric[0]
        return payload
    return {}


def _pick(d: dict[str, Any], *names: str) -> Any:
    lowered = {str(k).lower(): v for k, v in d.items()}
    for name in names:
        if name in d:
            return d[name]
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def xray_class(flux: float | None) -> str | None:
    if flux is None or flux <= 0:
        return None
    levels = [("X", 1e-4), ("M", 1e-5), ("C", 1e-6), ("B", 1e-7), ("A", 1e-8)]
    for letter, base in levels:
        if flux >= base:
            return f"{letter}{flux / base:.1f}"
    return f"A{flux / 1e-8:.1f}"


def parse_running_a(text: str) -> int | None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "running a" in line.lower():
            for row in lines[i + 1:i + 8]:
                s = row.strip()
                if not s or s.startswith("#"):
                    continue
                nums = re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?", s)
                if nums:
                    value = _num(nums[0])
                    return int(round(value)) if value is not None and value >= 0 else None
    return None


def parse_daily_solar(text: str) -> tuple[float | None, int | None]:
    """Return latest daily F10.7 and sunspot number from NOAA daily-solar-indices.txt."""
    best: tuple[int, float | None, int | None] | None = None
    for line in text.splitlines():
        nums = re.findall(r"-?\d+(?:\.\d+)?", line.strip())
        if len(nums) < 5:
            continue
        try:
            year, month, day = map(int, nums[:3])
            stamp = year * 10000 + month * 100 + day
            if not (1990 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31):
                continue
            flux = _num(nums[3])
            ssn_raw = _num(nums[4])
            ssn = int(round(ssn_raw)) if ssn_raw is not None and ssn_raw >= 0 else None
            if best is None or stamp > best[0]:
                best = (stamp, flux, ssn)
        except Exception:
            continue
    return (best[1], best[2]) if best else (None, None)


def parse_scales(payload: Any) -> tuple[int | None, int | None, int | None]:
    row = _record(payload, last=False)
    out = []
    for key in ("R", "S", "G"):
        value = row.get(key) or row.get(key.lower()) or {}
        if isinstance(value, dict):
            scale = _num(_pick(value, "Scale", "scale"))
        else:
            scale = _num(value)
        out.append(int(scale) if scale is not None else None)
    return tuple(out)  # type: ignore[return-value]


def _geomag_label(kp: float | None) -> str:
    if kp is None:
        return "unbekannt"
    if kp < 3:
        return "ruhig"
    if kp < 4:
        return "leicht aktiv"
    if kp < 5:
        return "aktiv"
    if kp < 6:
        return "Sturm G1"
    if kp < 7:
        return "Sturm G2"
    if kp < 8:
        return "Sturm G3"
    return "starker Sturm"


def _rating(data: dict[str, Any]) -> dict[str, str]:
    kp = _num(data.get("kp"))
    sfi = _num(data.get("sfi"))
    r = int(data.get("r_scale") or 0)
    bz = _num(data.get("bz_nt"))

    if r >= 2 or (kp is not None and kp >= 6):
        high = "🔴 schlecht/gestört"
    elif kp is not None and kp >= 5:
        high = "🟠 gestört"
    elif sfi is not None and sfi >= 150 and (kp is None or kp <= 3):
        high = "🟢 sehr gut"
    elif sfi is not None and sfi >= 115 and (kp is None or kp <= 4):
        high = "🟢 gut"
    else:
        high = "🟡 wechselhaft"

    if r >= 2:
        low = "🔴 Dämpfung möglich"
    elif kp is not None and kp >= 6:
        low = "🟠 gestört"
    elif kp is not None and kp <= 3:
        low = "🟢 ruhig/gut"
    else:
        low = "🟡 wechselhaft"

    geomag = "🟢 ruhig" if kp is not None and kp < 3 else ("🟡 aktiv" if kp is not None and kp < 5 else ("🔴 Sturm" if kp is not None else "⚪ unbekannt"))
    if bz is not None and bz <= -8 and kp is not None and kp >= 4:
        geomag += " · Bz südlich"
    return {"low_bands": low, "high_bands": high, "six_m": "⚪ HAM Spotter entscheidet", "geomagnetic": geomag}


async def _get_json(client: httpx.AsyncClient, url: str) -> Any:
    r = await client.get(url, headers={"User-Agent": f"HAM-Spotter/{settings.callsign}"})
    r.raise_for_status()
    return r.json()


async def _get_text(client: httpx.AsyncClient, url: str) -> str:
    r = await client.get(url, headers={"User-Agent": f"HAM-Spotter/{settings.callsign}"})
    r.raise_for_status()
    return r.text


async def refresh_once() -> dict[str, Any]:
    if not settings.space_weather_enabled:
        set_health("noaa_swpc", "DISABLED")
        return snapshot()

    old = load_space_weather() or {}
    data: dict[str, Any] = dict(old)
    errors: list[str] = []
    now = int(time.time())

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        # Summary values use NOAA's post-March-2026 standard JSON object/array format.
        jobs = {
            "sfi": _get_json(client, settings.noaa_sfi_url),
            "kp": _get_json(client, settings.noaa_kp_url),
            "wind": _get_json(client, settings.noaa_wind_url),
            "mag": _get_json(client, settings.noaa_mag_url),
            "scales": _get_json(client, settings.noaa_scales_url),
            "xray": _get_json(client, settings.noaa_xray_url),
            "indices": _get_text(client, settings.noaa_indices_url),
            "solar": _get_text(client, settings.noaa_daily_solar_url),
        }
        results = await asyncio.gather(*jobs.values(), return_exceptions=True)
        got = dict(zip(jobs, results))

    for name, value in got.items():
        if isinstance(value, Exception):
            errors.append(f"{name}: {value}")

    if len(errors) == len(jobs):
        set_health("noaa_swpc", "ERROR", error="; ".join(errors[:2]))
        if old:
            log.warning("NOAA SWPC unavailable; keeping cached Funkwetter data")
            return snapshot()
        raise RuntimeError("All NOAA SWPC sources failed")

    if not isinstance(got["sfi"], Exception):
        row = _record(got["sfi"])
        val = _num(_pick(row, "flux", "Flux", "f107"))
        if val is not None:
            data["sfi"] = round(val, 1)
    if not isinstance(got["kp"], Exception):
        row = _record(got["kp"])
        val = _num(_pick(row, "Kp", "kp", "kp_index"))
        if val is not None:
            data["kp"] = round(val, 2)
    if not isinstance(got["wind"], Exception):
        row = _record(got["wind"])
        val = _num(_pick(row, "WindSpeed", "wind_speed", "speed", "proton_speed"))
        if val is not None:
            data["solar_wind_kms"] = round(val, 1)
    if not isinstance(got["mag"], Exception):
        row = _record(got["mag"])
        bz = _num(_pick(row, "Bz", "bz", "bz_gsm"))
        bt = _num(_pick(row, "Bt", "bt"))
        if bz is not None:
            data["bz_nt"] = round(bz, 1)
        if bt is not None:
            data["bt_nt"] = round(bt, 1)
    if not isinstance(got["scales"], Exception):
        r, s, g = parse_scales(got["scales"])
        if r is not None: data["r_scale"] = r
        if s is not None: data["s_scale"] = s
        if g is not None: data["g_scale"] = g
    if not isinstance(got["xray"], Exception) and isinstance(got["xray"], list):
        candidates = [x for x in got["xray"] if isinstance(x, dict) and "0.1-0.8" in str(_pick(x, "energy") or "") and _num(_pick(x, "flux")) is not None]
        if not candidates:
            candidates = [x for x in got["xray"] if isinstance(x, dict) and _num(_pick(x, "flux")) is not None]
        if candidates:
            flux = _num(_pick(candidates[-1], "flux"))
            if flux is not None:
                data["xray_flux"] = flux
                data["xray_class"] = xray_class(flux)
    if not isinstance(got["indices"], Exception):
        a = parse_running_a(str(got["indices"]))
        if a is not None:
            data["a_index"] = a
    if not isinstance(got["solar"], Exception):
        daily_flux, ssn = parse_daily_solar(str(got["solar"]))
        if ssn is not None:
            data["ssn"] = ssn
        if data.get("sfi") is None and daily_flux is not None:
            data["sfi"] = round(daily_flux, 1)

    data["updated_at"] = now
    data["provider"] = "NOAA SWPC"
    data["geomagnetic_label"] = _geomag_label(_num(data.get("kp")))
    data["assessment"] = _rating(data)
    data["partial"] = bool(errors)
    data["errors"] = errors[:4]
    save_space_weather(data)

    if errors and len(errors) < len(jobs):
        set_health("noaa_swpc", "DEGRADED", seen=True, error="; ".join(errors[:2]))
    elif errors:
        set_health("noaa_swpc", "ERROR", error="; ".join(errors[:2]))
    else:
        set_health("noaa_swpc", "LIVE", seen=True)
    log.info("NOAA SWPC refreshed: SFI=%s Kp=%s A=%s SSN=%s wind=%s Bz=%s Xray=%s R/S/G=%s/%s/%s%s",
             data.get("sfi"), data.get("kp"), data.get("a_index"), data.get("ssn"), data.get("solar_wind_kms"), data.get("bz_nt"), data.get("xray_class"),
             data.get("r_scale"), data.get("s_scale"), data.get("g_scale"), " DEGRADED" if errors else "")
    return data


def snapshot() -> dict[str, Any]:
    data = load_space_weather() or {}
    if not data:
        return {"available": False, "updated_at": None, "provider": "NOAA SWPC", "assessment": {"low_bands":"⚪ —","high_bands":"⚪ —","six_m":"⚪ HAM Spotter entscheidet","geomagnetic":"⚪ —"}}
    data = dict(data)
    data["available"] = True
    updated = int(data.get("updated_at") or 0)
    data["age_seconds"] = max(0, int(time.time()) - updated) if updated else None
    data["stale"] = bool(updated and data["age_seconds"] > max(settings.space_weather_refresh_seconds * 3, 900))
    return data


def telegram_text() -> str:
    d = snapshot()
    if not d.get("available"):
        return "☀️ FUNKWETTER – NOAA SWPC\n\n⚪ Noch keine Funkwetter-Daten verfügbar."
    age = int(d.get("age_seconds") or 0)
    age_text = f"{age//60} Min." if age >= 60 else "<1 Min."
    flag = "⚠️ Daten veraltet · " if d.get("stale") else ""
    a = d.get("assessment") or {}
    def f(v: Any, unit: str = "") -> str:
        return "—" if v is None else f"{v}{unit}"
    return "\n".join([
        "☀️ FUNKWETTER – NOAA SWPC",
        f"📍 {settings.qth_locator} · {settings.callsign}",
        "",
        f"☀️ SFI: {f(d.get('sfi'))} · SSN: {f(d.get('ssn'))}",
        f"🧲 Kp: {f(d.get('kp'))} · A: {f(d.get('a_index'))} · {d.get('geomagnetic_label') or '—'}",
        f"💨 Solarwind: {f(d.get('solar_wind_kms'), ' km/s')} · Bz: {f(d.get('bz_nt'), ' nT')}",
        f"☢️ X-Ray: {d.get('xray_class') or '—'} · NOAA R{int(d.get('r_scale') or 0)} / S{int(d.get('s_scale') or 0)} / G{int(d.get('g_scale') or 0)}",
        "",
        f"HF Low Bands:  {a.get('low_bands','—')}",
        f"HF High Bands: {a.get('high_bands','—')}",
        f"6 m:           {a.get('six_m','—')}",
        f"Geomagnetik:   {a.get('geomagnetic','—')}",
        "",
        f"{flag}Update: {age_text} alt",
    ])


async def refresh_loop(stop_event: asyncio.Event) -> None:
    if not settings.space_weather_enabled:
        set_health("noaa_swpc", "DISABLED")
        return
    while not stop_event.is_set():
        try:
            await refresh_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            set_health("noaa_swpc", "ERROR", error=str(exc))
            log.exception("NOAA SWPC refresh failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(60, settings.space_weather_refresh_seconds))
        except asyncio.TimeoutError:
            pass
