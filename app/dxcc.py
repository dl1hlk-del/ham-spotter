from __future__ import annotations

import asyncio
import io
import json
import logging
import statistics
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import httpx

from .config import settings
from .db import set_health
from .geo import locator_to_latlon

log = logging.getLogger(__name__)

# Small emergency fallback. The normal catalogue is loaded from the official
# ADIF 3.1.7 resource archive and cached under /app/data.
_FALLBACK = {
    1: "CANADA",
    6: "ALASKA",
    50: "MEXICO",
    108: "BRAZIL",
    110: "HAWAII",
    150: "AUSTRALIA",
    223: "ENGLAND",
    227: "FRANCE",
    230: "FEDERAL REPUBLIC OF GERMANY",
    248: "ITALY",
    269: "POLAND",
    272: "PORTUGAL",
    279: "SCOTLAND",
    281: "SPAIN",
    291: "UNITED STATES OF AMERICA",
    318: "CHINA",
    339: "JAPAN",
}

_GERMAN_NAMES = {
    "UNITED STATES OF AMERICA": "USA",
    "CANADA": "Kanada",
    "ALASKA": "Alaska",
    "MEXICO": "Mexiko",
    "BRAZIL": "Brasilien",
    "ARGENTINA": "Argentinien",
    "CHILE": "Chile",
    "COLOMBIA": "Kolumbien",
    "VENEZUELA": "Venezuela",
    "FRANCE": "Frankreich",
    "FEDERAL REPUBLIC OF GERMANY": "Deutschland",
    "ENGLAND": "England",
    "SCOTLAND": "Schottland",
    "WALES": "Wales",
    "NORTHERN IRELAND": "Nordirland",
    "IRELAND": "Irland",
    "SPAIN": "Spanien",
    "PORTUGAL": "Portugal",
    "ITALY": "Italien",
    "GREECE": "Griechenland",
    "NETHERLANDS": "Niederlande",
    "BELGIUM": "Belgien",
    "SWITZERLAND": "Schweiz",
    "AUSTRIA": "Österreich",
    "DENMARK": "Dänemark",
    "NORWAY": "Norwegen",
    "SWEDEN": "Schweden",
    "FINLAND": "Finnland",
    "POLAND": "Polen",
    "CZECH REPUBLIC": "Tschechien",
    "SLOVAK REPUBLIC": "Slowakei",
    "HUNGARY": "Ungarn",
    "ROMANIA": "Rumänien",
    "BULGARIA": "Bulgarien",
    "CROATIA": "Kroatien",
    "SLOVENIA": "Slowenien",
    "SERBIA": "Serbien",
    "UKRAINE": "Ukraine",
    "EUROPEAN RUSSIA": "Europäisches Russland",
    "ASIATIC RUSSIA": "Asiatisches Russland",
    "TURKEY": "Türkei",
    "MOROCCO": "Marokko",
    "ALGERIA": "Algerien",
    "TUNISIA": "Tunesien",
    "EGYPT": "Ägypten",
    "REPUBLIC OF SOUTH AFRICA": "Südafrika",
    "JAPAN": "Japan",
    "CHINA": "China",
    "REPUBLIC OF KOREA": "Südkorea",
    "INDIA": "Indien",
    "THAILAND": "Thailand",
    "INDONESIA": "Indonesien",
    "AUSTRALIA": "Australien",
    "NEW ZEALAND": "Neuseeland",
}

_catalog: dict[int, str] = dict(_FALLBACK)
_catalog_loaded_at = 0


def _catalog_path() -> Path:
    return Path(settings.db_path).parent / "dxcc_entities.json"


def _display_name(name: str) -> str:
    raw = name.strip().upper()
    if raw in _GERMAN_NAMES:
        return _GERMAN_NAMES[raw]
    # Keep established DXCC names readable without inventing translations.
    return name.strip().title().replace(" I.", " I.").replace(" Is.", " Is.")


def dxcc_name(code: int | None) -> str | None:
    if code is None:
        return None
    name = _catalog.get(int(code))
    return _display_name(name) if name else None


def entity_display_name(name: str | None) -> str | None:
    return _display_name(str(name)) if name else None


_NAME_ALIASES = {
    "UNITED STATES": "UNITED STATES OF AMERICA",
    "USA": "UNITED STATES OF AMERICA",
    "FED. REP. OF GERMANY": "FEDERAL REPUBLIC OF GERMANY",
    "GERMANY": "FEDERAL REPUBLIC OF GERMANY",
    "SOUTH KOREA": "REPUBLIC OF KOREA",
    "SOUTH AFRICA": "REPUBLIC OF SOUTH AFRICA",
    "CZECH REPUBLIC": "CZECHIA",
}


def _norm_entity_name(name: str) -> str:
    value = " ".join(str(name or "").upper().replace("&", "AND").split())
    return _NAME_ALIASES.get(value, value)


def dxcc_code_by_name(name: str | None) -> int | None:
    """Best-effort bridge from CTY.DAT entity names to ADIF DXCC codes.

    CTY.DAT remains authoritative for the callsign-prefix match.  We only add
    a numeric ADIF DXCC code when the entity name maps unambiguously; otherwise
    callers still receive the country/entity name and no numeric code.
    """
    if not name:
        return None
    wanted = _norm_entity_name(name)
    for code, entity in _catalog.items():
        if _norm_entity_name(entity) == wanted:
            return int(code)
    return None


def _extract_catalog(payload: bytes) -> dict[int, str]:
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        candidates = [
            n for n in zf.namelist()
            if n.lower().endswith("/exports/json/enumerations_dxcc_entity_code.json")
            or n.lower().endswith("enumerations_dxcc_entity_code.json")
        ]
        if not candidates:
            raise RuntimeError("ADIF resource archive contains no DXCC entity JSON")
        data = json.loads(zf.read(candidates[0]).decode("utf-8"))

    enums = ((data.get("Adif") or {}).get("Enumerations") or {})
    table = None
    for key, value in enums.items():
        normalized = str(key).lower().replace(" ", "_")
        if normalized == "dxcc_entity_code":
            table = value
            break
    if not table:
        raise RuntimeError("ADIF DXCC enumeration not found in JSON")

    out: dict[int, str] = {}
    records = table.get("Records") or {}
    for key, row in records.items():
        if not isinstance(row, dict):
            continue
        code_raw = row.get("Entity Code") or row.get("DXCC Entity Code") or str(key).split(".", 1)[0]
        name = row.get("Entity Name") or row.get("DXCC Entity") or row.get("Country")
        try:
            code = int(code_raw)
        except (TypeError, ValueError):
            continue
        if name and code > 0:
            out[code] = str(name).strip()
    if len(out) < 300:
        raise RuntimeError(f"ADIF DXCC catalogue unexpectedly small: {len(out)}")
    return out


def _load_cached() -> bool:
    global _catalog, _catalog_loaded_at
    path = _catalog_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entities = {int(k): str(v) for k, v in (data.get("entities") or {}).items()}
        if len(entities) < 300:
            return False
        _catalog = entities
        _catalog_loaded_at = int(data.get("updated_at") or 0)
        return True
    except Exception:
        return False


def _save_cached(entities: dict[int, str]) -> None:
    path = _catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"updated_at": int(time.time()), "entities": entities}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def refresh_once(force: bool = False) -> int:
    global _catalog, _catalog_loaded_at
    cached = _load_cached()
    age = int(time.time()) - _catalog_loaded_at
    if cached and not force and age < settings.adif_refresh_hours * 3600:
        set_health("adif_dxcc", "LIVE", seen=True)
        return len(_catalog)

    headers = {"User-Agent": f"HAM-Spotter/{settings.callsign}", "Accept": "application/zip,*/*"}
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(settings.adif_resource_url, headers=headers)
            response.raise_for_status()
        entities = _extract_catalog(response.content)
        _catalog = entities
        _catalog_loaded_at = int(time.time())
        _save_cached(entities)
        set_health("adif_dxcc", "LIVE", seen=True)
        log.info("ADIF DXCC catalogue refreshed: %d entities", len(entities))
        return len(entities)
    except Exception as exc:
        if cached or _catalog:
            set_health("adif_dxcc", "DEGRADED", seen=bool(cached), error=str(exc))
            log.warning("ADIF DXCC refresh failed; using cache/fallback: %s", exc)
            return len(_catalog)
        set_health("adif_dxcc", "ERROR", error=str(exc))
        raise


async def refresh_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await refresh_once()
        except Exception:
            log.exception("ADIF DXCC catalogue refresh failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(3600, settings.adif_refresh_hours * 3600))
        except asyncio.TimeoutError:
            pass


def geo_region(lat: float, lon: float) -> str:
    # Coarse propagation regions. Exact country comes from ADIF DXCC; this label
    # only groups the dominant opening geographically. North America must be
    # evaluated before Central America/Caribbean so southern US grids are not
    # misclassified.
    if 24 <= lat <= 85 and -170 <= lon <= -50:
        return "Nordamerika"
    if 5 <= lat < 24 and -120 <= lon <= -55:
        return "Mittelamerika/Karibik"
    if -60 <= lat < 15 and -95 <= lon <= -30:
        return "Südamerika"
    if lat >= 34 and -25 <= lon <= 45:
        return "Europa"
    if 10 <= lat <= 45 and 30 < lon <= 65:
        return "Nahost"
    if 12 <= lat < 34 and -20 <= lon <= 45:
        return "Nordafrika"
    if -40 <= lat < 12 and -20 <= lon <= 55:
        return "Afrika"
    if lat >= 0 and 45 < lon <= 180:
        return "Asien"
    if lat < 0 and (90 <= lon <= 180 or lon <= -150):
        return "Ozeanien/Pazifik"
    return "andere Region"


def summarize_psk_rows(rows: Iterable[Any], top_sector: int | None) -> dict[str, Any]:
    rows = list(rows)
    focused = [r for r in rows if top_sector is not None and r["sector"] is not None and int(r["sector"]) == int(top_sector)]
    if len({str(r["tx_call"]).upper() for r in focused if r["tx_call"]}) < 3:
        focused = rows

    calls_by_dxcc: dict[int, set[str]] = defaultdict(set)
    calls_by_region: dict[str, set[str]] = defaultdict(set)
    for r in focused:
        call = str(r["tx_call"] or "").upper()
        if not call:
            continue
        if r["tx_dxcc"] is not None:
            try:
                calls_by_dxcc[int(r["tx_dxcc"])].add(call)
            except (TypeError, ValueError):
                pass
        grid = str(r["tx_grid"] or "").upper()
        if len(grid) >= 4:
            try:
                lat, lon = locator_to_latlon(grid[:8])
                dxcc_code = None
                if r["tx_dxcc"] is not None:
                    try:
                        dxcc_code = int(r["tx_dxcc"])
                    except (TypeError, ValueError):
                        pass
                # These entities span/brush the latitude boundary but belong to
                # North America for the purpose of propagation grouping.
                if dxcc_code in {1, 6, 50, 110, 291}:
                    region = "Nordamerika"
                else:
                    region = geo_region(lat, lon)
                calls_by_region[region].add(call)
            except ValueError:
                pass

    countries = []
    for code, calls in sorted(calls_by_dxcc.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:5]:
        countries.append({"dxcc": code, "name": dxcc_name(code) or f"DXCC {code}", "calls": len(calls)})

    regions = sorted(((name, len(calls)) for name, calls in calls_by_region.items()), key=lambda x: (-x[1], x[0]))
    dominant_region = regions[0][0] if regions else None
    dominant_region_calls = regions[0][1] if regions else 0
    target_distances = [
        float(r["tx_distance_km"])
        for r in focused
        if r["tx_distance_km"] is not None
    ]
    target_median_dx_km = round(statistics.median(target_distances)) if target_distances else 0
    return {
        "dominant_region": dominant_region,
        "dominant_region_calls": dominant_region_calls,
        "countries": countries,
        "country_basis": "PSK Reporter unique TX",
        "target_median_dx_km": target_median_dx_km,
    }
