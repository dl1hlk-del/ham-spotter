from __future__ import annotations

from typing import Any


STATE_ICON = {
    "CLOSED": "🔴",
    "WATCH": "🟡",
    "OPEN": "🟢",
    "STRONG": "🔥",
}


def band_label(band: str) -> str:
    b = str(band).strip().lower()
    if b.endswith("cm") and b[:-2].isdigit():
        return f"{b[:-2]} cm"
    if b.endswith("m") and b[:-1].isdigit():
        return f"{b[:-1]} m"
    return str(band).upper()


def _km(value: Any) -> str | None:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return f"{n:,}".replace(",", ".")


def _trend(details: dict[str, Any]) -> int | None:
    try:
        return int(round((float(details.get("trend_ratio")) - 1.0) * 100))
    except (TypeError, ValueError):
        return None


def _countries(details: dict[str, Any], limit: int = 4) -> str:
    countries = details.get("countries") or []
    parts: list[str] = []
    for country in countries[:limit]:
        name = str(country.get("name") or "").strip()
        calls = country.get("calls")
        if not name:
            continue
        parts.append(name if calls is None else f"{name} {calls}")
    return " · ".join(parts)


def _source_confirmation(details: dict[str, Any]) -> str:
    psk = int(details.get("psk_unique_tx") or 0)
    rbn = int(details.get("rbn_unique_tx") or 0)
    if psk and rbn:
        return "✅ PSK Reporter + RBN bestätigen die Aktivität"
    if psk:
        return "🟡 Aktivität derzeit nur durch PSK Reporter bestätigt"
    if rbn:
        return "🟡 Aktivität derzeit nur durch RBN bestätigt"
    return "⚪ Keine aktuelle Quellenbestätigung"


def _recommendation(state: str) -> str:
    state = str(state).upper()
    if state == "STRONG":
        return "🔥 Einschätzung: Sehr lohnend – Band jetzt prüfen"
    if state == "OPEN":
        return "✅ Einschätzung: Einschalten lohnt sich"
    if state == "WATCH":
        return "👀 Einschätzung: Beobachten – noch keine belastbare Öffnung"
    return "⛔ Einschätzung: Aktuell keine belastbare DX-Öffnung"


def _direction_line(details: dict[str, Any]) -> str | None:
    confidence = str(details.get("direction_confidence") or "NONE").upper()
    pct = int(details.get("direction_confidence_pct") or 0)
    calls = int(details.get("top_sector_unique_tx") or 0)
    labels = {"HIGH": "hoch", "MEDIUM": "mittel", "LOW": "gering"}
    if confidence not in labels:
        return None
    if details.get("direction_reliable"):
        return f"🎯 Richtungs-Sicherheit: {labels[confidence]} · {pct}% · {calls} DX im Hauptsektor"
    return f"⚠️ Richtung noch nicht eindeutig · {pct}% · {calls} DX im Hauptsektor"


def compact_status_text(snapshot: dict[str, Any], *, qth: str, callsign: str) -> str:
    title_call = f"{callsign} " if callsign else ""
    lines = [f"📡 {title_call}HAM SPOTTER – {qth}"]
    for b in snapshot.get("bands", []):
        state = str(b.get("state") or "CLOSED")
        details = b.get("details") or {}
        region = details.get("dominant_region")
        suffix = f" · {region}" if region else ""
        direction = b.get("direction_label") or "unbekannt"
        lines.append(
            f"{str(b.get('band') or ''):>3}  {STATE_ICON.get(state, '⚪')} "
            f"{state} {int(b.get('score') or 0)}/100  {direction}{suffix}"
        )
    return "\n".join(lines)


def band_detail_text(
    band: str,
    state: str,
    score: int,
    direction: str,
    details: dict[str, Any],
    *,
    qth: str,
    callsign: str,
) -> str:
    state = str(state).upper()
    icon = STATE_ICON.get(state, "⚪")
    region = details.get("dominant_region")
    countries = _countries(details)
    target_dx = _km(details.get("target_median_dx_km") or details.get("median_dx_km"))
    trend = _trend(details)
    psk_tx = int(details.get("psk_unique_tx") or 0)
    psk_rx = int(details.get("psk_unique_rx") or 0)
    rbn_tx = int(details.get("rbn_unique_tx") or 0)
    rbn_rx = int(details.get("rbn_unique_rx") or 0)
    best_snr = details.get("best_snr")
    reliable = bool(details.get("direction_reliable"))
    primary_mode = str(details.get("primary_mode") or details.get("mode") or "digital").lower()
    mode_icon = {"ssb": "🎙️", "cw": "📻", "digital": "💻"}.get(primary_mode, "📡")

    lines = [
        f"{icon} {band_label(band)} – {state}",
        f"{mode_icon} Primärmodus: {primary_mode.upper()}",
        f"📍 {qth}" + (f" · {callsign}" if callsign else ""),
    ]

    if region:
        lines.append(f"🌍 {'Zielgebiet' if reliable else 'Hauptsektor'}: {region}")
    if countries:
        lines.append(f"🏳️ {'DXCC' if reliable else 'DXCC im Hauptsektor'}: {countries}")

    lines.append(f"🧭 Richtung: {direction or 'unbekannt'}")
    confidence_line = _direction_line(details)
    if confidence_line:
        lines.append(confidence_line)

    if target_dx:
        lines.append(f"📏 {'Ziel-DX' if reliable else 'Hauptsektor-DX'}: ca. {target_dx} km")

    score_line = f"📊 Score: {int(score)}/100"
    if trend is not None:
        score_line += f" · Trend {trend:+d}%"
    lines.append(score_line)

    if primary_mode == "ssb":
        lines += [
            f"🎙️ SSB: {int(details.get('unique_tx') or 0)} DX / {int(details.get('unique_rx') or 0)} regionale Cluster-Spotter",
            f"✅ Mehrfach bestätigt: {int(details.get('confirmed_tx') or 0)} DX-Calls",
            f"💻 Digital-Kontext: {int(details.get('digital_context_score') or 0)}/100",
        ]
    elif primary_mode == "cw":
        lines += [
            f"📻 CW/RBN: {int(details.get('unique_tx') or 0)} Calls / {int(details.get('unique_rx') or 0)} lokale Skimmer",
            "🧭 Richtung nur bei zeitgleicher PSK-Korrelation",
        ]
    else:
        lines += [
            f"📡 PSK: {psk_tx} DX / {psk_rx} lokale RX",
            f"📻 RBN FT8: {rbn_tx} Calls / {rbn_rx} lokale Skimmer",
            _source_confirmation(details),
        ]

    if best_snr is not None:
        try:
            lines.append(f"📶 Bestes PSKR-SNR: {float(best_snr):+.0f} dB")
        except (TypeError, ValueError):
            pass

    if str(band).lower() == "6m" and details.get("top_sector") is not None:
        if reliable and int(details.get("top_sector_unique_tx") or 0) >= 3:
            try:
                lines.append(f"🎯 Yagi: ca. {int(details['top_sector']):03d}°")
            except (TypeError, ValueError):
                pass
        else:
            lines.append("⚠️ Keine Yagi-Empfehlung – Richtung noch nicht eindeutig")

        try:
            md = float(details.get("target_median_dx_km") or details.get("median_dx_km") or 0)
        except (TypeError, ValueError):
            md = 0
        if 700 <= md <= 2500:
            lines.append("⚡ Es-typischer Distanzbereich (kein sicherer Es-Nachweis)")

    lines.append(_recommendation(state))
    return "\n".join(lines)


def duration_text(seconds: Any) -> str:
    try:
        value = max(0, int(seconds or 0))
    except (TypeError, ValueError):
        return "—"
    hours, rem = divmod(value, 3600)
    minutes, _ = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def history_text(events: list[dict[str, Any]], *, qth: str, limit: int = 6) -> str:
    if not events:
        return f"🕘 OPENING-HISTORIE – {qth}\nNoch keine Opening-Ereignisse gespeichert."
    lines = [f"🕘 OPENING-HISTORIE – {qth}"]
    for event in events[:limit]:
        state = str(event.get("max_state") or "OPEN")
        icon = STATE_ICON.get(state, "⚪")
        region = str(event.get("dominant_region") or "ohne Zielgebiet")
        direction = str(event.get("direction_label") or "Richtung offen")
        live = " · LIVE" if event.get("active") else ""
        lines.append(
            f"{icon} {event.get('band','')} · {region} · {direction} · "
            f"{duration_text(event.get('duration_seconds'))} · max {int(event.get('max_score') or 0)}/100{live}"
        )
    return "\n".join(lines)


def stats_text(stats: dict[str, Any], *, qth: str) -> str:
    days = int(stats.get("days") or 0)
    lines = [f"📈 OPENING-STATISTIK – {qth} · {days} Tage"]
    for row in stats.get("bands", []):
        events = int(row.get("events") or 0)
        if not events:
            lines.append(f"{row.get('band',''):>3}  — keine Openings")
            continue
        region = str(row.get("top_region") or "—")
        sector = row.get("top_sector")
        direction = f"{int(sector):03d}°" if sector is not None else "—"
        lines.append(
            f"{row.get('band',''):>3}  {events} Opens · {duration_text(row.get('total_seconds'))} · "
            f"max {int(row.get('max_score') or 0)}/100 · {region} · {direction}"
        )
    return "\n".join(lines)
