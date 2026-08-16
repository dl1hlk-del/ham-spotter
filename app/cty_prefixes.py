from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import settings
from .db import set_health
from .dxcc import dxcc_code_by_name

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CtyMatch:
    call: str
    entity: str
    primary_prefix: str
    continent: str | None
    cq_zone: int | None
    itu_zone: int | None
    dxcc: int | None
    source: str = "cty.dat"


@dataclass(frozen=True, slots=True)
class _Rule:
    key: str
    exact: bool
    entity: str
    primary_prefix: str
    continent: str | None
    cq_zone: int | None
    itu_zone: int | None


_exact: dict[str, _Rule] = {}
_prefixes: list[_Rule] = []
_loaded_at = 0
_source = "fallback"

# Emergency fallback only. The normal runtime source is the current CTY.DAT
# country/prefix database, cached under /app/data. These entries cover common
# entities around Europe and the most frequent intercontinental DX prefixes so
# SSB/CW cards still show a country during a temporary first-start outage.
_FALLBACK_ENTITIES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("Slovenia", "S5", "EU", ("S5",)),
    ("Croatia", "9A", "EU", ("9A",)),
    ("England", "G", "EU", ("G", "M", "2E")),
    ("Federal Republic of Germany", "DL", "EU", ("DA", "DB", "DC", "DD", "DF", "DG", "DH", "DJ", "DK", "DL", "DM", "DN", "DO", "DP", "DQ", "DR")),
    ("France", "F", "EU", ("F",)),
    ("Spain", "EA", "EU", ("EA", "EB", "EC", "ED", "EE", "EF", "EG", "EH")),
    ("Portugal", "CT", "EU", ("CT", "CS", "CR", "CQ")),
    ("Italy", "I", "EU", ("I", "IK", "IZ", "IU", "IV", "IW")),
    ("Netherlands", "PA", "EU", ("PA", "PB", "PC", "PD", "PE", "PF", "PG", "PH", "PI")),
    ("Belgium", "ON", "EU", ("ON", "OO", "OP", "OQ", "OR", "OS", "OT")),
    ("Switzerland", "HB", "EU", ("HB",)),
    ("Austria", "OE", "EU", ("OE",)),
    ("Czech Republic", "OK", "EU", ("OK", "OL")),
    ("Slovak Republic", "OM", "EU", ("OM",)),
    ("Poland", "SP", "EU", ("SN", "SO", "SP", "SQ", "SR", "3Z")),
    ("Hungary", "HA", "EU", ("HA", "HG")),
    ("Romania", "YO", "EU", ("YO", "YP", "YQ", "YR")),
    ("Bulgaria", "LZ", "EU", ("LZ",)),
    ("Sweden", "SM", "EU", ("SA", "SB", "SC", "SD", "SE", "SF", "SG", "SH", "SI", "SJ", "SK", "SL", "SM", "7S", "8S")),
    ("Norway", "LA", "EU", ("LA", "LB", "LC", "LD", "LE", "LF", "LG", "LH", "LI", "LJ", "LK", "LL", "LM", "LN")),
    ("Finland", "OH", "EU", ("OH",)),
    ("Denmark", "OZ", "EU", ("OZ", "OU", "OV", "OW", "OX", "XP")),
    ("Ireland", "EI", "EU", ("EI", "EJ")),
    ("United States", "K", "NA", ("K", "N", "W")),
    ("Canada", "VE", "NA", ("VA", "VB", "VC", "VE", "VF", "VG", "VO", "VX", "VY", "CF", "CG", "CH", "CI", "CJ", "CK", "CY", "CZ")),
    ("Japan", "JA", "AS", ("JA", "JE", "JF", "JG", "JH", "JI", "JJ", "JK", "JL", "JM", "JN", "JO", "JP", "JQ", "JR", "JS", "7J", "7K", "7L", "7M", "7N", "8J", "8K", "8L", "8M", "8N")),
    ("Australia", "VK", "OC", ("VK",)),
    ("New Zealand", "ZL", "OC", ("ZL",)),
    ("Brazil", "PY", "SA", ("PP", "PQ", "PR", "PS", "PT", "PU", "PV", "PW", "PX", "PY", "ZV", "ZW", "ZX", "ZY", "ZZ")),
    ("Argentina", "LU", "SA", ("AY", "AZ", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "LO", "LP", "LQ", "LR", "LS", "LT", "LU", "LV", "LW")),
    ("Republic of South Africa", "ZS", "AF", ("ZS", "ZR", "ZT", "ZU")),
)

_MODIFIERS_RE = re.compile(r"(?:\([^)]*\)|\[[^]]*\]|<[^>]*>|\{[^}]*\}|~[^~]*~)")
_PORTABLE_SUFFIXES = {"P", "M", "MM", "AM", "QRP", "QRPP", "A", "B"}


def _cache_path() -> Path:
    return Path(settings.db_path).parent / "cty.dat"


def _safe_int(raw: str) -> int | None:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _clean_alias(token: str) -> tuple[str, bool]:
    token = token.strip()
    exact = token.startswith("=")
    if exact:
        token = token[1:]
    token = token.lstrip("*")
    token = _MODIFIERS_RE.sub("", token).strip().upper()
    return token, exact


def parse_cty(text: str) -> tuple[dict[str, _Rule], list[_Rule]]:
    exact: dict[str, _Rule] = {}
    prefixes: list[_Rule] = []
    current: tuple[str, int | None, int | None, str | None, str] | None = None
    alias_buf: list[str] = []

    def flush_aliases() -> None:
        nonlocal alias_buf
        if current is None or not alias_buf:
            alias_buf = []
            return
        entity, cq, itu, continent, primary = current
        joined = " ".join(alias_buf)
        for raw in joined.replace(";", ",").split(","):
            key, is_exact = _clean_alias(raw)
            if not key or key == "VER" or key.startswith("VER20"):
                continue
            rule = _Rule(key, is_exact, entity, primary, continent, cq, itu)
            if is_exact:
                exact[key] = rule
            else:
                prefixes.append(rule)
        alias_buf = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        # CTY entity header: Country:CQ:ITU:Cont:Lat:Lon:UTC:Primary:
        parts = line.split(":")
        if len(parts) >= 9 and line[:1] not in {" ", "\t"}:
            flush_aliases()
            entity = parts[0].strip()
            cq = _safe_int(parts[1])
            itu = _safe_int(parts[2])
            continent = parts[3].strip().upper() or None
            primary = parts[7].strip().lstrip("*").upper()
            current = (entity, cq, itu, continent, primary)
            continue
        if current is not None:
            alias_buf.append(line.strip())
            if ";" in line:
                flush_aliases()

    flush_aliases()
    # Longest-prefix match is essential: e.g. specific subentities must win
    # over a broad one-character prefix.
    prefixes.sort(key=lambda r: len(r.key), reverse=True)
    return exact, prefixes


def _install_fallback() -> None:
    global _exact, _prefixes, _source
    prefixes: list[_Rule] = []
    for entity, primary, continent, aliases in _FALLBACK_ENTITIES:
        for alias in aliases:
            prefixes.append(_Rule(alias.upper(), False, entity, primary, continent, None, None))
    prefixes.sort(key=lambda r: len(r.key), reverse=True)
    _exact = {}
    _prefixes = prefixes
    _source = "fallback"


def _install_cty(text: str, source: str) -> int:
    global _exact, _prefixes, _source
    exact, prefixes = parse_cty(text)
    if len(prefixes) < 200:
        raise RuntimeError(f"CTY.DAT unexpectedly small: {len(prefixes)} prefix rules")
    _exact = exact
    _prefixes = prefixes
    _source = source
    return len(exact) + len(prefixes)


def _candidate_calls(call: str) -> list[str]:
    c = str(call or "").strip().upper()
    if not c:
        return []
    out = [c]
    if "/" in c:
        parts = [p for p in c.split("/") if p]
        # Full slash call is tried first. For portable calls, prefer the part
        # that looks like a real callsign, but keep the prefix/suffix part as a
        # fallback because CTY.DAT contains many slash-specific rules.
        ranked = sorted(
            (p for p in parts if p not in _PORTABLE_SUFFIXES),
            key=lambda p: (any(ch.isdigit() for ch in p), len(p)),
            reverse=True,
        )
        out.extend(ranked)
    seen = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def lookup_call(call: str) -> CtyMatch | None:
    if not _prefixes:
        _install_fallback()
    for candidate in _candidate_calls(call):
        rule = _exact.get(candidate)
        if rule:
            return CtyMatch(
                call=str(call).upper(), entity=rule.entity, primary_prefix=rule.primary_prefix,
                continent=rule.continent, cq_zone=rule.cq_zone, itu_zone=rule.itu_zone,
                dxcc=dxcc_code_by_name(rule.entity), source=_source,
            )
    for candidate in _candidate_calls(call):
        for rule in _prefixes:
            if candidate.startswith(rule.key):
                return CtyMatch(
                    call=str(call).upper(), entity=rule.entity, primary_prefix=rule.primary_prefix,
                    continent=rule.continent, cq_zone=rule.cq_zone, itu_zone=rule.itu_zone,
                    dxcc=dxcc_code_by_name(rule.entity), source=_source,
                )
    return None


def _load_cached() -> bool:
    global _loaded_at
    path = _cache_path()
    if not path.exists():
        return False
    try:
        count = _install_cty(path.read_text(encoding="utf-8", errors="replace"), "cty.dat cache")
        _loaded_at = int(path.stat().st_mtime)
        log.info("CTY prefix cache loaded: %d rules", count)
        return True
    except Exception as exc:
        log.warning("CTY prefix cache invalid: %s", exc)
        return False


async def refresh_once(force: bool = False) -> int:
    global _loaded_at
    cached = _load_cached()
    age = int(time.time()) - int(_loaded_at or 0)
    if cached and not force and age < max(1, settings.cty_refresh_hours) * 3600:
        set_health("cty_prefixes", "LIVE", seen=True)
        return len(_exact) + len(_prefixes)

    headers = {"User-Agent": f"HAM-Spotter/{settings.callsign}", "Accept": "text/plain,*/*"}
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(settings.cty_resource_url, headers=headers)
            response.raise_for_status()
        text = response.text
        count = _install_cty(text, "cty.dat live")
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        _loaded_at = int(time.time())
        set_health("cty_prefixes", "LIVE", seen=True)
        log.info("CTY prefix catalogue refreshed: %d rules", count)
        return count
    except Exception as exc:
        if cached:
            set_health("cty_prefixes", "DEGRADED", seen=True, error=str(exc))
            log.warning("CTY refresh failed; using cache: %s", exc)
            return len(_exact) + len(_prefixes)
        _install_fallback()
        set_health("cty_prefixes", "DEGRADED", seen=True, error=str(exc))
        log.warning("CTY refresh failed; using built-in prefix fallback: %s", exc)
        return len(_prefixes)


async def refresh_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await refresh_once()
        except Exception:
            log.exception("CTY prefix refresh failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(3600, settings.cty_refresh_hours * 3600))
        except asyncio.TimeoutError:
            pass


_install_fallback()
